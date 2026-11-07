import copy
import json
import unittest
from pathlib import Path
from qa.audit import audit, fingerprint
from qa.prepare import prepare


class AuditTests(unittest.TestCase):
    def payload(self):
        return {'isSuccess':True,'error':'','data':{'dataset_group_id':'SYNTHETIC','total_records':1,
            'records':[{'conversation_id':'synthetic-1','language':'EN','channel':'agent chat','audio_url':None,
            'transcript':[{'speaker':'client','timestamp':'00:00','text':'Hello'},
                          {'speaker':'operator','timestamp':'00:02','text':'Hi, I am Test.'}]}]}}

    def test_no_text_or_audio_url_in_audit(self):
        payload=self.payload(); payload['data']['records'][0]['audio_url']='https://example.invalid/?secret=test'
        result=audit(payload)
        self.assertNotIn('Hello',json.dumps(result));self.assertNotIn('secret=test',json.dumps(result))

    def test_missing_and_invalid_time_detected(self):
        payload=self.payload(); m=payload['data']['records'][0]['transcript'][1]
        del m['speaker'];m['timestamp']='00:61';m['text']=' '
        result=audit(payload)
        self.assertEqual(result['counts']['missing_message_speaker'],1)
        self.assertEqual(result['counts']['blank_message_text'],1)
        self.assertEqual(result['issues'][0]['kind'],'invalid_timestamp')

    def test_content_order_role_time_affect_hash(self):
        t=self.payload()['data']['records'][0]['transcript']; original=fingerprint(t)
        for key,val in [('text','Changed'),('timestamp','00:03'),('speaker','bot')]:
            x=copy.deepcopy(t);x[1][key]=val;self.assertNotEqual(original,fingerprint(x))
        self.assertNotEqual(original,fingerprint(list(reversed(t))))

    def test_declared_failure_not_dataset(self):
        with self.assertRaises(ValueError):audit({'isSuccess':False})

    def test_actual_selection_metadata_consistency(self):
        # Uses sanitized metadata; no DEV transcript is loaded by the test suite.
        root=Path(__file__).parent
        read=lambda n:json.loads((root/n).read_text(encoding='utf-8'))
        a,p,c=read('data-audit.json'),read('selection-plan.json'),read('checklist.json')
        result=prepare(a,p,c)
        self.assertEqual(len(result['records']),26)
        self.assertEqual(result['counts'],{'tuning':12,'holdout':14})
        self.assertEqual(result['channel_counts']['voice call'],5)
        self.assertEqual(len([r for r in result['records'] if r['pilot']]),6)
        self.assertEqual(set(result['language_counts']),{'RU','KZ','EN'})
        p['groups'][0]['conversation_ids'].append(p['groups'][1]['conversation_ids'][0])
        with self.assertRaises(ValueError):prepare(a,p,c)


if __name__=='__main__':unittest.main()
