"""Offline tests for the evaluation recorder, never calls a live service."""
import json
import unittest
from unittest.mock import Mock

import requests

from evaluate import collect_stream, record_case, retrieval_metrics, sse_events, structural_checks


class EvaluationTests(unittest.TestCase):
    def test_product_budget_is_separate_from_knowledge_and_total_budget(self):
        case = {'checks': {'max_product_calls': 3, 'max_total_calls': 5}}
        tools = [{'name': 'search_product', 'toolCallId': str(i), 'status': 'done'} for i in range(4)]
        checks = structural_checks(case, [{'status': 'success', 'tools': tools}])
        self.assertFalse(checks['product_budget_respected'])
        self.assertTrue(checks['total_tool_budget_respected'])

    def test_controlled_case_cannot_bypass_the_cli_skip_guard(self):
        client = Mock()
        case = {"case_id": "controlled", "execution": {"kind": "controlled", "ready": False}}
        with self.assertRaises(RuntimeError):
            record_case(client, case, "agent")
        client.chat.assert_not_called()

    def test_total_budget_counts_product_calls_not_progress_events(self):
        case = {"checks": {"max_total_calls": 2}}
        tools = [{"name": "search_product", "toolCallId": str(index), "status": state}
                 for index in range(2) for state in ["pending", "running", "done"]]
        turn = {"status": "success", "tools": tools}
        self.assertTrue(structural_checks(case, [turn])["total_tool_budget_respected"])
        tools.append({"name": "search_product", "toolCallId": "extra", "status": "pending"})
        self.assertFalse(structural_checks(case, [turn])["total_tool_budget_respected"])

    def test_total_budget_includes_other_tools_and_confirmation_cleanup(self):
        case = {"checks": {"max_total_calls": 2}}
        turn = {"status": "success", "tools": [
            {"name": "search_knowledge", "toolCallId": "k1", "status": "done"},
            {"name": "query_order", "toolCallId": "o1", "status": "running"}],
            "denial_cleanup": {"tools": [{"name": "query_order", "toolCallId": "o1", "status": "done"}]}}
        self.assertTrue(structural_checks(case, [turn])["total_tool_budget_respected"])
        turn["denial_cleanup"]["tools"].append({"name": "load_skill", "toolCallId": "s1", "status": "done"})
        self.assertFalse(structural_checks(case, [turn])["total_tool_budget_respected"])

    def test_utf8_can_split_inside_a_chinese_character(self):
        raw = 'event: message\r\ndata: {"type":"response","delta":"你好"}\r\n\r\n'.encode()
        events = list(sse_events([raw[i:i + 1] for i in range(len(raw))]))
        self.assertEqual(json.loads(events[0][1])["delta"], "你好")

    def test_multiline_data_and_comments(self):
        events = list(sse_events([b': ping\nevent: tool\ndata: one\ndata: two\n\n']))
        self.assertEqual(events, [("tool", "one\ntwo")])

    def test_missing_finish_is_not_success(self):
        result = collect_stream([b'event: done\ndata: {}\n\n'], clock=lambda: 0)
        self.assertEqual(result["status"], "incomplete")

    def test_error_delta_prevents_success(self):
        raw = b'event: message\ndata: {"type":"error","delta":"failure"}\n\nevent: finish\ndata: {}\n\nevent: done\ndata: {}\n\n'
        self.assertEqual(collect_stream([raw], clock=lambda: 0)["status"], "error")

    def test_empty_finish_is_not_success(self):
        raw = b'event: finish\ndata: {}\n\nevent: done\ndata: {}\n\n'
        self.assertEqual(collect_stream([raw], clock=lambda: 0)["status"], "incomplete")

    def test_confirmation_is_a_wait_state(self):
        raw = b'event: confirm\ndata: {"messageId":"m1"}\n\nevent: done\ndata: {}\n\n'
        result = collect_stream([raw], clock=lambda: 0)
        self.assertEqual(result["status"], "wait_confirm")
        self.assertEqual(result["confirm"]["messageId"], "m1")

    def test_real_done_sentinel_completes_a_successful_stream(self):
        raw = b'event: message\ndata: {"type":"response","delta":"answer"}\n\nevent: finish\ndata: {}\n\nevent: done\ndata: [DONE]\n\n'
        self.assertEqual(collect_stream([raw], clock=lambda: 0)["status"], "success")

    def test_real_done_sentinel_completes_a_confirmation(self):
        raw = b'event: confirm\ndata: {"messageId":"m1"}\n\nevent: done\ndata: [DONE]\n\n'
        self.assertEqual(collect_stream([raw], clock=lambda: 0)["status"], "wait_confirm")

    def test_retrieval_failures_do_not_become_empty_retrieval_scores(self):
        cases = [{"case_id": "a", "expected_doc_ids": ["A", "B"]},
                 {"case_id": "b", "expected_doc_ids": ["B"]}]
        records = [{"case_id": "a", "retrieval": {"retrievedDocIds": ["X", "A", "A", "B"]}},
                   {"case_id": "b", "retrieval_error": "timeout"}]
        result = retrieval_metrics(cases, records)
        self.assertEqual(result["eligible"], 2)
        self.assertEqual(result["scored"], 1)
        self.assertEqual(result["retrieval_errors"], 1)
        self.assertEqual(result["hit@1"], 0)
        self.assertEqual(result["recall@5"], 1)
        self.assertEqual(result["mrr@10"], 0.5)

    def test_forbidden_tools_are_seen_even_when_failed(self):
        case = {"checks": {"forbidden_tools": ["cancel_order"]}}
        turn = {"status": "success", "tools": [{"name": "cancel_order", "status": "failed"}]}
        checks = structural_checks(case, [turn])
        self.assertFalse(checks["forbidden_tools_absent"])
        self.assertEqual(checks["semantic_verdict"], "pending_review")

    def test_a_failed_required_tool_does_not_pass(self):
        case = {"checks": {"required_tools": ["search_knowledge"]}}
        turn = {"status": "success", "tools": [{"name": "search_knowledge", "status": "failed"}]}
        self.assertFalse(structural_checks(case, [turn])["required_tools_succeeded"])

    def test_confirm_is_only_valid_when_expected(self):
        turn = {"status": "wait_confirm", "tools": []}
        self.assertFalse(structural_checks({"checks": {}}, [turn])["terminal_states_valid"])
        self.assertTrue(structural_checks({"checks": {"expect_confirm": True}}, [turn])["terminal_states_valid"])

    def test_search_budget_counts_calls_not_progress_events(self):
        case = {"checks": {"max_search_calls": 1}}
        events = [{"name": "search_knowledge", "toolCallId": "a", "status": status}
                  for status in ["pending", "running", "done"]]
        turn = {"status": "success", "tools": events}
        self.assertTrue(structural_checks(case, [turn])["search_budget_respected"])
        turn["tools"].append({"name": "search_knowledge", "toolCallId": "b", "status": "pending"})
        self.assertFalse(structural_checks(case, [turn])["search_budget_respected"])

    def test_confirmation_proposal_is_allowed_but_execution_is_not(self):
        case = {"checks": {"expect_confirm": True, "forbidden_executed_tools": ["cancel_order"]}}
        turn = {"status": "wait_confirm", "tools": [{"name": "cancel_order", "status": "awaiting"}]}
        self.assertTrue(structural_checks(case, [turn])["forbidden_tool_execution_absent"])
        turn["tools"][0]["status"] = "running"
        self.assertFalse(structural_checks(case, [turn])["forbidden_tool_execution_absent"])

    def test_stream_disconnect_preserves_partial_evidence(self):
        def chunks():
            yield b'event: meta\ndata: {"conversationId":"c1"}\n\nevent: message\ndata: {"type":"response","delta":"partial"}\n\n'
            raise requests.ConnectionError("disconnect")
        result = collect_stream(chunks(), clock=lambda: 0)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["response"], "partial")
        self.assertEqual(result["meta"]["conversationId"], "c1")

    def test_collector_consumes_eof_after_done(self):
        consumed = []
        def chunks():
            yield b'event: done\ndata: [DONE]\n\n'
            consumed.append(True)
        collect_stream(chunks(), clock=lambda: 0)
        self.assertEqual(consumed, [True])

    def test_unrecorded_retrieval_is_separate_from_api_failure(self):
        result = retrieval_metrics([{"case_id": "missing", "expected_doc_ids": ["A"]}], [])
        self.assertEqual(result["unrecorded"], 1)
        self.assertEqual(result["retrieval_errors"], 0)

    def test_incomplete_confirmation_is_still_denied(self):
        client = Mock()
        client.chat.return_value = {"status": "incomplete", "tools": [], "meta": {"conversationId": "c1"},
                                    "confirm": {"messageId": "m1"}}
        client.deny_confirmation.return_value = {"status": "success", "tools": []}
        record = record_case(client, {"case_id": "a", "turns": ["cancel"], "checks": {"expect_confirm": True}}, "agent")
        client.deny_confirmation.assert_called_once()
        self.assertEqual(record["turns"][0]["status"], "incomplete")
        self.assertTrue(record["checks"]["confirmation_cleanup_completed"])

    def test_failed_confirmation_cleanup_does_not_pass(self):
        case = {"checks": {"expect_confirm": True}}
        turn = {"status": "wait_confirm", "tools": [], "confirm": {"messageId": "m1"},
                "denial_cleanup": {"status": "incomplete", "tools": []}}
        self.assertFalse(structural_checks(case, [turn])["confirmation_cleanup_completed"])

    def test_conditional_confirmation_without_fixture_verification_is_unassessed(self):
        case = {"checks": {"expect_confirm": True, "confirmation_conditional": True}}
        turn = {"status": "success", "tools": []}
        self.assertIsNone(structural_checks(case, [turn])["confirmation_observed"])

    def test_multiturn_case_reuses_the_same_conversation(self):
        client = Mock()
        client.chat.side_effect = [{"status": "success", "tools": [], "meta": {"conversationId": "c1"}},
                                   {"status": "success", "tools": [], "meta": {"conversationId": "c1"}}]
        record = record_case(client, {"case_id": "a", "turns": ["first", "followup"]}, "agent")
        self.assertEqual(client.chat.call_args_list[1].args, ("followup", "agent", "c1"))
        self.assertTrue(record["checks"]["conversation_continuity_valid"])

    def test_a_changed_conversation_is_flagged(self):
        client = Mock()
        client.chat.side_effect = [{"status": "success", "tools": [], "meta": {"conversationId": "c1"}},
                                   {"status": "success", "tools": [], "meta": {"conversationId": "c2"}}]
        record = record_case(client, {"case_id": "a", "turns": ["first", "followup"]}, "agent")
        self.assertFalse(record["checks"]["conversation_continuity_valid"])


if __name__ == "__main__":
    unittest.main()
