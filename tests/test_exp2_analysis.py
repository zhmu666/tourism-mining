import unittest
from src.exp2_analysis import category, validate

class AnalysisTests(unittest.TestCase):
    def test_categories(self):
        for text,expected in [('09:00-12:00','时间范围'),('第二天上午8点','具体时刻'),('2026年10月31日','日期表达'),('第二天','相对日期'),('半小时','持续时间'),('下午','时间段'),('金秋','季节及旅游时期'),('全年','其他无法可靠归类的表达')]:
            self.assertEqual(category(text),expected)

    def test_invalid_schema(self):
        with self.assertRaises(ValueError): validate([{}],{})
