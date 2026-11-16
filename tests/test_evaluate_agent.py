import unittest
from scripts.evaluate_agent import hit, ratio, sent_context, aggregate


class EvaluationTests(unittest.TestCase):
    def test_gold_or_requires_exact_source_page_and_whole_quote(self):
        case={'gold':[{'source_id':'s','page':2,'evidence':'whole evidence'},
                      {'source_id':'s','page':3,'evidence':'alternative'}]}
        chunk={'source_id':'s','page':3,'text':'alternative'}
        self.assertTrue(hit([chunk],case))
        self.assertFalse(hit([dict(chunk,page=2)],case))
        self.assertFalse(hit([dict(chunk,source_id='x')],case))
        self.assertFalse(hit([{'source_id':'s','page':2,'text':'whole'},
                              {'source_id':'s','page':2,'text':'evidence'}],case))

    def test_prepared_and_failed_request_are_not_sent_context(self):
        trace={'model_calls':[{'delivery_confirmed':True,'context_chunks':[{'id':'old'}]},
                              {'delivery_confirmed':False,'context_chunks':[{'id':'new'}]}]}
        self.assertEqual(sent_context(trace),[{'id':'old'}])
        self.assertEqual(sent_context({'model_calls':[]}),[])

    def test_error_and_false_abstention_denominators_no_citations_na(self):
        def item(answerable,status,searches,grade,correct):
            return {'case':{'answerable':answerable},'context_hit':False,
                    'trace':{'status':status,'search_count':searches,'citations':[],
                             'context_ids':[],'model_call_count':1,'elapsed_ms':2,
                             'usage':dict.fromkeys(('input_tokens','output_tokens','cached_tokens','reasoning_tokens'))},
                    'review':{'context_grade':grade,'grounded_grade':grade,'citation_reviews':[],
                              'abstention_correct':correct}}
        s=aggregate([item(True,'abstain',1,0,None),item(True,'error',0,0,None),
                     item(False,'abstain',2,None,True),item(False,'error',0,None,False)])
        self.assertEqual(s['correct_abstention'],ratio(1,2))
        self.assertEqual(s['false_abstention'],ratio(1,2))
        self.assertEqual(s['tool_use_successful'],ratio(2,2))
        self.assertEqual(s['citation_validity'],ratio(0,0))
        self.assertEqual(s['context_evidence_rate'],ratio(0,2))
        self.assertEqual(s['errors'],2)
        self.assertEqual(s['grounded_mean_out_of_2'],0)

if __name__=='__main__':unittest.main()
