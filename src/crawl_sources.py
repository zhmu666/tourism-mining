"""Serial, configuration driven collection. No preprocessing or CSV export."""
import argparse
import csv
import json
import math
import re
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ['doc_id', 'scenic_id', 'source_type', 'source_name', 'source_url', 'enabled']
UA = 'TourismMiningCourse/1.0 (educational article collection; Python requests)'


def now():
    return datetime.now(timezone.utc).isoformat()


def url_key(url):
    try:
        p = urlsplit(url)
        if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
            return None
        port = p.port
        netloc = p.hostname.lower()
        if ':' in netloc:
            netloc = '[' + netloc + ']'
        if port and (p.scheme, port) not in [('https', 443), ('http', 80)]:
            netloc += ':' + str(port)
        if re.search(r'\s', url):
            return None
        return urlunsplit((p.scheme.lower(), netloc, p.path or '/', p.query, ''))
    except ValueError:
        return None


def load_config(spots_path, sources_path):
    spots_list = json.loads(Path(spots_path).read_text(encoding='utf-8-sig'))
    spots = {}
    for item in spots_list:
        sid = item['scenic_id']
        if sid in spots or sid not in ('S01', 'S02', 'S03') or not item['scenic_name'].strip():
            raise ValueError('景区配置存在重复、非法编号或空名称')
        spots[sid] = item['scenic_name']
    if set(spots) != {'S01', 'S02', 'S03'}:
        raise ValueError('景区配置必须包含 S01、S02、S03')
    with Path(sources_path).open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != FIELDS:
            raise ValueError('source_urls.csv 表头必须为: ' + ','.join(FIELDS))
        rows = []
        for line, row in enumerate(reader, 2):
            errors = []
            if None in row or any(row.get(k) is None for k in FIELDS):
                errors.append('列数不匹配')
            row = {k: (row.get(k) or '').strip() for k in FIELDS}
            if row['scenic_id'] not in spots:
                errors.append('scenic_id 不存在')
            if row['source_type'] not in ('official', 'tourist'):
                errors.append('source_type 非法')
            letter = {'official': 'O', 'tourist': 'T'}.get(row['source_type'], '?')
            if not re.fullmatch(re.escape(row['scenic_id'] + '-' + letter) + r'-[0-9]{3,}', row['doc_id']):
                errors.append('doc_id 格式或景区/类型不一致')
            if not row['source_name']:
                errors.append('source_name 为空')
            if not url_key(row['source_url']):
                errors.append('source_url 不是有效 HTTP(S) URL')
            if row['enabled'].lower() not in ('true', 'false'):
                errors.append('enabled 必须为 true 或 false')
            row['_enabled'] = row['enabled'].lower() == 'true'
            row['_line'] = line
            row['_errors'] = errors
            rows.append(row)
    ids = {}
    for row in rows:
        ids.setdefault(row['doc_id'], []).append(row)
    for group in ids.values():
        if len(group) > 1:
            for row in group:
                row['_errors'].append('doc_id 重复，涉及行 ' + ','.join(str(r['_line']) for r in group))
    return spots, rows


def extract(html):
    soup = BeautifulSoup(html, 'html.parser')
    title_node = soup.select_one('h1') or soup.title
    title = title_node.get_text(' ', strip=True) if title_node else ''
    for node in soup.select('script,style,noscript,svg,iframe,nav,footer,aside,[role=navigation],[role=dialog],.breadcrumb,.breadcrumbs,.related,.comments'):
        node.decompose()
    # Semantic containers first; common article selectors are explicit, never body.
    for selector in ('article', '[itemprop=articleBody]', '#zoom', '#UCAP-CONTENT', '.TRS_Editor', '.article-content', '.article_content', '.article-body', '.post-content', '.entry-content', '.content-detail', '.detail-content', '.text_content', 'main'):
        candidates = soup.select(selector)
        if candidates:
            node = max(candidates, key=lambda n: len(n.get_text(strip=True)))
            for br in node.select('br'):
                br.replace_with('\n')
            text = node.get_text('\n', strip=True)
            if text:
                review = len(text) < 200 or not title or selector == 'main'
                return title, text, 'container:' + selector, review
    # Conservative paragraph-density fallback, retained only for manual inspection.
    candidates = []
    for node in soup.find_all(['div', 'section']):
        text = node.get_text('\n', strip=True)
        paragraphs = node.find_all('p')
        linked = sum(len(a.get_text(strip=True)) for a in node.find_all('a'))
        if len(text) >= 80 and len(paragraphs) >= 2 and linked / max(len(text), 1) < .2:
            candidates.append((len(text), node))
    if candidates:
        node = min(candidates, key=lambda pair: pair[0])[1]
        return title, node.get_text('\n', strip=True), 'paragraph_candidate', True
    return title, '', 'none', False


class Fetcher:
    def __init__(self, session, timeout=15, delay=2, retries=2, sleep=time.sleep):
        self.session, self.timeout, self.delay, self.retries = session, timeout, delay, retries
        self.sleep = sleep
        self.started = False

    def fetch(self, url):
        for attempt in range(self.retries + 1):
            if self.started:
                self.sleep(max(self.delay, 2 ** attempt if attempt else self.delay))
            self.started = True
            try:
                response = self.session.get(url, timeout=self.timeout, allow_redirects=False)
                code = response.status_code
                # Redirects are reported for review; never request unlisted destinations.
                if not 200 <= code < 300:
                    if code in (500, 502, 503, 504) and attempt < self.retries:
                        continue
                    return response, f'HTTP {code}', 'failed'
                if not response.content.strip():
                    return response, '空响应', 'failed'
                return response, None, 'success'
            except requests.RequestException as exc:
                if isinstance(exc, (requests.Timeout, requests.ConnectionError)) and attempt < self.retries:
                    continue
                return None, f'{type(exc).__name__}: {exc}', 'failed'


def run(rows, spots, fetcher, limit=None, dry_run=False):
    records, documents, seen = [], [], {}
    requested = 0
    for row in rows:
        record = {k: None for k in ('status_code', 'final_url', 'title', 'parser_name', 'error', 'crawled_at')}
        record.update(doc_id=row['doc_id'], source_url=row['source_url'], line=row['_line'], fetch_status='pending', parse_status='not_attempted', text_length=0, needs_review=False)
        records.append(record)
        if row['_errors']:
            record.update(fetch_status='config_error', error='; '.join(row['_errors']))
            continue
        if not row['_enabled']:
            record['fetch_status'] = 'disabled'
            continue
        key = url_key(row['source_url'])
        if key in seen:
            record.update(fetch_status='duplicate', duplicate_of=seen[key])
            continue
        seen[key] = row['doc_id']
        if dry_run:
            record['fetch_status'] = 'validated'
            continue
        if limit is not None and requested >= limit:
            record['fetch_status'] = 'limited'
            continue
        requested += 1
        record['crawled_at'] = now()
        response, error, status = fetcher.fetch(row['source_url'])
        record.update(fetch_status=status, error=error)
        if response is not None:
            record.update(status_code=response.status_code, final_url=response.url)
        if status != 'success':
            continue
        # Preserve the one-round response for offline diagnosis without refetching.
        snapshot_dir = ROOT / 'data/raw/html'
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        snapshot_path = snapshot_dir / (row['doc_id'] + '.html')
        if snapshot_path.exists():
            snapshot_path = snapshot_dir / (row['doc_id'] + '-' + str(time.time_ns()) + '.html')
        snapshot_path.write_bytes(response.content)
        record['html_snapshot'] = str(snapshot_path.relative_to(ROOT))
        content_type = response.headers.get('Content-Type', '').lower()
        if content_type and 'html' not in content_type:
            record.update(parse_status='parse_failed', error='非 HTML 响应')
            continue
        # Honor HTTP charset if supplied; otherwise BeautifulSoup detects HTML meta/BOM.
        charset = re.search(r'charset\s*=\s*["\x27]?([^;\s"\x27]+)', content_type)
        html = response.content
        if charset:
            try:
                html = html.decode(charset.group(1))
            except (LookupError, UnicodeError):
                pass
        soup = BeautifulSoup(html, 'html.parser')
        visible = soup.get_text(' ', strip=True)
        if re.search(r'captcha|environment checking|请完成下面的验证|访问被拒绝|access denied|验证码', visible, re.I):
            record.update(fetch_status='blocked', error='验证页或访问限制，不重试')
            continue
        title, text, parser, review = extract(html)
        record.update(title=title or None, text_length=len(text), parser_name=parser)
        if not text:
            record.update(parse_status='parse_failed', error='未找到可信正文容器')
            continue
        # Existing candidates have not passed article-quality acceptance yet.
        review = True
        record.update(parse_status='needs_review' if review else 'success', needs_review=review)
        doc = {k: row[k] for k in ('doc_id', 'scenic_id', 'source_type', 'source_name', 'source_url')}
        doc.update(scenic_name=spots[row['scenic_id']], title=title or None, raw_text=text)
        doc.update({k: record[k] for k in ('crawled_at', 'final_url', 'status_code', 'parser_name', 'needs_review')})
        documents.append(doc)
        print(f"{row['doc_id']}: {record['parse_status']} ({len(text)} 字符)")
    summary = dict(configured_sources=len(rows), enabled_sources=sum(r['_enabled'] for r in rows),
                   request_success=sum(r['fetch_status'] == 'success' for r in records),
                   request_failed=sum(r['fetch_status'] in ('failed', 'blocked') for r in records),
                   parse_failed=sum(r['parse_status'] == 'parse_failed' for r in records),
                   duplicate_urls=sum(r['fetch_status'] == 'duplicate' for r in records),
                   needs_review=sum(r['needs_review'] for r in records), saved_documents=len(documents),
                   config_errors=sum(r['fetch_status'] == 'config_error' for r in records))
    return documents, dict(generated_at=now(), dry_run=dry_run, summary=summary, sources=records)


def save_outputs(directory, documents, report):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    paths = [directory / 'raw_documents.jsonl', directory / 'crawl_report.json']
    # Inspect existing artifacts before replacement; malformed data stops the write.
    for path in paths:
        if path.exists():
            content = path.read_text(encoding='utf-8')
            if path.suffix == '.jsonl':
                for line in content.splitlines():
                    json.loads(line)
            else:
                json.loads(content)
    existing = [p for p in paths if p.exists()]
    if existing:
        backup = directory / 'history' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup.mkdir(parents=True)
        for path in existing:
            shutil.copy2(path, backup / path.name)
        print(f'旧输出已备份: {backup}')
    contents = [''.join(json.dumps(d, ensure_ascii=False) + '\n' for d in documents), json.dumps(report, ensure_ascii=False, indent=2) + '\n']
    for path, content in zip(paths, contents):
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=directory, prefix='.tmp-', delete=False) as f:
            temp = Path(f.name)
            f.write(content)
        temp.replace(path)


def number(minimum, integer=False):
    def parse(value):
        try:
            result = int(value) if integer else float(value)
            if not math.isfinite(result) or result < minimum:
                raise ValueError()
            return result
        except ValueError:
            raise argparse.ArgumentTypeError(f'必须是 >= {minimum} 的有效数值')
    return parse


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--limit', type=number(1, True))
    parser.add_argument('--timeout', type=number(.1), default=15)
    parser.add_argument('--delay', type=number(2), default=2)
    parser.add_argument('--max-retries', type=number(0, True), default=2)
    args = parser.parse_args(argv)
    try:
        spots, rows = load_config(ROOT / 'config/scenic_spots.json', ROOT / 'config/source_urls.csv')
        with requests.Session() as session:
            session.headers.update({'User-Agent': UA, 'Accept': 'text/html,application/xhtml+xml'})
            documents, report = run(rows, spots, Fetcher(session, args.timeout, args.delay, args.max_retries), args.limit, args.dry_run)
        for record in report['sources']:
            if record['error']:
                print(f"行 {record['line']} {record['doc_id']}: {record['error']}")
        print(json.dumps(report['summary'], ensure_ascii=False))
        if not args.dry_run:
            save_outputs(ROOT / 'data/raw', documents, report)
        return 1 if report['summary']['config_errors'] else 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'配置或文件错误: {exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

