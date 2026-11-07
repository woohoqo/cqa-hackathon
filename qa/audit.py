"""Read organizer DEV locally; emit aggregate metadata only, never transcript/audio URLs."""
import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def audit(payload):
    if payload.get('isSuccess') is not True:
        raise ValueError('Organizer response is not successful')
    data = payload['data']
    records = data['records']
    if not isinstance(records, list):
        raise ValueError('data.records must be an array')
    counts, issues, ids, text_hashes = Counter(), [], Counter(), {}
    record_keys, message_keys, roles = set(), set(), Counter()
    languages, channels, cross = Counter(), Counter(), Counter()
    timestamp_examples = {}
    lengths, metadata = [], []
    for r in records:
        cid = r.get('conversation_id')
        ids[cid] += 1
        record_keys.update(r)
        languages[str(r.get('language'))] += 1
        channels[str(r.get('channel'))] += 1
        cross[str(r.get('language')) + '/' + str(r.get('channel'))] += 1
        if not isinstance(cid, str) or not cid:
            issues.append({'record_index':len(metadata), 'kind':'missing_id'})
        for key in ('conversation_id','channel','language','transcript','audio_url'):
            counts['missing_record_' + key] += key not in r
        t = r.get('transcript')
        if not isinstance(t, list):
            issues.append({'conversation_id':cid,'kind':'transcript_not_array'})
            continue
        counts['empty_transcripts'] += len(t) == 0
        counts['audio_present'] += bool(r.get('audio_url'))
        counts['chat_audio_present'] += r.get('channel') == 'agent chat' and bool(r.get('audio_url'))
        counts['call_audio_missing'] += r.get('channel') == 'voice call' and not r.get('audio_url')
        prev = None
        for i,m in enumerate(t):
            if not isinstance(m, dict):
                issues.append({'conversation_id':cid,'message_index':i,'kind':'message_not_object'})
                continue
            counts['messages'] += 1
            message_keys.update(m)
            roles[str(m.get('speaker'))] += 1
            for key in ('speaker','timestamp','text'):
                counts['missing_message_'+key] += key not in m
                counts['null_message_'+key] += m.get(key) is None
                counts['blank_message_'+key] += isinstance(m.get(key),str) and not m[key].strip()
            counts['unknown_roles'] += m.get('speaker') not in ('client','operator','bot','system')
            counts['redaction_markers'] += bool(re.search(r'\[(?:Персональные данные|Данные идентификации) скрыты\]', str(m.get('text',''))))
            stamp=m.get('timestamp')
            try:
                if r['channel']=='agent chat':
                    if not isinstance(stamp,str) or not re.fullmatch(r'\d{2,}:\d{2}',stamp):
                        raise ValueError('bad MM:SS')
                    minute,second=map(int,stamp.split(':'))
                    if second>=60: raise ValueError('bad seconds')
                    current=minute*60+second
                    kind='MM:SS'
                else:
                    current=datetime.fromisoformat(stamp.replace('Z','+00:00'))
                    if current.utcoffset() is None: raise ValueError('missing timezone')
                    kind='ISO8601_with_timezone'
                counts['timestamp_'+kind]+=1
                timestamp_examples[kind]=True
                if prev is not None and current < prev:
                    issues.append({'conversation_id':cid,'message_index':i,'kind':'timestamp_decrease'})
                counts['equal_adjacent_timestamps'] += prev is not None and current == prev
                prev=current
            except (ValueError,TypeError,AttributeError):
                issues.append({'conversation_id':cid,'message_index':i,'kind':'invalid_timestamp'})
            if i and m.get('text') == t[i-1].get('text') and m.get('speaker') == t[i-1].get('speaker'):
                counts['adjacent_duplicate_text_same_speaker']+=1
        lengths.append(len(t))
        content=fingerprint([{'speaker':m.get('speaker'),'text':m.get('text')} for m in t if isinstance(m,dict)])
        text_hashes.setdefault(content,[]).append(cid)
        metadata.append({'conversation_id':cid,'language':r.get('language'),
                         'channel':r.get('channel'),'messages':len(t),
                         'transcript_hash':fingerprint(t)})
    return {'dataset_group_id':data.get('dataset_group_id'),'reported_records':data.get('total_records'),
            'observed_records':len(records),'count_matches':data.get('total_records')==len(records),
            'dataset_content_hash':fingerprint(records),'languages':dict(languages),
            'channels':dict(channels),'language_channel':dict(cross),'roles':dict(roles),
            'record_keys':sorted(record_keys),'message_keys':sorted(message_keys),
            'top_level_keys':sorted(payload),'data_keys':sorted(data),
            'counts':dict(counts),'message_count_min':min(lengths,default=0),
            'message_count_max':max(lengths,default=0),'issues':issues,
            'duplicate_ids':[k for k,v in ids.items() if v>1],
            'identical_transcript_groups':[v for v in text_hashes.values() if len(v)>1],
            'reference_assessment':'No reference score fields in inspected schema; external references unknown',
            'records':metadata}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('input'); p.add_argument('output')
    a=p.parse_args()
    result=audit(json.loads(Path(a.input).read_text(encoding='utf-8-sig')))
    Path(a.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('observed_records','languages','channels','issues')},ensure_ascii=False))


if __name__=='__main__': main()
