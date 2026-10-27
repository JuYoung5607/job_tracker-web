import unittest

from nodes.verifier_node import parse_minimum_years


class ExperienceParsingTest(unittest.TestCase):
    def test_upper_bound_without_entry_is_unknown(self):
        self.assertIsNone(parse_minimum_years("경력 5년 이하 (개인·팀 프로젝트 경험 인정)"))
        self.assertEqual(parse_minimum_years("신입~경력 8년"), 0)
        self.assertEqual(parse_minimum_years("신입 또는 경력 5년 이하"), 0)
        self.assertEqual(parse_minimum_years("경력 8년 이상"), 8)


if __name__ == "__main__":
    unittest.main()
