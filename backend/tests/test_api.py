import json
import os
import sqlite3
import sys
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace


DB_PATH = Path(__file__).with_name("test.sqlite3")
os.environ["DATABASE_PATH"] = str(DB_PATH)

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.personalization import load_screenings_for_user  # noqa: E402
from nodes.job_store import SQLiteJobStore  # noqa: E402
from schemas.candidate import JobPosting  # noqa: E402
from schemas.lifecycle import LifecycleStatus  # noqa: E402
from schemas.screening import ScreeningDecision, ScreeningResult  # noqa: E402


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()
        with sqlite3.connect(DB_PATH) as connection:
            for table in ("sessions", "job_feedback", "correction_requests", "preference_profiles",
                          "search_runs",
                          "user_screening_results", "users",
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
        now = datetime.now(timezone.utc)
        store = SQLiteJobStore(DB_PATH)
        store.save(JobPosting(
            job_id="job-1", company_name="테스트", job_title="AI Engineer",
            employment_type="정규직", experience_level="신입", location=["서울"],
            source_url="https://jumpit.saramin.co.kr/position/1",
            source_urls=["https://jumpit.saramin.co.kr/position/1"],
            lifecycle_status=LifecycleStatus.ACTIVE, content_hash="b" * 64,
            first_seen_at=now, last_seen_at=now, created_at=now, updated_at=now,
        ))
        with sqlite3.connect(DB_PATH) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS user_feeds(user_id TEXT PRIMARY KEY, payload TEXT)")
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
        with sqlite3.connect(DB_PATH) as connection:
            payload = connection.execute(
                "SELECT payload FROM preference_profiles WHERE user_id=?", (user["user_id"],)
            ).fetchone()[0]
        profile = json.loads(payload)
        self.assertEqual(profile["features"][0]["value"], "AI Engineer")
        self.assertEqual(profile["features"][0]["positive_count"], 1)
        # 같은 현재 선택을 다시 저장해도 이벤트처럼 두 번 누적하지 않는다.
        self.client.put("/api/jobs/job-1/feedback", json={
            "decision": "interested", "reasons": ["job"], "comment": None
        })
        with sqlite3.connect(DB_PATH) as connection:
            repeated = json.loads(connection.execute(
                "SELECT payload FROM preference_profiles WHERE user_id=?", (user["user_id"],)
            ).fetchone()[0])
        self.assertEqual(repeated["features"][0]["positive_count"], 1)
        report = self.client.post("/api/correction-requests", json={
            "job_id": "job-1", "source_url": "https://jumpit.saramin.co.kr/position/1",
            "request_type": "correction", "message": "지역 정보를 확인해 주세요."
        })
        self.assertEqual(report.status_code, 201)

    def test_search_run_is_user_scoped_and_rejects_duplicate_start(self):
        self.register()
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}), \
             patch("dotenv.load_dotenv", return_value=False):
            self.assertEqual(self.client.post("/api/search-runs").status_code, 503)
        self.assertEqual(self.client.put("/api/me/search-conditions", json={
            "target_jobs": ["AI Engineer"], "locations": ["서울"],
        }).status_code, 200)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-only"}), \
             patch("dotenv.load_dotenv", return_value=False), \
             patch("app.search_runs.run_search", return_value=None):
            started = self.client.post("/api/search-runs")
            self.assertEqual(started.status_code, 202)
            run_id = started.json()["run_id"]
            self.assertEqual(started.json()["status"], "queued")
            self.assertEqual(self.client.get("/api/search-runs/latest").json()["run"]["run_id"], run_id)
            self.assertEqual(self.client.get(f"/api/search-runs/{run_id}").status_code, 200)
            self.assertEqual(self.client.post("/api/search-runs").status_code, 409)

        class EmptyGraph:
            def stream(self, *_args, **_kwargs):
                yield {"searcher_node": {"candidates": []}}
                yield {"verifier_node": {"screening_result": ScreeningResult(
                    candidate_id="candidate-test",
                    source_url="https://www.jobkorea.co.kr/Recruit/GI_Read/99999999",
                    decision=ScreeningDecision.ELIGIBLE,
                    reasons=["테스트 판정"], minimum_experience_years=0,
                )}}

        from app.search_runs import run_search

        fake_graph = SimpleNamespace(build_workflow=lambda **_kwargs: EmptyGraph())
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-only"}), \
             patch.dict(sys.modules, {"graph": fake_graph}):
            run_search(run_id, self.client.get("/api/auth/me").json()["user_id"])
        completed = self.client.get(f"/api/search-runs/{run_id}").json()
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["discovered_count"], 0)
        self.assertEqual(completed["eligible_count"], 1)
        with sqlite3.connect(DB_PATH) as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM user_screening_results WHERE user_id=?",
                (self.client.get("/api/auth/me").json()["user_id"],),
            ).fetchone()[0], 1)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM screening_results WHERE canonical_url LIKE '%99999999'"
            ).fetchone()[0], 0)

    def test_new_label_uses_latest_completed_feed_delta(self):
        user = self.register()
        self.assertEqual(self.client.put("/api/me/search-conditions", json={
            "target_jobs": ["AI Engineer"], "locations": ["서울"],
        }).status_code, 200)
        store = SQLiteJobStore(DB_PATH)
        now = datetime.now(timezone.utc)
        for job_id in ("old-job", "new-job"):
            url = f"https://example.com/jobs/{job_id}"
            store.save(JobPosting(
                job_id=job_id, company_name="테스트", job_title="AI Engineer",
                source_url=url, source_urls=[url], location=["서울"],
                lifecycle_status=LifecycleStatus.ACTIVE, content_hash="e" * 64,
                first_seen_at=now, last_seen_at=now, created_at=now, updated_at=now,
            ))

        def feed_item(job_id):
            return {"job_id": job_id, "company_name": "테스트", "job_title": "AI Engineer",
                    "source_url": f"https://example.com/jobs/{job_id}", "score": .8,
                    "reasons": ["AI 직무"], "screening_decision": "eligible",
                    "review_reasons": []}

        def save_feed(ids):
            payload = json.dumps({"version": 1, "generated_at": None,
                                  "items": [feed_item(job_id) for job_id in ids]})
            with sqlite3.connect(DB_PATH) as connection:
                connection.execute(
                    "INSERT INTO user_feeds(user_id,payload) VALUES(?,?) "
                    "ON CONFLICT(user_id) DO UPDATE SET payload=excluded.payload",
                    (user["user_id"], payload),
                )

        save_feed(["old-job"])
        from app.search_runs import create_run, get_run, run_search
        run_id = create_run(user["user_id"])["run_id"]

        class EmptyGraph:
            def stream(self, *_args, **_kwargs):
                yield {"searcher_node": {"candidates": []}}

        def refresh(_user_id):
            save_feed(["old-job", "new-job"])
            return 2

        fake_graph = SimpleNamespace(build_workflow=lambda **_kwargs: EmptyGraph())
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-only"}), \
             patch.dict(sys.modules, {"graph": fake_graph}), \
             patch("app.search_runs.refresh_user_feed", side_effect=refresh):
            run_search(run_id, user["user_id"])
        self.assertEqual(json.loads(get_run(run_id, user["user_id"])["new_job_ids"]),
                         ["new-job"])
        items = {item["job_id"]: item for item in self.client.get("/api/jobs").json()["items"]}
        self.assertFalse(items["old-job"]["is_new"])
        self.assertTrue(items["new-job"]["is_new"])

    def test_expired_job_is_hidden_but_kept_in_database(self):
        user = self.register()
        store = SQLiteJobStore(DB_PATH)
        now = datetime.now(timezone.utc)
        base = {
            "company_name": "테스트", "job_title": "AI Engineer",
            "employment_type": "정규직", "experience_level": "신입",
            "location": ["서울"], "lifecycle_status": LifecycleStatus.ACTIVE,
            "content_hash": "c" * 64, "first_seen_at": now, "last_seen_at": now,
            "created_at": now, "updated_at": now,
        }
        expired_url = "https://example.com/jobs/expired"
        active_url = "https://example.com/jobs/active"
        store.save(JobPosting(**base, job_id="expired-job", source_url=expired_url,
                              source_urls=[expired_url], closes_at=now - timedelta(days=2)))
        store.save(JobPosting(**base, job_id="active-job", source_url=active_url,
                              source_urls=[active_url], closes_at=now + timedelta(days=2)))
        for url in (expired_url, active_url):
            store.save_screening(ScreeningResult(
                candidate_id=url, source_url=url, decision=ScreeningDecision.ELIGIBLE,
                reasons=["테스트 판정"], minimum_experience_years=0,
            ))
        response = self.client.put("/api/me/search-conditions", json={
            "target_jobs": ["AI Engineer"], "locations": ["서울"],
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["visible_job_count"], 1)
        self.assertEqual([item["job_id"] for item in self.client.get("/api/jobs").json()["items"]],
                         ["active-job"])
        expired = store.get(expired_url)
        self.assertEqual(expired.lifecycle_status, LifecycleStatus.CLOSED)
        self.assertIsNotNone(expired.closes_at)

    def test_deadline_remains_open_through_closing_day_in_korea(self):
        store = SQLiteJobStore(DB_PATH)
        url = "https://example.com/jobs/closes-today"
        close_at = datetime(2026, 10, 26, 0, 0, tzinfo=timezone(timedelta(hours=9)))
        store.save(JobPosting(
            job_id="closes-today", company_name="테스트", job_title="AI Engineer",
            source_url=url, source_urls=[url], closes_at=close_at,
            lifecycle_status=LifecycleStatus.ACTIVE, content_hash="d" * 64,
            first_seen_at=close_at, last_seen_at=close_at,
            created_at=close_at, updated_at=close_at,
        ))
        before_midnight = datetime(2026, 10, 26, 14, 59, 59, tzinfo=timezone.utc)
        self.assertEqual(store.close_past_deadline(before_midnight), 0)
        self.assertEqual(store.get(url).lifecycle_status, LifecycleStatus.ACTIVE)
        self.assertEqual(store.close_past_deadline(before_midnight + timedelta(seconds=1)), 1)
        self.assertEqual(store.get(url).lifecycle_status, LifecycleStatus.CLOSED)
        self.assertEqual(store.close_past_deadline(before_midnight + timedelta(seconds=1)), 0)

    def test_daily_search_quota_refunds_only_before_external_request(self):
        user = self.register()
        self.assertEqual(self.client.put("/api/me/search-conditions", json={
            "target_jobs": ["AI Engineer"], "locations": ["서울"],
        }).status_code, 200)
        from app.search_runs import create_run, get_daily_quota

        self.assertEqual(get_daily_quota(user["user_id"])["remaining"], 1)
        first = create_run(user["user_id"])
        self.assertEqual(get_daily_quota(user["user_id"])["remaining"], 0)
        with self.assertRaises(RuntimeError):
            create_run(user["user_id"])

        with sqlite3.connect(DB_PATH) as connection:
            connection.execute("UPDATE search_runs SET status='failed' WHERE run_id=?",
                               (first["run_id"],))
        self.assertEqual(get_daily_quota(user["user_id"])["remaining"], 1)
        second = create_run(user["user_id"])
        with sqlite3.connect(DB_PATH) as connection:
            connection.execute(
                "UPDATE search_runs SET status='failed',external_started_at=? WHERE run_id=?",
                (datetime.now(timezone.utc).isoformat(), second["run_id"]),
            )
        self.assertEqual(self.client.get("/api/search-runs/quota").json()["remaining"], 0)
        with self.assertRaises(RuntimeError):
            create_run(user["user_id"])

    def test_daily_search_quota_resets_at_korean_midnight(self):
        user = self.register()
        self.assertEqual(self.client.put("/api/me/search-conditions", json={
            "target_jobs": ["AI Engineer"], "locations": ["서울"],
        }).status_code, 200)
        from app.search_runs import create_run, get_daily_quota

        before = datetime(2026, 10, 23, 14, 59, 59, tzinfo=timezone.utc)
        with patch("app.search_runs._now", return_value=before):
            create_run(user["user_id"])
            quota = get_daily_quota(user["user_id"])
        self.assertEqual(quota["remaining"], 0)
        self.assertEqual(quota["next_available_at"], "2026-10-24T00:00:00+09:00")

        with sqlite3.connect(DB_PATH) as connection:
            connection.execute("UPDATE search_runs SET status='completed'")
        with patch("app.search_runs._now", return_value=before + timedelta(seconds=1)):
            self.assertEqual(get_daily_quota(user["user_id"])["remaining"], 1)
            self.assertEqual(create_run(user["user_id"])["status"], "queued")

    def test_failed_search_records_external_start_from_graph_callback(self):
        user = self.register()
        self.assertEqual(self.client.put("/api/me/search-conditions", json={
            "target_jobs": ["AI Engineer"], "locations": ["서울"],
        }).status_code, 200)
        from app.search_runs import create_run, get_daily_quota, get_run, run_search

        run_id = create_run(user["user_id"])["run_id"]

        class FailingGraph:
            def stream(self, *_args, **_kwargs):
                callback()
                raise RuntimeError("test failure after request start")
                yield  # pragma: no cover

        def build_workflow(**kwargs):
            nonlocal callback
            callback = kwargs["on_external_start"]
            return FailingGraph()

        callback = lambda: None
        fake_graph = SimpleNamespace(build_workflow=build_workflow)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-only"}), \
             patch.dict(sys.modules, {"graph": fake_graph}):
            run_search(run_id, user["user_id"])
        record = get_run(run_id, user["user_id"])
        self.assertEqual(record["status"], "failed")
        self.assertIsNotNone(record["external_started_at"])
        self.assertEqual(get_daily_quota(user["user_id"])["remaining"], 0)

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
        scoped = ScreeningResult(
            candidate_id="candidate-1",
            source_url="https://www.jobkorea.co.kr/Recruit/GI_Read/12345678",
            decision=ScreeningDecision.EXCLUDED,
            reasons=["이 사용자 조건에는 맞지 않습니다."],
        )
        with sqlite3.connect(DB_PATH) as connection:
            connection.execute(
                "INSERT INTO user_screening_results(user_id,canonical_url,payload,verified_at) "
                "VALUES(?,?,?,?)",
                (user["user_id"], "https://jobkorea.co.kr/Recruit/GI_Read/12345678",
                 scoped.model_dump_json(), now.isoformat()),
            )
        self.assertEqual(load_screenings_for_user(user["user_id"], store)[0].decision,
                         ScreeningDecision.EXCLUDED)
        self.assertEqual(load_screenings_for_user("another-user", store)[0].decision,
                         ScreeningDecision.ELIGIBLE)
        self.assertEqual(self.client.post("/api/me/feed/refresh").json()["visible_job_count"], 0)


if __name__ == "__main__":
    unittest.main()
