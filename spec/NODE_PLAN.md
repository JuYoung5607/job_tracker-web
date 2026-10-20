# NODE_PLAN — 노드 계약 (v2)

## 공통 규칙

각 노드는 필요한 State만 읽고 업데이트할 필드만 반환한다. LLM은 전략 생성·문서 추출·근거 검증처럼 의미 판단이 필요한 곳에만 쓴다. 저장, 반복, 예산 관리, 피드백 집계는 일반 Python Worker가 기본이다. RAG Retriever와 Critic은 MVP의 필수 노드가 아니다.

| 노드 | 성격 | 입력 | 출력 | 실패 정책 |
| --- | --- | --- | --- | --- |
| Planner | LLM Agent | SearchConditions, SearchBudget, SearchHistory | SearchPlan | 스키마 검증 후 안전한 기본 계획 |
| Searcher | 검색 도구 Worker | SearchPlan, SearchBudget | Candidate[] | 호출 상한·시간 상한, 확장용 슬롯 예약 |
| Candidate Iterator | 제어 Worker | Candidate[], cursor, 처리 완료·신규 후보 | current_candidate, cursor, 후보 큐 | 중복·초과 후보 건너뜀 |
| Navigator | 접근 Worker | Candidate | 상세: JobDocument / 목록: 신규 Candidate[], 접근 상태 | 후보별 오류 기록 |
| Extractor | LLM Agent | JobDocument | JobPostingDraft, Evidence[] | 빈 필드 추측 금지 |
| Company Enricher | Worker/Agent | Draft, 확인 가능한 출처 | CompanyProfile? | 실패해도 계속 진행 |
| Verifier | LLM+규칙 | Draft, Evidence, Document | VerificationResult | 충돌·누락을 검토 대기로 |
| Lifecycle | 규칙 Worker | ScreeningResult, JobDocument 접근 상태, JobPostingDraft, 기존 JobPosting | LifecycleResult | 추천 적합도와 공고 상태 분리, 기존 정상값 보존 |
| Job Persister | DB Worker | Draft, VerificationResult, LifecycleResult | JobPosting | 멱등 upsert |
| Ranker | 규칙 Worker (MVP) | JobPosting[], SearchConditions, PreferenceProfile | RankResult[] | 결정 근거 기록 |
| Feed Publisher | Worker | RankResult[] | 사용자별 Feed | 버전·정렬 이유 포함 |
| HITL Feedback | 웹 이벤트 | 사용자 선택, 공고 | JobFeedback 요청 | 입력 검증 |
| Feedback Store | DB Worker | JobFeedback | 저장된 피드백 | 멱등 저장 |
| Preference Updater | 규칙 Worker (MVP) | 기존 프로필, 피드백, 공고 특징 | PreferenceProfile | 신뢰도·상한 적용 |

## 노드별 경계

### Planner / Searcher

Planner는 사용자가 직접 정한 직무·기술·경력·지역·고용형태를 포괄하도록 검색 track과 query를 만든다. 학습된 비선호로 query를 자동 삭제하지 않는다. Searcher는 후보를 폭넓게 발견하고 URL, 출처, 검색어, 발견시각을 남긴다. 검색 결과의 목록 페이지를 채용 상세 페이지로 오인하지 않는다. 검색 호출 수는 최대치이지 반드시 모두 소진해야 하는 목표가 아니다.

사용자가 `preferred_source_domains`를 지정하면 Planner는 검색 호출 예산의 최대 절반을 해당 도메인별 검색 경로에 예약한다. 나머지는 모든 사이트를 대상으로 하는 일반 검색에 사용한다. 사이트 경로는 `SearchQuery.allowed_domains`로 기록하고 일반 검색보다 먼저 실행한다. 현재 기본 검색 모델 `gpt-4.1-mini`는 웹 검색 도구의 `filters`를 지원하지 않으므로 Searcher는 검색어에 `site:도메인`을 붙이고, 반환된 후보 URL이 지정 도메인에 속하는지 다시 확인한다. 신입·주니어만 선택한 경우 Planner는 사이트별·일반 검색어 모두에 해당 경력 수준을 반영한다. Searcher는 개별 공고 검색 결과 제목에 최소 요구 경력이 설정 상한을 초과한다고 명시된 후보만 조기 제외하고, 신입·경력 동시 지원 또는 경력 불명 후보는 본문 검증으로 넘긴다. Searcher는 예산·시간이 허용하는 검색을 모두 실행한 뒤 경로별로 후보를 번갈아 골라 전체 후보 상한을 지킨다. 사이트당 최소 한 번을 우선하되, 예산보다 사이트가 많으면 입력 순서대로 가능한 수만 검색한다. 도메인 선호는 검색 경로일 뿐 기업 국적의 근거가 아니다. URL 후보가 실제로 열리는지와 마감·경력·최종 모회사 국적은 이후 단계에서 별도 검증한다.

### Candidate Iterator / Navigator 목록 확장

사람인 목록 실험은 `search/recruit`의 실제 `item_recruit` 카드에서 제목·경력 표시·공고 ID를 읽고, 지원 버튼 링크가 아닌 카드 제목 링크만 Candidate로 만든다. 신입·주니어 선택 시 사람인의 신입/경력·연차 상한·경력무관 필터를 검색 요청에 반영하고, 카드에서 명백한 연차 초과·상위 직책을 다시 확인한다. 목록 HTML이 2MB를 조금 넘으므로 사람인 목록 조회에만 3MB 상한을 적용한다. 상세 페이지는 동일한 `rec_idx`의 공개 공고 본문을 별도로 읽어 부모 페이지 앞에 합친다. 상세 본문이 없거나 ID가 다르면 부모의 사이트 메뉴·요약만으로 판정하지 않고 `review`로 남긴다.

현재 채용 목록 직접 탐색 실험에서는 `JOB_TRACKER_DISCOVERY=listing`으로
Planner·OpenAI 웹 검색을 우회하고, JobKorea·Incruit·Saramin 검색 목록과
Jumpit 현재 채용 목록 API에서 상세 공고 URL을 Candidate로 수집한다. 네 플랫폼은
`max_search_calls`와 `timeout_seconds`를 공유한다. 현재 두 목표 직무·호출 상한 10회에서는
세 HTML 플랫폼에 각 직무당 1회씩 총 6회, Jumpit에 직무별 넓은 검색과 신입 검색
총 4회를 배분한다. 각 플랫폼 후보는 3:1로 섞고 URL 중복을 제거해 `max_candidates`를 지킨다.
Jumpit의 개별 모드(`JOB_TRACKER_DISCOVERY=jumpit`)는 비교 실험용으로 유지한다.
접근이 막힌 JobPlanet은 직접 목록 대상과 기본 선호 출처에서 제외한다.
JobKorea는 직무명에 신입·주니어를 추가해 검색하고, Incruit는 해당 단어를 함께
검색할 때 결과가 사라져 직무명과 사이트의 신입 필터(`crr=1`)를 사용한다.
인크루트 목록 카드의 경력 표시와 제목에서 명백한 연차 초과·상위 직책을 조기
제외하지만 `신입/경력` 혼합 공고와 박사 학위 표기만 있는 공고는 상세 검증으로 넘긴다.
목록의 제목 없는 홍보 링크는 제외한다. 첫 페이지에서 후보가 부족하면 남은 검색
호출·시간 예산 안에서 다음 페이지를 탐색한다. 후보 발견 이후의
Iterator → Navigator → Extractor → Verifier 경로는 기존과 동일하다.
목록에서 발견했다는 사실은 모집 중의 증거가 아니며 Verifier가 상세 페이지에서
마감·경력 여부를 다시 판정한다. 신입·주니어 검색에서 상세 직무명에 총괄·센터장·
시니어 등 상위 직책이 확인되면 경력무관 표시와 관계없이 제외한다.
이 경로는 네 플랫폼을 이용한 비교 실험이고,
기본 웹 검색 경로를 대체하지 않는다.

`candidates`는 인덱스가 있는 순차 큐다. 최초 `candidate_cursor=0`; Iterator는 `candidates[candidate_cursor]`를 `current_candidate`로 선택한다. 같은 후보를 선택한 즉시 커서를 올리지 않는다. 상세 공고의 저장 완료, 목록 확장 완료 또는 후보별 실패가 보고되면 Iterator가 Navigator의 신규 후보를 원래 큐 뒤에 병합하고 URL 중복·총 후보 상한을 확인한 다음 커서를 1 올린다. 다음 선택 전에 후보별 State를 초기화한다. 커서가 큐 길이에 도달하면 종료한다.

`job_detail`은 본문 추출 경로로 보낸다. `listing`/`unknown`은 Navigator가 실제 페이지를 열어 상세 공고 URL을 찾아 새 Candidate로 반환한다. 목록 자체를 Extractor·Job Persister에 보내지 않는다. 새 후보에는 부모 목록 URL/ID와 `discovery_depth=1`을 기록한다. MVP에서는 목록에서 목록으로 이어지는 확장은 금지한다. Searcher와 Iterator는 함께 `SearchBudget.max_candidates`를 지킨다. Searcher는 처음부터 모든 슬롯을 쓰지 않고 목록 확장용 슬롯을 예약한다. 예약 크기와 상세 URL 추출 수는 설정값으로 두되 전체 상한을 넘지 않는다.

### Navigator / Extractor / Verifier

인크루트 상세 공고는 모집 직무·자격요건이 `jobpostcont.asp` iframe 안에 있을 수 있다. Navigator는 부모 페이지에서 해당 iframe 주소를 읽고, 부모와 동일한 `job` ID를 가진 인크루트 상세 본문만 추가 조회해 합친다. iframe을 찾지 못하거나 본문을 확인할 수 없으면 부모 요약만으로 판정하지 않고 `review`로 남긴다.
잡코리아 개별 공고의 모집 부문은 부모 HTML이 아닌 `GI_Read_Comt_Ifrm` 상세 페이지에 있을 수 있다. 부모 페이지 데이터에 동일한 `Gno`의 상세 경로가 있을 때만 이를 추가 조회해 본문 앞에 합친다. 상세 본문이 없으면 부모 요약만으로 판정하지 않고 `review`로 남긴다.
인크루트 부모 페이지의 동일 지원 영역에 `채용시` 표시와 활성 `홈페이지 지원` 버튼이 함께 있을 때만 현재 모집 중인 근거로 사용한다. 지원 버튼만 있거나 추천 공고에 `채용시`가 보인다는 이유로 활성으로 판단하지 않는다. 이미 지난 마감일 또는 명시적 마감 상태는 이 신호보다 우선한다. 이 신호는 경력·직무 적합성을 대신 증명하지 않는다.

Navigator는 원문과 접근 상태만 책임진다. Extractor는 title, company, location, employment type, experience, deadline, requirements 등을 구조화하고 근거를 연결한다. Verifier는 특히 job_title, experience_level, closes_at, employment_type, location을 검증한다. 확인되지 않은 값은 추정하지 않고 missing/conflict로 남긴다. 회사 정보 보강 실패는 검증 가능한 공고를 버리는 이유가 아니다.

현재 구현에서는 Extractor가 공고의 제목·회사·경력·마감일·모집 상태 등 핵심 항목과 원문 인용을 우선 추출한다. Verifier는 인용이 실제 원문에 있는지 확인하고 마감일과 최소 요구 경력을 규칙으로 판정한다. `ScreeningResult`는 `eligible/excluded/review`로 구분하며, 근거 부족은 `review`다. 수집 그래프의 Job Persister는 공통 공고를 저장하고, 수집 종료 후 별도 개인화 그래프의 Ranker·Feed Publisher가 사용자별 피드를 만든다. 다른 필드에 대한 전체 검증은 후속 구현이다.

Company Enricher는 Extractor가 확인한 회사명 근거를 공고 원문과 다시 대조한다. 현재 기본 실행에서는 API 비용을 줄이기 위해 외부 웹 검색을 하지 않고 회사명과 원문 근거만 남긴다. 후속 관심 공고 처리에서만 `enable_web_search=True`로 외부 정보를 선택적으로 보강할 수 있다. 외부 검색을 사용하는 경우에도 업종·규모·본사 위치를 참고 정보로만 다루고, 공식 홈페이지 여부를 판정하거나 필수 조건으로 삼지 않는다. 값·짧은 인용·검색 결과에 실제 포함된 출처 URL·조회 시각을 남긴다. 출처 페이지를 직접 재조회해 인용 일치까지 검증하지 않으므로 외부 필드의 `verification_status`는 `unknown`이며, 확정값으로 취급하지 않는다. 출처가 없거나 회사 식별이 맞지 않으면 필드를 `unknown`/`null`로 남긴다. 검색 실패도 Verifier 진행을 막지 않으며, 회사명만으로 기업 국적을 추정하지 않는다.

사람인 상세 페이지 제목의 `D-n`, `오늘 마감`, `채용시 마감`은 해당 공고 URL의 최신 페이지에서 확인했을 때 모집 상태 근거로 사용한다. 최소 경력 연수가 `null`이어도 원문에 주니어가 명시되면 `experience_basis=junior_without_years`로 구분하고, 근거 자체가 없으면 `review`로 둔다. 여러 포지션이 한 공고에 있으면 각 포지션의 직무·필수 경력을 한 묶음으로 추출한다. 동일 원문 구간에서 둘의 연결을 확인할 수 없으면 해당 포지션은 `review`로 남기며, 다른 포지션의 신입 조건을 빌려 `eligible`로 만들지 않는다. 현재는 한 URL에서 관련 포지션 하나를 대표 판정하며, 포지션별 여러 DB 레코드 발행은 후속 단계다.

`신입 우대`처럼 필수 지원 자격으로 확정할 수 없는 문구는 `eligible` 근거로 사용하지 않는다. 여러 포지션에 공통 적용되는 경력은 원문에서 공통 범위와 필수 경력을 함께 확인한 경우에만 각 포지션에 적용한다. 사람인의 상세 본문에 상한을 넘는 필수 경력 문구가 있는데 바깥 공고 정보에서는 신입으로 추출되면 자동 적합 처리하지 않고 `review`로 둔다. Verifier 로그에는 경력 인용의 원본·검증 여부·출처 구간과 포지션별 적용 근거를 남긴다.

접수기간에 날짜가 두 개 있으면 끝 날짜만 마감일로 읽고, 시작일만 있거나 `~ 채용시`이면 마감일을 추정하지 않는다. 학력의 `4년제`는 경력 연수로 읽지 않는다. `신입 또는 경력 5년 이내`는 신입도 지원 가능한 조건으로 해석한다. 복수 포지션의 공통 경력이 LLM 출력에서 빠지더라도, 상세 본문에 공통 적용 범위와 경력 문구가 가까이 명시된 경우에만 검증된 공통 근거로 보강한다.

Verifier는 Extractor의 마감일 인용 대조 외에 원문에서 `마감일`에 붙은 날짜와 접수기간의 종료 날짜를 독립적으로 찾는다. 추출 날짜가 빠졌고 명시적 날짜가 하나면 근거를 등록해 보완한다. `채용시 마감`과 고정 날짜가 공존하거나 여러 고정 날짜가 충돌하면 `closes_at=conflict`, 사용자 판정 `review`로 남기고 하나를 임의로 확정하지 않는다. 단순히 원문에 등장하는 다른 날짜는 마감일로 간주하지 않는다.
`신입~N년`과 `경력 신입~N년`도 N년을 최소 경력이 아닌 범위의 상한으로 보고 최소 요구 경력을 0년으로 기록한다. Company Enricher는 검색 시작·캐시 재사용·검색 출처 수·채택 필드·미채택 이유·파싱 오류를 진단 로그로 남긴다.

### Lifecycle / Job Persister

`content_hash` v2는 잡코리아·사람인·인크루트처럼 상세 본문과 플랫폼 페이지 정보를 병합한 원문에서 상세 본문만 분리해 계산한다. 분리되지 않은 일반 원문에서는 조회수 같은 변동 숫자를 제거한 뒤 공백을 정규화한다. 기존 v1 해시는 전체 페이지 기준이므로 v2와 직접 비교하지 않는다. 첫 재실행에서 추출 필드가 달라도 `changed`를 만들지 않고 기존 필드를 보존하면서 v2 해시로 이전한다. 필드 차이는 검토 대상으로 남긴다. Lifecycle 진단에는 비교한 기존·새 해시와 차이 난 필드의 이전·새 값을 남긴다.

동일한 v2 원문 해시에서 추출된 회사명·직무명·경력·근무지 등의 문자열만 달라졌다면 원문 변경이 아니라 추출 흔들림으로 취급한다. `changed`를 발행하지 않고 `LifecycleResult.review_fields`에 기록하며 기존 구조화 값을 보존한다. 원문 해시가 달라졌을 때는 비교 전 고용형태·경력 등 명확한 동의 표현만 규칙으로 정규화한다. 원문 값과 근거는 수정하지 않는다. Job Persister는 실제 변경으로 확인된 필드만 새 추출값으로 갱신한다.

Lifecycle은 추천 적합도인 `eligible/excluded/review`와 독립된 `LifecycleStatus(active/changed/closed/unavailable/unknown)`를 판정한다. 접근 실패 한 번은 곧바로 `closed`로 바꾸지 않는다. 명시적 마감 근거 또는 지난 마감일이 있을 때만 `closed`로 판단한다. 접근 가능하고 현재 모집 근거가 있을 때 기존 공고의 content_hash와 새 원문 hash를 비교해 `changed` 또는 `active`로 판정한다. 근거가 부족하면 `unknown`이다. 현재 그래프는 Iterator 다음에 SQLite에서 기존 공고를 조회하고, 정상 상세 공고에서 `Verifier → Lifecycle → Job Persister → Iterator`를 실행한다. Job Persister는 추적 매개변수를 제거한 canonical URL로 중복을 제거하고, Evidence·최초 발견 시각과 공고 ID를 보존한다. `unknown/unavailable`은 기존 정상 기록을 덮어쓰지 않는다. 사용자별 `excluded`도 공통 공고 DB에는 저장할 수 있지만 `should_publish=false`로 사용자 피드에는 노출하지 않는다. Navigator 접근 실패나 Extractor 실패는 현재 Lifecycle/Persister를 건너뛰므로, 이 경로의 `unavailable` 기록은 후속 과제다. `recommended`/`rejected`는 공고 상태가 아니다.

### Ranker / Feed Publisher

Ranker는 사용자가 명시한 강한 조건을 우선 적용한다. PreferenceProfile의 가중치는 순위만 바꾸며 자동 제외 조건이 되지 않는다. 신규 사용자에게는 검색 조건과 직무 관련성으로 초기 점수를 준다. 점수·이유·사용한 프로필 버전을 남긴다. 피드는 일부 탐색용 결과를 포함해 선호 편향이 굳어지는 것을 막는다.

현재 MVP는 수집 실행의 `ScreeningResult[]`를 별도 개인화 그래프에 전달한다. Ranker는 공통 Job DB의 현재 공고를 다시 조회하고 `excluded`, 마감/불명 상태, 명시적 지역·고용형태·경력·제외 조건 밖의 공고를 제거한다. `review`는 이유와 함께 남긴다. `RankerConfig`의 규칙 계수로 0~1 상대 점수를 만들며, 선호 효과는 상한을 둔 순위 신호다. Feed Publisher는 결과를 다시 검증해 `user_feeds` 테이블에 사용자별 순서·점수·검토 이유·프로필 버전을 저장한다. 내용이 같으면 피드 버전을 올리지 않는다. 최소 5건 중 하나는 명시 조건을 통과한 낮은 순위 공고를 탐색용으로 배치한다. 피드백 이후의 비동기 재랭킹을 위해 사용자별 `ScreeningResult` 영속화는 후속 단계다.

### HITL / Preference Updater

피드백 결정은 `interested`, `hold`, `not_interested`이며 이유는 복수 선택과 구체값을 허용한다. Feedback Store는 원본 피드백을 보존한다. Updater는 동일 이유·특징의 누적 빈도와 신뢰도를 반영한다. 한 번의 클릭으로 사용자의 취향을 확정하거나 검색 조건을 변경하지 않는다. `hold`는 0 또는 매우 약한 신호다.

## 실행 경로

공통 수집: Planner → Searcher → Iterator → Navigator; 상세 공고는 Extractor → Company Enricher → Verifier → Lifecycle → Job Persister → Iterator, 목록/종류 불명은 상세 URL 후보 발견 → Iterator로 돌아간다.

개인화: Job DB → Ranker → Feed Publisher.

피드백: HITL → Feedback Store → Preference Updater → Ranker → Feed Publisher.
