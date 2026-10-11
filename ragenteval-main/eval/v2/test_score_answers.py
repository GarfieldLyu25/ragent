"""Offline tests for scoring saved answers without rerunning the backend."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from eval.v2.score_answers import main, prepare_inputs, validate_resume


class SavedAnswerTests(unittest.TestCase):
    def setUp(self):
        self.cases = [{"case_id": "DOC-1", "query": "question", "category": "policy",
                       "expected_doc_ids": ["policy"], "reference": "reference"},
                      {"case_id": "CHAT-1", "query": "hi", "category": "chat",
                       "expected_doc_ids": [], "reference": "hello"}]
        self.records = [{"case_id": "DOC-1", "turns": [
            {"status": "success", "response": "saved answer"}]}]

    def test_correctness_does_not_require_separate_retrieval(self):
        inputs, skipped = prepare_inputs(self.cases, self.records)
        self.assertEqual(inputs[0]["response"], "saved answer")
        self.assertEqual(inputs[0]["reference"], "reference")
        self.assertEqual(skipped, [])
        self.assertNotIn("retrieved_contexts", inputs[0])

    def test_excludes_unlabelled_cases_and_keeps_source_unchanged(self):
        before = copy.deepcopy((self.cases, self.records))
        inputs, _ = prepare_inputs(self.cases, self.records)
        self.assertEqual([x["case_id"] for x in inputs], ["DOC-1"])
        self.assertEqual((self.cases, self.records), before)

    def test_missing_and_failed_answers_remain_visible(self):
        _, skipped = prepare_inputs(self.cases, [])
        self.assertEqual(skipped, [{"case_id": "DOC-1", "reason": "missing_record"}])
        self.records[0]["turns"][0]["status"] = "error"
        self.assertEqual(prepare_inputs(self.cases, self.records)[1][0]["reason"], "not_success")
        self.records[0]["turns"][0].update(status="success", response=" ")
        self.assertEqual(prepare_inputs(self.cases, self.records)[1][0]["reason"], "empty_response")

    def test_multiple_turns_cannot_be_silently_truncated(self):
        self.records[0]["turns"].append({"status": "success", "response": "second"})
        self.assertEqual(prepare_inputs(self.cases, self.records)[1][0]["reason"], "not_single_turn")

    def test_duplicate_identifiers_are_rejected(self):
        with self.assertRaises(ValueError):
            prepare_inputs(self.cases + [self.cases[0]], self.records)
        with self.assertRaises(ValueError):
            prepare_inputs(self.cases, self.records * 2)

    def test_resume_requires_same_sources_and_judge(self):
        signature = {"cases_sha256": "a", "records_sha256": "b", "judge_model": "judge"}
        validate_resume(signature, signature.copy())
        with self.assertRaises(ValueError):
            validate_resume(signature, {**signature, "judge_model": "other"})
        with self.assertRaises(ValueError):
            validate_resume(signature, {**signature, "records_sha256": "changed"})

    def test_rejected_resume_preserves_existing_inputs(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as temporary:
            folder = Path(temporary)
            (folder / 'metadata.json').write_text(json.dumps({'mode': 'rag', 'suite': 'rag'}))
            for name, items in [('cases', self.cases), ('records', self.records)]:
                (folder / f'{name}.jsonl').write_text('\n'.join(json.dumps(item) for item in items))
            (folder / '.env').write_text('AIHUBMIX_API_KEY=file-key\nAIHUBMIX_BASE_URL=https://example.com/v1\nJUDGE_MODEL=judge\nEMBEDDING_MODEL=embedding\n')
            output = folder / 'answer_correctness'
            output.mkdir()
            (output / 'signature.json').write_text('{}')
            artifact = output / 'inputs.jsonl'
            artifact.write_text('existing-inputs')
            with patch('eval.v2.score_answers.PROJECT_ROOT', folder), patch('eval.v2.score_answers.version', return_value='test'):
                with self.assertRaises(ValueError):
                    main(['--run-dir', str(folder)])
            self.assertEqual(artifact.read_text(), 'existing-inputs')

    def test_scoring_settings_use_user_file_not_inherited_key(self):
        from eval.v2.score_answers import scoring_settings
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as temporary:
            config = Path(temporary) / '.env'
            config.write_text('AIHUBMIX_API_KEY=file-key\nAIHUBMIX_BASE_URL=https://example.com/v1\nJUDGE_MODEL=judge\nEMBEDDING_MODEL=embedding\n')
            with patch.dict(os.environ, {'AIHUBMIX_API_KEY': 'inherited-key'}):
                self.assertEqual(scoring_settings(config)['AIHUBMIX_API_KEY'], 'file-key')

    def test_provider_failure_does_not_copy_sensitive_response(self):
        from eval.v2.score_answers import failure_details
        error = RuntimeError('secret response')
        error.status_code = 404
        error.request = SimpleNamespace(url=SimpleNamespace(path='/v1/embeddings'))
        error.body = {'message': 'secret-key'}
        self.assertEqual(failure_details(error), {'error_type': 'RuntimeError',
                         'http_status': 404, 'endpoint': 'embeddings'})

    def test_serial_scoring_reuses_success_and_retries_failure_once(self):
        from eval.v2.score_answers import score_pending
        items = [{'case_id': name, 'category': 'policy'} for name in ['old', 'retry', 'new']]
        previous = {'old': {'status': 'scored'}, 'retry': {'status': 'failed'}}
        def scorer(item):
            return 0.75
        rows = list(score_pending(items, previous, scorer))
        self.assertEqual({row['case_id'] for row in rows}, {'retry', 'new'})
        self.assertTrue(all(row['status'] == 'scored' for row in rows))

    def test_first_attempt_failure_stops_after_reused_cases(self):
        from eval.v2.score_answers import score_pending
        items = [{'case_id': name, 'category': 'policy'} for name in ['old', 'bad', 'new']]
        called = []
        def scorer(item):
            called.append(item['case_id'])
            raise RuntimeError('provider failure')
        rows = list(score_pending(items, {'old': {'status': 'scored'}}, scorer))
        self.assertEqual(called, ['bad'])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['status'], 'failed')

    def test_failed_atomic_replace_leaves_existing_checkpoint_readable(self):
        from eval.v2.score_answers import write_checkpoint, read_jsonl
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as temporary:
            path = Path(temporary) / 'scores.jsonl'
            write_checkpoint(path, [{'case_id': 'old'}])
            with patch.object(Path, 'replace', side_effect=OSError('interrupted')):
                with self.assertRaises(OSError):
                    write_checkpoint(path, [{'case_id': 'new'}])
            self.assertEqual(read_jsonl(path), [{'case_id': 'old'}])


if __name__ == "__main__":
    unittest.main()
