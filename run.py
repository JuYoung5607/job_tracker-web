# run.py

import os
from datetime import datetime, timezone
from pathlib import Path

# 그래프를 만들기 전에 경로를 지정해야 모든 노드의 입력·출력이 기록됩니다.
trace_path = Path(os.getenv("JOB_TRACKER_TRACE_FILE") or (
    Path(__file__).resolve().parent / "logs" /
    f"node-flow-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
))
os.environ["JOB_TRACKER_TRACE_FILE"] = str(trace_path)

from graph import app, jumpit_app, listing_app, personalization_app
from run_metrics import RunMetrics
from schemas.search import (
    ExperienceLevel,
    SearchBudget,
    SearchConditions,
)


initial_state = {
    "run_id": "run-001",
    "user_id": "user-001",
    "started_at": datetime.now(timezone.utc),

    "search_conditions": SearchConditions(
        target_jobs=[
            "AI Engineer",
            "Embedded AI",
        ],
        skills=[
            "Python",
            "STM32",
            "LangGraph",
        ],
        exclusion_conditions=[
            "파견",
            "상주 근무",
        ],
        locations=[
            "서울",
            "경기",
        ],
        preferred_source_domains=[
            "jobkorea.co.kr",
            "saramin.co.kr",
            "incruit.com",
            "jumpit.saramin.co.kr",
        ],
        experience_levels=[
            ExperienceLevel.ENTRY,
            ExperienceLevel.JUNIOR,
        ],
        max_experience_years=3,
    ),

    "search_budget": SearchBudget(
        max_search_calls=10,
        max_candidates=30,
        timeout_seconds=300,
    ),
}


screening_results = []
metrics = RunMetrics()
discovery_mode = os.getenv("JOB_TRACKER_DISCOVERY", "web_search")
if discovery_mode not in {"web_search", "listing", "jumpit"}:
    raise ValueError("JOB_TRACKER_DISCOVERY는 web_search, listing 또는 jumpit이어야 합니다.")
selected_app = {"web_search": app, "listing": listing_app, "jumpit": jumpit_app}[discovery_mode]
print(f"[Run] 후보 발견 방식: {discovery_mode}")
print(f"[Run] 원문·추출 과정 로그: {trace_path}")

for event in selected_app.stream(
    initial_state,
    config={"recursion_limit": max(100, initial_state["search_budget"].max_candidates * 8 + 20)},
    stream_mode="updates",
):
    for node_name, update in event.items():
        metrics.observe(node_name, update)
        print(f"\n===== {node_name} =====")

        search_plan = update.get("search_plan")

        if search_plan is not None:
            print(
                search_plan.model_dump_json(
                    indent=2,
                )
            )

        candidates = update.get("candidates", [])

        if node_name in {"searcher_node", "listing_searcher_node", "jumpit_searcher_node"}:
            print("아래는 검증 전 URL 후보이며, 추천 공고가 아닙니다.")
            for index, candidate in enumerate(update.get("candidates", []), start=1):
                print(f"\n--- candidate {index} ---")
                print(candidate.model_dump_json(indent=2))

        if node_name == "candidate_iterator_node":
            current = update.get("current_candidate")
            print(f"cursor: {update.get('candidate_cursor')}")
            print(f"current: {current.url if current else '없음 — 종료'}")

        screening_result = update.get("screening_result")
        if screening_result is not None:
            screening_results.append(screening_result)
            print(screening_result.model_dump_json(indent=2))

        lifecycle_result = update.get("lifecycle_result")
        if lifecycle_result is not None:
            print(lifecycle_result.model_dump_json(indent=2))

        if node_name == "job_persister_node":
            print(f"저장 공고 ID: {update.get('persisted_job_id') or '없음'}")

        for error in update.get("errors", []):
            print(error.model_dump_json(indent=2))

metrics.print_summary()
print("\n===== 검증된 공고 요약 =====")
for decision in ("eligible", "excluded", "review"):
    matching = [result for result in screening_results if result.decision == decision]
    print(f"{decision}: {len(matching)}건")
    if decision == "eligible":
        for result in matching:
            print(f"  {result.source_url}")

personalization = personalization_app.invoke({
    "user_id": initial_state["user_id"],
    "search_conditions": initial_state["search_conditions"],
    "screening_results": screening_results,
    "preference_profile": None,
})
feed = personalization["feed_result"]
print(f"\n===== 사용자 피드 (버전 {feed.version}) =====")
for index, item in enumerate(feed.items, start=1):
    label = "검토" if item.screening_decision == "review" else "확인"
    marker = " · 탐색" if item.is_exploration else ""
    print(f"{index}. [{label}{marker}] {item.company_name} — {item.job_title} "
          f"(상대 점수 {item.score:.2f})")
    print(f"   {item.source_url}")
    print(f"   이유: {'; '.join(item.reasons)}")
