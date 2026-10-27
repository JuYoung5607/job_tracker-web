# STATE_PLAN — 실행 상태와 영속 데이터 (v2)

## 분리 원칙

LangGraph State는 한 번의 실행에 필요한 임시 데이터다. Job DB, Feedback Store, PreferenceProfile은 실행을 넘어 유지되는 영속 데이터다. 사용자별 데이터가 공통 공고 레코드에 섞이면 안 된다.

## 수집 그래프 State

| 필드 | 타입 | 의미 |
| --- | --- | --- |
| run_id, started_at | str, datetime | 실행 식별·시간 |
| search_conditions | SearchConditions | 사용자가 명시한 검색 범위 |
| search_budget | SearchBudget | 최대 호출·후보·시간 |
| candidates | list[Candidate] | 검색 및 목록 확장으로 얻은 URL 후보 큐; 전체 고유 URL 상한 적용 |
| candidate_cursor | int | 다음 처리 위치; 최초 0, 후보 처리 완료 시 증가 |
| current_candidate | Candidate `| None` | 처리 중 후보 |
| discovered_candidates | list[Candidate] | 목록 탐색에서 새로 찾은 후보; 다음 Iterator 실행에서 병합 후 초기화 |
| candidate_processing_complete | bool | 현재 후보 처리 완료/실패 표시; 완료 후에만 커서 증가 |
| job_document | JobDocument `| None` | 접근 결과 |
| extracted_facts | ExtractedPostingFacts `| None` | 원문에서 추출하고 인용을 확인한 사실 |
| job_posting_draft | JobPostingDraft `| None` | 추출 결과 |
| evidence | list[Evidence] | 현재 후보의 필드별 근거 |
| company_profile | CompanyProfile `| None` | 선택적 회사 정보 |
| verification_result | VerificationResult `| None` | 검증 결과 |
| screening_result | ScreeningResult `| None` | 사용자 검색 조건에 대한 eligible/excluded/review; lifecycle 상태와 별개 |
| existing_job | JobPosting `| None` | 기존 공고 |
| lifecycle_result | LifecycleResult `| None` | 추천 적합도와 별개인 공고 상태·변경 |
| persisted_job_id | str `| None` | 저장 결과 |

현재 공통 공고 저장소는 기본 경로 `data/jobs.sqlite3`의 로컬 SQLite다. `JOB_TRACKER_DB_PATH`로 경로를 바꿀 수 있다. Iterator가 후보를 선택하면 기존 공고를 조회해 `existing_job`에 넣고, Lifecycle 뒤 Job Persister가 공고 ID·최초 발견 시각·근거를 유지하며 갱신한다. 사용자별 `screening_result`는 공통 공고 레코드에 저장하지 않는다.
| errors | 누적 list[AgentError] | 단계별 오류 |
| retry_counts | dict[str, int] | 단계별 재시도 |

Iterator는 첫 진입에 후보 0을 선택하고, 이후에는 `candidate_processing_complete=True`일 때만 새 후보를 중복 제거·상한 확인 후 큐 뒤에 합치고 커서를 증가시킨다. `candidate_cursor >= len(candidates)`이면 종료한다. 다음 후보 선택 전 `current_candidate` 이외의 후보별 문서·초안·근거·회사 정보·검증·기존 공고·lifecycle·저장 결과·신규 후보·완료 표시를 초기화한다. 후보별 실패도 오류를 남기고 완료 처리해 다음 후보로 넘어갈 수 있어야 한다. 병렬 후보 처리를 도입한다면 후보별 서브그래프 State로 격리한다. `Annotated[..., operator.add]`는 의도적으로 누적할 필드에만 쓴다.

## 개인화 요청 State

현재 개인화 그래프는 `user_id`, `search_conditions`, 이번 수집의 `screening_results`, 선택적 `preference_profile`, `rank_results`, `feed_result`를 사용한다. 공통 Job DB에서 URL별 공고를 다시 읽고 사용자별 피드를 SQLite `user_feeds`에 저장한다. 수집 State와 별도 그래프이며, 향후 피드백 후 비동기 재랭킹에 필요한 사용자별 검증 결과 영속화는 후속 과제다.

## 피드백 요청 State

`user_id`, `job_id`, `feedback`, `stored_feedback_id`, `updated_preference_profile`, `rerank_requested`, `errors`를 사용한다. 웹 요청은 수집 그래프가 완료된 뒤에도 언제든 들어올 수 있다.

## 핵심 스키마

- SearchConditions: target_jobs, skills, experience_levels, locations, employment_types, 사용자가 직접 지정한 제외 조건. 명시적 변경 전에는 학습 결과로 바꾸지 않는다.
- Candidate: candidate_id, URL, page_type(job_detail/listing/unknown), source_type/name, discovery_source, search_query/track, title/company hint, discovered_at, 선택적 parent_candidate_id/parent_listing_url, discovery_depth(검색 결과 0·목록에서 확장 1). 힌트는 검증된 공고 정보가 아니다. 실제 코드에는 부모·깊이 필드가 아직 없다.
- JobPosting: 공통 공고 정보, canonical URL, Evidence, verification, 별도 `LifecycleStatus`, first_seen_at, last_seen_at, content_hash, result_version. `LifecycleStatus`는 `active/changed/closed/unavailable/unknown`이며 사용자별 `eligible/excluded/review`나 추천 decision/confidence를 넣지 않는다. 기존 `JobStatus`는 레거시 필드로 당장 제거하지 않고 Lifecycle 판정에는 사용하지 않는다.
- RankResult: user_id, job_id, score(0~1), reasons, exploration 여부, preference_profile_version, ranked_at. 확률이 아닌 상대 순위 점수다.
- JobFeedback: feedback_id, user_id, job_id, decision, reasons, comment?, created_at, idempotency_key?.
- FeedbackReasonDetail: category(company/job/skill/experience/location/employment_type/other), value?. 복수 개를 저장한다.
- PreferenceProfile: user_id, feature_weights, evidence_counts, confidence, updated_at, version. feature key는 category+value로 구분한다.

기존 `ProfileChunk.USER_FEEDBACK`은 설명/검색 보조용 파생 자료로 사용할 수 있지만 원본 피드백의 저장소를 대체하지 않는다. `MatchDecision`, `CritiqueResult`는 MVP 실행 State에서 제외한다.

## 저장·개인정보

피드백과 선호 프로필은 사용자별 접근 통제를 적용한다. 원본 피드백은 수정 이력과 삭제 요청을 처리할 수 있게 보존한다. 공고 공개 데이터와 사용자 행동 데이터는 다른 테이블/컬렉션으로 저장한다.
