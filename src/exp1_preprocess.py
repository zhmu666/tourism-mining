"""Experiment 1: conservative, auditable offline cleaning; never modify source files."""
import argparse
import csv
import hashlib
import html
import itertools
import json
import re
import shutil
import sys
import tempfile
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ['doc_id', 'scenic_id', 'scenic_name', 'source_type', 'source_name',
          'source_url', 'title', 'raw_text', 'clean_text']
REQUIRED = FIELDS[:-1]
OUTPUTS = ['documents.csv', 'anomalies.csv', 'duplicate_candidates.csv',
           'cleaning_report.md', 'cleaning_audit.jsonl', 'cleaning_summary.json']
RULE_VERSION = 'exp1-frozen-1'
DATE = r'\d{4}[.\-/年]\d{1,2}[.\-/月]\d{1,2}日?(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?'
METRIC = r'[\d,.]+\s*[万千亿wkWKMm]?'
NOTICE = ('公告：\n亲爱的用户：感谢您对游记业务的长久支持。由于战略方向的调整，'
          '我们将于2025-11-30 23:59 停止游记业务的运维。您已发布的游记会继续保留，'
          '但不再提供“编辑”功能。后续您可以通过【\n发布笔记\n】，记录您的美好旅行。')
REVIEW_FLAGS = {
    'editorial_repost': '平台整理过的游客原帖，非独立核实的亲历证据',
    'historical_route_facts_to_verify': '原游记路线事实需人工核实，清洗不纠正事实',
    'generic_title': '原题泛化为站点名称，按接口保留不补写',
    'limited_target_section': '目标景区段落较短，保留多景区原文，不作段落过滤',
    'climate_overview': '包含气候综述与具体路线，保留非模板正文',
    'no_personal_trip_date': '无法确认个人出游日期，不把推荐行程当已核实亲历',
}


class BodyText(HTMLParser):
    """Recognize HTML structure; keep unknown angle-bracket text verbatim."""
    blocks = set('p div section article main h1 h2 h3 h4 h5 h6 ul ol li table tr blockquote pre'.split())
    inline = set('a span b strong i em u s small big font sub sup code tbody td th html body head title'.split())
    hidden = {'script', 'style', 'noscript', 'nav', 'footer'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.suppressed = []

    def handle_starttag(self, tag, attrs):
        if self.suppressed:
            if tag == self.suppressed[-1]:
                self.suppressed.append(tag)
            return
        if tag in self.hidden:
            self.suppressed.append(tag)
        elif tag in self.blocks or tag in ('br', 'hr'):
            self.parts.append('\n')
        elif tag not in self.inline and tag not in ('img', 'meta', 'link', 'input'):
            self.parts.append(self.get_starttag_text())

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if self.suppressed and self.suppressed[-1] == tag:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.suppressed:
            if tag == self.suppressed[-1]:
                self.suppressed.pop()
            return
        if tag in self.blocks:
            self.parts.append('\n')
        elif tag not in self.inline and tag not in ('br', 'hr'):
            self.parts.append('</' + tag + '>')

    def handle_data(self, data):
        if not self.suppressed:
            self.parts.append(data)


HTML_TAG = re.compile(r'</?(?:p|div|span|section|article|main|h[1-6]|br|hr|script|style|noscript|nav|footer|ul|ol|li|table|tr|td|strong|em|b|i|a)(?:\s|/?>)', re.I)


def clean_text(raw_text, title='', source_url=''):
    """Return clean text plus deterministic edits; raw_text is never assigned to."""
    text = raw_text
    edits = []

    def apply(rule, new, matches=1):
        nonlocal text
        if new != text:
            edits.append(dict(rule=rule, matches=matches, before_length=len(text),
                              after_length=len(new), character_delta=len(text)-len(new)))
            text = new

    def sub(rule, pattern, replacement='', flags=0):
        new, count = re.subn(pattern, replacement, text, flags=flags)
        if count:
            apply(rule, new, count)

    apply('normalize_newlines', text.replace('\r\n', '\n').replace('\r', '\n'))
    # Decode entities before HTML detection, including double-encoded site fragments.
    for _ in range(3):
        decoded = html.unescape(text)
        if decoded == text:
            break
        apply('decode_html_entities', decoded)
    if HTML_TAG.search(text):
        parser = BodyText()
        parser.feed(text)
        parser.close()
        apply('html_tags_and_page_code', ''.join(parser.parts))
    apply('remove_control_characters', ''.join(c for c in text if
          not (unicodedata.category(c) == 'Cc' and c not in '\n\t')
          and c not in '\ufeff\u200b\u2060'))
    # Fixed Ctrip notice: full wording and closing boundary required, never a generic 公告.* deletion.
    notice_pattern = re.escape(NOTICE).replace(r'\
', r'\s*')
    sub('ctrip_fixed_notice', notice_pattern)
    try:
        host = (urlsplit(source_url).hostname or '').lower()
    except ValueError:
        host = ''
    if host.endswith('.ctrip.com') or host == 'ctrip.com':
        # A standalone date is publication metadata ONLY when anchored to the exact article title.
        if title:
            sub('ctrip_publication_header', r'\A\s*' + DATE + r'\s*\n(?=' + re.escape(title) + r'(?:\n|$))')
        sub('ctrip_publication_footer', r'(?m)^' + DATE + r'\s*\n发布\s*\n(?=阅读量\s*\n' + METRIC + r'\s*(?:\n|$))')
    sub('display_metrics', r'(?m)^(?:阅读量|浏览量|点赞数)\s*\n' + METRIC + r'\s*(?:\n|$)')
    sub('display_metrics_inline', r'(?m)^(?:阅读量|浏览量|点赞数)\s*[:：]\s*' + METRIC + r'\s*$')
    sub('explicit_publication_date', r'(?m)^(?:发布时间|发布日期)\s*[:：]\s*' + DATE + r'\s*$')
    # The observed Qunar layout has bounded comment/card sections. Never cut at a broad 推荐 keyword.
    if host == 'travelnews.qunar.com':
        if title:
            sub('qunar_byline_publication', r'(?m)(?<=' + re.escape(title) + r')\n[^\n]+\n·\n[^\n]+\n用户\n·\n[^\n]+\n· 原帖发布于\n' + DATE + r'(?=\n)')
        sub('qunar_original_post_metadata', r'(?m)^原帖由作者发布于\n' + DATE + r'\n，文中的行程、价格与营业信息为其当时所见。')
        sub('qunar_editor_metadata', r'(?m)^本页由去哪儿内容团队整理于\n' + DATE + r'\n。')
        sub('qunar_comments', r'(?ms)^其他旅行者怎么说（\d+ 条）\n.*?^共 \d+ 条评论，此处展示 \d+ 条。\s*$')
        sub('qunar_related_cards', r'(?ms)^更多[^\n]+的真实游记\n看全部[^\n]+游记 ›\n相关游记\n.*?(?=^[^\n]*的实时机票与酒店价格，在去哪儿旅行 App 里比一比再定。$)')
        sub('qunar_app_promotion', r'(?m)^[^\n]*的实时机票与酒店价格，在去哪儿旅行 App 里比一比再定。$')
        sub('qunar_editor_disclaimer', r'(?m)^📝 本文为旅行者原创发布，经内容团队整理后收录。[^\n]*出行前请以商家与官方渠道的最新信息为准。$')
    sub('explicit_template_lines', r'(?m)^(?:扫码下载去哪儿旅行 App|‹ 返回全部游记|打印此页|关闭窗口|游记目录|返回顶部)\s*$')
    if host == 'touch.travel.qunar.com':
        sub('qunar_parent_article_link', r'(?m)^来自游记\n[^\n]+\s*\Z')
    # These exact standalone account advertisements are confirmed in the input; not a hotel/门票 filter.
    sub('confirmed_account_promotion', r'(?m)^(?:搜索微信公众号“繁简气象”，更多精彩。|关注微信公众号“繁简气象”)\s*$')
    sub('end_template', r'(?m)^-{3,}THE END-{3,}\s*$')
    sub('copyright_footer', r'(?mi)^(?:copyright\s*(?:©|\(c\))[^\n]*|[京粤皖]ICP备\d+号(?:-\d+)?)\s*$')
    # Whitespace only: preserve punctuation, numbering, units, dates, arrows and paragraph order.
    normalized = re.sub(r'[^\S\n]+', ' ', text)
    normalized = '\n'.join(line.strip() for line in normalized.split('\n'))
    normalized = re.sub(r'\n{3,}', '\n\n', normalized).strip()
    apply('normalize_whitespace', normalized)
    return text, edits


def display(value):
    text = value if isinstance(value, str) else ''
    return text.encode('utf-8', errors='backslashreplace').decode('utf-8')


def issue(doc_id, line, kind, reason, action='review'):
    return dict(doc_id=display(doc_id), input_line=line, issue_type=kind, reason=display(reason), action=action)


def read_input(path):
    data = Path(path).read_bytes()
    records, anomalies = [], []
    for line, value in enumerate(data.splitlines(), 1):
        try:
            obj = json.loads(value.decode('utf-8-sig'))
            if not isinstance(obj, dict):
                raise ValueError('JSON记录必须为对象')
            records.append((line, obj))
        except (UnicodeError, ValueError) as exc:
            anomalies.append(issue('', line, 'invalid_json', str(exc), 'exclude'))
    return data, records, anomalies


def validate(records, spots):
    anomalies = []
    missing = Counter({key: 0 for key in REQUIRED})
    ids = Counter(d.get('doc_id') for _, d in records if isinstance(d.get('doc_id'), str) and d['doc_id'].strip())
    for line, doc in records:
        did = doc.get('doc_id') if isinstance(doc.get('doc_id'), str) else ''
        for key in REQUIRED:
            value = doc.get(key)
            if not isinstance(value, str) or not value.strip():
                missing[key] += 1
                anomalies.append(issue(did, line, 'required_field', f'{key}缺失、非字符串或为空', 'exclude'))
        if ids.get(did, 0) > 1:
            anomalies.append(issue(did, line, 'doc_id_conflict', '所有同ID记录均排除，待人工确认正确记录', 'exclude'))
        sid = doc.get('scenic_id')
        if isinstance(sid, str) and sid not in spots:
            anomalies.append(issue(did, line, 'invalid_scenic_id', f'未知景区编号：{sid}', 'exclude'))
        elif isinstance(sid, str) and doc.get('scenic_name') != spots[sid]:
            anomalies.append(issue(did, line, 'scenic_name_mismatch', '景区名称与正式配置映射不一致，不擅自纠正', 'exclude'))
        if doc.get('source_type') not in ('official', 'tourist'):
            anomalies.append(issue(did, line, 'invalid_source_type', '仅允许official或tourist', 'exclude'))
        url = doc.get('source_url')
        if isinstance(url, str) and url.strip():
            try:
                parts = urlsplit(url)
                valid = parts.scheme in ('http', 'https') and bool(parts.hostname) and not re.search(r'\s', url)
            except ValueError:
                valid = False
            if not valid:
                anomalies.append(issue(did, line, 'invalid_url', 'URL不是有效HTTP(S)网址', 'exclude'))
        if any(isinstance(doc.get(k), str) and any(0xD800 <= ord(c) <= 0xDFFF for c in doc[k]) for k in REQUIRED):
            anomalies.append(issue(did, line, 'unpaired_surrogate', '存在无法用UTF-8原样输出的孤立代理码点', 'exclude'))
    return anomalies, dict(missing)


def find_duplicates(records, clean_by_line, threshold=.80):
    """Exact groups plus character 5-shingle Jaccard; no candidate is removed."""
    candidates = []
    for kind, getter in [('same_source_url', lambda line, d: d.get('source_url')),
                         ('same_raw_text', lambda line, d: d.get('raw_text')),
                         ('same_clean_text', lambda line, d: clean_by_line.get(line))]:
        groups = {}
        for line, doc in records:
            value = getter(line, doc)
            if isinstance(value, str) and value.strip():
                groups.setdefault(value, []).append((line, doc))
        for group in groups.values():
            for (a, da), (b, db) in itertools.combinations(group, 2):
                candidates.append(dict(duplicate_type=kind, doc_id_a=display(da.get('doc_id', '')), input_line_a=a,
                    doc_id_b=display(db.get('doc_id', '')), input_line_b=b, similarity=1.0,
                    method='exact_string', action='retain_pending_confirmation'))
    shingles = {}
    for line, doc in records:
        t = re.sub(r'\s+', '', clean_by_line.get(line, ''))
        if len(t) >= 80:
            shingles[line] = {t[i:i+5] for i in range(len(t)-4)}
    for (a, da), (b, db) in itertools.combinations(records, 2):
        if a not in shingles or b not in shingles or clean_by_line[a] == clean_by_line[b]:
            continue
        left, right = shingles[a], shingles[b]
        score = len(left & right) / len(left | right)
        if score >= threshold:
            candidates.append(dict(duplicate_type='near_clean_text', doc_id_a=display(da.get('doc_id', '')), input_line_a=a,
                doc_id_b=display(db.get('doc_id', '')), input_line_b=b, similarity=round(score, 6),
                method='whitespace_removed_character_5gram_jaccard>=0.80;min_length=80', action='retain_pending_confirmation'))
    return candidates


def review_text(doc, line, text):
    issues = []
    raw = doc['raw_text']
    if len(raw.strip()) < 200 or len(text) < 200:
        issues.append(issue(doc['doc_id'], line, 'short_text', f'原文{len(raw)}、清洗后{len(text)}字符，短文仅复核不按长度删除'))
    if '\ufffd' in raw or any(unicodedata.category(c) == 'Co' for c in raw):
        issues.append(issue(doc['doc_id'], line, 'suspected_garbled', '原文含替换字符或私用区码点，保留可输出原值，不猜测修复'))
    for value in re.findall(r'(?m)^' + DATE + r'\s*$', text):
        issues.append(issue(doc['doc_id'], line, 'ambiguous_date', f'独立日期{value.strip()}缺乏明确发布日期边界，保留待复核'))
    if re.search(r'(?m)^公告[:：]', text):
        issues.append(issue(doc['doc_id'], line, 'unknown_notice', '非已确认固定公告，保留待人工判断'))
    return issues


def inherited_reviews(path, records):
    if not path or not Path(path).exists():
        return []
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        flags = {r['doc_id']: r.get('quality_flags', '').split(';') for r in csv.DictReader(f)}
    return [issue(doc['doc_id'], line, 'source_quality_review', REVIEW_FLAGS[flag])
            for line, doc in records for flag in flags.get(doc.get('doc_id'), []) if flag in REVIEW_FLAGS]


def backup_outputs(directory):
    existing = [directory/name for name in OUTPUTS if (directory/name).exists()]
    if not existing:
        return None
    history = directory/'history'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    history.mkdir(parents=True)
    for file in existing:
        shutil.copy2(file, history/file.name)
    return history


def csv_string(fields, rows):
    import io
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode('utf-8-sig')


def atomic_write(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.exp1-', delete=False) as f:
        temp = Path(f.name)
        f.write(data)
    temp.replace(path)


def make_report(summary, stats, anomalies, duplicates, audits):
    lines = ['# 实验一：数据清洗与格式统一质量报告', '',
        f"验收结论：{summary['conclusion']}。", '',
        f"输入：`{summary['input_file']}`；SHA-256 `{summary['input_sha256']}`。",
        f"输出：`{summary['output_file']}`，UTF-8-SIG；Python {summary['python_version']}。规则版本：{RULE_VERSION}。",
        '未在工作区找到独立任务书、评分细则或小组docx；依据用户冻结规则和README九字段约定实施，没有发现已知接口冲突。',
        '字段映射：JSONL中的前八个正式字段同名复制（忽略采集追溯附加字段），只新生成clean_text；原始title、source_name、raw_text逐值保留。', '',
        f"输入行数{summary['input_records']}，可解析对象{summary['parsed_records']}，输出{summary['output_records']}，排除{summary['excluded_records']}。",
        f"可解析原文字符合计{summary['raw_characters']}；输出对应原文{summary['output_raw_characters']}，清洗后{summary['clean_characters']}，净减少{summary['removed_characters']}（{summary['reduction_percent']}%）。字符数为Python len，包含换行与空白。", '',
        '| 景区 | official | tourist | 合计 |', '|---|---:|---:|---:|']
    for sid, c in summary['distribution'].items():
        lines.append(f"| {sid} | {c['official']} | {c['tourist']} | {sum(c.values())} |")
    lines += ['', '## 必需字段及异常', '', '| 字段 | 缺失/类型错误/空值条数 |', '|---|---:|']
    lines += [f'| {key} | {value} |' for key, value in summary['missing_fields'].items()]
    lines += ['', f"空原文{summary['empty_raw_records']}，空清洗正文{summary['empty_clean_records']}；异常事项{len(anomalies)}，需复核文档{summary['review_documents']}篇；详细行号、原因和处理见anomalies.csv。", '',
        '| issue_type | 条数 |', '|---|---:|']
    lines += [f'| {key} | {value} |' for key, value in sorted(Counter(a['issue_type'] for a in anomalies).items())]
    lines += ['', '## 重复候选', '',
        f"候选关系{len(duplicates)}条（同一对可有多个检测原因），涉及{summary['duplicate_documents']}篇；同URL、原文完全相同、清洗后完全相同逐对检测。近似重复用去空白后的字符5-gram集合Jaccard≥0.80，最短80字符，仅筛高度相似，不代表不存在局部引用。",
        '除无法确认的doc_id冲突全部排除外，不自动删除任何重复候选；同URL或同正文候选保留。详见duplicate_candidates.csv。', '',
        '## 每篇字符变化', '', '| doc_id | 输入行 | 原文 | 清洗后 | 净减少 | 输出 |', '|---|---:|---:|---:|---:|---|']
    lines += [f"| {s['doc_id']} | {s['input_line']} | {s['raw_length']} | {s['clean_length']} | {s['raw_length']-s['clean_length']} | {'是' if s['included'] else '否'} |" for s in stats]
    lines += ['', '## 清洗规则及实际命中', '',
        '使用HTMLParser按真实标签结构保留正文和段落；HTML实体解码、删除明确脚本样式、统一换行及特殊空白、删除无意义控制字符。未知尖括号内容不按宽泛正则删除。',
        '公告只匹配已核验完整固定措辞；去哪儿评论和推荐卡片要求成对的明确模板边界，不从“推荐”或“酒店”等关键词截断文章。酒店、门票、多景区和历史旅游正文默认保留。',
        '携程头部日期仅在紧邻精确原题且匹配平台来源时作为发布日期去除；尾部日期要求“发布/阅读量”上下文。出发时间、行程天数、人均花费、同行人员、游览日期/价格/距离/时长与编号箭头不删除。未知日期保留并复核。', '',
        '| rule | 文档阶段命中次数 | 匹配合计 |', '|---|---:|---:|']
    counts = Counter(e['rule'] for a in audits for e in a['edits'])
    matches = Counter()
    for audit in audits:
        for edit in audit['edits']:
            matches[edit['rule']] += edit['matches']
    lines += [f'| {rule} | {count} | {matches[rule]} |' for rule, count in sorted(counts.items())]
    if not counts.get('html_tags_and_page_code') and not counts.get('decode_html_entities'):
        lines += ['', f"本批{summary['parsed_records']}篇原文没有命中残留HTML标签/实体规则，相关功能由离线样例测试覆盖；不虚报真实数据中删除了HTML。"]
    lines += ['', 'cleaning_audit.jsonl逐篇记录各阶段字符变化，不重写原始记录。', '', '## 三个景区的清洗前后对比', '']
    # Contextual examples, actual input/output slices; at least one per scenic area.
    for sid in ('S01', 'S02', 'S03'):
        options = [a for a in audits if a['scenic_id'] == sid and a['raw_text'] != a['clean_text']]
        if not options:
            options = [a for a in audits if a['scenic_id'] == sid]
        if not options:
            continue
        a = max(options, key=lambda item: len(item['raw_text'])-len(item['clean_text']))
        lines += [f"### {a['doc_id']}", '', '清洗前：', '```text', a['raw_text'][:650], '```', '', '清洗后：', '```text', a['clean_text'][:650], '```', '']
    for a in audits:
        if a['source_type'] == 'official':
            lines += [f"### 路线保护抽查 {a['doc_id']}", '', '清洗后实际片段：', '```text', a['clean_text'][:850], '```', '']
    special = next((a for a in audits if '\u00a0' in a['raw_text']), None)
    if special:
        offset = special['raw_text'].index('\u00a0')
        fragment = special['raw_text'][max(0, offset-30):offset+45]
        lines += [f"### 特殊空白 {special['doc_id']}", '', '以下用Python repr显示不间断空格，便于检查：', '```text', '清洗前：'+repr(fragment), '空白统一后：'+repr(re.sub(r'[^\S\n]+', ' ', fragment)), '```', '']
    lines += ['## 人工复核清单', '', '| doc_id | 行 | 类型 | 原因 |', '|---|---:|---|---|']
    lines += [f"| {a['doc_id']} | {a['input_line']} | {a['issue_type']} | {a['reason'].replace('|','/')} |" for a in anomalies if a['action'] == 'review']
    lines += ['', '## 输出验收', '',
        '生成后重新读取CSV并验证九字段顺序、UTF-8-SIG、行数、逐记录前八字段及raw_text与原始输入一致；原始输入和景区配置SHA-256前后一致。重复运行从同一JSONL重新开始，旧输出只备份本模块六个文件，不累加记录。',
        f"实际输出分布是否满足三个景区各1官方+5游记：{summary['target_met']}。", 
        '技术清洗验收不等于来源事实、亲历真实性或历史票价得到独立核实；上述人工复核不会被静默修补，不阻止结构正确且正文可用的记录进入CSV。']
    return '\n'.join(lines) + '\n'


def run_pipeline(input_path=ROOT/'data/raw/selected_documents.jsonl',
                 spots_path=ROOT/'config/scenic_spots.json',
                 output_dir=ROOT/'data/processed', acceptance_path=ROOT/'data/raw/source_acceptance.csv'):
    input_path, spots_path, output_dir = map(lambda p: Path(p).resolve(), (input_path, spots_path, output_dir))
    if output_dir == input_path.parent or output_dir == spots_path.parent:
        raise ValueError('输出目录不能是原始数据或配置目录')
    spot_bytes = spots_path.read_bytes()
    spot_list = json.loads(spot_bytes.decode('utf-8-sig'))
    spots = {s['scenic_id']: s['scenic_name'] for s in spot_list}
    if set(spots) != {'S01', 'S02', 'S03'} or len(spots) != len(spot_list):
        raise ValueError('景区映射必须唯一且包含S01/S02/S03')
    source_bytes, records, anomalies = read_input(input_path)
    errors, missing = validate(records, spots)
    anomalies += errors
    excluded_lines = {a['input_line'] for a in anomalies if a['action'] == 'exclude'}
    cleaned, audits, stats, output = {}, [], [], []
    for line, doc in records:
        raw = doc.get('raw_text')
        if not isinstance(raw, str):
            continue
        text, edits = clean_text(raw, doc.get('title') if isinstance(doc.get('title'), str) else '',
                                 doc.get('source_url') if isinstance(doc.get('source_url'), str) else '')
        cleaned[line] = text
        if not text.strip():
            anomalies.append(issue(doc.get('doc_id'), line, 'empty_clean_text', '清洗后正文为空，不可进入正式CSV', 'exclude'))
            excluded_lines.add(line)
        if line not in excluded_lines:
            anomalies += review_text(doc, line, text)
            output.append({**{k: doc[k] for k in REQUIRED}, 'clean_text': text})
        stats.append(dict(doc_id=display(doc.get('doc_id', '')), input_line=line, raw_length=len(raw), clean_length=len(text), included=line not in excluded_lines))
        if not any(isinstance(doc.get(k), str) and any(0xD800 <= ord(c) <= 0xDFFF for c in doc[k]) for k in REQUIRED):
            audits.append(dict(doc_id=doc.get('doc_id', ''), input_line=line, scenic_id=doc.get('scenic_id', ''),
                source_type=doc.get('source_type', ''), raw_text=raw, clean_text=text, edits=edits))
    duplicates = find_duplicates(records, cleaned)
    anomalies += inherited_reviews(acceptance_path, [(line, d) for line, d in records if line not in excluded_lines])
    for pair in duplicates:
        for side in ('a', 'b'):
            anomalies.append(issue(pair['doc_id_'+side], pair['input_line_'+side], 'duplicate_candidate', pair['duplicate_type']+'，保留等待确认', 'retain_duplicate'))
    distribution = {sid: {kind: sum(d['scenic_id'] == sid and d['source_type'] == kind for d in output)
                          for kind in ('official', 'tourist')} for sid in spots}
    target_met = len(output) == 18 and all(c == {'official': 1, 'tourist': 5} for c in distribution.values())
    raw_length = sum(len(d['raw_text']) for _, d in records if isinstance(d.get('raw_text'), str))
    output_raw = sum(len(d['raw_text']) for d in output)
    clean_length = sum(len(d['clean_text']) for d in output)
    summary = dict(rule_version=RULE_VERSION, input_file=str(input_path), output_file=str(output_dir/'documents.csv'),
        python_version=sys.version.split()[0], input_sha256=hashlib.sha256(source_bytes).hexdigest(),
        input_records=len(source_bytes.splitlines()), parsed_records=len(records), output_records=len(output),
        excluded_records=len(source_bytes.splitlines())-len(output), missing_fields=missing,
        empty_raw_records=sum(not isinstance(d.get('raw_text'), str) or not d['raw_text'].strip() for _, d in records),
        empty_clean_records=sum(not t.strip() for t in cleaned.values()), distribution=distribution,
        raw_characters=raw_length, output_raw_characters=output_raw, clean_characters=clean_length,
        removed_characters=output_raw-clean_length, reduction_percent=round(100*(output_raw-clean_length)/max(output_raw, 1), 4),
        duplicate_relations=len(duplicates), duplicate_documents=len({p['input_line_'+side] for p in duplicates for side in ('a', 'b')}),
        review_documents=len({a['input_line'] for a in anomalies if a['action'] == 'review'}), target_met=target_met,
        conclusion='通过（保留报告列明的人工复核事项）' if target_met else '未达到18篇分布目标，详见异常及实际数量')
    output_dir.mkdir(parents=True, exist_ok=True)
    history = backup_outputs(output_dir)
    artifacts = {
        'documents.csv': csv_string(FIELDS, output),
        'anomalies.csv': csv_string(['doc_id', 'input_line', 'issue_type', 'reason', 'action'], anomalies),
        'duplicate_candidates.csv': csv_string(['duplicate_type', 'doc_id_a', 'input_line_a', 'doc_id_b', 'input_line_b', 'similarity', 'method', 'action'], duplicates),
        'cleaning_audit.jsonl': ''.join(json.dumps(a, ensure_ascii=False)+'\n' for a in audits).encode('utf-8'),
        'cleaning_summary.json': (json.dumps(summary, ensure_ascii=False, indent=2)+'\n').encode('utf-8'),
        'cleaning_report.md': make_report(summary, stats, anomalies, duplicates, audits).encode('utf-8'),
    }
    for name, content in artifacts.items():
        atomic_write(output_dir/name, content)
    if not (output_dir/'documents.csv').read_bytes().startswith(b'\xef\xbb\xbf'):
        raise ValueError('正式CSV缺少UTF-8-SIG BOM')
    with (output_dir/'documents.csv').open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != FIELDS or list(reader) != output:
            raise ValueError('CSV回读字段、原文或行数不一致')
    if input_path.read_bytes() != source_bytes or spots_path.read_bytes() != spot_bytes:
        raise ValueError('原始输入或景区配置发生变化')
    return summary, history


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        summary, history = run_pipeline()
        if history:
            print(f'旧清洗输出已备份：{history}')
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if summary['target_met'] else 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'实验一执行失败：{exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
