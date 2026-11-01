"""Offline dictionary and regex entity recognition; offsets refer to clean_text."""
import csv
import json
import os
from pathlib import Path
import re
import tempfile
import jieba

ROOT = Path(__file__).resolve().parents[1]


def longest(matches):
    kept = []
    for item in sorted(matches, key=lambda x: (-(x['end']-x['start']), x['start'], x['entity_type'])):
        if not any(item['start'] < k['end'] and k['start'] < item['end'] for k in kept):
            kept.append(item)
    return sorted(kept, key=lambda x: (x['start'], x['end'], x['entity_type']))


class Recognizer:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        def read(name, fields):
            with (self.root/'config'/name).open(encoding='utf-8-sig', newline='') as f:
                reader = csv.DictReader(f)
                if reader.fieldnames != fields:
                    raise ValueError(f'Invalid dictionary schema: {name}')
                rows = list(reader)
                if not rows or any(not r.get(k) for r in rows for k in fields):
                    raise ValueError(f'Empty dictionary value: {name}')
                return rows
        self.poi = read('poi_aliases.csv', ['scenic_id','alias','canonical_name','poi_id'])
        self.transport = read('transport_terms.csv', ['alias','canonical_name'])
        self.times = (self.root/'config/time_terms.txt').read_text(encoding='utf-8-sig').splitlines()
        self.tokenizer = jieba.Tokenizer()
        for word in [r['alias'] for r in self.poi+self.transport]+self.times:
            if word.strip():
                self.tokenizer.add_word(word.strip(), freq=100000)
        number = r'[零一二两三四五六七八九十百\d]+'
        clock = r'(?:[01]?\d|2[0-3])[:：][0-5]\d|(?:[01]?\d|2[0-3])点(?:半|[0-5]?\d分?)?'
        self.time_pattern = re.compile(
            rf'(?<![\d.．A-Za-z_])(?:\d{{4}}年)?(?:1[0-2]|0?[1-9])月(?![\d日号])'
            rf'|(?:星期|周)[一二三四五六日天]\s*[-—–~～至到]\s*(?:星期|周)[一二三四五六日天]'
            rf'|' 
            rf'(?:第{number}天|今天|明天|昨天|次日)?(?:凌晨|早晨|早上|上午|中午|下午|傍晚|晚上)?(?:{clock})(?:\s*[-—–~～至到]\s*(?:{clock}))?'
            rf'|(?:\d{{4}}年)?(?:1[0-2]|0?[1-9])月(?:3[01]|[12]\d|0?[1-9])[日号]'
            rf'|(?<![\d.．]){number}(?:小时|分钟|秒钟|天|夜)(?:{number}(?:小时|分钟|天|夜))?')

    @staticmethod
    def item(text, start, end, kind, canonical=None, poi=None):
        return dict(entity_type=kind, text=text[start:end], start=start, end=end, canonical_name=canonical, poi_id=poi)

    def dictionary(self, text, rows, kind):
        result = []
        for row in rows:
            for match in re.finditer(re.escape(row['alias']), text):
                start, end = match.span()
                if kind == 'TRANSPORT':
                    # Station and ordinary object suffixes are not movement mentions.
                    if re.match(r'(?:\d+号)?(?:站|口|模型|玩具|票价|票)', text[end:]):
                        continue
                    if text[max(0,start-2):start].endswith(('玩具','模型')):
                        continue
                result.append(self.item(text,start,end,kind,row['canonical_name'],row.get('poi_id')))
        return longest(result)

    def recognize_poi(self, text, scenic_id):
        return self.dictionary(text,[r for r in self.poi if r['scenic_id']==scenic_id],'POI')

    def recognize_transport(self, text):
        return self.dictionary(text,self.transport,'TRANSPORT')

    def recognize_time(self, text):
        matches = [self.item(text,m.start(),m.end(),'TIME') for m in self.time_pattern.finditer(text)]
        for word in self.times:
            if word:
                matches.extend(self.item(text,m.start(),m.end(),'TIME') for m in re.finditer(re.escape(word),text))
        return longest(matches)

    def recognize(self, doc):
        if any(k not in doc or not isinstance(doc[k],str) for k in ('doc_id','scenic_id','clean_text')):
            raise ValueError('Required string fields: doc_id, scenic_id, clean_text')
        if not doc['doc_id'] or doc['scenic_id'] not in {'S01','S02','S03'}:
            raise ValueError('Invalid document or scenic ID')
        text = doc['clean_text']
        tokens = list(self.tokenizer.tokenize(text))
        assert all(text[s:e] == w for w,s,e in tokens)
        matches = longest(self.recognize_poi(text,doc['scenic_id'])+self.recognize_transport(text)+self.recognize_time(text))
        for i, item in enumerate(matches,1):
            item.update(mention_id=f"{doc['doc_id']}-E{i:03d}",doc_id=doc['doc_id'],scenic_id=doc['scenic_id'])
            assert text[item['start']:item['end']] == item['text']
        return matches


def main():
    recognizer = Recognizer()
    with (ROOT/'data/processed/documents.csv').open(encoding='utf-8-sig',newline='') as f:
        docs = list(csv.DictReader(f))
    if len(docs)!=18 or len({d['doc_id'] for d in docs})!=18 or {d['scenic_id'] for d in docs}!={'S01','S02','S03'}:
        raise ValueError('Expected 18 unique documents across three scenic areas')
    entities = [e for doc in docs for e in recognizer.recognize(doc)]
    assert len({e['mention_id'] for e in entities}) == len(entities)
    output = ROOT/'results/exp2/entities.json'
    output.parent.mkdir(parents=True,exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=output.parent,delete=False) as f:
            temporary = Path(f.name)
            json.dump(entities,f,ensure_ascii=False,indent=2)
        assert json.loads(temporary.read_text(encoding='utf-8')) == entities
        os.replace(temporary,output)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    from collections import Counter
    print('Documents:',len(docs))
    print('Types:',dict(Counter(e['entity_type'] for e in entities)))
    print('Scenic:',dict(Counter(e['scenic_id'] for e in entities)))
    print('Documents:',{d['doc_id']:sum(e['doc_id']==d['doc_id'] for e in entities) for d in docs})


if __name__ == '__main__':
    main()
