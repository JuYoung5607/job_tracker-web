from langgraph.graph import END, START, StateGraph

from node_trace import trace_node
from nodes.candidate_iterator import candidate_iterator_node
from nodes.company_enricher_node import company_enricher_node
from nodes.combined_listing_searcher_node import make_combined_listing_searcher_node
from nodes.extractor_node import extractor_node
from nodes.feed_publisher_node import SQLiteFeedStore, make_feed_publisher_node
from nodes.listing_searcher_node import make_listing_searcher_node
from nodes.jumpit_searcher_node import make_jumpit_position_fetcher, make_jumpit_searcher_node
from nodes.job_persister_node import existing_job_lookup_node, job_persister_node, job_store
from nodes.lifecycle_node import lifecycle_node
from nodes.navigator_node import make_navigator_node
from nodes.page_fetcher import make_page_fetcher
from nodes.planner_node import planner_node
from nodes.ranker_node import make_ranker_node
from nodes.searcher_node import searcher_node
from nodes.verifier_node import verifier_node
from state import State
from personalization_state import PersonalizationState


def route_after_planner(state: State) -> str:
    return "searcher" if state.get("search_plan") is not None else "end"


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
listing_searcher_node = make_combined_listing_searcher_node(
    make_listing_searcher_node(fetch_page),
    make_jumpit_searcher_node(make_jumpit_position_fetcher(), broad_first=True),
)
jumpit_searcher_node = make_jumpit_searcher_node(make_jumpit_position_fetcher())


def build_workflow(*, listing_experiment: bool = False, jumpit_experiment: bool = False):
    if listing_experiment and jumpit_experiment:
        raise ValueError("후보 발견 방식은 하나만 선택해야 합니다.")
    workflow = StateGraph(State)
    workflow.add_node("candidate_iterator_node", trace_node("candidate_iterator_node", candidate_iterator_node))
    workflow.add_node("existing_job_lookup_node", trace_node("existing_job_lookup_node", existing_job_lookup_node))
    workflow.add_node("navigator_node", trace_node("navigator_node", navigator_node))
    workflow.add_node("extractor_node", trace_node("extractor_node", extractor_node))
    workflow.add_node("company_enricher_node", trace_node("company_enricher_node", company_enricher_node))
    workflow.add_node("verifier_node", trace_node("verifier_node", verifier_node))
    workflow.add_node("lifecycle_node", trace_node("lifecycle_node", lifecycle_node))
    workflow.add_node("job_persister_node", trace_node("job_persister_node", job_persister_node))

    if jumpit_experiment:
        workflow.add_node("jumpit_searcher_node", trace_node("jumpit_searcher_node", jumpit_searcher_node))
        workflow.add_edge(START, "jumpit_searcher_node")
        workflow.add_edge("jumpit_searcher_node", "candidate_iterator_node")
    elif listing_experiment:
        workflow.add_node("listing_searcher_node", trace_node("listing_searcher_node", listing_searcher_node))
        workflow.add_edge(START, "listing_searcher_node")
        workflow.add_edge("listing_searcher_node", "candidate_iterator_node")
    else:
        workflow.add_node("planner_node", trace_node("planner_node", planner_node))
        workflow.add_node("searcher_node", trace_node("searcher_node", searcher_node))
        workflow.add_edge(START, "planner_node")
        workflow.add_conditional_edges(
            "planner_node", route_after_planner,
            {"searcher": "searcher_node", "end": END},
        )
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
listing_app = build_workflow(listing_experiment=True)
jumpit_app = build_workflow(jumpit_experiment=True)


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
