"""Build acceptance outputs from explicit, evidence-based review decisions; no HTTP."""
import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / 'data/raw'


def build():
    with (ROOT/'config/source_urls.csv').open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        sources = list(reader)
    report = json.loads((RAW/'crawl_report.json').read_text(encoding='utf-8'))
    results = {r['doc_id']: r for r in report['sources']}
    docs = {d['doc_id']: d for d in map(json.loads, (RAW/'raw_documents.jsonl').read_text(encoding='utf-8').splitlines())}
    decisions = json.loads((RAW/'acceptance_decisions.json').read_text(encoding='utf-8'))
    selected, acceptance = [], []
    for source in sources:
        did = source['doc_id']
        result = results[did]
        decision = decisions[did]
        document = docs.get(did)
        choose = decision['selected_for_final']
        if choose:
            if decision['quality_decision'] != 'accepted' or not document or result['fetch_status'] != 'success':
                raise ValueError('Selected document must have successful fetch and reviewed content: ' + did)
            for field in ('scenic_id', 'source_type', 'source_url'):
                if document[field] != source[field]:
                    raise ValueError('Source identity mismatch: ' + did)
            selected.append(document)
        row = {k: source[k] for k in ('doc_id','scenic_id','source_type','source_name','source_url')}
        row.update({k: result[k] for k in ('fetch_status','parse_status','status_code','title','text_length')})
        row.update(quality_decision=decision['quality_decision'], selected_for_final=str(choose).lower(), reason=decision['reason'])
        acceptance.append(row)
    counts, gaps = {}, []
    for sid in ('S01','S02','S03'):
        counts[sid] = {}
        for kind, goal in [('official',1),('tourist',5)]:
            count = sum(d['scenic_id']==sid and d['source_type']==kind for d in selected)
            if count > goal:
                raise ValueError('Selection exceeds per-scenic quota')
            counts[sid][kind] = count
            if count < goal:
                gaps.append(dict(scenic_id=sid,source_type=kind,missing=goal-count))
    manifest = json.loads((RAW/'history_manifest_before_final.json').read_text(encoding='utf-8'))
    for path, expected in manifest.items():
        if hashlib.sha256((ROOT/path).read_bytes()).hexdigest() != expected:
            raise ValueError('Historical artifact changed: '+path)
    # The original run configuration is immutable evidence of 23 enabled requests.
    original = RAW/'batch_source_urls.csv'
    if not original.exists():
        original.write_bytes((ROOT/'config/source_urls.csv').read_bytes())
    with (RAW/'source_acceptance.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(acceptance[0]));writer.writeheader();writer.writerows(acceptance)
    (RAW/'selected_documents.jsonl').write_text(''.join(json.dumps(d,ensure_ascii=False)+'\n' for d in selected),encoding='utf-8')
    chosen={d['doc_id'] for d in selected}
    for source in sources:
        source['enabled']=str(source['doc_id'] in chosen).lower()
    with (ROOT/'config/source_urls.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(sources)
    summary=dict(crawl_generated_at=report['generated_at'],crawl_summary=report['summary'],selected_count=len(selected),counts=counts,gaps=gaps,target_met=not gaps,history_files_verified=len(manifest),network_rounds=1)
    (RAW/'acceptance_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=['# 本轮采集验收报告','',f"采集时间（UTC）：{report['generated_at']}",f"最终选入 {len(selected)} / 18 篇；目标{'已满足' if not gaps else '未满足'}。",'', '仅使用本轮真实采集正文；历史成功记录保存在 history，不混入本轮结果。','', '| 景区 | 官方路线 / 1 | 游记 / 5 |','|---|---:|---:|']
    for sid,c in counts.items():lines.append(f"| {sid} | {c['official']} | {c['tourist']} |")
    lines += ['', '详细逐源验收见 source_acceptance.csv，人工内容判断依据见 acceptance_decisions.json。','', '| doc_id | HTTP | 字符数 | 质量 | 入选 | 原因 |','|---|---|---:|---|---|---|']
    for a in acceptance:
        lines.append(f"| {a['doc_id']} | {a['status_code'] if a['status_code'] is not None else '无响应/未请求'} | {a['text_length']} | {a['quality_decision']} | {a['selected_for_final']} | {a['reason'].replace('|','/')} |")
    lines += ['', '停止条件：所给候选已完成本轮尝试，不再搜索新网址或重复批量请求。未入选来源全部禁用；后续不得把历史正文或 HTTP 200 无关内容冒充本轮合格样本。']
    (RAW/'acceptance_report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':
    build()
