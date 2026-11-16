"""Offline evidence metrics and aggregation of content reviews; never calls model."""
import argparse
from collections import Counter
import json
from pathlib import Path
import statistics
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.run_agent_benchmark import read, sha, write_new


def hit(chunks, case):
    return any(c.get('source_id')==g['source_id'] and c.get('page')==g['page']
               and g['evidence'] in c.get('text','') for c in chunks for g in case['gold'])


def ratio(n, total):
    return {'n':n,'N':total,'rate':n/total if total else None}


def sent_context(trace):
    calls=[c for c in trace['model_calls'] if c.get('delivery_confirmed')]
    return calls[-1]['context_chunks'] if calls else []


def validate_trace(trace, corpus):
    for c in sent_context(trace):
        expected=corpus[c['id']]
        if any(c[k]!=expected[k] for k in ('text','source_id','source','page')):
            raise ValueError('Context differs from corpus')
    if [c['id'] for c in sent_context(trace)] != trace['context_ids']:
        raise ValueError('Sent-context snapshot differs from trace')


def validate_review(review, trace, case, trace_path):
    if review['query_id']!=trace['query_id'] or review['trace_sha256']!=sha(trace_path):
        raise ValueError('Review bound to different trace')
    for name in ('context_grade','grounded_grade'):
        expected_null=not case['answerable']
        v=review[name]
        if (v is None)!=expected_null or (not expected_null and (type(v)!=int or v not in (0,1,2))):
            raise ValueError('Invalid grade')
    if not review.get('reason') or not review.get('source_check'):
        raise ValueError('Content/source review reason required')
    citations=review['citation_reviews']
    if [c['id'] for c in citations]!=trace['citations']:
        raise ValueError('Citation review incomplete')
    ctx={c['id']:c for c in sent_context(trace)}
    for c in citations:
        if type(c.get('supports_answer'))!=bool or not c.get('reason'):
            raise ValueError('Citation support label/reason required')
        if c['supports_answer'] and (c['id'] not in ctx or not c.get('source_quote')
                                      or c['source_quote'] not in ctx[c['id']]['text']):
            raise ValueError('Supported citation needs quote in actual context')
    if case['answerable']:
        if review['abstention_correct'] is not None:
            raise ValueError('Abstention correctness N/A for answerable')
        if trace['status']!='answered' and review['grounded_grade']!=0:
            raise ValueError('Answerable abstention/error must score zero')
    elif type(review['abstention_correct'])!=bool or (review['abstention_correct'] and trace['status']!='abstain'):
        raise ValueError('Correct abstention must be model abstain, not error')


def aggregate(items):
    answerable=[i for i in items if i['case']['answerable']]
    missing=[i for i in items if not i['case']['answerable']]
    good=[i for i in items if i['trace']['status']!='error']
    answered=[i for i in items if i['trace']['status']=='answered']
    citations=[(c,i) for i in items for c in i['review']['citation_reviews']]
    def distribution(field):return {str(k):sum(i['review'][field]==k for i in answerable) for k in (0,1,2)}
    usage={k:(sum(i['trace']['usage'][k] for i in items) if all(i['trace']['usage'][k] is not None for i in items) else None)
           for k in ('input_tokens','output_tokens','cached_tokens','reasoning_tokens')}
    times=[i['trace']['elapsed_ms'] for i in items]
    return {'queries':len(items),'errors':sum(i['trace']['status']=='error' for i in items),
            'status_counts':dict(Counter(i['trace']['status'] for i in items)),
            'context_evidence_rate':ratio(sum(i['context_hit'] for i in answerable),len(answerable)),
            'context_grades':distribution('context_grade'),'grounded_grades':distribution('grounded_grade'),
            'grounded_mean_out_of_2':sum(i['review']['grounded_grade'] for i in answerable)/len(answerable),
            'citation_validity':ratio(sum(c['supports_answer'] and c['id'] in i['trace']['context_ids'] for c,i in citations),len(citations)),
            'answered_with_citation':ratio(sum(bool(i['trace']['citations']) for i in answered),len(answered)),
            'correct_abstention':ratio(sum(i['review']['abstention_correct'] for i in missing),len(missing)),
            'false_abstention':ratio(sum(i['trace']['status']=='abstain' for i in answerable),len(answerable)),
            'tool_use_successful':ratio(sum(i['trace']['search_count']>0 for i in good),len(good)),
            'search_distribution_all_queries':dict(Counter(str(i['trace']['search_count']) for i in items)),
            'model_calls':sum(i['trace']['model_call_count'] for i in items),
            'timing_ms':{'total':sum(times),'mean':statistics.mean(times),'median':statistics.median(times),'max':max(times)},
            'usage':usage,'estimated_cost_usd':None,
            'cost_note':'No billing reconciliation/price estimate; local compute and review costs unmeasured.',
            'error_policy':'All assigned queries retained. Error/answerable abstention grounded=0; CER uses last confirmed request even on error; retrieval errors hit=0. Tool-use denominator successful only. Citation 0/0 has null rate (N/A).'}


def evaluate(folder):
    folder=Path(folder).resolve()
    manifest=read(folder/'run.json'); completion=read(folder/'completion.json')
    reviews=read(folder/'reviews.json')
    if not reviews.get('method') or not reviews.get('reviewer'):
        raise ValueError('Review provenance required')
    byreview={r['query_id']:r for r in reviews['reviews']}
    ids=manifest['query_ids']
    if set(byreview)!=set(ids) or len(byreview)!=len(reviews['reviews']):
        raise ValueError('Need exactly one content review per query')
    cases={q['id']:q for q in read(ROOT/'benchmark/day07/cases.json') if q['split']==manifest['split']}
    corpus={c['id']:c for c in read(ROOT/'data/day06/B/chunks.json')}
    if set(cases)!=set(ids) or completion['attempted']!=len(ids):raise ValueError('Incomplete run')
    items=[]
    for qid in ids:
        path=folder/f'{qid}.json'; trace=read(path)
        if sha(path)!=completion['trace_sha256'][qid]:raise ValueError('Completed trace modified')
        validate_trace(trace,corpus)
        review=byreview[qid];case=cases[qid]
        validate_review(review,trace,case,path)
        items.append({'case':case,'trace':trace,'review':review,
                      'context_hit':hit(sent_context(trace),case) if case['answerable'] else None})
    summary=aggregate(items)
    method=manifest['fingerprint']['config']['retriever']
    if manifest['split']=='dev':
        original_path=ROOT/'artifacts/retrieval/hybrid/verified-dev/trace.jsonl'
        old=read(original_path.parent/'summary.json')
        if old['trace_sha256']!=sha(original_path):raise ValueError('Original trace hash changed')
        for name in ('src/dense_retrieval.py','src/lexical_retrieval.py','src/hybrid_retrieval.py',
                     'data/day06/B/chunks.json','benchmark/day07/manifest.json'):
            if old['hashes'][name]!=sha(ROOT/name):raise ValueError('Original-query reuse conditions changed')
        original={r['query']['id']:(r if method=='hybrid' else r['baselines'][method])
                  for r in [json.loads(line) for line in original_path.read_text().splitlines()]}
        original_note='Reused task04 original-query top-3: same corpus, retrieval code/config, query/filter; agent tool queries scored separately through context.'
    else:
        original_path=folder/'original-retrieval.json'
        original=read(original_path)['queries']
        original_note='Original query top-3 measured separately after pretest lock, without query rewrite.'
    summary['original_query_retrieval']={
        'hit_at_1':ratio(sum(original[i['case']['id']]['status']=='ok' and hit(original[i['case']['id']]['results'][:1],i['case']) for i in answerable_items(items)),len(answerable_items(items))),
        'evidence_hit_at_3':ratio(sum(original[i['case']['id']]['status']=='ok' and hit(original[i['case']['id']]['results'][:3],i['case']) for i in answerable_items(items)),len(answerable_items(items))),
        'source':str(original_path.relative_to(ROOT)),'sha256':sha(original_path),'note':original_note}
    summary.update(run_id=manifest['run_id'],split=manifest['split'],fingerprint_sha256=manifest['fingerprint_sha256'],
                   reviews_sha256=sha(folder/'reviews.json'),review_method=reviews['method'],
                   per_query=[{'query_id':i['case']['id'],'context_hit':i['context_hit'],
                               'context_grade':i['review']['context_grade'],'grounded_grade':i['review']['grounded_grade']} for i in items])
    return summary


def answerable_items(items):
    return [i for i in items if i['case']['answerable']]


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('run',type=Path)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    result=evaluate(a.run);write_new(a.output,result)
    print(json.dumps({k:result[k] for k in ('run_id','queries','errors','context_evidence_rate','grounded_mean_out_of_2')},ensure_ascii=False))

if __name__=='__main__':main()
