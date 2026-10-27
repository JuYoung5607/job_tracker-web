from langgraph.graph import END, START, StateGraph
from collections.abc import Callable

from node_trace import trace_node
from nodes.candidate_iterator import candidate_iterator_node
from nodes.company_enricher_node import company_enricher_node
from nodes.extractor_node import extractor_node
from nodes.feed_publisher_node import SQLiteFeedStore, make_feed_publisher_node
from nodes.searcher_node import make_html_listing_collector, make_searcher_node
from nodes.jumpit_searcher_node import make_jumpit_position_fetcher, make_jumpit_searcher_node
from nodes.existing_job_lookup_node import existing_job_lookup_node, make_existing_job_lookup_node
from nodes.job_persister_node import job_persister_node, make_job_persister_node
from nodes.job_store import job_store
from nodes.lifecycle_node import lifecycle_node
from nodes.navigator_node import make_navigator_node
from nodes.page_fetcher import make_page_fetcher
from nodes.ranker_node import make_ranker_node
from nodes.verifier_node import verifier_node
from state import State
from personalization_state import PersonalizationState


def route_after_iterator(state: State) -> str:
    return "lookup" if state.get("current_candidate") is not None else "end"


def route_after_navigator(state: State) -> str:
    # 상세 공고 본문을 얻었으면 Extractor로 보냅니다.
    if state.get("job_document") is not None:
        return "extractor"

    # 목록 확장이나 접근 실패는 이미 완료 표시가 있으므로 다음 후보로.
    return "iterator"


def route_after_extractor(state: State) -> str:
    return "company_enricher" if state.get("extracted_facts") is not None else "iterator"


fetch_page = make_page_fetcher()
navigator_node = make_navigator_node(fetch_page)
searcher_node = make_searcher_node(
    make_html_listing_collector(fetch_page),
    make_jumpit_searcher_node(make_jumpit_position_fetcher(), broad_first=True),
)


def build_workflow(*, store=None, user_scoped_screening: bool = False,
                   on_external_start: Callable[[], None] | None = None):
    if on_external_start is not None:
        run_fetch_page = make_page_fetcher(on_request_start=on_external_start)
        run_navigator = make_navigator_node(run_fetch_page)
        run_jumpit_fetcher = make_jumpit_position_fetcher(on_request_start=on_external_start)
        run_searcher = make_searcher_node(
            make_html_listing_collector(run_fetch_page),
            make_jumpit_searcher_node(run_jumpit_fetcher, broad_first=True),
        )
    else:
        run_navigator = navigator_node
        run_searcher = searcher_node
    workflow = StateGraph(State)
    workflow.add_node("candidate_iterator_node", trace_node("candidate_iterator_node", candidate_iterator_node))
    lookup = make_existing_job_lookup_node(store) if store is not None else existing_job_lookup_node
    persister = make_job_persister_node(store) if store is not None else job_persister_node
    if user_scoped_screening:
        base_persister = persister

        def persister(state: State) -> dict:
            # Common job facts may be shared; a user's eligibility may not.
            return base_persister({**state, "screening_result": None})

    workflow.add_node("existing_job_lookup_node", trace_node("existing_job_lookup_node", lookup))
    workflow.add_node("navigator_node", trace_node("navigator_node", run_navigator))
    workflow.add_node("extractor_node", trace_node("extractor_node", extractor_node))
    workflow.add_node("company_enricher_node", trace_node("company_enricher_node", company_enricher_node))
    workflow.add_node("verifier_node", trace_node("verifier_node", verifier_node))
    workflow.add_node("lifecycle_node", trace_node("lifecycle_node", lifecycle_node))
    workflow.add_node("job_persister_node", trace_node("job_persister_node", persister))

    workflow.add_node("searcher_node", trace_node("searcher_node", run_searcher))
    workflow.add_edge(START, "searcher_node")
    workflow.add_edge("searcher_node", "candidate_iterator_node")

    workflow.add_conditional_edges(
        "candidate_iterator_node", route_after_iterator,
        {"lookup": "existing_job_lookup_node", "end": END},
    )
    workflow.add_edge("existing_job_lookup_node", "navigator_node")
    workflow.add_conditional_edges(
        "navigator_node", route_after_navigator,
        {"extractor": "extractor_node", "iterator": "candidate_iterator_node"},
    )
    workflow.add_conditional_edges(
        "extractor_node", route_after_extractor,
        {"company_enricher": "company_enricher_node", "iterator": "candidate_iterator_node"},
    )
    workflow.add_edge("company_enricher_node", "verifier_node")
    workflow.add_edge("verifier_node", "lifecycle_node")
    workflow.add_edge("lifecycle_node", "job_persister_node")
    workflow.add_edge("job_persister_node", "candidate_iterator_node")
    return workflow.compile()


app = build_workflow()


def build_personalization_workflow(*, store=job_store, feed_store=None):
    feed_store = feed_store or SQLiteFeedStore(store.path)
    workflow = StateGraph(PersonalizationState)
    workflow.add_node("ranker_node", trace_node("ranker_node", make_ranker_node(store)))
    workflow.add_node("feed_publisher_node", trace_node(
        "feed_publisher_node", make_feed_publisher_node(store, feed_store),
    ))
    workflow.add_edge(START, "ranker_node")
    workflow.add_edge("ranker_node", "feed_publisher_node")
    workflow.add_edge("feed_publisher_node", END)
    return workflow.compile()


personalization_app = build_personalization_workflow()

if __name__ == "__main__":
    print(app.get_graph().draw_mermaid())
