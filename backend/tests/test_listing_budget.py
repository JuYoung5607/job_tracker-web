import unittest

from nodes.searcher_node import make_searcher_node
from schemas.search import SearchBudget, SearchConditions


class SearcherBudgetTest(unittest.TestCase):
    def test_eight_calls_are_shared_between_html_and_jumpit(self):
        allocated = []

        def capture(state):
            allocated.append(state["search_budget"].max_search_calls)
            return {"candidates": [], "errors": []}

        searcher = make_searcher_node(capture, capture)
        searcher({
            "search_conditions": SearchConditions(target_jobs=["AI Engineer"]),
            "search_budget": SearchBudget(
                max_search_calls=8, max_candidates=12, timeout_seconds=300,
            ),
        })
        self.assertEqual(allocated, [5, 3])


if __name__ == "__main__":
    unittest.main()
