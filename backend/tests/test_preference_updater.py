import json
import os
import sqlite3
import unittest
from datetime import datetime, timezone
from pathlib import Path


DB_PATH = Path(__file__).with_name("preference-test.sqlite3")
os.environ["DATABASE_PATH"] = str(DB_PATH)

from app.database import initialize_database  # noqa: E402
from app.preference_updater import rebuild_preference_profile  # noqa: E402
from app.config import settings  # noqa: E402
from nodes.job_store import SQLiteJobStore  # noqa: E402
from schemas.candidate import JobPosting  # noqa: E402
from schemas.lifecycle import LifecycleStatus  # noqa: E402


class PreferenceUpdaterTest(unittest.TestCase):
    def setUp(self):
        self.db_path = settings.database_path
        initialize_database(self.db_path)
        now = datetime.now(timezone.utc)
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            for table in ("sessions", "job_feedback", "preference_profiles",
                          "user_search_conditions", "users"):
                connection.execute(f"DELETE FROM {table}")
            connection.execute(
                "INSERT INTO users(user_id,email,password_hash,created_at) VALUES(?,?,?,?)",
                ("user-1", "user@example.com", "unused", now.isoformat()),
            )
            connection.execute(
                "INSERT INTO user_search_conditions(user_id,payload,updated_at) VALUES(?,?,?)",
                ("user-1", json.dumps({
                    "target_jobs": ["AI Engineer"], "skills": ["Python"],
                    "exclusion_conditions": [], "locations": ["서울"],
                    "experience_levels": ["entry"], "max_experience_years": 0,
                    "employment_types": ["정규직"], "preferred_source_domains": [],
                }, ensure_ascii=False), now.isoformat()),
            )
            connection.execute(
                "INSERT INTO job_feedback VALUES(?,?,?,?,?,?,?,?)",
                ("feedback-1", "user-1", "job-1", "interested",
                 '["company", "skill"]', None, now.isoformat(), now.isoformat(),),
            )
        SQLiteJobStore(self.db_path).save(JobPosting(
            job_id="job-1", company_name="예시테크", job_title="AI Engineer",
            requirements=["Python 활용 능력"], employment_type="정규직",
            experience_level="신입", location=["서울"],
            source_url="https://example.com/job/1", source_urls=["https://example.com/job/1"],
            lifecycle_status=LifecycleStatus.ACTIVE, content_hash="c" * 64,
            first_seen_at=now, last_seen_at=now, created_at=now, updated_at=now,
        ))

    def test_rebuild_is_idempotent_and_extracts_selected_values(self):
        first = rebuild_preference_profile("user-1")
        second = rebuild_preference_profile("user-1")
        values = {(item.category, item.value): item for item in second.features}
        self.assertEqual(values[("company", "예시테크")].positive_count, 1)
        self.assertEqual(values[("skill", "Python")].positive_count, 1)
        self.assertEqual(second.version, first.version)


if __name__ == "__main__":
    unittest.main()
