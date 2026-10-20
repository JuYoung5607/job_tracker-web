import json
import os
import sqlite3
import unittest
from datetime import datetime, timezone
from pathlib import Path


DB_PATH = Path(__file__).with_name("test.sqlite3")
os.environ["DATABASE_PATH"] = str(DB_PATH)

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from nodes.job_persister_node import SQLiteJobStore  # noqa: E402
from schemas.candidate import JobPosting  # noqa: E402
from schemas.lifecycle import LifecycleStatus  # noqa: E402
from schemas.screening import ScreeningDecision, ScreeningResult  # noqa: E402


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()
        with sqlite3.connect(DB_PATH) as connection:
            for table in ("sessions", "job_feedback", "correction_requests", "users",
                          "user_search_conditions", "user_feeds", "screening_results", "jobs"):
                exists = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone()
                if exists:
                    connection.execute(f"DELETE FROM {table}")

    def tearDown(self):
        self.client_context.__exit__(None, None, None)

    def register(self):
        response = self.client.post("/api/auth/register", json={
            "email": "person@example.com", "password": "correct-horse-123"
        })
        self.assertEqual(response.status_code, 201)
        return response.json()

    def test_register_session_logout_and_login(self):
        user = self.register()
        self.assertEqual(self.client.get("/api/auth/me").json()["user_id"], user["user_id"])
        self.assertEqual(self.client.post("/api/auth/logout").status_code, 204)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)
        self.assertEqual(self.client.post("/api/auth/login", json={
            "email": "person@example.com", "password": "wrong-password"
        }).status_code, 401)
        self.assertEqual(self.client.post("/api/auth/login", json={
            "email": "person@example.com", "password": "correct-horse-123"
        }).status_code, 200)

    def test_feed_hides_excluded_and_accepts_feedback_and_correction(self):
        user = self.register()
        with sqlite3.connect(DB_PATH) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS jobs(canonical_url TEXT PRIMARY KEY, job_id TEXT UNIQUE, payload TEXT)")
            connection.execute("CREATE TABLE IF NOT EXISTS user_feeds(user_id TEXT PRIMARY KEY, payload TEXT)")
            connection.execute("INSERT INTO jobs VALUES(?,?,?)", (
                "https://jumpit.saramin.co.kr/position/1", "job-1", json.dumps({
                    "job_id": "job-1", "source_url": "https://jumpit.saramin.co.kr/position/1",
                    "location": ["서울"], "experience_level": "신입",
                    "employment_type": "정규직", "lifecycle_status": "active"
                }, ensure_ascii=False)
            ))
            items = [
                {"job_id": "job-1", "company_name": "테스트", "job_title": "AI Engineer",
                 "source_url": "https://jumpit.saramin.co.kr/position/1", "score": .9,
                 "reasons": ["AI 직무"], "screening_decision": "eligible", "review_reasons": []},
                {"job_id": "job-x", "company_name": "제외", "job_title": "영업",
                 "source_url": "https://example.com/x", "score": .2, "reasons": [],
                 "screening_decision": "excluded", "review_reasons": []}
            ]
            connection.execute("INSERT INTO user_feeds VALUES(?,?)", (
                user["user_id"], json.dumps({"version": 1, "generated_at": None, "items": items})
            ))

        feed = self.client.get("/api/jobs")
        self.assertEqual(feed.status_code, 200)
        self.assertEqual(len(feed.json()["items"]), 1)
        self.assertEqual(feed.json()["items"][0]["source_name"], "점핏")
        feedback = self.client.put("/api/jobs/job-1/feedback", json={
            "decision": "interested", "reasons": ["job"], "comment": None
        })
        self.assertEqual(feedback.status_code, 200)
        report = self.client.post("/api/correction-requests", json={
            "job_id": "job-1", "source_url": "https://jumpit.saramin.co.kr/position/1",
            "request_type": "correction", "message": "지역 정보를 확인해 주세요."
        })
        self.assertEqual(report.status_code, 201)

    def test_search_conditions_generate_ranked_feed(self):
        user = self.register()
        now = datetime.now(timezone.utc)
        store = SQLiteJobStore(DB_PATH)
        store.save(JobPosting(
            job_id="rank-job-1", company_name="예시테크", job_title="AI Engineer",
            employment_type="정규직", experience_level="신입", location=["서울"],
            source_url="https://www.jobkorea.co.kr/Recruit/GI_Read/12345678",
            source_urls=["https://www.jobkorea.co.kr/Recruit/GI_Read/12345678"],
            lifecycle_status=LifecycleStatus.ACTIVE, content_hash="a" * 64,
            first_seen_at=now, last_seen_at=now, created_at=now, updated_at=now,
        ))
        store.save_screening(ScreeningResult(
            candidate_id="candidate-1",
            source_url="https://www.jobkorea.co.kr/Recruit/GI_Read/12345678",
            decision=ScreeningDecision.ELIGIBLE,
            reasons=["모집 상태·요구 경력·직무가 현재 검색 조건에 맞습니다."],
            minimum_experience_years=0,
        ))
        response = self.client.put("/api/me/search-conditions", json={
            "target_jobs": ["AI Engineer"], "skills": ["Python"],
            "exclusion_conditions": [], "locations": ["서울"],
            "experience_levels": ["entry", "junior"], "max_experience_years": 3,
            "employment_types": ["정규직"], "preferred_source_domains": ["jobkorea.co.kr"]
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["visible_job_count"], 1)
        conditions = self.client.get("/api/me/search-conditions")
        self.assertEqual(conditions.status_code, 200)
        self.assertEqual(conditions.json()["target_jobs"], ["AI Engineer"])
        feed = self.client.get("/api/jobs").json()
        self.assertEqual(len(feed["items"]), 1)
        self.assertEqual(feed["items"][0]["job_id"], "rank-job-1")
        self.assertEqual(feed["items"][0]["source_name"], "잡코리아")


if __name__ == "__main__":
    unittest.main()
