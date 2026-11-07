"""Create metadata manifest and blank labels; never copy a transcript."""
import argparse
import json
from collections import Counter
from pathlib import Path
from qa.audit import fingerprint


def prepare(audit, plan, checklist):
    records={r['conversation_id']:r for r in audit['records']}
    assigned={}
    for group in plan['groups']:
        if group['split'] not in ('tuning','holdout'): raise ValueError('Invalid split')
        for cid in group['conversation_ids']:
            if cid not in records or cid in assigned: raise ValueError('Unknown/duplicate grouped ID')
            assigned[cid]=group
    if set(assigned)!=set(records): raise ValueError('Every DEV record must be grouped')
    if len(set(plan['selected']))!=len(plan['selected']): raise ValueError('Duplicate selection')
    if not set(plan['pilot'])<=set(plan['selected']): raise ValueError('Pilot must be selected')
    manifest=[]
    for cid in plan['selected']:
        group=assigned[cid]
        pilot=cid in plan['pilot']
        if pilot and group['split']!='tuning': raise ValueError('Pilot cannot enter holdout')
        manifest.append({**records[cid],'scenario_group_id':group['scenario_group_id'],
                         'split':group['split'],'pilot':pilot})
    return {'format_version':1,'config_version':checklist['config_version'],
            'checklist_hash':fingerprint(checklist),'dataset_content_hash':audit['dataset_content_hash'],
            'selection_version':'qa-proposal-v1','freeze_status':'pending_human_group_review',
            'grouping_source':'automatically_proposed_not_ground_truth','records':manifest,
            'counts':dict(Counter(r['split'] for r in manifest)),
            'language_counts':dict(Counter(r['language'] for r in manifest)),
            'channel_counts':dict(Counter(r['channel'] for r in manifest))}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory',default='qa')
    a=p.parse_args(); root=Path(a.directory)
    read=lambda name:json.loads((root/name).read_text(encoding='utf-8-sig'))
    manifest=prepare(read('data-audit.json'),read('selection-plan.json'),read('checklist.json'))
    (root/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:manifest[k] for k in ('counts','language_counts','channel_counts','checklist_hash')}))


if __name__=='__main__': main()
