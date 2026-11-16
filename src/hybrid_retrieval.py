"""Rank-only Reciprocal Rank Fusion; no labels, services or query rewriting.

candidate_k=20 and rrf_k=60 are initial choices, not proven optimal values.
Adapters must return unique IDs, finite scores and contiguous 1-based ranks.
"""
from copy import deepcopy
import math
from time import perf_counter

from src.lexical_retrieval import CORE_FIELDS, validate_request

BRANCHES = ('dense', 'lexical')


class HybridRetrievalError(RuntimeError):
    """A failed branch or inconsistent adapter contract is never a success."""


def validate_parameters(candidate_k, rrf_k):
    if type(candidate_k) is not int or candidate_k <= 0:
        raise ValueError('candidate_k must be a positive integer, excluding bool')
    if type(rrf_k) is not int or rrf_k < 0:
        raise ValueError('rrf_k must be a nonnegative integer, excluding bool')


def merge_metadata(left, right, path='metadata'):
    """Union supplemental fields, recursively; never overwrite a conflict."""
    result = deepcopy(left)
    for key, value in right.items():
        field = f'{path}.{key}'
        if key not in result:
            result[key] = deepcopy(value)
        elif isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_metadata(result[key], value, field)
        elif type(result[key]) is not type(value) or result[key] != value:
            raise HybridRetrievalError(f'Conflicting shared field: {field}')
    return result


def validate_ranking(rows, branch, limit, filters):
    """Check adapter output rather than quietly repairing a broken ranking."""
    if not isinstance(rows, list) or len(rows) > limit:
        raise HybridRetrievalError(f'{branch}: results must be a list within cutoff')
    seen = set()
    for rank, row in enumerate(rows, 1):
        if not isinstance(row, dict) or any(key not in row for key in CORE_FIELDS):
            raise HybridRetrievalError(f'{branch}: missing core fields')
        if any(not isinstance(row[key], str) or not row[key].strip()
               for key in ('id', 'text', 'source_id', 'source')):
            raise HybridRetrievalError(f'{branch}: invalid core string')
        if (type(row['page']) is not int or row['page'] <= 0
                or not isinstance(row['metadata'], dict)):
            raise HybridRetrievalError(f'{branch}: invalid page/metadata')
        if (type(row.get('rank')) is not int or row['rank'] != rank
                or row.get('strategy') != branch):
            raise HybridRetrievalError(f'{branch}: expected contiguous 1-based ranks/strategy')
        if type(row.get('score')) not in (int, float) or not math.isfinite(row['score']):
            raise HybridRetrievalError(f'{branch}: score must be finite')
        if row['id'] in seen:
            raise HybridRetrievalError(f'{branch}: duplicate ID within branch')
        seen.add(row['id'])
        if any(row['metadata'].get(key) != value for key, value in (filters or {}).items()):
            raise HybridRetrievalError(f'{branch}: result outside requested filters')
    if rows != sorted(rows, key=lambda row: (-row['score'], row['id'])):
        raise HybridRetrievalError(f'{branch}: invalid score/ID order')


def fuse_rankings(rankings, *, candidate_k=20, rrf_k=60, filters=None, top_k=3):
    """Pure fusion also used to independently replay a saved branch trace."""
    validate_parameters(candidate_k, rrf_k)
    validate_request('fusion', filters, top_k)
    if candidate_k < top_k:
        raise ValueError('candidate_k must be >= top_k')
    fused = {}
    for branch in BRANCHES:
        rows = rankings[branch]
        validate_ranking(rows, branch, candidate_k, filters)
        for row in rows:
            identifier = row['id']
            if identifier not in fused:
                fused[identifier] = {
                    **deepcopy({key: row[key] for key in CORE_FIELDS}),
                    'score': 0., 'strategy': 'hybrid',
                    'component_ranks': {}, 'component_scores': {},
                }
            item = fused[identifier]
            for key in ('id', 'text', 'source_id', 'source', 'page'):
                if item[key] != row[key]:
                    raise HybridRetrievalError(f'Same ID has conflicting core field: {key}')
            item['metadata'] = merge_metadata(item['metadata'], row['metadata'])
            item['component_ranks'][branch] = row['rank']
            item['component_scores'][branch] = row['score']
            # Original cosine/TF-IDF scores are diagnostic only, never added.
            item['score'] += 1. / (rrf_k + row['rank'])
    ordered = sorted(fused.values(), key=lambda item: (-item['score'], item['id']))[:top_k]
    return [dict(item, rank=rank) for rank, item in enumerate(ordered, 1)]


class HybridRetriever:
    """Sequential, fail-closed fusion over two caller-owned retrievers.

    last_trace records full candidate rankings, timings and explicit branch errors.
    Empty rankings are valid. A failed branch raises even if its peer succeeds.
    """

    def __init__(self, dense, lexical, candidate_k=20, rrf_k=60):
        validate_parameters(candidate_k, rrf_k)
        for name, adapter in (('dense', dense), ('lexical', lexical)):
            if not callable(getattr(adapter, 'search', None)):
                raise ValueError(f'{name} must expose search(query, *, filters, top_k)')
        self.dense, self.lexical = dense, lexical
        self.candidate_k, self.rrf_k = candidate_k, rrf_k
        self.last_trace = {}

    def search(self, query, *, filters=None, top_k=3):
        self.last_trace = dict(query=query, filters=deepcopy(filters), top_k=top_k,
                               candidate_k=self.candidate_k, rrf_k=self.rrf_k,
                               status='error', branches={})
        validate_request(query, filters, top_k)
        validate_parameters(self.candidate_k, self.rrf_k)
        if self.candidate_k < top_k:
            raise ValueError('candidate_k must be >= top_k')
        failures = []
        rankings = {}
        for branch in BRANCHES:
            adapter = getattr(self, branch)
            started = perf_counter()
            record = dict(status='error', results=[])
            self.last_trace['branches'][branch] = record
            try:
                rows = adapter.search(query, filters=deepcopy(filters), top_k=self.candidate_k)
                record['results'] = deepcopy(rows)
                validate_ranking(rows, branch, self.candidate_k, filters)
                rankings[branch] = record['results']
                record['status'] = 'ok'
            except Exception as exc:
                # Raw adapter exceptions can contain credentials. Keep type, stage,
                # and any deliberately safe contract error; never log arbitrary bodies.
                record['error'] = dict(type=type(exc).__name__, message=(
                    str(exc) if isinstance(exc, HybridRetrievalError)
                    else f'{branch} search failed; adapter details withheld'))
                failures.append(branch)
            record['elapsed_ms'] = (perf_counter() - started) * 1000
            record['adapter_trace'] = deepcopy(getattr(adapter, 'last_trace', {}))
        if failures:
            self.last_trace['failed_stage'] = 'branches'
            raise HybridRetrievalError('Branch failure: ' + ', '.join(failures)) from None
        started = perf_counter()
        try:
            results = fuse_rankings(rankings, candidate_k=self.candidate_k, rrf_k=self.rrf_k,
                                    filters=filters, top_k=top_k)
        except Exception:
            self.last_trace['failed_stage'] = 'fusion'
            raise
        finally:
            self.last_trace['fusion_ms'] = (perf_counter() - started) * 1000
        self.last_trace['status'] = 'ok'
        return results
