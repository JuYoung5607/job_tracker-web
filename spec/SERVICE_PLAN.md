# SERVICE_PLAN — Job Tracker 서비스 설계 (v2)

## 목표

사용자가 처음에는 명시적 조건에 맞는 다양한 채용 공고를 보고, 관심/보류/관심 없음과 구체적 이유를 남기면 다음 피드가 점진적으로 개인화되는 서비스다. MVP의 개인화는 모델 재학습이 아니라 저장된 피드백을 이용한 온라인 점수 갱신이다.

## 핵심 원칙

- SearchConditions는 사용자가 직접 정한 직무, 기술, 경력, 지역, 고용형태 및 명시적 제외 조건이다.
- PreferenceProfile은 행동에서 얻은 약한 선호다. 검색 범위를 자동으로 축소하거나 강한 제외 조건으로 승격하지 않는다.
- 공통 Job DB의 공고 사실·lifecycle과 사용자별 RankResult·JobFeedback을 분리한다.
- 모든 단계를 Agent로 만들지 않는다. 반복·저장·집계·상태 갱신은 일반 Worker를 우선한다.
- 공고의 출처와 주요 필드의 Evidence를 보존하며 불확실성은 사용자에게 드러낸다.

## 사용자 경험

1. 사용자가 초기 검색 조건을 설정한다.
2. 시스템은 폭넓게 공고를 찾아 검증·중복 제거 후 공통 Job DB에 저장한다.
3. 첫 피드는 명시적 조건과 직무 관련성 중심으로 정렬한다.
4. 사용자가 각 공고에 관심/보류/관심 없음 및 복수 이유를 남긴다. 가능하면 이유의 구체값도 받는다.
5. 피드백 저장 후 개인 선호 점수를 갱신하고 같은 공고 집합을 재정렬한다.
6. 다음 수집에서도 폭넓은 탐색을 유지한다. 학습된 선호는 검색 제거가 아닌 랭킹에 주로 사용한다.
7. 사용자는 명시적 조건과 학습된 선호를 구분해 확인하고 수정·초기화할 수 있다.

## 시스템 경계

```text
[공통 수집]
SearchConditions → Planner → Searcher → Candidate Iterator
 → Navigator → Extractor → Company Enricher → Verifier
 → Lifecycle → Job Persister → Job DB

[사용자별 피드]
Job DB + SearchConditions + PreferenceProfile
 → Ranker → Feed Publisher → Web Feed

[비동기 피드백]
Web Feed → HITL Feedback → Feedback Store
 → Preference Updater → Ranker 재실행 → Feed 갱신
```

웹에서 발생하는 피드백은 수집 작업을 중단시키지 않는다. 동기식 운영자 승인에만 선택적으로 LangGraph interrupt를 고려한다.

## MVP 범위

- 명시적 조건 입력·수정, 공고 검색·URL 후보 수집, 본문 접근, 정보 추출, Evidence 검증.
- 상세 공고와 목록 URL을 구별하고 목록은 추가 탐색용 단서로 사용.
- 기존 공고와 비교한 lifecycle 판정 및 멱등 DB 저장.
- 규칙 기반 Ranker, 정렬 이유, 제한된 탐색용 결과.
- 사용자별 피드백 저장, 이유 category/value, 누적 가중치·신뢰도, 피드 재정렬.
- 공고 상태 및 사용자별 결과 분리, 감사 가능한 실행·피드백 이력.
- 로그인/사용자 식별, 사용자별 데이터 접근 통제, 삭제·수정 가능성.

## MVP 제외 및 후속 후보

RAG Retriever, Critic, LLM 기반 취향 일반화, 모델 재학습, 자동 지원, 알림 최적화는 필수 경로에서 제외한다. 피드백과 클릭이 쌓이면 오프라인 평가를 거쳐 탐색 비율·가중치를 조정하고 필요할 때만 LLM 보조 또는 학습 모델을 도입한다.

## 데이터 소유권

| 데이터 | 범위 | 핵심 내용 |
| --- | --- | --- |
| SearchConditions | 사용자 | 직접 고른 검색 범위·제외 |
| JobPosting | 공통 | 검증된 공고 사실·Evidence·lifecycle |
| JobFeedback | 사용자×공고 | 선택, 복수 이유·구체값, 시각·이력 |
| PreferenceProfile | 사용자 | 특징 가중치, 횟수, 신뢰도, 버전 |
| RankResult/Feed | 사용자×공고 | 점수, 이유, 탐색 표시, 피드 버전 |

민감한 사용자 행동 데이터는 공고 공통 데이터와 분리해 접근 제어한다. 원본 피드백을 보존하되 사용자의 삭제 요청과 보존 정책을 지원한다.

## 성공 기준

- 후보 발견량뿐 아니라 검증 가능한 상세 공고 비율, 중복률, 목록 URL 오인율을 측정한다.
- 신규 사용자에게도 충분히 다양한 공고가 노출되는지 확인한다.
- 피드백 이후 순위가 설명 가능하게 바뀌는지 검증한다.
- 한 번의 피드백으로 명시적 검색 범위가 바뀌거나 직무가 영구 배제되지 않아야 한다.
- 피드백 저장과 재랭킹이 실패해도 공통 공고 DB가 오염되지 않아야 한다.

구체적 운영 상한과 점수 계수는 설정값으로 두고 테스트 데이터 및 사용자 관찰로 확정한다. 관련 계약은 WORK_FLOW, NODE_PLAN, STATE_PLAN, Decision_Policy, Prompt_Spec, REQUIREMENTS를 따른다.




