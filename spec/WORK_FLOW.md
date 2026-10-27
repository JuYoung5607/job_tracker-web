# WORK_FLOW — 공고 수집과 개인화 (v2)

## 설계 원칙

검색 조건(SearchConditions)은 사용자가 직접 지정한 범위다. 학습된 선호(PreferenceProfile)는 검색 범위를 자동으로 줄이는 조건이 아니라 랭킹에 사용하는 약한 신호다. 공고의 생애주기와 사용자별 추천 점수·피드백은 별도 데이터다.

## 1. 공통 공고 수집 파이프라인

사용자가 웹에서 `새 공고 찾기`를 요청하면 저장된 SearchConditions로 새 실행을 시작한다. 요청에는 실행 ID를 즉시 반환하고, 화면은 실행 상태와 발견·중복·검증·노출·실패 건수를 별도로 조회한다. 동일 사용자의 중복 실행과 검색 예산 초과를 막는다. 피드백 여부는 실행 완료 조건이 아니다.

```text
START → Planner(채용 목록 수집) → Candidate Iterator
                         ↓ (후보마다 순차 처리)
Navigator → Extractor → Company Enricher → Verifier
  → Lifecycle → Job Persister → 다음 후보
                         ↓ (모든 후보 처리 완료)
                       END (수집 실행 완료)
```

- Planner: 잡코리아·인크루트·사람인·점핏의 현재 채용 목록에 호출 예산을 배분하고 URL 후보를 합친다. LLM 계획·OpenAI 웹 검색은 사용하지 않는다.
- Candidate Iterator: 후보 목록을 큐처럼 사용해 하나씩 선택하고, 처리 완료 후 커서를 전진시킨다.
- Navigator: 접근 가능 여부와 원문을 확보한다. 목록/종류 불명 페이지에서는 상세 공고 URL을 발견해 새 후보로 반환한다.
- Extractor: 구조화된 초안과 필드별 Evidence를 만든다.
- Company Enricher: 현재 기본 실행에서는 공고 원문에 있는 회사명과 근거만 확인하고 외부 웹 검색은 하지 않는다. 외부 정보 보강은 추후 관심 공고에만 선택적으로 사용한다. 실패해도 공고 처리를 중단하지 않는다.
- Verifier: 핵심 필드의 근거, 누락, 충돌을 판정한다. 검증 불충분한 공고는 공개 대기 또는 수동 검토 대상으로 둔다.
- Lifecycle: 사용자 조건 적합도(`eligible/excluded/review`)와 별개로 원문·접근 상태·기존 기록을 비교해 `active/changed/closed/unavailable/unknown` 및 변경 사항을 판정한다. 한 번의 접근 실패로 `closed` 처리하지 않는다.
- 사용자 노출 정책: Lifecycle의 `should_publish`는 `excluded`이면 항상 `false`다. `eligible`과 `review`만 피드 후보이며, 실제 Feed Publisher에서도 이 조건을 재확인한다.
- Job Persister: 검증 결과와 lifecycle을 포함한 공통 공고 기록을 멱등적으로 저장한다.

Lifecycle을 저장보다 먼저 실행한다. 그래야 기존 공고와 비교한 결과를 원자적으로 저장할 수 있다.
현재 그래프는 `Candidate Iterator → Existing Job Lookup → Navigator → Extractor → Company Enricher → Verifier → Lifecycle → Job Persister → Candidate Iterator`로 흐른다. 저장소는 로컬 SQLite이며, 사용자별 적합 판정을 공통 공고 레코드에 넣지 않는다. 기존 공고는 URL로 조회해 Lifecycle 변경 판정에 사용하고, 원문과 근거를 중복 없이 갱신한다. Navigator 접근 실패나 추출 실패는 현재 Lifecycle·Persister를 거치지 않고 다음 후보로 넘어간다. `eligible`은 사용자 검색 조건에 맞는다는 뜻이지 `active`의 증거가 아니다.

### 후보 큐와 목록 확장

```text
Iterator: candidates[candidate_cursor] 선택
  ├─ job_detail → Existing Job Lookup → Navigator → Extractor → Company Enricher → Verifier → Lifecycle → Job Persister → Iterator
  └─ listing/unknown → Navigator → 상세 URL 후보 발견 (공고로 저장하지 않음)
처리 완료/후보별 실패 → Iterator: 새 후보 병합·중복 제거 → cursor + 1 → 다음 후보
cursor >= len(candidates) → 종료
```

`candidate_cursor`의 초기값은 0이다. 선택만으로 커서를 증가시키지 않고, 상세 공고 저장·목록 확장·후보별 실패 등 해당 후보의 처리가 끝났을 때 증가시킨다. 다음 후보를 선택하기 전에 문서·초안·근거·검증 결과 등 후보별 State를 초기화한다. 목록에서 발견한 상세 URL은 출처 목록 URL과 확장 깊이를 기록해 큐 뒤에 추가한다. 목록에서 또 다른 목록을 따라가는 재귀 확장은 MVP에서 허용하지 않는다. URL 중복 제거 및 확장 후를 포함한 전체 고유 후보 수는 `SearchBudget.max_candidates`를 넘지 않는다. Planner와 Iterator는 함께 이 상한을 지킨다.

## 2. 사용자별 피드 생성

```text
Job DB + SearchConditions + PreferenceProfile
  → Ranker → Feed Publisher → 사용자 웹 피드
```

Ranker는 명시적 조건을 먼저 적용하고 나머지 공고에 0~1 상대 점수를 매긴다. 점수는 확률이 아니다. 낮은 점수의 공고를 DB에서 삭제하거나 다음 검색에서 무조건 제외하지 않는다. Feed Publisher는 정렬 이유와 탐색용 공고를 함께 보여준다.

## 3. 비동기 HITL 피드백

```text
웹 피드의 관심/보류/관심 없음 + 복수 이유·대상값
  → Feedback Store → Preference Updater
  → 해당 사용자 Ranker 재실행 → Feed 갱신
```

피드백은 선택 사항이다. 사용자가 아무 행동도 하지 않으면 수집 실행과 피드 생성은 이미 완료된 상태로 남는다. 사용자가 선택을 제출했을 때만 별도 피드백 실행을 시작한다. 이 실행은 `유효한 피드백인가?`라는 조건 분기로 유효하지 않은 요청을 종료하고, 유효한 요청만 저장·선호 갱신·재랭킹한다. 사용자의 피드백은 이력으로 보존하며 중복 요청은 멱등 처리한다.

HITL은 사람이 판단에 참여한다는 설계 개념이며, 반드시 LangGraph `interrupt`를 뜻하지 않는다. `interrupt()`를 호출한 실행만 체크포인트에 상태를 보존하고 사람의 응답을 기다렸다가 재개한다. 기능 실험이 필요하면 **운영자가 명시적으로 검토를 요청한 불확실한 공고 한 건**에 한정해 `review_requested? → interrupt → 운영자 판단 → resume` 분기를 별도 실행으로 둔다. 기본 검색·피드백 경로는 이 분기를 거치지 않으며, 사용자가 응답하지 않아도 수집 실행은 완료된다. 실제 도입 전에는 영속 체크포인터, 실행/thread ID, 재개 권한, 중복 재개 방지와 대기 실행 정리 정책을 정한다.

## 실패와 재시도

검색·접근 실패는 예산 내에서 재시도하고 후보별 오류로 기록한다. Company Enricher 실패는 비차단이다. 검증 실패나 접근 불가가 기존의 정상 데이터를 지우면 안 된다. 피드백 저장 실패 시 선호도 갱신 및 재랭킹을 진행하지 않는다. 각 단계는 실행 ID와 공고 ID로 추적 가능해야 한다.
