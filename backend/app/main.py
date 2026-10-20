import json
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import Cookie, Depends, FastAPI, HTTPException, Response, status

from .config import settings
from .database import connect, initialize_database, table_exists
from .personalization import load_search_conditions, refresh_user_feed
from .schemas import (
    CorrectionRequestInput,
    Credentials,
    FeedbackInput,
    SearchConditionsInput,
    UserView,
)
from .security import hash_password, hash_session_token, new_session_token, verify_password


SESSION_COOKIE = "job_tracker_session"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield


app = FastAPI(title="Job Tracker API", version="0.1.0", lifespan=lifespan)


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_days * 24 * 60 * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )


def current_user(session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE)) -> UserView:
    if not session_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="로그인이 필요합니다.")
    now = iso_now()
    with connect() as connection:
        row = connection.execute(
            "SELECT u.user_id, u.email FROM sessions s "
            "JOIN users u ON u.user_id=s.user_id "
            "WHERE s.token_hash=? AND s.expires_at>?",
            (hash_session_token(session_token), now),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="세션이 만료되었습니다.")
    return UserView(user_id=row["user_id"], email=row["email"])


def create_session(connection: sqlite3.Connection, user_id: str) -> str:
    token = new_session_token()
    now = utc_now()
    connection.execute(
        "INSERT INTO sessions(session_id,user_id,token_hash,expires_at,created_at) VALUES(?,?,?,?,?)",
        (str(uuid4()), user_id, hash_session_token(token),
         (now + timedelta(days=settings.session_days)).isoformat(), now.isoformat()),
    )
    return token


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/auth/register", response_model=UserView, status_code=status.HTTP_201_CREATED)
def register(credentials: Credentials, response: Response):
    user_id = str(uuid4())
    try:
        with connect() as connection:
            connection.execute(
                "INSERT INTO users(user_id,email,password_hash,created_at) VALUES(?,?,?,?)",
                (user_id, credentials.email, hash_password(credentials.password), iso_now()),
            )
            token = create_session(connection, user_id)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="이미 가입된 이메일입니다.")
    set_session_cookie(response, token)
    return UserView(user_id=user_id, email=credentials.email)


@app.post("/api/auth/login", response_model=UserView)
def login(credentials: Credentials, response: Response):
    with connect() as connection:
        row = connection.execute(
            "SELECT user_id,email,password_hash FROM users WHERE email=?", (credentials.email,)
        ).fetchone()
        if row is None or not verify_password(credentials.password, row["password_hash"]):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                detail="이메일 또는 비밀번호가 올바르지 않습니다.")
        connection.execute("DELETE FROM sessions WHERE expires_at<=?", (iso_now(),))
        token = create_session(connection, row["user_id"])
    set_session_cookie(response, token)
    return UserView(user_id=row["user_id"], email=row["email"])


@app.post("/api/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response, session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    if session_token:
        with connect() as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash=?",
                               (hash_session_token(session_token),))
    response.delete_cookie(SESSION_COOKIE, path="/")


@app.get("/api/auth/me", response_model=UserView)
def me(user: UserView = Depends(current_user)):
    return user


@app.get("/api/me/search-conditions")
def get_search_conditions(user: UserView = Depends(current_user)):
    conditions = load_search_conditions(user.user_id)
    if conditions is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="검색 조건이 아직 등록되지 않았습니다.")
    return conditions


@app.put("/api/me/search-conditions")
def save_search_conditions(value: SearchConditionsInput,
                           user: UserView = Depends(current_user)):
    from schemas.search import SearchConditions

    conditions = SearchConditions.model_validate(value.model_dump())
    with connect() as connection:
        connection.execute(
            "INSERT INTO user_search_conditions(user_id,payload,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
            (user.user_id, conditions.model_dump_json(), iso_now()),
        )
    visible_count = refresh_user_feed(user.user_id)
    return {"conditions": conditions, "visible_job_count": visible_count}


@app.post("/api/me/feed/refresh")
def refresh_feed(user: UserView = Depends(current_user)):
    if load_search_conditions(user.user_id) is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="먼저 검색 조건을 등록해 주세요.")
    return {"visible_job_count": refresh_user_feed(user.user_id)}


def source_name(url: str) -> str:
    host = urlsplit(url).netloc.lower().removeprefix("www.")
    names = {
        "jumpit.saramin.co.kr": "점핏",
        "jobkorea.co.kr": "잡코리아",
        "saramin.co.kr": "사람인",
        "incruit.com": "인크루트",
    }
    return next((name for domain, name in names.items() if host.endswith(domain)), host)


@app.get("/api/jobs")
def jobs(user: UserView = Depends(current_user)):
    with connect() as connection:
        if not table_exists(connection, "user_feeds"):
            return {"version": 0, "generated_at": None, "items": []}
        row = connection.execute(
            "SELECT payload FROM user_feeds WHERE user_id=?", (user.user_id,)
        ).fetchone()
        if row is None:
            return {"version": 0, "generated_at": None, "items": []}
        feed = json.loads(row["payload"])
        feedback_rows = connection.execute(
            "SELECT job_id,decision FROM job_feedback WHERE user_id=?", (user.user_id,)
        ).fetchall()
        feedback = {item["job_id"]: item["decision"] for item in feedback_rows}
        enriched = []
        for item in feed.get("items", []):
            if item.get("screening_decision") == "excluded":
                continue
            job_row = None
            if table_exists(connection, "jobs"):
                job_row = connection.execute(
                    "SELECT payload FROM jobs WHERE job_id=?", (item.get("job_id"),)
                ).fetchone()
            job = json.loads(job_row["payload"]) if job_row else {}
            url = str(item.get("source_url") or job.get("source_url") or "")
            enriched.append({
                **item,
                "source_url": url,
                "source_name": source_name(url),
                "location": job.get("location", []),
                "experience_level": job.get("experience_level"),
                "employment_type": job.get("employment_type"),
                "recruiting_status": job.get("lifecycle_status", "unknown"),
                "closes_at": job.get("closes_at"),
                "feedback_decision": feedback.get(item.get("job_id")),
            })
    return {"version": feed.get("version", 0), "generated_at": feed.get("generated_at"),
            "items": enriched}


@app.put("/api/jobs/{job_id}/feedback")
def save_feedback(job_id: str, value: FeedbackInput, user: UserView = Depends(current_user)):
    now = iso_now()
    with connect() as connection:
        connection.execute(
            "INSERT INTO job_feedback(feedback_id,user_id,job_id,decision,reasons_json,comment,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(user_id,job_id) DO UPDATE SET "
            "decision=excluded.decision,reasons_json=excluded.reasons_json,comment=excluded.comment,"
            "updated_at=excluded.updated_at",
            (str(uuid4()), user.user_id, job_id, value.decision,
             json.dumps(value.reasons, ensure_ascii=False), value.comment, now, now),
        )
    return {"job_id": job_id, "decision": value.decision, "saved_at": now}


@app.post("/api/correction-requests", status_code=status.HTTP_201_CREATED)
def request_correction(value: CorrectionRequestInput, user: UserView = Depends(current_user)):
    request_id = str(uuid4())
    with connect() as connection:
        connection.execute(
            "INSERT INTO correction_requests(request_id,user_id,job_id,source_url,request_type,message,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (request_id, user.user_id, value.job_id, str(value.source_url),
             value.request_type, value.message, iso_now()),
        )
    return {"request_id": request_id, "status": "received"}


@app.get("/api/source-stats")
def source_stats(_: UserView = Depends(current_user)):
    with connect() as connection:
        rows = connection.execute(
            "SELECT source_name,request_count,accumulated_job_count,last_collected_at "
            "FROM source_stats ORDER BY source_name"
        ).fetchall()
    return [dict(row) for row in rows]
