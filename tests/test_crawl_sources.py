import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from src.crawl_sources import ROOT, Fetcher, extract, load_config, run, save_outputs, main

HTML = '<html><title>路线</title><nav>导航</nav><article><h1>游览线路</h1><p>1. 08:30 南门→山顶，乘公交。</p><p>' + '沿路步行游览景点。' * 30 + '</p><script>坏脚本</script><footer>页脚</footer></article></html>'


def response(code=200, html=HTML):
    r = requests.Response()
    r.status_code = code
    r.url = 'https://example.com/article'
    r._content = html.encode('utf-8')
    r.headers['Content-Type'] = 'text/html; charset=utf-8'
    return r


class CrawlerTests(unittest.TestCase):
    def setUp(self):
        self.temp_root = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_root.cleanup)
        self.root_patch = patch('src.crawl_sources.ROOT', Path(self.temp_root.name))
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.spots, self.rows = load_config(ROOT/'config/scenic_spots.json', ROOT/'config/source_urls.csv')
        self.rows = self.rows[:1]
        self.rows[0]['_enabled'] = True
        self.session = Mock()
        self.session.get.return_value = response()
        self.sleep = Mock()
        self.fetcher = Fetcher(self.session, retries=1, sleep=self.sleep)

    def test_extract_and_preserve_route(self):
        title, text, parser, review = extract(HTML)
        self.assertEqual(title, '游览线路')
        self.assertIn('1. 08:30 南门→山顶', text)
        self.assertIn('乘公交', text)
        for noise in ('导航', '坏脚本', '页脚'):
            self.assertNotIn(noise, text)
        self.assertEqual(parser, 'container:article')

    def test_no_whole_body_fallback(self):
        self.assertEqual(extract('<body>菜单内容</body>')[1], '')
        self.assertEqual(extract('<article></article>')[1], '')

    def test_http_errors_no_retry_restrictions(self):
        for code in (403, 404, 412, 429, 302):
            with self.subTest(code=code):
                self.session.reset_mock()
                self.session.get.return_value = response(code)
                docs, report = run(self.rows, self.spots, self.fetcher)
                self.assertEqual(docs, [])
                self.assertEqual(report['sources'][0]['status_code'], code)
                self.assertEqual(report['sources'][0]['fetch_status'], 'failed')
                self.assertEqual(self.session.get.call_count, 1)

    def test_timeout_and_retry(self):
        self.session.get.side_effect = requests.Timeout('timeout')
        docs, report = run(self.rows, self.spots, self.fetcher)
        self.assertEqual(self.session.get.call_count, 2)
        self.assertIn('Timeout', report['sources'][0]['error'])
        self.assertEqual(report['summary']['request_failed'], 1)
        self.assertEqual(docs, [])
        self.assertGreaterEqual(self.sleep.call_args.args[0], 2)

    def test_transient_success(self):
        self.session.get.side_effect = [response(503), response()]
        docs, _ = run(self.rows, self.spots, self.fetcher)
        self.assertEqual(len(docs), 1)
        self.assertEqual(self.session.get.call_count, 2)

    def test_parse_failure(self):
        self.session.get.return_value = response(html='<title>空正文</title><body>菜单</body>')
        docs, report = run(self.rows, self.spots, self.fetcher)
        self.assertEqual(docs, [])
        self.assertEqual(report['sources'][0]['parse_status'], 'parse_failed')

    def test_captcha(self):
        self.session.get.return_value = response(html='<title>captcha</title><article>请输入验证码</article>')
        docs, report = run(self.rows, self.spots, self.fetcher)
        self.assertFalse(docs)
        self.assertEqual(report['sources'][0]['fetch_status'], 'blocked')
        self.assertEqual(self.session.get.call_count, 1)

    def test_validation(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'urls.csv'
            fields = ['doc_id','scenic_id','source_type','source_name','source_url','enabled']
            with path.open('w', encoding='utf-8-sig', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fields)
                writer.writeheader()
                row = {k:self.rows[0][k] for k in fields}
                row.update(scenic_id='S99',source_type='bad',enabled='yes',source_url='bad')
                writer.writerows([row,row])
            _, rows = load_config(ROOT/'config/scenic_spots.json',path)
            errors = ';'.join(rows[0]['_errors'])
            for word in ('scenic_id','source_type','doc_id 重复','source_url','enabled'):
                self.assertIn(word,errors)
            docs, report = run(rows,self.spots,self.fetcher)
            self.session.get.assert_not_called()
            self.assertEqual(report['summary']['config_errors'],2)

    def test_duplicate_url(self):
        duplicate = dict(self.rows[0], doc_id='S01-O-002', source_url=self.rows[0]['source_url']+'#anchor')
        docs, report = run(self.rows+[duplicate],self.spots,self.fetcher)
        self.assertEqual(len(docs),1)
        self.assertEqual(self.session.get.call_count,1)
        self.assertEqual(report['sources'][1]['duplicate_of'],self.rows[0]['doc_id'])

    def test_jsonl_and_rerun_backup(self):
        docs, report = run(self.rows,self.spots,self.fetcher)
        with tempfile.TemporaryDirectory() as d:
            save_outputs(d,docs,report)
            save_outputs(d,docs,report)
            parsed = [json.loads(line) for line in (Path(d)/'raw_documents.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual(len(parsed),1)
            for field in ('doc_id','scenic_id','scenic_name','source_type','source_name','source_url','title','raw_text','crawled_at','parser_name'):
                self.assertIn(field,parsed[0])
            self.assertEqual(len(list((Path(d)/'history').glob('*/raw_documents.jsonl'))),1)
            (Path(d)/'raw_documents.jsonl').write_text('broken',encoding='utf-8')
            with self.assertRaises(ValueError):
                save_outputs(d,docs,report)

    def test_dry_run_limit_disabled(self):
        docs, report = run(self.rows,self.spots,self.fetcher,dry_run=True)
        self.session.get.assert_not_called()
        self.assertEqual(report['sources'][0]['fetch_status'],'validated')
        second = dict(self.rows[0],doc_id='S01-O-002',source_url='https://example.com/second')
        _, report = run(self.rows+[second],self.spots,self.fetcher,limit=1)
        self.assertEqual(report['sources'][1]['fetch_status'],'limited')
        second['_enabled'] = False
        _, report = run([second],self.spots,self.fetcher)
        self.assertEqual(report['sources'][0]['fetch_status'],'disabled')


if __name__ == '__main__':
    unittest.main()
