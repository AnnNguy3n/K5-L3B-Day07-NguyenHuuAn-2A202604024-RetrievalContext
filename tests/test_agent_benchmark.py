"""Runner guard tests; no API calls and no quality claims."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts import run_agent_benchmark as r
from src.openai_agent import AgentFailure


class RunnerTests(unittest.TestCase):
    def test_test_guard_runs_before_queries_or_services(self):
        output=r.ROOT/'artifacts/evaluation/guard-unit-unused'
        with patch.object(r,'load_config',return_value={}), \
             patch.object(r,'identity',return_value=({'fixed':True},None)), \
             patch.object(r,'queries') as query, patch.object(r,'make_client') as client:
            with self.assertRaisesRegex(AgentFailure,'test_lock_required'):
                r.run(Path('config'), 'test', output, Path('env'))
            query.assert_not_called();client.assert_not_called()
        self.assertFalse(output.exists())

    def test_lock_checks_identity_and_supporting_dev_files(self):
        fp={'code':'a'}
        with self.assertRaises(AgentFailure):r.validate_test_lock({},fp)
        lock={'state':'locked_before_test','created_at_utc':'2026-11-16',
              'allowed_fingerprints':[fp], 'dev_evidence_sha256':{'README.md':r.sha(r.ROOT/'README.md')}}
        r.validate_test_lock(lock,fp)
        with self.assertRaises(AgentFailure):r.validate_test_lock(lock,{'code':'b'})
        lock['dev_evidence_sha256']['README.md']='wrong'
        with self.assertRaises(AgentFailure):r.validate_test_lock(lock,fp)

    def test_projection_rejects_label_fields(self):
        with patch.object(r,'read',return_value={'artifacts_sha256':{'queries.dev.json':'x'}}), \
             patch.object(r,'read_locked',return_value=[{'id':str(i),'question':'Q','answerable':True} for i in range(22)]):
            with self.assertRaisesRegex(AgentFailure,'runtime_label_leak'):r.queries('dev')

    def test_exclusive_output_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'trace.json';r.write_new(path,{'attempt':1})
            with self.assertRaises(FileExistsError):r.write_new(path,{'attempt':2})
            self.assertEqual(r.read(path),{'attempt':1})

if __name__=='__main__':unittest.main()
