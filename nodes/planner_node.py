"""Entry point for current job-listing collection."""

from collections.abc import Callable
from state import State


def make_planner_node(searcher: Callable[[State], dict]):
    """Delegate the four-platform listing search to the shared Searcher."""
    def planner_node(state: State) -> dict:
        return searcher(state)

    return planner_node
