"""Bounded live runner. Runtime reads query projections only; scoring is offline."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.check_connections import load_config
from scripts.run_openai_agent import build_retriever, CORPUS_HASH, read_locked
from src.lexical_retrieval import validate_request
from src.openai_agent import (AgentConfig, AgentFailure, INSTRUCTIONS, SEARCH_TOOL,
                              FINAL_FORMAT, dumps, endpoint_url, make_client,
                              redact, run_responses_agent)

RUNTIME_FILES = ('src/openai_agent.py', 'src/dense_retrieval.py', 'src/lexical_retrieval.py',
                 'src/hybrid_retrieval.py', 'scripts/run_agent_benchmark.py',
                 'scripts/run_openai_agent.py', 'scripts/check_connections.py', 'requirements.txt')
SAFE_EMBEDDING = ('EMBEDDING_MODEL','EMBEDDING_DIGEST','EMBEDDING_DIMENSIONS',
                  'EMBEDDING_QUERY_PREFIX','EMBEDDING_DOCUMENT_PREFIX','DATABASE_SCHEMA','DATABASE_TABLE')


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def objsha(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def write_new(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as handle:
        handle.write(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def resolved(config_path, env):
    raw = read(config_path)
    if set(raw) != {'id','retriever','agent','model_source'} or raw['model_source'] != 'OPENAI_MODEL':
        raise AgentFailure('invalid_experiment_config')
    if raw['retriever'] not in ('dense','lexical','hybrid'):
        raise AgentFailure('invalid_retriever')
    cfg = AgentConfig(model=env.get('OPENAI_MODEL',''), **raw['agent'])
    safe = {'id':raw['id'],'retriever':raw['retriever'],'agent':asdict(cfg),
            'endpoint':endpoint_url(env.get('OPENAI_BASE_URL')),
            'sdk':importlib.metadata.version('openai'), 'python':platform.python_version(),
            'embedding':{k:env[k] for k in SAFE_EMBEDDING},
            'hybrid':{'candidate_k':20,'rrf_k':60} if raw['retriever']=='hybrid' else None}
    return safe, cfg


def identity(config_path, env):
    config, cfg = resolved(config_path, env)
    fp = {'config':config, 'code_sha256':{p:sha(ROOT/p) for p in RUNTIME_FILES},
          'prompt_sha256':objsha([INSTRUCTIONS,SEARCH_TOOL,FINAL_FORMAT]),
          'config_file_sha256':sha(config_path),
          'experiment_protocol_sha256':sha(ROOT/'artifacts/evaluation/dev-protocol.json'),
          'corpus_sha256':CORPUS_HASH,
          'benchmark_manifest_sha256':sha(ROOT/'benchmark/day07/manifest.json')}
    if sha(ROOT/'data/day06/B/chunks.json') != CORPUS_HASH:
        raise AgentFailure('corpus_hash_mismatch')
    return fp, cfg


def queries(split):
    manifest = read(ROOT/'benchmark/day07/manifest.json')
    name = f'queries.{split}.json'
    rows = read_locked(ROOT/'benchmark/day07'/name, manifest['artifacts_sha256'][name])
    expected = 22 if split=='dev' else 7
    if len(rows)!=expected or len({q['id'] for q in rows})!=expected:
        raise AgentFailure('invalid_query_count')
    for q in rows:
        if set(q)-{'id','question','filter'} or not isinstance(q.get('id'),str):
            raise AgentFailure('runtime_label_leak')
        validate_request(q.get('question'),q.get('filter'),3)
    return rows


def validate_test_lock(lock, fingerprint):
    if lock.get('state')!='locked_before_test' or fingerprint not in lock.get('allowed_fingerprints',[]):
        raise AgentFailure('test_requires_matching_pretest_lock')
    if not lock.get('created_at_utc') or not lock.get('dev_evidence_sha256'):
        raise AgentFailure('incomplete_pretest_lock')
    for name, expected in lock['dev_evidence_sha256'].items():
        if sha(ROOT/name)!=expected:
            raise AgentFailure('locked_dev_evidence_changed')


def run(config_path, split, output, env_file, lock_path=None):
    output=output.resolve()
    if not output.is_relative_to(ROOT/'artifacts/evaluation') or output.exists():
        raise AgentFailure('output_must_be_new_under_artifacts_evaluation')
    env=load_config(env_file)
    fp,cfg=identity(config_path,env)
    lock=None
    if split=='test':
        if not lock_path:
            raise AgentFailure('test_lock_required')
        lock=read(lock_path)
        validate_test_lock(lock,fp)  # before opening any test queries or services
    rows=queries(split)
    secrets=tuple(env.get(k,'') for k in ('OPENAI_API_KEY','DATABASE_URL'))
    client=make_client(env.get('OPENAI_API_KEY'),cfg,env.get('OPENAI_BASE_URL'))
    retriever,close=build_retriever(fp['config']['retriever'],env)
    output.mkdir(parents=True,exist_ok=False)
    manifest={'schema_version':1,'run_id':output.name,'split':split,'created_at_utc':now(),
              'fingerprint':fp,'fingerprint_sha256':objsha(fp),
              'queries_sha256':sha(ROOT/f'benchmark/day07/queries.{split}.json'),
              'test_lock_sha256':sha(lock_path) if lock else None,
              'query_ids':[q['id'] for q in rows], 'retry_policy':'SDK retries=0; no runner retry/resume; errors retained. Use a new run directory for any explicit rerun.',
              'runtime_has_labels':False,
              'git_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()}
    write_new(output/'run.json',manifest)
    results=[]
    try:
        for q in rows:
            write_new(output/f"{q['id']}.started.json",{'query_id':q['id'],'started_at_utc':now(),'attempt':1})
            result=run_responses_agent(q['question'],client,retriever,cfg,filters=q.get('filter'),secrets=secrets)
            result.update(query_id=q['id'],question=q['question'],filters=q.get('filter'),
                          filter_source='inherited_user_query_filter' if q.get('filter') else 'none',
                          split=split,run_id=output.name,config_sha256=objsha(fp),created_at_utc=now(),attempt=1)
            write_new(output/f"{q['id']}.json",redact(result,secrets))
            # Completed trace includes the attempt and time; remove only start marker.
            (output/f"{q['id']}.started.json").unlink()
            results.append(result)
            print(f"{output.name} {q['id']}: {result['status']} ({result['stop_reason']}, {result['model_call_count']} calls)",flush=True)
        write_new(output/'completion.json',{'completed_at_utc':now(),'attempted':len(results),
                   'errors':sum(r['status']=='error' for r in results),
                   'trace_sha256':{r['query_id']:sha(output/f"{r['query_id']}.json") for r in results}})
    finally:
        close()
        client.close()
    return 0


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',type=Path,required=True)
    p.add_argument('--split',choices=('dev','test'),required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--env-file',type=Path,default=ROOT/'.env')
    p.add_argument('--lock',type=Path)
    a=p.parse_args()
    try:
        return run(a.config,a.split,a.output,a.env_file,a.lock)
    except AgentFailure as exc:
        print(dumps({'status':'error','reason':str(exc)}));return 1
    except Exception:
        print(dumps({'status':'error','reason':'runner_setup_or_io_error_no_provider_details'}));return 1

if __name__=='__main__':
    sys.exit(main())
