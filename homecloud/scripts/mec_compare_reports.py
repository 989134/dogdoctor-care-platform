"""Create a reviewer-friendly comparison from Edge Agent JSON reports."""
import argparse
import csv
import json
from pathlib import Path


FIELDS=(
    'label','frames','local_safety_frames','remote_success','remote_failed',
    'remote_dropped','rtt_p50_ms','rtt_p95_ms','rtt_p99_ms',
    'end_to_end_p50_ms','end_to_end_p95_ms','end_to_end_p99_ms',
    'fallback_p50_ms','fallback_p95_ms','average_encoded_mbps',
    'events_delivered','events_pending','final_state',
)


def summarize(label,report):
    remote=report.get('remote',{});rtt=report.get('rtt_ms',{})
    e2e=report.get('end_to_end_ms',{});fallback=report.get('fallback_available_ms',{})
    outbox=report.get('outbox',{})
    return {
        'label':label,'frames':report.get('frames_captured'),
        'local_safety_frames':report.get('local_safety_frames'),
        'remote_success':remote.get('sent'),'remote_failed':remote.get('failed'),
        'remote_dropped':remote.get('dropped'),'rtt_p50_ms':rtt.get('p50'),
        'rtt_p95_ms':rtt.get('p95'),'rtt_p99_ms':rtt.get('p99'),
        'end_to_end_p50_ms':e2e.get('p50'),'end_to_end_p95_ms':e2e.get('p95'),
        'end_to_end_p99_ms':e2e.get('p99'),'fallback_p50_ms':fallback.get('p50'),
        'fallback_p95_ms':fallback.get('p95'),
        'average_encoded_mbps':report.get('average_encoded_mbps'),
        'events_delivered':report.get('events_delivered'),
        'events_pending':outbox.get('pending'),'final_state':remote.get('state'),
    }


def parse_spec(spec):
    if '=' not in spec:raise ValueError('report must use LABEL=PATH')
    label,path=spec.split('=',1)
    if not label.strip() or not path.strip():raise ValueError('report must use LABEL=PATH')
    return label.strip(),Path(path.strip())


def write_outputs(rows,output_prefix):
    output_prefix=Path(output_prefix);output_prefix.parent.mkdir(parents=True,exist_ok=True)
    json_path=output_prefix.with_suffix('.json')
    csv_path=output_prefix.with_suffix('.csv')
    markdown_path=output_prefix.with_suffix('.md')
    json_path.write_text(json.dumps({'tests':rows},ensure_ascii=False,indent=2),encoding='utf-8')
    with csv_path.open('w',newline='',encoding='utf-8-sig') as handle:
        writer=csv.DictWriter(handle,fieldnames=FIELDS);writer.writeheader();writer.writerows(rows)
    headings=['網路','影格','MEC 成功','失敗','RTT p50','RTT p95','RTT p99',
              '端到端 p95','本地備援 p95','Mbps','事件送達','待補送','最終狀態']
    keys=['label','frames','remote_success','remote_failed','rtt_p50_ms','rtt_p95_ms',
          'rtt_p99_ms','end_to_end_p95_ms','fallback_p95_ms','average_encoded_mbps',
          'events_delivered','events_pending','final_state']
    lines=['# MEC 實網比較報告','',
           '> 所有名稱由測試者填寫；只有真的透過該網路完成的報告才能標示為 5G 或 MEC。','',
           '| '+' | '.join(headings)+' |','| '+' | '.join(['---']*len(headings))+' |']
    for row in rows:
        lines.append('| '+' | '.join('—' if row.get(k) is None else str(row[k]) for k in keys)+' |')
    markdown_path.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return {'json':str(json_path),'csv':str(csv_path),'markdown':str(markdown_path)}


def main():
    parser=argparse.ArgumentParser(description='Compare Wi-Fi, 5G and MEC Edge Agent reports')
    parser.add_argument('--report',action='append',required=True,metavar='LABEL=PATH')
    parser.add_argument('--output-prefix',type=Path,
                        default=Path('mec_output/network_comparison'))
    args=parser.parse_args();rows=[]
    for spec in args.report:
        label,path=parse_spec(spec)
        rows.append(summarize(label,json.loads(path.read_text(encoding='utf-8'))))
    outputs=write_outputs(rows,args.output_prefix)
    print(json.dumps({'tests':rows,'outputs':outputs},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
