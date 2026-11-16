"""One real OpenAI question, or one label-free dev query. Never runs test/score."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.check_connections import load_config
from src.lexical_retrieval import LexicalRetriever, validate_request
from src.openai_agent import (AgentConfig, AgentFailure, endpoint_url, make_client,
                              redact, run_responses_agent)

DEV_HASH = 'a5f7709a253962aba1cdb316616d1e62f431b43a85922a16d29a3306d3e859eb'
CORPUS_HASH = '8d7c19984861079c6a11c5de242a4b2c4a03cf95db232eb570545d1fa42c1cc5'


def read_locked(path, expected_hash):
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_hash:
        raise AgentFailure('runtime_file_hash_mismatch')
    return json.loads(raw)


def select_dev(query_id):
    rows = read_locked(ROOT / 'benchmark/day07/queries.dev.json', DEV_HASH)
    seen = set()
    for row in rows:
        if (not isinstance(row, dict) or set(row) - {'id', 'question', 'filter'}
                or not isinstance(row.get('id'), str) or row['id'] in seen):
            raise AgentFailure('invalid_dev_schema')
        seen.add(row['id'])
        validate_request(row.get('question'), row.get('filter'), 3)
    selected = [r for r in rows if r['id'] == query_id]
    if len(selected) != 1:
        raise AgentFailure('unknown_dev_query_id')
    return selected[0]


def build_retriever(name, config):
    """Return retriever and caller-owned close callback; no framework or fallback."""
    chunks = read_locked(ROOT / 'data/day06/B/chunks.json', CORPUS_HASH)
    if len(chunks) != 131 or len({(c['source_id'], c['page']) for c in chunks}) != 32:
        raise AgentFailure('invalid_corpus_snapshot')
    if name == 'lexical':
        return LexicalRetriever(chunks), lambda: None
    from src.dense_retrieval import DenseRetriever
    dense = DenseRetriever(config)
    if name == 'dense':
        return dense, dense.close
    try:
        from src.hybrid_retrieval import HybridRetriever
        return HybridRetriever(dense, LexicalRetriever(chunks), candidate_k=20, rrf_k=60), dense.close
    except Exception:
        dense.close()
        raise


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument('--question')
    source.add_argument('--query-id', help='ID from benchmark/day07/queries.dev.json only')
    p.add_argument('--env-file', type=Path, default=ROOT / '.env')
    p.add_argument('--model', help='Overrides OPENAI_MODEL; no automatic model fallback')
    p.add_argument('--retriever', choices=('dense', 'lexical', 'hybrid'), default='hybrid')
    p.add_argument('--filters', help='JSON section/language/document_type; only with --question')
    p.add_argument('--output', type=Path, help='New JSON file under artifacts/agent/ (never overwritten)')
    for flag in ('max-model-calls', 'max-searches', 'max-output-tokens', 'top-k',
                 'tool-chars', 'context-chars', 'history-chars', 'max-retries'):
        p.add_argument('--' + flag, type=int)
    p.add_argument('--timeout-seconds', type=float)
    p.add_argument('--reasoning-effort', choices=('omit', 'none', 'low', 'medium', 'high', 'xhigh', 'max'))
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    output = None
    if args.output:
        output = args.output.resolve()
        if not output.is_relative_to(ROOT / 'artifacts/agent') or output.exists():
            print('{"status":"error","stop_reason":"output_must_be_new_under_artifacts_agent"}')
            return 1
    result = {'status': 'error', 'stop_reason': 'configuration_error', 'answer': '', 'citations': [],
              'model_call_count': 0, 'search_count': 0}
    client = None
    close = lambda: None
    secrets = ()
    try:
        c = load_config(args.env_file)
        secrets = tuple(v for k in ('OPENAI_API_KEY', 'DATABASE_URL') if (v := c.get(k)))
        model = args.model if args.model is not None else c.get('OPENAI_MODEL', '')
        overrides = {}
        defaults = AgentConfig.__dataclass_fields__
        for name in defaults:
            if name == 'model':
                continue
            cli = getattr(args, name)
            raw = c.get('AGENT_' + name.upper())
            if cli is not None:
                value = cli
            elif raw is not None and raw != '':
                value = (float(raw) if name == 'timeout_seconds' else
                         raw if name == 'reasoning_effort' else int(raw))
            else:
                value = defaults[name].default
            overrides[name] = None if name == 'reasoning_effort' and value == 'omit' else value
        cfg = AgentConfig(model=model, **overrides)
        if args.query_id:
            if args.filters is not None:
                raise AgentFailure('dev_filter_override_forbidden')
            query = select_dev(args.query_id)
        else:
            query = {'question': args.question, 'filter': json.loads(args.filters) if args.filters else None}
        validate_request(query['question'], query.get('filter'), cfg.top_k)
        # Validate credentials before touching local services. Never print config.
        client = make_client(c.get('OPENAI_API_KEY'), cfg, c.get('OPENAI_BASE_URL'))
        retriever, close = build_retriever(args.retriever, c)
        result = run_responses_agent(query['question'], client, retriever, cfg,
                                     filters=query.get('filter'), secrets=secrets)
        result.update(query_id=query.get('id'), question=query['question'], filters=query.get('filter'),
                      retriever=args.retriever, physical_strategy='B',
                      hybrid_parameters={'candidate_k': 20, 'rrf_k': 60} if args.retriever == 'hybrid' else None,
                      sdk_version=importlib.metadata.version('openai'), python=platform.python_version(),
                      endpoint=endpoint_url(c.get('OPENAI_BASE_URL')), store=False,
                      corpus_sha256=CORPUS_HASH, dev_sha256=DEV_HASH if args.query_id else None,
                      sources_registry='data/day06/B/sources.json', test_executed=False)
    except AgentFailure as exc:
        result['stop_reason'] = str(exc)
    except Exception:
        result['stop_reason'] = 'configuration_or_retriever_setup_error'
    finally:
        for callback in (close, client.close if client else lambda: None):
            try:
                callback()
            except Exception:
                result.update(status='error', stop_reason='resource_close_error')
    result['created_at_utc'] = datetime.now(timezone.utc).isoformat()
    serialized = json.dumps(redact(result, secrets), ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('x', encoding='utf-8') as handle:
            handle.write(serialized)
    print(serialized, end='')
    return 1 if result['status'] == 'error' else 0


if __name__ == '__main__':
    sys.exit(main())
