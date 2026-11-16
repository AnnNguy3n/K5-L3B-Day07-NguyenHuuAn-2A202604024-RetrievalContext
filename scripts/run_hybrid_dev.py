"""Run three retrieval methods on label-free dev; audit/score saved traces offline."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import statistics
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import run_dense_dev as dense_runner
from scripts import run_lexical_dev as lexical_runner
from scripts.check_connections import load_config
from src.dense_retrieval import DenseRetriever
from src.hybrid_retrieval import (
    HybridRetriever, HybridRetrievalError, fuse_rankings, validate_parameters, validate_ranking,
)
from src.lexical_retrieval import CORE_FIELDS, LexicalRetriever

read, write, sha256 = lexical_runner.read, lexical_runner.write, lexical_runner.sha256
object_hash = lexical_runner.object_hash
ROOT_HASHES = (
    'data/day06/B/chunks.json', 'benchmark/day07/manifest.json',
    'benchmark/day07/queries.dev.json', 'src/dense_retrieval.py',
    'src/lexical_retrieval.py', 'src/hybrid_retrieval.py', 'scripts/run_hybrid_dev.py',
    'scripts/run_dense_dev.py', 'scripts/run_lexical_dev.py', 'scripts/check_connections.py',
    'starter/baseline.py', 'starter/convert_day6.py',
)
METHODS = ('dense', 'lexical', 'hybrid')
OUTPUT = ROOT / 'artifacts/retrieval/hybrid'


def error_record(exc):
    if isinstance(exc, HybridRetrievalError):
        return dict(type=type(exc).__name__, message=str(exc))
    return dense_runner.error_record(exc)


def search_record(retriever, query):
    record = dict(status='error', results=[])
    started = perf_counter()
    try:
        record['results'] = retriever.search(query['question'], filters=query.get('filter'), top_k=3)
        record['status'] = 'ok'
    except Exception as exc:
        record['error'] = error_record(exc)
    record['elapsed_ms'] = (perf_counter() - started) * 1000
    record['trace'] = deepcopy(getattr(retriever, 'last_trace', {}))
    return record


def project(records, method):
    """Each method keeps the identical original query/filter and cutoff."""
    return [dict((record if method == 'hybrid' else record['baselines'][method]),
                 query=record['query'], filters=record['filters'], top_k=record['top_k'])
            for record in records]


def run(output, candidate_k=20, rrf_k=60):
    # Validate before services, including the fixed dev cutoff top_k=3.
    validate_parameters(candidate_k, rrf_k)
    if candidate_k < 3:
        raise ValueError('candidate_k must be >= top_k=3')
    output.mkdir(parents=True, exist_ok=False)
    summary = dict(created_at_utc=datetime.now(timezone.utc).isoformat(), mode='live-hybrid-dev',
                   status='error', split='dev', top_k=3, candidate_k=candidate_k, rrf_k=rrf_k,
                   attempted=0, succeeded=0, errors=0, python=platform.python_version(),
                   test_executed=False, agent_executed=False, defaults_proven_optimal=False)
    dense = None
    records = []
    try:
        manifest = read(lexical_runner.BENCHMARK / 'manifest.json')
        summary['benchmark_version'] = manifest['benchmark_version']
        summary['hashes'] = {name: sha256(ROOT / name) for name in ROOT_HASHES}
        if sha256(lexical_runner.CORPUS) != manifest['snapshot_sha256']['data/day06/B/chunks.json']:
            raise ValueError('Corpus B differs from locked snapshot')
        queries = lexical_runner.dev_queries(manifest)
        chunks = read(lexical_runner.CORPUS)
        if len(chunks) != 131 or len({(c['source_id'], c['page']) for c in chunks}) != 32:
            raise ValueError('Expected fixed B corpus: 131 chunks / 32 pages')
        config = load_config(ROOT / '.env')
        safe_keys = ('EMBEDDING_ENDPOINT', 'EMBEDDING_MODEL', 'EMBEDDING_DIGEST',
                     'EMBEDDING_DIMENSIONS', 'EMBEDDING_QUERY_PREFIX', 'EMBEDDING_DOCUMENT_PREFIX',
                     'DATABASE_SCHEMA', 'DATABASE_TABLE', 'CONNECTION_TIMEOUT')
        summary['config'] = dict(
            physical_strategy='B', chunks=131, pages=32, top_k=3,
            candidate_k=candidate_k, rrf_k=rrf_k, query_rewrite=False,
            formula='sum(1 / (rrf_k + branch_rank)); rank starts at 1',
            tie_break='id ascending', branch_errors='raise; no fallback',
            filters='original runtime filter; exact AND; before branch cutoff',
            dense={**{key: config[key] for key in safe_keys}, 'day06': str(config['day06']),
                   'database_url': 'withheld', 'transaction': 'read only / repeatable read'},
            lexical=deepcopy(lexical_runner.CONFIG),
            execution='per query: standalone dense top-3, lexical top-3, hybrid candidates; sequential',
        )
        summary['config_sha256'] = object_hash(summary['config'])
        started = perf_counter()
        dense = DenseRetriever(config)
        summary['dense_preflight_ms'] = (perf_counter() - started) * 1000
        summary['readiness'] = deepcopy(dense.readiness)
        started = perf_counter()
        lexical = LexicalRetriever(chunks)
        summary['lexical_build_ms'] = (perf_counter() - started) * 1000
        summary['vocabulary_size'] = lexical.vocabulary_size
        hybrid = HybridRetriever(dense, lexical, candidate_k, rrf_k)
        for query in queries:
            baselines = {name: search_record(adapter, query)
                         for name, adapter in (('dense', dense), ('lexical', lexical))}
            record = dict(search_record(hybrid, query), query=deepcopy(query),
                          filters=deepcopy(query.get('filter')), top_k=3, baselines=baselines)
            records.append(record)
        summary['attempted'] = len(records)
        summary['succeeded'] = sum(r['status'] == 'ok' for r in records)
        summary['errors'] = summary['attempted'] - summary['succeeded']
        summary['methods'] = {}
        for method in METHODS:
            rows = project(records, method)
            times = [row['elapsed_ms'] for row in rows]
            summary['methods'][method] = dict(
                attempted=len(rows), succeeded=sum(row['status'] == 'ok' for row in rows),
                errors=sum(row['status'] != 'ok' for row in rows),
                timings_ms=dict(search_total=sum(times), search_mean=statistics.mean(times),
                                search_median=statistics.median(times), search_max=max(times)),
            )
        summary['status'] = 'ok' if all(m['succeeded'] == 22 for m in summary['methods'].values()) else 'error'
        summary['cost'] = dict(
            external_paid_api_calls=0, llm_calls=0,
            dense_search_calls=2 * len(records),
            successful_query_embeddings=sum(
                bool(entry.get('trace', {}).get('events')) for r in records
                for entry in (r['baselines']['dense'],
                              {'trace': r['trace']['branches']['dense']['adapter_trace']})
            ),
            note='Local embedding/SQL/TF-IDF; monetary, energy and token costs not measured. '
                 '44 dense search calls in a complete comparison; constructor probes extra. '
                 'Each standalone dense or hybrid query embeds once; lexical embeds zero times.',
        )
    except Exception as exc:
        summary['preflight_error'] = error_record(exc)
    finally:
        if dense is not None:
            dense.close()
        trace = output / 'trace.jsonl'
        trace.write_text(''.join(json.dumps(r, ensure_ascii=False, allow_nan=False) + '\n' for r in records),
                         encoding='utf-8')
        summary['trace_sha256'] = sha256(trace)
        write(output / 'summary.json', summary)
    print(json.dumps({k: summary[k] for k in ('status', 'attempted', 'succeeded', 'errors')}, indent=2))
    print(str(output))
    return 0 if summary['status'] == 'ok' else 1


def verify_corpus_rows(rows, method, limit, filters, corpus):
    validate_ranking(rows, method, limit, filters)
    for row in rows:
        expected = corpus.get(row['id'])
        if expected is None or any(row[key] != expected[key] for key in CORE_FIELDS if key != 'metadata'):
            raise ValueError('Recorded core differs from fixed corpus B')
        metadata = deepcopy(expected['metadata'])
        if method == 'dense':
            metadata.update(document_id=expected['source_id'], source=expected['source'], page=expected['page'])
        if row['metadata'] != metadata:
            raise ValueError('Recorded metadata differs from SQL/JSON corpus contract')
        if method == 'lexical' and row['score'] <= 0:
            raise ValueError('Lexical score must be positive')


def audit_records(records, summary):
    """Audit before labels: corpus, filters, ranks, branch traces and exact RRF replay."""
    queries = lexical_runner.dev_queries(read(lexical_runner.BENCHMARK / 'manifest.json'))
    if [r['query'] for r in records] != queries:
        raise ValueError('Trace must retain all 22 original dev queries in order')
    corpus = {r['id']: r for r in read(lexical_runner.CORPUS)}
    candidate_k, rrf_k = summary['candidate_k'], summary['rrf_k']
    for record in records:
        filters = record['query'].get('filter')
        if record['top_k'] != 3 or record['filters'] != filters:
            raise ValueError('Trace cutoff/filter changed')
        for method in ('dense', 'lexical'):
            baseline = record['baselines'][method]
            if baseline['status'] == 'ok':
                verify_corpus_rows(baseline['results'], method, 3, filters, corpus)
            elif baseline['status'] != 'error' or not baseline.get('error') or baseline['results']:
                raise ValueError('Invalid baseline error record')
        trace = record['trace']
        if any(trace[key] != value for key, value in dict(
                query=record['query']['question'], filters=filters, top_k=3,
                candidate_k=candidate_k, rrf_k=rrf_k).items()):
            raise ValueError('Hybrid trace request/config mismatch')
        if set(trace['branches']) != {'dense', 'lexical'}:
            raise ValueError('Missing branch trace')
        for branch, entry in trace['branches'].items():
            if entry['status'] == 'ok':
                verify_corpus_rows(entry['results'], branch, candidate_k, filters, corpus)
                baseline = record['baselines'][branch]
                if baseline['status'] == 'ok' and baseline['results'] != entry['results'][:3]:
                    raise ValueError('Standalone top-3 differs from branch candidate prefix')
            elif entry['status'] != 'error' or not entry.get('error'):
                raise ValueError('Invalid branch error record')
        if record['status'] == 'ok':
            if trace['status'] != 'ok' or any(e['status'] != 'ok' for e in trace['branches'].values()):
                raise ValueError('Branch failure cannot be hybrid success')
            replay = fuse_rankings({b: e['results'] for b, e in trace['branches'].items()},
                                   candidate_k=candidate_k, rrf_k=rrf_k, filters=filters, top_k=3)
            if record['results'] != replay:
                raise ValueError('Hybrid result differs from RRF replay')
        elif record['status'] != 'error' or not record.get('error') or record['results'] or trace['status'] != 'error':
            raise ValueError('Invalid hybrid error record')


def score(output):
    # Pure offline command: neither retriever nor API/DB is constructed.
    summary = read(output / 'summary.json')
    trace = output / 'trace.jsonl'
    if summary['mode'] != 'live-hybrid-dev' or sha256(trace) != summary['trace_sha256']:
        raise ValueError('Trace integrity/mode mismatch')
    if object_hash(summary['config']) != summary['config_sha256']:
        raise ValueError('Config integrity mismatch')
    if any(summary[key] != summary['config'][key] for key in ('candidate_k', 'rrf_k', 'top_k')):
        raise ValueError('Summary/config mismatch')
    for name in ROOT_HASHES:
        if sha256(ROOT / name) != summary['hashes'][name]:
            raise ValueError('Run code/corpus/benchmark hash no longer matches')
    records = [json.loads(line) for line in trace.read_text(encoding='utf-8').splitlines() if line]
    audit_records(records, summary)
    manifest = read(lexical_runner.BENCHMARK / 'manifest.json')
    labels = lexical_runner.BENCHMARK / 'cases.json'
    if sha256(labels) != manifest['artifacts_sha256']['cases.json']:
        raise ValueError('Offline label hash differs from manifest')
    # Shared container; select dev immediately. Never rank or score test queries.
    cases = [c for c in read(labels) if c['split'] == 'dev']
    metrics = {method: lexical_runner.evaluate(project(records, method), cases) for method in METHODS}
    result = dict(mode='offline-dev-comparison', top_k=3, benchmark_version=summary['benchmark_version'],
                  corpus_sha256=summary['hashes']['data/day06/B/chunks.json'],
                  config_sha256=summary['config_sha256'], trace_sha256=sha256(trace),
                  summary_sha256=sha256(output / 'summary.json'), cases_sha256=sha256(labels),
                  candidate_k=summary['candidate_k'], rrf_k=summary['rrf_k'],
                  methods=metrics, timings=summary['methods'], cost=summary['cost'],
                  context_metrics=None, answer_metrics=None,
                  limitations='Single local dev run; no tuning, test evaluation, agent or context builder.')
    write(output / 'metrics.json', dict(metrics['hybrid'], trace_sha256=result['trace_sha256'],
                                       summary_sha256=result['summary_sha256'],
                                       cases_sha256=result['cases_sha256'],
                                       config_sha256=result['config_sha256']))
    write(output / 'comparison.json', result)
    print(json.dumps({m: {k: metrics[m][k] for k in ('hit_at_1', 'evidence_hit_at_3', 'errors')}
                      for m in METHODS}, indent=2))
    return 1 if any(m['errors'] for m in metrics.values()) else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('run', 'score'))
    parser.add_argument('--output', type=Path)
    parser.add_argument('--candidate-k', type=int, default=20, help='Initial choice, not tuned')
    parser.add_argument('--rrf-k', type=int, default=60, help='Initial choice, not tuned')
    args = parser.parse_args(argv)
    if args.command == 'score' and args.output is None:
        parser.error('score requires --output')
    output = args.output or OUTPUT / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    try:
        return run(output, args.candidate_k, args.rrf_k) if args.command == 'run' else score(output)
    except (ValueError, OSError, KeyError, TypeError, HybridRetrievalError) as exc:
        parser.exit(2, f'Hybrid dev command failed ({type(exc).__name__}); check paths/config/schema/hash.\n')


if __name__ == '__main__':
    sys.exit(main())
