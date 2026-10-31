import csv
import json
import tempfile
import unittest
from pathlib import Path

from src.exp1_preprocess import FIELDS, NOTICE, clean_text, run_pipeline


def document(did='S01-T-001', raw='1. 08:30 南门→能仁寺→摩星岭，步行30分钟，门票5元。', **changes):
    d=dict(doc_id=did,scenic_id='S01',scenic_name='白云山',source_type='tourist',
           source_name='测试平台',source_url='https://example.com/'+did,title='出游记录',raw_text=raw)
    d.update(changes)
    return d


class CleaningRulesTests(unittest.TestCase):
    def test_html_and_code(self):
        text,_=clean_text('<nav>首页 菜单</nav><p>第一站<b>能仁寺</b></p><p>随后到摩星岭<br>下山</p><script>var a=1;</script><style>.x{}</style><footer>页脚</footer>')
        self.assertEqual(text,'第一站能仁寺\n\n随后到摩星岭\n下山')
        for word in ['菜单','页脚','var a','.x']:self.assertNotIn(word,text)

    def test_entities_and_plain_angles(self):
        text,_=clean_text('A &amp; B&nbsp;→ C &lt;景点&gt;，1 < 3 > 2')
        self.assertEqual(text,'A & B → C <景点>，1 < 3 > 2')
        text,_=clean_text('<p>经过<景点>后，A < B > C</p>')
        self.assertIn('<景点>',text)
        self.assertIn('A < B > C',text)

    def test_whitespace_and_controls(self):
        text,_=clean_text('  第一站\t\u00a0能仁寺  \r\n\r\n\r\n  随后→摩星岭\r下山\x00\u200b')
        self.assertEqual(text,'第一站 能仁寺\n\n随后→摩星岭\n下山')

    def test_fixed_notice_local_removal(self):
        text,edits=clean_text(NOTICE+'\n2021.01.08\n出游记录\n行程天数\n2\n天\n上午→山顶\n2021-01-08\n发布\n阅读量\n1.7万',title='出游记录',source_url='https://you.ctrip.com/travels/1.html')
        self.assertNotIn('战略方向',text)
        self.assertNotIn('2021.01.08',text)
        self.assertNotIn('2021-01-08',text)
        self.assertIn('行程天数\n2\n天',text)
        self.assertIn('上午→山顶',text)
        self.assertIn('ctrip_fixed_notice',[e['rule'] for e in edits])

    def test_publication_vs_trip_date(self):
        text,_=clean_text('发布时间：2020-06-29\n出游日期：2018.2.4\n2018.2.5由于班车较少改变行程\n出发时间\n10\n月\n同行人员：父母\n人均花费：800元\n游览时长2小时')
        self.assertNotIn('2020-06-29',text)
        for term in ['出游日期：2018.2.4','2018.2.5由于','出发时间\n10\n月','父母','800元','2小时']:self.assertIn(term,text)
        unknown,_=clean_text('2021-01-08\n去了白云山',source_url='https://example.com')
        self.assertIn('2021-01-08',unknown)

    def test_route_and_punctuation(self):
        raw='（一）第一站（10:00）：南门→小火车→青龙山\n1. 随后步行2.5公里；下午坐缆车30分钟（20元/人）。\n第二天：黄山—迎客松——玉屏索道'
        self.assertEqual(clean_text(raw)[0],raw)

    def test_multiscenic_hotel_tickets_preserved(self):
        raw='广州推荐：白云山\n酒店寄存行李后乘索道上山，门票5元。\n第二天：顺德清晖园、宏村。'
        self.assertEqual(clean_text(raw)[0],raw)

    def test_template_ads_not_all_recommendations(self):
        raw='酒店推荐：山顶住宿方便看日出\n搜索微信公众号“繁简气象”，更多精彩。\n推荐线路：云谷寺→始信峰\n关注微信公众号“繁简气象”\nCopyright © Example\n扫码下载去哪儿旅行 App'
        text,_=clean_text(raw)
        for term in ['繁简气象','Copyright','扫码下载']:self.assertNotIn(term,text)
        self.assertIn('酒店推荐：山顶住宿方便看日出',text)
        self.assertIn('推荐线路：云谷寺→始信峰',text)

    def test_qunar_bounded_noise_retains_tail_article(self):
        raw='正文：南门→摩星岭\n其他旅行者怎么说（5 条）\n甲\n评论\n共 5 条评论，此处展示 3 条。\n更多广州的真实游记\n看全部广州游记 ›\n相关游记\n无关卡片\n广州的实时机票与酒店价格，在去哪儿旅行 App 里比一比再定。\n正文续篇：玉屏索道下山\n扫码下载去哪儿旅行 App'
        text,_=clean_text(raw,source_url='https://travelnews.qunar.com/ugc/test')
        self.assertIn('南门→摩星岭',text)
        self.assertIn('正文续篇：玉屏索道下山',text)
        self.assertNotIn('评论',text)
        self.assertNotIn('无关卡片',text)

    def test_incomplete_noise_boundary_retained(self):
        raw='正文\n相关游记\n酒店推荐\n黄山→迎客松'
        self.assertEqual(clean_text(raw,source_url='https://travelnews.qunar.com/ugc/test')[0],raw)

    def test_deterministic(self):
        raw=NOTICE+'\n1. 上午\t 南门→山顶'
        self.assertEqual(clean_text(raw),clean_text(raw))
        cleaned=clean_text(raw)[0]
        self.assertEqual(clean_text(cleaned)[0],cleaned)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.path=self.root/'input.jsonl';self.out=self.root/'processed'
        self.spots=self.root/'spots.json'
        self.spots.write_text(json.dumps([{'scenic_id':sid,'scenic_name':name} for sid,name in [('S01','白云山'),('S02','长隆'),('S03','黄山')]],ensure_ascii=False),encoding='utf-8')

    def run_docs(self,docs):
        self.path.write_text(''.join(json.dumps(d,ensure_ascii=False)+'\n' for d in docs),encoding='utf-8')
        return run_pipeline(self.path,self.spots,self.out,acceptance_path=None)[0]

    def csv(self,name='documents.csv'):
        with (self.out/name).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))

    def test_raw_text_and_csv_interface(self):
        raw='<p>第一站：南门\r\n门票5元，步行30分钟</p>\t&amp;'
        self.run_docs([document(raw=raw)])
        self.assertTrue((self.out/'documents.csv').read_bytes().startswith(b'\xef\xbb\xbf'))
        with (self.out/'documents.csv').open(encoding='utf-8-sig',newline='') as f:
            reader=csv.DictReader(f);self.assertEqual(reader.fieldnames,FIELDS);rows=list(reader)
        self.assertEqual(rows[0]['raw_text'],raw)
        self.assertNotEqual(rows[0]['raw_text'],rows[0]['clean_text'])
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['raw_text'],raw)

    def test_missing_empty_invalid_fields(self):
        bad=document('BAD');bad.pop('title')
        docs=[bad,document('EMPTY',raw=''),document('TYPE',source_type='bad'),document('SID',scenic_id='S99'),document('NAME',scenic_name='错名'),document('NOURL',source_url=''),document('CLEAREMPTY',raw='<script>nothing</script>')]
        summary=self.run_docs(docs)
        self.assertEqual(summary['output_records'],0)
        self.assertEqual(summary['excluded_records'],7)
        kinds={a['issue_type'] for a in self.csv('anomalies.csv')}
        self.assertTrue({'required_field','invalid_source_type','invalid_scenic_id','scenic_name_mismatch','empty_clean_text'}<=kinds)

    def test_doc_id_conflict_all_excluded(self):
        summary=self.run_docs([document(),document(raw='另一条正文'),document('UNIQUE')])
        self.assertEqual(summary['output_records'],1)
        self.assertEqual([d['doc_id'] for d in self.csv()],['UNIQUE'])
        self.assertEqual(sum(a['issue_type']=='doc_id_conflict' for a in self.csv('anomalies.csv')),2)

    def test_exact_duplicates_retained(self):
        docs=[document('A'),document('B'),document('C',raw='<p>'+document()['raw_text']+'</p>')]
        docs[1]['source_url']=docs[0]['source_url']
        summary=self.run_docs(docs)
        self.assertEqual(summary['output_records'],3)
        kinds={d['duplicate_type'] for d in self.csv('duplicate_candidates.csv')}
        self.assertTrue({'same_source_url','same_raw_text','same_clean_text'}<=kinds)

    def test_near_duplicates_retained(self):
        text='上午从南门进入白云山，沿途经过能仁寺后前往摩星岭，然后步行到明珠楼，坐公交回家。'*10
        summary=self.run_docs([document('A',raw=text),document('B',raw=text+'最后喝了一杯水。')])
        self.assertEqual(summary['output_records'],2)
        self.assertIn('near_clean_text',{d['duplicate_type'] for d in self.csv('duplicate_candidates.csv')})

    def test_short_garbled_and_date_kept_for_review(self):
        summary=self.run_docs([document(raw='2020-01-01\n南门→山顶\ufffd')])
        self.assertEqual(summary['output_records'],1)
        issues=self.csv('anomalies.csv')
        self.assertTrue({'short_text','suspected_garbled','ambiguous_date'}<={a['issue_type'] for a in issues})
        self.assertIn('\ufffd',self.csv()[0]['clean_text'])

    def test_invalid_json_and_nonobject_tracked(self):
        self.path.write_text('{broken\n[]\n'+json.dumps(document())+'\n',encoding='utf-8')
        summary,_=run_pipeline(self.path,self.spots,self.out,acceptance_path=None)
        self.assertEqual(summary['input_records'],3)
        self.assertEqual(summary['output_records'],1)
        self.assertEqual([a['input_line'] for a in self.csv('anomalies.csv') if a['issue_type']=='invalid_json'],['1','2'])

    def test_repeat_deterministic_backup_no_append(self):
        self.run_docs([document()]);original=self.path.read_bytes();output=(self.out/'documents.csv').read_bytes();stats=(self.out/'cleaning_summary.json').read_bytes()
        unrelated=self.out/'other_experiment.txt';unrelated.write_text('keep',encoding='utf-8')
        run_pipeline(self.path,self.spots,self.out,acceptance_path=None)
        self.assertEqual(self.path.read_bytes(),original)
        self.assertEqual((self.out/'documents.csv').read_bytes(),output)
        self.assertEqual((self.out/'cleaning_summary.json').read_bytes(),stats)
        backups=list((self.out/'history').glob('*/documents.csv'))
        self.assertEqual(len(backups),1);self.assertEqual(backups[0].read_bytes(),output)
        self.assertEqual(len(list(backups[0].parent.iterdir())),6)
        self.assertEqual(unrelated.read_text(encoding='utf-8'),'keep')

    def test_valid_fixture_distribution(self):
        docs=[]
        for sid,name in [('S01','白云山'),('S02','长隆'),('S03','黄山')]:
            for kind,num in [('official',1),('tourist',5)]:
                for i in range(num):docs.append(document(f'{sid}-{kind}-{i}',scenic_id=sid,scenic_name=name,source_type=kind,raw=f'第{i+1}站：{name}，上午步行→山顶。'))
        summary=self.run_docs(docs)
        self.assertTrue(summary['target_met']);self.assertEqual(summary['output_records'],18)

    def test_unpaired_surrogate_excluded_with_diagnostics(self):
        docs=[document('BAD\ud800'),document('BADTEXT',raw='bad\ud800'),document('GOOD')]
        self.path.write_text(''.join(json.dumps(d,ensure_ascii=True)+'\n' for d in docs),encoding='utf-8')
        summary,_=run_pipeline(self.path,self.spots,self.out,acceptance_path=None)
        self.assertEqual(summary['output_records'],1)
        self.assertEqual(sum(a['issue_type']=='unpaired_surrogate' for a in self.csv('anomalies.csv')),2)

    def test_invalid_url_does_not_abort_batch(self):
        summary=self.run_docs([document('BADURL',source_url='https://[broken'),document('GOOD')])
        self.assertEqual(summary['output_records'],1)
        self.assertEqual(self.csv()[0]['doc_id'],'GOOD')

    def test_output_guard(self):
        self.path.write_text(json.dumps(document()),encoding='utf-8')
        with self.assertRaises(ValueError):run_pipeline(self.path,self.spots,self.path.parent,acceptance_path=None)


if __name__=='__main__':unittest.main()
