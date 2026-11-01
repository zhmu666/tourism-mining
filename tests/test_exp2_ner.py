import unittest
from src.exp2_ner import Recognizer

class EntityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = Recognizer()

    def entities(self,text,scenic='S01'):
        return self.r.recognize(dict(doc_id='test',scenic_id=scenic,clean_text=text))

    def test_dictionary_aliases(self):
        for row in self.r.poi:
            results=self.entities(row['alias'],row['scenic_id'])
            self.assertTrue(any(e['poi_id']==row['poi_id'] for e in results),row)

    def test_repeated(self):
        word=self.r.poi[0]['alias']
        self.assertEqual(len(self.entities(word+'，'+word)),2)

    def test_isolation(self):
        self.assertFalse(any(e['entity_type']=='POI' for e in self.entities('麓湖景区','S03')))

    def test_exclusions(self):
        self.assertEqual(self.entities('珠海长隆海洋王国，海洋奇观，某某酒店，饭店，广州南站','S02'),[])

    def test_station(self):
        found=self.entities('从汉溪长隆地铁站坐地铁前往景区')
        self.assertEqual([e['text'] for e in found],['地铁'])

    def test_objects(self):
        self.assertEqual(self.entities('玩具小火车和地铁模型'),[])

    def test_seasons(self):
        self.assertEqual(len(self.entities('春季、夏季、秋季、冬季、金秋')),5)

    def test_time(self):
        for text in ['2026年10月31日','10月1日','第二天上午8点','下午3点半','08:30','8:00—10:00','30分钟','2小时','两天一夜']:
            self.assertEqual([e['text'] for e in self.entities(text)],[text])

    def test_numbers(self):
        self.assertEqual(self.entities('票价300元，编号123，2026，18篇'),[])

    def test_offsets(self):
        text=' 空格\n麓湖景区，坐地铁，下午3点半。'
        for e in self.entities(text):
            self.assertEqual(text[e['start']:e['end']],e['text'])

    def test_empty(self):
        self.assertEqual(self.entities(''),[])

    def test_invalid(self):
        for doc in [{},dict(doc_id='x',scenic_id='S04',clean_text='')]:
            with self.assertRaises(ValueError): self.r.recognize(doc)

    def test_determinism(self):
        self.assertEqual(self.entities('步行到麓湖景区'),self.entities('步行到麓湖景区'))

    def test_longest(self):
        self.assertEqual([e['text'] for e in self.entities('免费穿梭巴士')],['免费穿梭巴士'])

    def test_iteration_boundaries(self):
        for text in ['周六至周日','周六到周日','星期六至星期日','周一—周五','12月','1月','2月','2026年10月']:
            self.assertEqual([e['text'] for e in self.entities(text)],[text])
        for text in ['7.4天','14.2天','14.7天','12312月','A12月']:
            self.assertEqual(self.entities(text),[])
        items=self.entities('鸣春谷景区，鸣春谷')
        self.assertEqual([e['poi_id'] for e in items],['S01-POI-003']*2)
        self.assertEqual([e['canonical_name'] for e in items],['鸣春谷景区']*2)
        self.assertEqual(self.entities('黄山南大门','S03')[0]['entity_type'],'POI')
        self.assertEqual(self.entities('海珠有轨电车1号线')[0]['entity_type'],'TRANSPORT')

    def test_frozen_inputs(self):
        import hashlib,json
        from pathlib import Path
        root=Path(__file__).resolve().parents[1]
        for name,digest in json.loads((root/'results/exp2/iteration_integrity.json').read_text()).items():
            self.assertEqual(hashlib.sha256((root/name).read_bytes()).hexdigest(),digest,name)
