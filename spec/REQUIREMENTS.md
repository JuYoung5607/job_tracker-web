# REQUIREMENTS — 기능·비기능 요구사항 (v2)

## 범위와 용어

본 문서는 폭넓은 수집 + 비동기 HITL + 점진적 개인화 MVP의 수용 기준이다. SearchConditions는 사용자가 직접 입력한 강한 범위, PreferenceProfile은 행동에서 추론한 약한 선호다. JobPosting의 lifecycle과 사용자별 RankResult는 별개다.

## 기능 요구사항

### FR-01 검색 조건

사용자는 목표 직무, 기술, 경력, 지역, 고용형태 및 명시적 제외 조건을 입력·수정할 수 있어야 한다. 학습된 선호가 이 값을 자동 수정해서는 안 된다. 조건 변경은 다음 수집/랭킹에 반영한다.

### FR-02 넓은 수집

Searcher는 SearchConditions와 SearchBudget을 사용해 네 채용 플랫폼의 현재 목록 조회 예산을 배분한다. 플랫폼별 수집기는 실제 출처 URL만 Candidate로 반환하고 검색어·track·발견 시각을 기록한다. PreferenceProfile의 부정 가중치 때문에 명시적 검색 범위를 줄이지 않는다. 최대 호출·후보·시간 예산을 지킨다. 목록 URL은 상세 공고로 바로 저장하지 않는다.

### FR-03 후보 처리

Candidate Iterator는 `candidate_cursor=0`부터 큐의 한 후보를 선택하고, 처리 완료/실패 후에만 커서를 증가시킨다. 다음 후보 전에 후보별 State를 초기화한다. Navigator는 상세 공고의 접근 상태와 본문을 보존하고, 목록/종류 불명 페이지에서는 상세 URL을 새 Candidate로 반환한다. 새 후보는 원본 목록과 확장 깊이를 추적하며 중복 제거 후 큐 뒤에 추가한다. 목록→목록 재귀 확장은 MVP에서 금지한다. 검색 및 목록 확장을 합한 전체 고유 후보 수는 `SearchBudget.max_candidates` 이하여야 한다. 목록 페이지를 Extractor·Job Persister로 보내지 않는다. Extractor는 구조화된 초안과 필드별 Evidence를 만든다. Company Enricher 실패는 비차단이다. Verifier는 제목·경력·마감일·고용형태·지역의 근거와 충돌을 검증한다.

### FR-04 공고 저장·상태

Lifecycle은 기존 공고와 비교해 active/changed/closed/unavailable/unknown을 결정한다. 단발성 접근 실패만으로 closed가 되면 안 된다. Job Persister는 canonical URL과 식별자 기준으로 멱등 upsert하고 content_hash, first_seen_at, last_seen_at, 버전, 근거를 관리한다. 검증되지 않은 값으로 기존 정상 정보를 덮어쓰지 않는다. 공고 상태에 recommended/rejected를 저장하지 않는다.

### FR-05 사용자별 랭킹과 피드

Ranker는 명시적 강한 조건을 우선하고, 통과한 공고에 직무 관련성과 PreferenceProfile을 반영한 0~1 상대 점수를 준다. 점수는 확률이 아니다. 신규 사용자는 명시적 조건 기반 피드를 받는다. Feed Publisher는 사용자별 순서, 점수의 주요 이유, 데이터 확인 상태, 일부 탐색용 결과를 제공한다. 낮은 점수만으로 공고를 공통 DB에서 삭제하지 않는다.

### FR-06 HITL 피드백

사용자는 노출된 공고에 interested/hold/not_interested 중 하나를 선택하고 여러 이유를 고를 수 있다. 이유는 company/job/skill/experience/location/employment_type/other 중 하나와 선택적 구체값을 가진다. 선택적 자유 의견과 시각을 기록한다. 피드백은 수집 그래프 종료 후에도 제출 가능하다.

### FR-07 피드백 저장·선호 갱신

Feedback Store는 user_id, job_id, decision, reasons, comment, created_at, idempotency_key를 검증·저장한다. 중복 요청은 중복 반영하지 않는다. 선택 변경 이력을 추적한다. Preference Updater는 이유가 지정된 특징에만 제한된 양·음 가중치를 주고 누적 횟수·신뢰도·최근성을 반영한다. hold는 중립 또는 매우 약한 신호다. 단일 피드백으로 확정적 비선호나 hard exclusion을 만들지 않는다. 저장 성공 후 해당 사용자만 재랭킹한다.

### FR-08 사용자 제어

사용자는 명시적 검색 조건과 학습된 선호를 구분해 볼 수 있어야 한다. 잘못 학습된 선호를 수정·초기화할 수 있어야 한다. 피드백 정정·삭제 요청을 처리할 수 있어야 한다. 공고 데이터와 사용자 행동 데이터는 별도로 저장한다.

## 비기능 요구사항

- 검증 가능성: 주요 공고 필드와 상태 전환에는 URL·근거·수집/확인 시각을 연결한다.
- 안정성: 검색/접근/LLM/DB 오류를 단계별로 기록하고 예산 안에서만 재시도한다. Company Enricher 오류는 비차단이다.
- 보안·개인정보: 사용자 인증, user_id별 접근 통제, 피드백/프로필 삭제·보존 정책, 웹 콘텐츠 프롬프트 인젝션 방어를 적용한다.
- 비용: 검색 및 LLM 호출 상한을 두고 중복 URL·중복 본문 처리를 줄인다.
- 재현성: SearchConditions·SearchBudget, 사용 모델/프롬프트 버전, Evidence, Ranker 설정, PreferenceProfile 버전과 결과를 추적한다.
- 확장성: 후보별 순차 처리에서 시작하되 이후 독립 후보를 병렬화할 수 있게 상태를 격리한다.

## 수용 테스트

1. 신규 사용자는 피드백 없이도 명시적 조건에 맞는 다양한 공고를 본다.
2. 목록 페이지 URL만 확인된 경우 JobPosting으로 확정 저장되지 않는다.
3. 임베디드 AI에 관심을 표시해도 다음 검색에서 일반 AI 공고가 자동 배제되지 않는다.
4. 계약직 공고에 not_interested + employment_type=계약직을 남기면 해당 사용자의 계약직 순위가 낮아진다. 다른 사용자의 피드는 변하지 않는다.
5. 관심 이유가 job와 skill 둘 다이면 각각의 category/value가 저장된다.
6. 한 번의 not_interested로 SearchConditions나 hard exclusion이 변하지 않는다.
7. hold는 큰 가중치 변화를 만들지 않는다.
8. 같은 피드백 요청을 두 번 보내도 선호 가중치는 한 번만 반영된다.
9. 접속 일시 실패는 기존 active 공고를 즉시 closed로 바꾸지 않는다.
10. 검증 충돌이 있는 핵심 필드는 공개 대기/수동 검토로 분기한다.
11. 공고 lifecycle 변경과 사용자별 랭킹 변경이 서로 독립적으로 기록된다.
12. 사용자 피드백을 정정/삭제하고 프로필을 재계산할 수 있다.
13. Iterator는 선택 시 커서를 올리지 않고 처리 완료 후 증가시키며, 후보별 필드를 초기화한다.
14. 목록에서 찾은 상세 URL은 중복·전체 상한을 지키며 한 단계만 확장된다. 목록 자체는 공고로 저장되지 않는다.

## 구현 순서

1. 공통 공고 데이터 모델 및 기존 수집 파이프라인 계약 정리.
2. Lifecycle → Job Persister와 상세 URL 확인·검증.
3. 사용자별 RankResult와 규칙 기반 Ranker·Feed.
4. JobFeedback 저장, 이유 입력 UI, Preference Updater, 재랭킹.
5. 품질 지표와 사용자 제어·운영 검토, 이후 가중치 튜닝.

현 코드의 `MatchDecision`, `CritiqueResult`, `ProfileChunk` 등은 이 목표 명세와 아직 다를 수 있다. 본 문서는 설계 목표이며 코드 구현 완료를 뜻하지 않는다.

