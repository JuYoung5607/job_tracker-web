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

웹의 `새 공고 찾기`를 사용하려면 `.env`에 `OPENAI_API_KEY`를 설정해야 합니다.
키가 없으면 기존 피드 조회·피드백은 동작하지만 새 검색 시작은 거절됩니다.
버튼은 JobKorea·Incruit·Saramin 목록과 Jumpit 검색을 사용하며 OpenAI 웹 검색
도구는 사용하지 않습니다. 단, 공고 원문 추출에는 OpenAI 모델 호출이 발생합니다.
한 실행의 상한은 목록 호출 8회·후보 12건입니다. 사용자당 한국시간 하루 1회만
새 검색을 시작할 수 있으며 중복 실행도 막습니다. 실제 외부 요청이 시작되기 전에
실패한 실행은 횟수를 돌려주고, 요청이 시작된 뒤 실패한 실행은 당일 사용으로 셉니다.
다음 사용 가능 시각은 한국시간 다음 날 00:00입니다.
새 검색에서 발견되지 않은 기존 공고는 그대로 유지합니다. 저장된 마감일이 한국시간
기준으로 지난 공고는 DB 기록을 보존한 채 `closed`로 변경해 피드에서 숨깁니다.
마감일이 없는 공고는 검색 누락만으로 자동 마감 처리하지 않습니다.

브라우저에서 `http://localhost:8080`을 엽니다. 기존 파이프라인 데이터를 사용하려면
`data/jobs.sqlite3`에 DB를 두고, 수집 파이프라인의 `JOB_TRACKER_DB_PATH`도 같은 파일을
가리키게 합니다.

운영 환경에서 HTTPS를 연결한 뒤에는 `.env`의 `COOKIE_SECURE=true`를 사용해야 합니다.

### Docker 없이 로컬 실행

PowerShell 두 창에서 각각 실행합니다. 첫 번째 창은 API 서버입니다.

```powershell
cd C:\dev\job-tracker-web
$env:PYTHONPATH = "C:\dev\job-tracker-web;C:\dev\job-tracker-web\backend"
$env:DATABASE_PATH = "C:\dev\job-tracker-web\data\jobs.sqlite3"
.\.venv-web\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload --port 8000
```

로컬 가상환경에 새 수집 의존성이 없다면 먼저
`.\.venv-web\Scripts\python.exe -m pip install -r backend\requirements.txt`를 실행하세요.
API 서버를 켜는 PowerShell에서 `$env:OPENAI_API_KEY`를 설정하거나 프로젝트
`.env`에 입력할 수 있습니다. `.env`는 Git에 올리지 마세요.

두 번째 창은 프론트엔드입니다. Node.js와 pnpm이 설치되어 있어야 합니다.

```powershell
cd C:\dev\job-tracker-web\frontend
pnpm.cmd dev
```

`http://localhost:5173`에서 확인합니다.

### 기존 공고로 화면 확인

기존 프로젝트의 DB는 공고만 저장했고 과거 Verifier 판정은 보존하지 않았습니다.
아래 도구는 기존 DB를 읽기만 하며, 웹 DB에 없는 공고 중 마감일이 지나지 않은
공고를 가져옵니다. 모두 `검토 필요`로 표시되어 원문 재확인이 필요합니다.
OpenAI API나 웹 검색은 호출하지 않습니다.

```powershell
cd C:\dev\job-tracker-web
.\.venv-web\Scripts\python.exe -X utf8 -m backend.import_legacy_jobs `
  --source C:\dev\job_tracker\data\jobs.sqlite3 `
  --target C:\dev\job-tracker-web\data\jobs.sqlite3
```

건수를 확인한 뒤 마지막에 `--apply`를 붙이면 실제로 가져오고 사용자별 피드를
다시 계산합니다. 같은 원문 URL은 다시 가져오지 않습니다. 이후 브라우저를 새로고침하면
추천 피드가 보입니다. 현재 파이프라인을 다시 실행해 검증 결과를 저장하면 이
임시 `검토 필요` 판정은 실제 판정으로 갱신됩니다.

### 현재 구현된 API

- `POST /api/auth/register`, `POST /api/auth/login`, `POST /api/auth/logout`
- `GET /api/auth/me`
- `GET`, `PUT /api/me/search-conditions`
- `POST /api/me/feed/refresh`
- `GET /api/jobs`
- `POST /api/search-runs`, `GET /api/search-runs/latest`, `GET /api/search-runs/{run_id}`
- `PUT /api/jobs/{job_id}/feedback`
- `POST /api/correction-requests`
- `GET /api/source-stats`

회원가입한 사용자는 희망 직무·기술·지역·경력·고용 형태·제외 키워드를 먼저 입력합니다.
저장된 조건으로 기존 규칙 기반 Ranker와 Feed Publisher가 실행되고, 사용자 ID별 피드가
생성됩니다. 수집 파이프라인을 이 버전으로 한 번 실행해야 기존 DB에도
`screening_results`가 저장되어 새 사용자 피드를 만들 수 있습니다.

웹의 새 공고 검색은 기존 피드백을 기다리지 않고 완료됩니다. 진행 상황은 사용자별
`search_runs`에 저장되며, 검증 판정은 `user_screening_results`에 분리 저장한 뒤
공통 공고 DB를 갱신하고 해당 사용자 피드를 다시 계산합니다. 이 MVP는 API 프로세스의
백그라운드 작업으로 실행하므로 서버가 중간에 재시작되면 실행이 멈출 수 있습니다.
장기 운영 전에는 별도 작업 큐·워커와 재시작 복구를 추가해야 합니다.

## 현재 채용 목록 수집

웹 검색과 `python run.py`는 모두 Searcher 노드에서 수집을 시작합니다.
Searcher는 JobKorea·Incruit·Saramin의 현재 채용 목록과 Jumpit 포지션 목록을 하나의
검색 예산으로 조회하고, 중복을 제거해 실제 공고 URL 후보를 합칩니다. 수집한 후보는
Navigator → Extractor → Verifier 경로로 처리하며, Extractor의 LLM 호출은
계속 발생합니다. OpenAI 웹 검색 도구는 후보 수집에 사용하지 않습니다.

```powershell
python run.py
```

접근이 막힌 JobPlanet은 직접 목록 수집 대상에서 제외했습니다. 목록에 표시됐다는 사실만으로
모집 중이라고 확정하지 않으며, 상세 페이지의 마감일·모집 상태를 Verifier가
다시 확인합니다.
현재 `eligible` 판정은 모집 상태와 요구 경력에 대한 1차 검증이며,
직무·기업·지역까지 검증한 최종 추천 결과가 아닙니다.

### 가져온 공고의 실제 판정 갱신

이전 DB에서 가져온 공고는 검증 결과가 없어 `검토 필요`로 표시됩니다. 아래 명령은
현재 계정의 피드에서 재확인할 URL과 비용이 발생할 수 있는 Extractor 호출 수를
읽기 전용으로 보여줍니다. 현재 계정이 둘 이상이면 `--user-id`를 추가합니다.

```powershell
cd C:\dev\job-tracker-web
C:\dev\job_tracker\.venv\Scripts\python.exe -X utf8 -m backend.reverify_saved_jobs `
  --db C:\dev\job-tracker-web\data\jobs.sqlite3
```

실행을 결정한 뒤 `--limit 1 --apply`처럼 최대 처리 건수를 명시하고,
필요하면 `--env-file C:\dev\job_tracker\.env`를 붙입니다. 각 공고의 현재 원문을
다시 가져오고 Extractor를 최대 한 번 호출합니다. 목록 Searcher와 기업 웹 검색은
사용하지 않습니다. Verifier 결과는 사용자별로 저장하고 해당 계정의 피드를 갱신합니다.
페이지 접근이나 추출에 실패한 공고는 적합 판정으로 올리지 않습니다.
재검증한 공고를 다시 확인하려면 `--include-processed`를 추가합니다.
