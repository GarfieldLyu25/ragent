"""Offline checks for Agent decision scenarios and their execution scope."""
import json
import io
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from agent_scenarios import build_agent_scenarios
from evaluate import main, select_runnable_cases

ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / 'ragenteval-main'


class AgentScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rag = [json.loads(line) for line in
                   (PROJECT / 'eval/rag/dataset/eval_set_v2.jsonl').read_text(encoding='utf-8').splitlines()]
        docs = {path.stem: path for path in
                (ROOT / 'resources/initializer/bit-selection/docs/knowledge').rglob('*.md')}
        cls.cases = build_agent_scenarios(cls.rag, docs, ROOT)

    def test_counts_and_unique_user_inputs(self):
        self.assertEqual(len(self.cases), 40)
        self.assertEqual(len({case['case_id'] for case in self.cases}), 40)
        self.assertEqual(sum(len(case['turns']) for case in self.cases), 48)
        self.assertEqual(Counter(case['category'] for case in self.cases)['evidence_coverage'], 8)

    def test_coverage_uses_identical_baseline_questions(self):
        baseline = {case['case_id']: case for case in self.rag}
        for case in self.cases:
            if case['category'] == 'evidence_coverage':
                original = baseline[case['baseline_case_id']]
                self.assertEqual(case['turns'], [original['query']])
                self.assertEqual(case['expected_doc_ids'], original['expected_doc_ids'])
                self.assertGreaterEqual(len(case['evidence_requirements']), 2)
                self.assertNotIn('min_search_calls', case['checks'])

    def test_each_case_has_success_failure_and_trace_criteria(self):
        for case in self.cases:
            self.assertTrue(case['success_criteria'])
            self.assertTrue(case['failure_criteria'])
            self.assertIn('tool_inputs', case['trace_review_fields'])
            self.assertIn('tool_outputs', case['trace_review_fields'])
            self.assertEqual(case['gold_status'], 'agent_decision_rubric_pending_review')

    def test_controlled_recovery_cannot_be_run_as_live(self):
        runnable, skipped = select_runnable_cases(self.cases)
        self.assertEqual(len(runnable), 34)
        self.assertEqual(len(skipped), 6)
        self.assertTrue(all(case['execution']['kind'] == 'live' for case in runnable))
        for case in self.cases:
            if case['execution']['kind'] == 'controlled':
                self.assertFalse(case['execution']['ready'])
                self.assertTrue(case['controlled_fixture']['trigger'])
                self.assertEqual(case['execution']['required_capability'], 'tool_result_injection')

    def test_source_excerpts_are_real_local_evidence(self):
        for case in self.cases:
            for span in case.get('source_spans', []):
                self.assertIn(span['excerpt'], (ROOT / span['path']).read_text(encoding='utf-8'))

    def test_legacy_cases_remain_runnable(self):
        runnable, skipped = select_runnable_cases([{'case_id': 'legacy'}])
        self.assertEqual(runnable, [{'case_id': 'legacy'}])
        self.assertEqual(skipped, [])

    def test_unknown_execution_kind_is_never_assumed_live(self):
        runnable, skipped = select_runnable_cases([{'case_id': 'x', 'execution': {'kind': 'unknown'}}])
        self.assertEqual(runnable, [])
        self.assertEqual(skipped[0]['case_id'], 'x')

    def test_recording_report_exposes_skips_and_never_executes_controlled_cases(self):
        client = Mock()
        client.chat.return_value = {'status': 'success', 'response': 'mock answer', 'tools': [],
                                    'meta': {'conversationId': 'mock'}, 'ttft_ms': 1}
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as temporary:
            run = Path(temporary) / 'run'
            with patch('evaluate.Client', return_value=client), patch('evaluate.preflight', return_value={}), redirect_stdout(io.StringIO()):
                self.assertEqual(main(['run', '--mode', 'agent', '--suite', 'agentic', '--run-dir', str(run)]), 0)
            summary = json.loads((run / 'summary.json').read_text(encoding='utf-8'))
            records = [json.loads(line) for line in (run / 'records.jsonl').read_text(encoding='utf-8').splitlines()]
            controlled_ids = {case['case_id'] for case in self.cases if case['execution']['kind'] == 'controlled'}
            self.assertEqual(summary['planned_cases'], 40)
            self.assertEqual(summary['scheduled_cases'], 34)
            self.assertEqual(summary['recorded_cases'], 34)
            self.assertEqual(client.chat.call_count, 42)
            self.assertFalse(controlled_ids.intersection(record['case_id'] for record in records))
            self.assertEqual(summary['groups']['feedback_recovery']['skipped'], 6)
            self.assertEqual(summary['groups']['feedback_recovery']['structural_all_checks_pass'], 0)


if __name__ == '__main__':
    unittest.main()
