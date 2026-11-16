"""Read-only exact dense retrieval over the verified Day06 B snapshot."""
from copy import deepcopy
import importlib.util
import json
import math
from pathlib import Path
from time import perf_counter

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from scripts import check_connections as checks
from starter.convert_day6 import normalize_sql_row

ROOT = Path(__file__).resolve().parents[1]
FILTER_KEYS = {'section', 'language', 'document_type'}
CORE = ('id', 'source_id', 'source', 'page', 'text')
B_CORPUS_SHA256 = '8d7c19984861079c6a11c5de242a4b2c4a03cf95db232eb570545d1fa42c1cc5'


class DenseRetrievalError(RuntimeError):
    """Safe to log: never includes DSNs, API bodies, or raw driver exceptions."""


def validate_request(query, filters, top_k):
    if not isinstance(query, str) or not query.strip():
        raise ValueError('query must be a nonempty string')
    if type(top_k) is not int or top_k <= 0:
        raise ValueError('top_k must be a positive integer, not bool')
    if filters is None:
        return {}
    if not isinstance(filters, dict) or filters.keys() - FILTER_KEYS:
        raise ValueError('filters only support section/language/document_type')
    if not all(isinstance(value, str) for value in filters.values()):
        raise ValueError('filter values must be strings')
    return deepcopy(filters)


def validate_vector(vector, dimensions):
    if (not isinstance(vector, list) or len(vector) != dimensions
            or not all(type(v) in (int, float) and math.isfinite(v) for v in vector)
            or not any(vector)):
        raise DenseRetrievalError('Invalid query embedding: dimension/nonfinite/zero/type mismatch')


def day06_embedder(config):
    """Load only the standalone Day06 module, never its starter package."""
    spec = importlib.util.spec_from_file_location(
        '_day07_day06_embeddings', config['day06'] / 'starter/src/embeddings.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class VerifiedEmbedder(module.MyEmbedder):
        def request(self, path, payload=None):
            reply = checks.request(config, path, payload)
            if path == 'tags':
                tag = next((t for t in reply['models'] if t.get('name') == config['EMBEDDING_MODEL']), None)
                if tag is None or checks.digest(tag['digest']) != checks.digest(config['EMBEDDING_DIGEST']):
                    raise DenseRetrievalError('Live model digest mismatch or model absent')
                if tag.get('details', {}).get('embedding_length') != config['EMBEDDING_DIMENSIONS']:
                    raise DenseRetrievalError('Live model dimension mismatch')
            return reply

        def _embed(self, inputs):
            vectors = super()._embed(inputs)
            for vector in vectors:
                validate_vector(vector, config['EMBEDDING_DIMENSIONS'])
            return vectors

    return VerifiedEmbedder(config['EMBEDDING_MODEL'], config['EMBEDDING_ENDPOINT'])


class DenseRetriever:
    def __init__(self, config: dict):
        self.config = dict(config)
        self.db = None
        self.last_trace = {}
        self.readiness = {}
        try:
            # This checker never reads labels. It checks the significant prefix spaces too.
            self.readiness['day06'] = checks.check_artifacts(self.config)
            corpus_path = ROOT / 'data/day06/B/chunks.json'
            if checks.sha256(corpus_path) != B_CORPUS_SHA256:
                raise DenseRetrievalError('Day07 corpus hash differs from fixed snapshot B')
            corpus = json.loads(corpus_path.read_text(encoding='utf-8'))
            self.expected = {row['id']: row for row in corpus}
            exported = [json.loads(line) for line in (
                self.config['day06'] / 'starter/results/chunks_B.jsonl'
            ).read_text(encoding='utf-8').splitlines() if line.strip()]
            from starter.convert_day6 import convert_chunk
            if (len(corpus) != 131 or len(self.expected) != 131
                    or corpus != [convert_chunk(row) for row in exported]):
                raise DenseRetrievalError('Day07 corpus differs from Day06 snapshot B')
            self.readiness['corpus_sha256'] = checks.sha256(corpus_path)
            self.embedder = day06_embedder(self.config)
            self.readiness['embedding'] = self.embedder.info
            self.db = psycopg.connect(
                self.config['DATABASE_URL'], connect_timeout=self.config['CONNECTION_TIMEOUT'],
                options='-c default_transaction_read_only=on -c statement_timeout=10000',
                row_factory=dict_row)
            self.db.read_only = True
            self.db.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            self._verify_database()
        except Exception as exc:
            self.close()
            raise self._safe_error(exc, 'preflight') from None

    @staticmethod
    def _safe_error(exc, stage):
        if isinstance(exc, DenseRetrievalError):
            return exc
        if isinstance(exc, checks.CheckFailed):
            return DenseRetrievalError(f'{stage}: {exc}')
        if isinstance(exc, psycopg.Error):
            return DenseRetrievalError(f'{stage}: PostgreSQL connection/query failed; '
                                       f'SQLSTATE={exc.sqlstate or "unavailable"}; details withheld')
        return DenseRetrievalError(f'{stage}: {type(exc).__name__}; schema/API/files invalid; details withheld')

    def _table(self):
        return sql.Identifier(self.config['DATABASE_SCHEMA'], self.config['DATABASE_TABLE'])

    def _verify_row(self, raw):
        row = normalize_sql_row(raw)
        expected = self.expected.get(row['id'])
        if expected is None or any(row[key] != expected[key] for key in CORE):
            raise DenseRetrievalError('SQL core ID/text/source/page differs from snapshot B')
        metadata = dict(expected['metadata'], document_id=expected['source_id'],
                        source=expected['source'], page=expected['page'])
        if row['metadata'] != metadata:
            raise DenseRetrievalError('SQL metadata differs from snapshot B')
        if checks.digest(row['model_digest']) != checks.digest(self.config['EMBEDDING_DIGEST']):
            raise DenseRetrievalError('Stored model digest mismatch')
        return row

    def _verify_database(self):
        setting = self.db.execute("SELECT current_setting('transaction_read_only') AS value").fetchone()
        if setting['value'] != 'on':
            raise DenseRetrievalError('Database transaction must be read-only')
        rows = self.db.execute(sql.SQL('''
            SELECT chunk_id, text, metadata, model_digest,
                   vector_dims(embedding) AS dimensions, vector_norm(embedding) AS norm
            FROM {} WHERE strategy=%s ORDER BY chunk_id
        ''').format(self._table()), ('B',)).fetchall()
        if len(rows) != 131 or {r['chunk_id'] for r in rows} != set(self.expected):
            raise DenseRetrievalError('SQL B count/unique IDs differ from snapshot')
        digests = set()
        for raw in rows:
            self._verify_row(raw)
            if raw['dimensions'] != self.config['EMBEDDING_DIMENSIONS'] or not (
                    isinstance(raw['norm'], (int, float)) and math.isfinite(raw['norm']) and raw['norm'] > 0):
                raise DenseRetrievalError('Stored vector dimension/norm mismatch')
            digests.add(raw['model_digest'])
        if len(digests) != 1:
            raise DenseRetrievalError('B must have one exact stored digest')
        self.stored_digest = digests.pop()
        self.readiness['database'] = dict(physical_strategy='B', rows=len(rows),
                                         digest=self.stored_digest, transaction_read_only='on',
                                         isolation='repeatable read', exact_cosine=True)

    def search(self, query: str, *, filters: dict | None = None, top_k: int = 3) -> list[dict]:
        self.last_trace = dict(embedding_ms=None, search_ms=None, events=[])
        filters = validate_request(query, filters, top_k)
        if self.db is None or self.db.closed:
            self.last_trace['failed_stage'] = 'closed'
            raise DenseRetrievalError('Retriever is closed')
        stage = 'embedding'
        started = perf_counter()
        try:
            # Pin the mutable model tag again before each real query embedding.
            self.embedder.request('tags')
            vector = self.embedder.embed_query(query)
            validate_vector(vector, self.config['EMBEDDING_DIMENSIONS'])
            self.last_trace['embedding_ms'] = (perf_counter() - started) * 1000
            self.last_trace['events'].append('live digest verified; query embedded with unchanged Day06 prefix')
            stage = 'search'
            started = perf_counter()
            rows = self.db.execute(sql.SQL('''
                WITH candidates AS MATERIALIZED (
                    SELECT chunk_id, text, metadata, model_digest,
                           1 - (embedding <=> %s::vector) AS score
                    FROM {} WHERE strategy=%s AND model_digest=%s AND metadata @> %s
                )
                SELECT * FROM candidates ORDER BY score DESC, chunk_id COLLATE "C" ASC LIMIT %s
            ''').format(self._table()),
                (json.dumps(vector, allow_nan=False), 'B', self.stored_digest, Jsonb(filters), top_k)).fetchall()
            results = []
            for raw in rows:
                row = self._verify_row(raw)
                if type(row['score']) not in (int, float) or not math.isfinite(row['score']):
                    raise DenseRetrievalError('Invalid cosine score')
                row.update(strategy='dense', rank=len(results) + 1)
                results.append(row)
            if (len(results) > top_k or len({r['id'] for r in results}) != len(results)
                    or results != sorted(results, key=lambda r: (-r['score'], r['id']))):
                raise DenseRetrievalError('Invalid SQL ranking/duplicate IDs')
            if any(any(row['metadata'].get(k) != v for k, v in filters.items()) for row in results):
                raise DenseRetrievalError('SQL returned a row outside the metadata filter')
            self.last_trace['search_ms'] = (perf_counter() - started) * 1000
            self.last_trace['events'].append('exact cosine; B + digest + metadata filter before top_k')
            return results
        except Exception as exc:
            self.last_trace[f'{stage}_ms'] = (perf_counter() - started) * 1000
            self.last_trace['failed_stage'] = stage
            # A failed SQL transaction cannot be reused as a verified snapshot.
            if stage == 'search':
                self.close()
            raise self._safe_error(exc, stage) from None

    def close(self):
        if self.db is not None:
            self.db.close()  # rolls back the read-only snapshot; never commits writes
            self.db = None
