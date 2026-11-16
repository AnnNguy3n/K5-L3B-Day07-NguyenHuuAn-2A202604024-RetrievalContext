"""Failure-path checks only; these mocks do not certify live Day06 services."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import psycopg

from scripts import check_connections as check


class ConnectionChecks(unittest.TestCase):
    def config(self):
        with patch.dict('os.environ', {}, clear=True):
            return check.load_config(Path('/nonexistent/day07.env'))

    def test_config_preserves_prefix_spaces_and_environment_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / '.env'
            env.write_text('EMBEDDING_MODEL=from-file\nEMBEDDING_QUERY_PREFIX="query: "\n')
            with patch.dict('os.environ', {'EMBEDDING_MODEL': 'from-shell'}, clear=True):
                c = check.load_config(env)
            self.assertEqual(c['EMBEDDING_MODEL'], 'from-shell')
            self.assertEqual(c['EMBEDDING_QUERY_PREFIX'], 'query: ')
            self.assertEqual(c['EMBEDDING_DOCUMENT_PREFIX'], 'title: none | text: ')

    def test_rejects_digest_change_before_embedding(self):
        c = self.config()
        with patch.object(check, 'request', return_value={'models': [
            {'name': c['EMBEDDING_MODEL'], 'digest': 'wrong-digest'}
        ]}) as request:
            with self.assertRaisesRegex(check.CheckFailed, 'digest mismatch'):
                check.check_embedding(c)
            self.assertEqual(request.call_count, 1)

    def test_rejects_wrong_dimension_nan_and_zero_vectors(self):
        c = self.config()
        tag = {'name': c['EMBEDDING_MODEL'], 'digest': c['EMBEDDING_DIGEST'],
               'capabilities': ['embedding'], 'details': {'embedding_length': 768}}
        for invalid in ([1.0] * 767, [0.0] * 768, [float('nan')] * 768):
            with self.subTest(vector_kind=repr(invalid[0])):
                replies = [{'models': [tag]}, {'model': c['EMBEDDING_MODEL'],
                                               'embeddings': [invalid, [1.0] * 768]}]
                with patch.object(check, 'request', side_effect=replies):
                    with self.assertRaisesRegex(check.CheckFailed, 'Invalid/nonfinite/zero'):
                        check.check_embedding(c)

    def test_database_error_does_not_expose_secret(self):
        c = self.config()
        c['DATABASE_URL'] = 'postgresql://user:secret-marker@127.0.0.1/test'
        with patch.object(psycopg, 'connect', side_effect=psycopg.OperationalError(c['DATABASE_URL'])):
            with self.assertRaises(check.CheckFailed) as caught:
                check.check_database(c, None)
        self.assertNotIn('secret-marker', str(caught.exception))
        self.assertNotIn('postgresql://', str(caught.exception))

    def test_embedding_failure_still_checks_database_without_fake_vector(self):
        output = io.StringIO()
        with patch.object(check, 'load_config', return_value=self.config()), \
                patch.object(check, 'check_artifacts', return_value={}), \
                patch.object(check, 'check_embedding', side_effect=check.CheckFailed('endpoint down')), \
                patch.object(check, 'check_database', return_value={}) as database, \
                contextlib.redirect_stdout(output):
            result = check.main([])
        self.assertEqual(result, 1)
        self.assertIsNone(database.call_args.args[1])
        report = json.loads(output.getvalue())
        self.assertEqual(report['checks']['embedding']['status'], 'error')
        self.assertFalse(report['services_verified'])

    def test_offline_does_not_call_services_or_claim_live_success(self):
        output = io.StringIO()
        with patch.object(check, 'load_config', return_value=self.config()), \
                patch.object(check, 'check_artifacts', return_value={}), \
                patch.object(check, 'check_embedding') as embedding, \
                patch.object(check, 'check_database') as database, \
                contextlib.redirect_stdout(output):
            result = check.main(['--offline'])
        self.assertEqual(result, 0)
        embedding.assert_not_called()
        database.assert_not_called()
        self.assertFalse(json.loads(output.getvalue())['services_verified'])


if __name__ == '__main__':
    unittest.main()
