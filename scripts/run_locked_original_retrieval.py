"""Original-query test top-3, only after a matching pretest lock. No model calls."""
import argparse
from pathlib import Path
import sys
from time import perf_counter
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.check_connections import load_config
from scripts.run_agent_benchmark import (identity, queries, read, validate_test_lock,
                                        write_new, sha, now)
from scripts.run_openai_agent import build_retriever


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',type=Path,required=True)
    p.add_argument('--config',type=Path,required=True)
    p.add_argument('--lock',type=Path,required=True)
    a=p.parse_args();env=load_config(ROOT/'.env');fp,_=identity(a.config,env)
    validate_test_lock(read(a.lock),fp)
    run=read(a.run/'run.json')
    if run['split']!='test' or run['fingerprint']!=fp or run['test_lock_sha256']!=sha(a.lock):
        raise ValueError('Run and pretest lock differ')
    target=a.run/'original-retrieval.json'
    if target.exists():raise FileExistsError('Original ranking must be a new artifact')
    r,close=build_retriever(fp['config']['retriever'],env)
    records={}
    try:
        for q in queries('test'):
            started=perf_counter()
            try:
                rows=r.search(q['question'],filters=q.get('filter'),top_k=3)
                record={'status':'ok','results':rows}
            except Exception:
                record={'status':'error','results':[],'reason':'retrieval_error_details_withheld'}
            record.update(query=q,elapsed_ms=(perf_counter()-started)*1000,
                          filter_source='runtime_query_filter' if q.get('filter') else 'none')
            records[q['id']]=record
    finally:close()
    write_new(target,{'created_at_utc':now(),'lock_sha256':sha(a.lock),'top_k':3,
                      'query_rewrite':False,'queries':records,'model_calls':0,
                      'cost_usd':None,'cost_note':'Local query embedding/vector search; compute cost not measured.'})
    print('Saved original top-3 for',len(records),'test queries; no model calls.')

if __name__=='__main__':main()
