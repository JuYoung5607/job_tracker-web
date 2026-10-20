# Job Tracker

여러 출처의 채용 공고를 발견·검증·개인화하고 사용자를 원문으로 연결하는 서비스입니다.
회사·직무·지역·경력·모집 상태·추천 이유와 출처 링크만 제공하며, 상세 문단·이미지·긴
인용문은 재게시하지 않습니다.

## 웹 애플리케이션

- 프론트엔드: React + TypeScript + Vite
- 백엔드: FastAPI
- 인증: 서버 저장 세션 + HttpOnly/SameSite 쿠키
- 데이터: 기존 `jobs`와 `user_feeds` SQLite 테이블에 사용자·피드백·정정 요청 테이블 추가
- 배포: Docker Compose 단일 서버 구성을 Oracle Cloud Always Free에서 실행

### 로컬 실행

```powershell
Copy-Item .env.example .env
docker compose up --build
```

브라우저에서 `http://localhost:8080`을 엽니다. 기존 파이프라인 데이터를 사용하려면
`data/jobs.sqlite3`에 DB를 두고, 수집 파이프라인의 `JOB_TRACKER_DB_PATH`도 같은 파일을
가리키게 합니다.

운영 환경에서 HTTPS를 연결한 뒤에는 `.env`의 `COOKIE_SECURE=true`를 사용해야 합니다.

### 현재 구현된 API

- `POST /api/auth/register`, `POST /api/auth/login`, `POST /api/auth/logout`
- `GET /api/auth/me`
- `GET`, `PUT /api/me/search-conditions`
- `POST /api/me/feed/refresh`
- `GET /api/jobs`
- `PUT /api/jobs/{job_id}/feedback`
- `POST /api/correction-requests`
- `GET /api/source-stats`

회원가입한 사용자는 희망 직무·기술·지역·경력·고용 형태·제외 키워드를 먼저 입력합니다.
저장된 조건으로 기존 규칙 기반 Ranker와 Feed Publisher가 실행되고, 사용자 ID별 피드가
생성됩니다. 수집 파이프라인을 이 버전으로 한 번 실행해야 기존 DB에도
`screening_results`가 저장되어 새 사용자 피드를 만들 수 있습니다.

## 현재 채용 목록 직접 탐색 실험

기본 `python run.py`는 기존 OpenAI 웹 검색 경로를 실행합니다. 아래 명령은
JobKorea·Incruit·Saramin의 현재 검색 목록과 Jumpit 포지션 검색을 하나의
검색 예산으로 실행합니다. 수집한 개별 공고 URL은 기존
Navigator → Extractor → Verifier 경로로 처리합니다. 이 모드에서는
Planner와 OpenAI 웹 검색 도구를 호출하지 않지만 Extractor의 LLM 호출은
그대로 발생합니다.

```powershell
$env:JOB_TRACKER_DISCOVERY = "listing"
python run.py
```

`JOB_TRACKER_DISCOVERY`를 지우거나 `web_search`로 설정하면 기존 경로로 돌아갑니다.
`jumpit`으로 설정하면 점핏만 별도로 비교 실험할 수 있습니다. 접근이 막힌
JobPlanet은 직접 목록 수집 대상에서 제외했습니다. 목록에 표시됐다는 사실만으로
모집 중이라고 확정하지 않으며, 상세 페이지의 마감일·모집 상태를 Verifier가
다시 확인합니다.
현재 `eligible` 판정은 모집 상태와 요구 경력에 대한 1차 검증이며,
직무·기업·지역까지 검증한 최종 추천 결과가 아닙니다.
