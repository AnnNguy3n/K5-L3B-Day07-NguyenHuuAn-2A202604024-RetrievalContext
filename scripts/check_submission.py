"""Read-only integrity/review/metric audit of the submitted runs. No API/services."""
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.run_agent_benchmark import read,sha,validate_test_lock
from scripts.evaluate_agent import evaluate,sent_context
from scripts.day07_benchmark import check_hashes
from src.openai_agent import dumps


def check():
    check_hashes(ROOT/'benchmark/day07',read(ROOT/'benchmark/day07/manifest.json'))
    lock=read(ROOT/'artifacts/evaluation/selection-lock.json')
    correction=read(ROOT/'artifacts/evaluation/scorer-path-correction.json')
    assert correction['locked_sha256']==lock['evaluation_code_sha256']['scripts/evaluate_agent.py']
    assert correction['corrected_sha256']==sha(ROOT/'scripts/evaluate_agent.py')
    assert not any(correction[k] for k in ('algorithm_changed','runtime_fingerprint_changed','dev_selection_changed','paid_api_rerun','trace_modified'))
    assert sha(ROOT/'scripts/run_locked_original_retrieval.py')==lock['evaluation_code_sha256']['scripts/run_locked_original_retrieval.py']
    total=0;citations=0;calls=0;contexts=0
    folders=('dev-baseline-v1','dev-lexical-v1','dev-hybrid-context-v1','test-baseline-final-v1')
    for name in folders:
        folder=ROOT/'artifacts/evaluation'/name
        run=read(folder/'run.json');config=run['fingerprint']['config'];cfg=config['agent']
        for file,expected in run['fingerprint']['code_sha256'].items():assert sha(ROOT/file)==expected,file
        assert sha(ROOT/f"configs/agent/{config['id']}.json")==run['fingerprint']['config_file_sha256']
        assert sha(ROOT/'artifacts/evaluation/dev-protocol.json')==run['fingerprint']['experiment_protocol_sha256']
        assert run['queries_sha256']==sha(ROOT/f"benchmark/day07/queries.{run['split']}.json")
        assert not list(folder.glob('*.started.json')),'Interrupted query marker'
        # Validates exact trace/completion hashes, corpus context, every review,
        # source quote, gold denominators, original rankings and saved metrics.
        metrics=evaluate(folder)
        assert metrics==read(folder/'metrics.json'),name
        assert len(run['query_ids'])==(22 if run['split']=='dev' else 7)
        if run['split']=='test':
            validate_test_lock(lock,run['fingerprint'])
            assert run['test_lock_sha256']==sha(ROOT/'artifacts/evaluation/selection-lock.json')
            assert run['created_at_utc']>lock['created_at_utc']
            original=read(folder/'original-retrieval.json')
            assert original['lock_sha256']==run['test_lock_sha256']
            assert original['created_at_utc']>lock['created_at_utc']
            projected={q['id']:q for q in read(ROOT/'benchmark/day07/queries.test.json')}
            corpus={c['id']:c for c in read(ROOT/'data/day06/B/chunks.json')}
            assert set(original['queries'])==set(projected)
            for qid,r in original['queries'].items():
                assert r['query']==projected[qid]
                for rank,c in enumerate(r['results'],1):
                    assert c['rank']==rank and rank<=3
                    assert all(c[k]==corpus[c['id']][k] for k in ('id','text','source_id','source','page'))
        for qid in run['query_ids']:
            trace=read(folder/f'{qid}.json');total+=1
            assert trace['model_requested']==cfg['model']
            assert trace['config_sha256']==run['fingerprint_sha256']
            assert trace['models_actual']==[cfg['model']]
            assert trace['model_call_count']<=cfg['max_model_calls']
            assert trace['search_count']<=cfg['max_searches']
            assert trace['estimated_cost_usd'] is None and trace['cost_note']
            assert cfg['max_retries']==0
            for call in trace['model_calls']:
                assert call['request_chars']<=cfg['history_chars']
                assert call['context_chars_unique']<=cfg['context_chars']
                assert call['usage']['output_tokens']<=cfg['max_output_tokens']
            for event in trace['events']:
                if event['status']=='ok':
                    assert event['output_chars']==len(dumps(event['results']))
                    assert event['output_chars']<=cfg['tool_chars']
            contexts+=len(sent_context(trace));citations+=len(trace['citations']);calls+=trace['model_call_count']
    mapping=read(ROOT/'artifacts/evaluation/test-comparison.json')
    assert mapping['baseline']==mapping['final']=='baseline-dense'
    assert mapping['baseline_run']==mapping['final_run']=='test-baseline-final-v1'
    assert mapping['same_configuration_shared_run']
    assert mapping['metrics_sha256']==sha(ROOT/'artifacts/evaluation/test-baseline-final-v1/metrics.json')
    assert read(ROOT/'artifacts/evaluation/dev-selection.json')['selected_config']=='baseline-dense'
    return {'status':'passed','runs':4,'dev_queries':66,'test_queries':7,'total_query_traces':total,
            'content_reviewed_citations':citations,'model_calls_recorded':calls,
            'final_context_chunks_across_queries':contexts,'baseline_equals_final':True,
            'benchmark_snapshots_unchanged':True,'metric_recomputation':'exactly matches all four saved metrics',
            'cost_policy':'USD null with explanation; provider usage retained',
            'scope':'offline integrity/aggregation, not a new content review, human validation or API evaluation'}

if __name__=='__main__':
    print(json.dumps(check(),ensure_ascii=False,indent=2))
