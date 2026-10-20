# WORK_FLOW — 공고 수집과 개인화 (v2)

## 설계 원칙

검색 조건(SearchConditions)은 사용자가 직접 지정한 범위다. 학습된 선호(PreferenceProfile)는 검색 범위를 자동으로 줄이는 조건이 아니라 랭킹에 사용하는 약한 신호다. 공고의 생애주기와 사용자별 추천 점수·피드백은 별도 데이터다.

## 1. 공통 공고 수집 파이프라인

```text
START → Planner → Searcher → Candidate Iterator
                         ↓ (후보마다 순차 처리)
Navigator → Extractor → Company Enricher → Verifier
  → Lifecycle → Job Persister → 다음 후보
                         ↓ (모든 후보 처리 완료)
                       END
```

- Planner: 명시적 검색 조건과 예산으로 폭넓은 SearchPlan을 작성한다.
- Searcher: 검색 결과에서 URL 후보를 발견한다. 추천·최종 조건 판정은 하지 않는다.
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

`candidate_cursor`의 초기값은 0이다. 선택만으로 커서를 증가시키지 않고, 상세 공고 저장·목록 확장·후보별 실패 등 해당 후보의 처리가 끝났을 때 증가시킨다. 다음 후보를 선택하기 전에 문서·초안·근거·검증 결과 등 후보별 State를 초기화한다. 목록에서 발견한 상세 URL은 출처 목록 URL과 확장 깊이를 기록해 큐 뒤에 추가한다. 목록에서 또 다른 목록을 따라가는 재귀 확장은 MVP에서 허용하지 않는다. URL 중복 제거 및 확장 후를 포함한 전체 고유 후보 수는 `SearchBudget.max_candidates`를 넘지 않는다. Searcher는 목록 확장용 후보 슬롯을 남겨야 하며, 현 코드에는 이 예약·확장 로직이 아직 구현되지 않았다.

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

피드백은 사용자가 나중에 남길 수도 있으므로 수집 그래프를 정지시키지 않는다. LangGraph `interrupt`는 동기적 승인·수정이 필요한 운영자 검토에만 선택적으로 사용한다. 사용자의 피드백은 이력으로 보존하며 중복 요청은 멱등 처리한다.

## 실패와 재시도

검색·접근 실패는 예산 내에서 재시도하고 후보별 오류로 기록한다. Company Enricher 실패는 비차단이다. 검증 실패나 접근 불가가 기존의 정상 데이터를 지우면 안 된다. 피드백 저장 실패 시 선호도 갱신 및 재랭킹을 진행하지 않는다. 각 단계는 실행 ID와 공고 ID로 추적 가능해야 한다.
