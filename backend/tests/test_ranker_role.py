import unittest

from nodes.ranker_node import _role_relevance


class RoleRelevanceTest(unittest.TestCase):
    def test_field_and_role_must_both_match(self):
        targets = ["AI Engineer"]
        self.assertGreater(_role_relevance("AI Agent Engineer", targets), 0)
        self.assertGreater(_role_relevance("ML/MLOps 엔지니어", targets), 0)
        self.assertGreater(_role_relevance("AI/LLM 기반 Software 개발", targets), 0)
        self.assertEqual(_role_relevance("Java 개발자", targets), 0)
        self.assertEqual(_role_relevance("웹 개발자", targets), 0)
        self.assertEqual(_role_relevance("AI 콘텐츠 총괄", targets), 0)


if __name__ == "__main__":
    unittest.main()
