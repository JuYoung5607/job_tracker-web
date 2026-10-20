import json
import os
from collections.abc import Callable
from datetime import datetime, timezone

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from schemas.errors import AgentError
from schemas.search import ExperienceLevel, SearchConditions, SearchPlan, SearchQuery
from state import State

from dotenv import load_dotenv

load_dotenv()


SearchHistoryRetriever = Callable[
    [str, SearchConditions, datetime | None],
    list[str],
]


PLANNER_SYSTEM_PROMPT = """
당신은 채용공고 탐색을 위한 Planner Agent입니다.

사용자의 검색 조건과 과거 탐색 정보를 바탕으로이번 실행에서 직무군을 빠뜨리지 않고 폭넓게 탐색할 검색 전략을 수립합니다.

당신의 목적은 가능한 많은 검색어를 만드는 것이 아니라,
적은 검색 호출로 서로 다른 경로에서 충분한 채용공고 후보를 발견하는 것입니다.


[입력 해석]

- target_jobs:
  사용자가 찾고 싶은 직무입니다.
  의미가 유사하거나 같은 직무군에 속하는 항목은 하나의 검색 방향으로 묶을 수 있습니다.

- skills:
  사용자의 보유 기술입니다.
  모든 기술을 검색어에 포함할 필요가 없습니다.
  직무 검색 범위를 지나치게 좁히는 프레임워크나 도구명은 기본적으로 제외합니다.

- experience_levels:
  검색 범위를 조정하기 위한 경력 수준입니다.

- locations:
  검색 지역입니다.

- exclusion_conditions:
  이후 실제 공고 내용을 확인할 때 사용할 조건입니다.
  검색어만으로 제외 여부를 판단하지 않습니다.

- employment_types:
  사용자가 명시적으로 선택한 고용형태입니다.
  검색 계획에서 고려하되, 모든 query에 강제로 넣어
  관련 공고가 검색에서 누락되지 않도록 합니다.
  실제 고용형태의 일치 여부는 공고 본문 확인 후 판단합니다.

- preferred_source_domains:
  별도 사이트 검색 경로에 사용할 도메인입니다.
  일반 검색을 이 도메인들로 제한하지 않습니다.

- search_budget.max_search_calls:
  사용할 수 있는 최대 검색 횟수입니다.
  반드시 모두 사용할 필요는 없습니다.

[검색 전략]
1. target_jobs에 있는 서로 다른 직무군을 모두 포괄한다.
2. 각 직무군을 탐색할 수 있는 query를 최소 하나 이상 만든다.
3. 의미가 유사한 직무는 묶을 수 있지만,
   묶어서 특정 직무군의 공고를 놓칠 가능성이 크면 별도 query로 나눈다.
4. locations와 experience_levels를 일부 query에 반영하되, 모든 query에
   동시에 넣어 검색 범위를 지나치게 좁히지 않는다.
5. employment_types는 사용자의 명시적 조건으로 고려하되,
   모든 query에 강제로 넣어 검색 범위를 지나치게 좁히지 않는다.
6. 각 직무군에 대해 직무명만 사용하는 넓은 query와 지역·경력 중
   일부를 더한 query를 함께 만든다.
7. skills는 검색 범위를 좁히지 않는 선에서 선택적으로 사용한다.
8. 서로 다른 경로의 공고를 발견하면서 중복 검색 호출을 줄인다.
   max_search_calls는 상한이며 반드시 모두 사용할 필요는 없다.

[search_tracks]

search_track은 개별 검색어가 아니라
"왜 이 검색을 수행하는가"를 나타내는 상위 검색 전략입니다.

예:
- AI 및 임베디드 AI 일반 채용 공고 탐색

각 query는 반드시 하나의 search_track에 속해야 합니다.


[query 작성 규칙]

- 한국 채용 검색에 사용할 수 있는 짧고 자연스러운 검색어를 작성합니다.
- 검색 도구의 문법을 추측하지 않습니다.
- AND, OR, 괄호, 마이너스 제외 연산자를 사용하지 않습니다.
- 공식 채용 도메인을 모르면 URL을 추측하지 않고 "공식 채용"이라는 표현을 사용합니다.
- experience_levels가 제공되어도 모든 query에 경력 수준을 강제하지 않습니다.
- ENTRY와 JUNIOR가 함께 있으면 "신입 주니어"처럼 넓게 표현할 수 있습니다.
- 입력에 없는 구체적인 경력 연수는 만들지 않습니다.


[stop_conditions]

다음 search_budget 값을 그대로 반영합니다.

- max_search_calls
- max_candidates
- timeout_seconds

후보 수는 중복 URL 제거 후의 후보 수를 의미합니다.


[금지사항]

- 직접 웹 검색
- 실제 채용공고 내용 추출
- 기업 규모 또는 기업 유형 판단
- 사용자와 공고의 적합도 판단
- 추천 또는 탈락 결정
- 입력에 없는 사용자 조건 추측
"""


def empty_search_history_retriever(
    user_id: str,
    search_conditions: SearchConditions,
    last_searched_at: datetime | None,
) -> list[str]:
    """검색 이력 저장소가 연결되기 전까지 빈 이력을 반환합니다."""
    return []


def expand_search_plan(
    plan: SearchPlan,
    conditions: SearchConditions,
    max_search_calls: int,
) -> SearchPlan:
    """일반 검색과 사용자 지정 사이트 검색에 호출 예산을 나눕니다."""
    domains = list(dict.fromkeys(
        domain.lower().removeprefix("www.")
        for domain in conditions.preferred_source_domains
    ))
    site_slots = min(len(domains), max_search_calls // 2)
    general_limit = max_search_calls - site_slots
    queries = [
        item.model_copy(update={"allowed_domains": []})
        for item in plan.queries[:general_limit]
    ]
    seen = {" ".join(item.query.casefold().split()) for item in queries}

    for target_job in conditions.target_jobs:
        variants = [target_job]
        variants.extend(
            f"{target_job} {location}" for location in conditions.locations
        )
        for query in variants:
            if len(queries) >= general_limit:
                break
            normalized = " ".join(query.casefold().split())
            if normalized in seen:
                continue
            queries.append(SearchQuery(
                query=query,
                search_track=f"{target_job} 폭넓은 채용 탐색",
            ))
            seen.add(normalized)

    site_queries = []
    for index, domain in enumerate(domains[:site_slots]):
        target_job = conditions.target_jobs[index % len(conditions.target_jobs)]
        site_queries.append(SearchQuery(
            query=target_job,
            search_track=f"{domain} 채용공고 탐색",
            allowed_domains=[domain],
        ))

    queries = site_queries + queries
    selected_levels = set(conditions.experience_levels)
    if selected_levels and selected_levels.issubset({
        ExperienceLevel.ENTRY, ExperienceLevel.JUNIOR
    }):
        terms = []
        if ExperienceLevel.ENTRY in selected_levels:
            terms.append("신입")
        if ExperienceLevel.JUNIOR in selected_levels:
            terms.append("주니어")
        queries = [
            item.model_copy(update={
                "query": " ".join([
                    item.query,
                    *(term for term in terms if term not in item.query),
                ])
            })
            for item in queries
        ]

    tracks = list(dict.fromkeys(
        [*plan.search_tracks, *(item.search_track for item in queries)]
    ))
    return plan.model_copy(update={"queries": queries, "search_tracks": tracks})


def create_planner_node(
    llm: BaseChatModel,
    history_retriever: SearchHistoryRetriever = (
        empty_search_history_retriever
    ),
):
    structured_llm = llm.with_structured_output(SearchPlan)

    def planner_node(state: State) -> dict:
        user_id = state["user_id"]
        search_conditions = state["search_conditions"]
        search_budget = state["search_budget"]
        last_searched_at = state.get("last_searched_at")

        try:
            search_history = history_retriever(
                user_id,
                search_conditions,
                last_searched_at,
            )

            planner_context = {
                "search_conditions": search_conditions.model_dump(
                    mode="json"
                ),
                "last_searched_at": (
                    last_searched_at.isoformat()
                    if last_searched_at is not None
                    else None
                ),
                "search_budget": search_budget.model_dump(
                    mode="json"
                ),
                "search_history": search_history,
            }

            human_message = HumanMessage(
                content=(
                    "다음 입력을 바탕으로 이번 실행의 "
                    "채용공고 검색 전략을 작성하십시오.\n\n"
                    f"{json.dumps(planner_context, ensure_ascii=False)}"
                )
            )

            print("[Planner] LLM 호출 시작")
            search_plan = structured_llm.invoke(
                [
                    SystemMessage(content=PLANNER_SYSTEM_PROMPT),
                    human_message,
                ]
            )
            print("[Planner] LLM 호출 완료")

            if not isinstance(search_plan, SearchPlan):
                search_plan = SearchPlan.model_validate(
                    search_plan
                )

            search_plan = expand_search_plan(
                search_plan, search_conditions, search_budget.max_search_calls
            )

            return {
                "search_plan": search_plan,
            }

        except Exception as exc:
            error = AgentError(
                node_name="planner",
                error_type=type(exc).__name__,
                message=str(exc),
                occurred_at=datetime.now(timezone.utc),
                retryable=True,
            )

            return {
                "search_plan": None,
                "errors": [error],
            }

    return planner_node


planner_llm = ChatOpenAI(
    model=os.getenv(
        "OPENAI_MODEL",
        "gpt-4.1-mini",
    ),
    temperature=0.1,
)

planner_node = create_planner_node(
    llm=planner_llm,
)
