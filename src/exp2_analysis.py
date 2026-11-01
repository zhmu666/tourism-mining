"""Reproducible prepare stage; human review is never filled automatically."""
import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import re
import shutil
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'results/exp2'
FIELDS = set('mention_id doc_id scenic_id entity_type text start end canonical_name poi_id'.split())
TYPES = ('POI','TRANSPORT','TIME')
SEED = 20261101


def csv_write(path, fields, rows):
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def category(text):
    if re.search(r'^(?:周|星期).+[-—–~～至到]',text): return '时间范围'
    if re.fullmatch(r'(?:\d{4}年)?\d{1,2}月',text): return '日期表达'
    if re.search(r'[:：点].*[-—–~～至到]',text): return '时间范围'
    if re.search(r'[:：]|\d点',text): return '具体时刻'
    if re.search(r'\d月\d+[日号]|\d{4}年',text): return '日期表达'
    if re.search(r'第.+天|今天|明天|昨天|当天|次日|前一天|后一天|次年|当年|今年',text): return '相对日期'
    if re.search(r'小时|分钟|秒钟|[一二两三四五六七八九十\d]+天|半天|夜$',text): return '持续时间'
    if re.search(r'早|上午|中午|下午|午后|傍晚|晚上|夜里|凌晨|天黑|日出|日落',text): return '时间段'
    if re.search(r'春|夏|秋|冬|季|暑期|寒假|国庆|节|假期|长假|周末|雪期|梅雨',text): return '季节及旅游时期'
    return '其他无法可靠归类的表达'


def validate(entities, docs):
    ids=set(); positions=set()
    with (ROOT/'config/poi_aliases.csv').open(encoding='utf-8-sig') as f:
        mappings={(r['scenic_id'],r['alias'],r['canonical_name'],r['poi_id']) for r in csv.DictReader(f)}
    for e in entities:
        if set(e)!=FIELDS or e['entity_type'] not in TYPES or e['doc_id'] not in docs: raise ValueError('Invalid entity schema/source')
        d=docs[e['doc_id']]; s=e['start']; t=e['end']
        if type(s)!=int or type(t)!=int or not 0<=s<t<=len(d['clean_text']): raise ValueError('Invalid offsets')
        if e['scenic_id']!=d['scenic_id'] or d['clean_text'][s:t]!=e['text']: raise ValueError('Source mismatch')
        position=(e['doc_id'],s,t,e['entity_type'])
        if e['mention_id'] in ids or position in positions: raise ValueError('Duplicate entity')
        ids.add(e['mention_id']); positions.add(position)
        if e['entity_type']=='POI':
            if (e['scenic_id'],e['text'],e['canonical_name'],e['poi_id']) not in mappings: raise ValueError('POI mapping mismatch')
        elif e['poi_id'] is not None: raise ValueError('Non-POI ID')


def prepare(stage="before"):
    after = stage == "after"
    with (ROOT/'data/processed/documents.csv').open(encoding='utf-8-sig',newline='') as f: rows=list(csv.DictReader(f))
    docs={d['doc_id']:d for d in rows}
    if len(docs)!=len(rows) or any(d['source_type'] not in ('official','tourist') for d in rows): raise ValueError('Invalid documents')
    source=OUT/'entities.json'
    before=OUT/'entities_before.json'
    entities=json.loads(((OUT/'entities_after.json') if after else (before if before.exists() else source)).read_text(encoding='utf-8'))
    validate(entities,docs)
    if not before.exists(): shutil.copyfile(source,before)
    manifest=OUT/'baseline_manifest.json'
    digest=hashlib.sha256(before.read_bytes()).hexdigest()
    if manifest.exists():
        if json.loads(manifest.read_text(encoding='utf-8'))['sha256']!=digest: raise ValueError('Frozen baseline changed')
    else:
        manifest.write_text(json.dumps(dict(sha256=digest,file_count=1,document_count=len(rows),mention_count=len(entities),frozen_at=datetime.now(timezone.utc).isoformat(),source_modified_at=datetime.fromtimestamp(source.stat().st_mtime,timezone.utc).isoformat(),note='原生成时间未记录；仅记录源文件修改时间和冻结时间'),ensure_ascii=False,indent=2),encoding='utf-8')
    freq=OUT/('frequency_'+stage); charts=OUT/('charts_'+stage)
    freq.mkdir(exist_ok=True); charts.mkdir(exist_ok=True)
    denominators={s:sum(len(d['clean_text']) for d in rows if d['scenic_id']==s) for s in sorted({d['scenic_id'] for d in rows})}
    groups=defaultdict(list)
    for e in entities:
        key=e['poi_id'] or e['canonical_name'] or e['text'] if e['entity_type']=='POI' else (e['canonical_name'] or e['text'] if e['entity_type']=='TRANSPORT' else e['text'])
        groups[e['entity_type'],e['scenic_id'],key].append(e)
    stats=[]
    for (kind,scenic,key),items in sorted(groups.items()):
        stats.append(dict(scenic_id=scenic,entity_type=kind,entity_key=key,display_name=items[0]['canonical_name'] or items[0]['text'],mention_count=len(items),document_frequency=len({e['doc_id'] for e in items}),normalized_frequency=len(items)/denominators[scenic]*1000,time_category=category(items[0]['text']) if kind=='TIME' else ''))
    fields=list(stats[0])
    for kind in TYPES: csv_write(freq/(kind.lower()+'_frequency.csv'),fields,[r for r in stats if r['entity_type']==kind])
    density=[]
    for scenic,total in denominators.items():
        for kind in TYPES:
            items=[e for e in entities if e['scenic_id']==scenic and e['entity_type']==kind]
            density.append(dict(scenic_id=scenic,entity_type=kind,mention_count=len(items),document_frequency=len({e['doc_id'] for e in items}),scenic_clean_text_total_chars=total,normalized_frequency=len(items)/total*1000))
    for name in ('frequency_summary.csv','scenic_density.csv'): csv_write(freq/name,list(density[0]),density)
    cats=Counter((e['scenic_id'],category(e['text'])) for e in entities if e['entity_type']=='TIME')
    catrows=[dict(scenic_id=s,entity_type='TIME',time_category=c,mention_count=n,document_frequency=len({e['doc_id'] for e in entities if e['scenic_id']==s and e['entity_type']=='TIME' and category(e['text'])==c}),normalized_frequency=n/denominators[s]*1000) for (s,c),n in sorted(cats.items())]
    csv_write(freq/'time_category_frequency.csv',list(catrows[0]),catrows)
    fonts={f.name for f in font_manager.fontManager.ttflist}
    font=next((f for f in ('Microsoft YaHei','SimHei','Noto Sans CJK SC','SimSun') if f in fonts),None)
    if not font: raise RuntimeError('No Chinese font available')
    plt.rcParams.update({'font.family':font,'axes.unicode_minus':False})
    stops={line.strip() for line in (ROOT/'config/visualization_stopwords.txt').read_text(encoding='utf-8-sig').splitlines() if line.strip() and not line.startswith('#')}
    def plot(name,title,counts,xlabel='提及次数'):
        items=sorted(((k,v) for k,v in counts.items() if k not in stops),key=lambda x:(-x[1],x[0]))[:15][::-1]
        fig,ax=plt.subplots(figsize=(10,7))
        bars=ax.barh([k for k,v in items],[v for k,v in items],color='#4477AA')
        ax.bar_label(bars,fmt='%.2f' if xlabel!='提及次数' else '%.0f',padding=3)
        ax.set(title=title,xlabel=xlabel,ylabel='实体 / 类别'); ax.margins(x=.18)
        fig.tight_layout(); fig.savefig(charts/name,dpi=300); plt.close(fig)
    for scenic in denominators:
        plot(f'poi_{scenic}_top15.png',f'{scenic} 内部景点提及 Top 15',Counter({r['display_name']:r['mention_count'] for r in stats if r['entity_type']=='POI' and r['scenic_id']==scenic}))
    for kind in ('TRANSPORT','TIME'):
        counts=Counter()
        for r in stats:
            if r['entity_type']==kind: counts[r['display_name']]+=r['mention_count']
        plot(kind.lower()+'_top15.png',f'全部18篇文档 {kind} 提及 Top 15',counts)
    plot('time_categories.png','全部18篇文档 时间类别提及分布',Counter(category(e['text']) for e in entities if e['entity_type']=='TIME'))
    plot('scenic_density.png','三个景区三类实体密度（全部文档）',{r['scenic_id']+' '+r['entity_type']:r['normalized_frequency'] for r in density},'每千字提及次数')
    if after: return
    rng=random.Random(SEED); review=[]
    for scenic in denominators:
        for kind in TYPES:
            pool=[e for e in entities if e['scenic_id']==scenic and e['entity_type']==kind]; rng.shuffle(pool); chosen=[]
            for e in pool:
                if not chosen or (e['doc_id']!=chosen[0]['doc_id'] and docs[e['doc_id']]['source_type']!=docs[chosen[0]['doc_id']]['source_type']): chosen.append(e)
                if len(chosen)==2: break
            for e in pool:
                if len(chosen)==2: break
                if e not in chosen and all(e['doc_id']!=x['doc_id'] for x in chosen): chosen.append(e)
            for e in pool:
                if len(chosen)==2: break
                if e not in chosen: chosen.append(e)
            for e in chosen:
                d=docs[e['doc_id']]
                review.append(dict(review_id=f'R{len(review)+1:03d}',**{k:d[k] for k in ('doc_id','scenic_id','scenic_name','source_type')},**{k:e[k] for k in ('entity_type','text','canonical_name','poi_id','start','end')},context=d['clean_text'][max(0,e['start']-60):e['end']+60],manual_label='',error_type='',reason='',action='',review_status='pending'))
    if not (OUT/'manual_review.csv').exists(): csv_write(OUT/'manual_review.csv',list(review[0]),review)
    snippets=[]
    for scenic in denominators:
        for source_type in ('official','tourist'):
            pool=[d for d in rows if d['scenic_id']==scenic and d['source_type']==source_type]
            d=rng.choice(pool); text=d['clean_text']; sentences=list(re.finditer(r'[^。！？\n]+[。！？\n]*',text)); index=rng.randrange(len(sentences)); selected=sentences[index:index+2]; start=selected[0].start(); end=selected[-1].end()
            snippets.append(dict(snippet_id=f'SN{len(snippets)+1:03d}',**{k:d[k] for k in ('doc_id','scenic_id','scenic_name','source_type')},start=start,end=end,snippet_text=text[start:end],predicted_entities=json.dumps([e for e in entities if e['doc_id']==d['doc_id'] and start<=e['start'] and e['end']<=end],ensure_ascii=False),missed_entities='',manual_notes='',review_status='pending'))
    if not (OUT/'manual_review_snippets.csv').exists(): csv_write(OUT/'manual_review_snippets.csv',list(snippets[0]),snippets)
    if not (OUT/'manual_missed_entities.csv').exists(): csv_write(OUT/'manual_missed_entities.csv','snippet_id entity_type text canonical_name poi_id manual_confirmed notes'.split(),[])
    (OUT/'manual_review_guide.md').write_text('''# 人工抽查填写说明
本表必须由用户人工填写，机器未作正确性标注。实体表和片段表均完成后才可讨论优化。

manual_label：correct（正确）、incorrect（误识别）、ambiguous（歧义）。error_type：none、false_positive、wrong_type、wrong_boundary、wrong_normalization、ambiguous。reason填写上下文依据；action填写keep、exclude_context、fix_boundary、fix_type、fix_normalization或needs_discussion。完成后review_status改为completed。

边界检查：原文clean_text[start:end]必须等于text；start从0起，end不包含结束字符。结合上下文确认是否为当前景区内部POI、实际交通方式或时间表达。

片段表需逐句阅读，不能只核对predicted_entities。漏识别填写missed_entities，同时在manual_missed_entities.csv中逐条填写，manual_confirmed使用yes/no，notes说明理由及原文绝对位置。无遗漏可填写“无”；manual_notes记录歧义、类型或边界问题，再将review_status改为completed。不要将小样本正确率推断为全量准确率。
''',encoding='utf-8')
    totals=Counter(e['entity_type'] for e in entities)
    (OUT/'optimization_report.md').write_text(f'''# 实验二 prepare 报告
实际文档：{len(rows)}；实体：{len(entities)}；分类统计：{dict(totals)}。
基线SHA256：{digest}。源生成时间没有可靠历史记录，清单记录源修改时间与冻结时间。

已生成6份统计CSV和7张300 DPI中文图表。归一化分母为对应景区全部clean_text字符数，包含无实体文档。图表只展示高频项，不代表低频实体无效。

实体样例{len(review)}条、原文片段{len(snippets)}条，固定种子{SEED}。抽样优先不同文档和不同来源，片段不依赖预测实体选择。已有人工表不会被重跑覆盖。

人工核验尚未完成，所有人工字段留空，状态pending。尚无人工确认的错误、漏识别或修复；没有修改词典、识别规则或正式实体结果。排除配置仅为空表；展示停用词为空。高频和低频均不能单独作为噪声证据。潜在问题包括交通站点后缀、旅游正文中历史介绍的时间语义、复杂连续时间边界，均为机器待核查方向，不是人工结论。

当前停在prepare；entities_after、frequency_after和charts_after尚未生成，不存在可报告的优化前后质量变化。等待人工抽查。小样本无法估计全量准确率，实体数量变化不能直接解释为质量改善。
''',encoding='utf-8')
    print(dict(documents=len(rows),entities=dict(totals),review=len(review),snippets=len(snippets),font=font))

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('stage',choices=['prepare','after']); args=parser.parse_args(); prepare('after' if args.stage=='after' else 'before')
