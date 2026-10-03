"""HTTP contract for the Smart Study Planner routes."""

import tempfile
import unittest
from datetime import timedelta

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.date_utils import local_now
from app.graph_agent import QueryPlan
from app.multiuser import COOKIE, UserRuntime, create_multiuser_app
from app.user_store import UserStore
from tests.test_advanced_agent import Planner
from tests.test_calendar_service import FakeService
from tests.test_multiuser import Model


class StudyApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = UserStore(self.temp.name, Fernet.generate_key())
        for uid in ("alice", "bob"):
            self.store.save_user(uid, {"id": uid, "email": uid + "@example.com"},
                                 {"refresh_token": "private-" + uid})
        self.services = {uid: FakeService(list_result={"items": []}) for uid in ("alice", "bob")}
        runtime = UserRuntime(
            self.store,
            lambda: Model(Planner(QueryPlan(intent="list"))),
            self.services.__getitem__,
        )
        self.app = create_multiuser_app(self.store, runtime, origin="http://127.0.0.1:5173")
        self.alice = self._client("alice")
        self.bob = self._client("bob")

    def _client(self, user_id):
        client = TestClient(self.app)
        client.cookies.set(COOKIE, self.store.session(user_id))
        client.headers.update({"origin": "http://127.0.0.1:5173", "x-task-pilot": "1"})
        return client

    def _add(self, client, title, hours, **extra):
        body = {"title": title, "due": (local_now() + timedelta(hours=hours)).isoformat()}
        body.update(extra)
        response = client.post("/assignments", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_requires_sign_in(self):
        anonymous = TestClient(self.app)
        self.assertEqual(anonymous.get("/study/plan").status_code, 401)
        self.assertEqual(anonymous.get("/study/progress").status_code, 401)

    def test_plan_is_empty_without_assignments(self):
        generated = self.alice.post("/study/plan").json()
        self.assertEqual(generated["sessions"], [])
        self.assertEqual(generated["unscheduled"], [])

    def test_generates_and_persists_a_plan(self):
        self._add(self.alice, "DBMS assignment", 72, subject="DBMS",
                  estimated_minutes=180, priority="high")
        generated = self.alice.post("/study/plan").json()
        self.assertTrue(generated["sessions"])
        self.assertTrue(all(s["subject"] == "DBMS" for s in generated["sessions"]))

        # Persisted, so a page reload shows the same plan.
        reloaded = self.alice.get("/study/plan").json()["sessions"]
        self.assertEqual([s["id"] for s in reloaded], [s["id"] for s in generated["sessions"]])

    def test_reports_work_that_cannot_be_scheduled(self):
        self._add(self.alice, "Impossible", 1, estimated_minutes=900, priority="urgent")
        generated = self.alice.post("/study/plan").json()
        self.assertTrue(generated["unscheduled"])
        self.assertEqual(generated["unscheduled"][0]["title"], "Impossible")

    def test_marking_a_session_done_feeds_progress(self):
        self._add(self.alice, "Maths", 96, subject="Maths", estimated_minutes=120)
        sessions = self.alice.post("/study/plan").json()["sessions"]
        self.assertTrue(sessions)

        before = self.alice.get("/study/progress").json()
        self.assertEqual(before["studied_minutes"], 0)

        updated = self.alice.put(f"/study/sessions/{sessions[0]['id']}?status=done")
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["status"], "done")

        after = self.alice.get("/study/progress").json()
        self.assertEqual(after["studied_minutes"], sessions[0]["minutes"])
        self.assertGreater(after["completion"], 0)

    def test_rejects_an_unknown_status(self):
        self._add(self.alice, "Maths", 96, estimated_minutes=60)
        sessions = self.alice.post("/study/plan").json()["sessions"]
        response = self.alice.put(f"/study/sessions/{sessions[0]['id']}?status=procrastinated")
        self.assertEqual(response.status_code, 422)

    def test_replanning_keeps_completed_sessions(self):
        """Work already done must survive a re-plan."""
        self._add(self.alice, "Maths", 120, subject="Maths", estimated_minutes=180)
        sessions = self.alice.post("/study/plan").json()["sessions"]
        self.alice.put(f"/study/sessions/{sessions[0]['id']}?status=done")

        self.alice.post("/study/plan")
        kept = self.alice.get("/study/plan").json()["sessions"]
        self.assertIn("done", [s["status"] for s in kept])

    def test_replanning_only_schedules_what_is_left(self):
        self._add(self.alice, "Maths", 120, subject="Maths", estimated_minutes=180)
        first = self.alice.post("/study/plan").json()["sessions"]
        done_minutes = first[0]["minutes"]
        self.alice.put(f"/study/sessions/{first[0]['id']}?status=done")

        second = self.alice.post("/study/plan").json()["sessions"]
        still_planned = sum(s["minutes"] for s in second if s["status"] == "planned")
        self.assertEqual(still_planned, 180 - done_minutes)

    def test_finished_assignments_are_not_scheduled(self):
        created = self._add(self.alice, "Already done", 72, estimated_minutes=120)
        self.alice.put(f"/assignments/{created['id']}", json={
            "title": "Already done", "due": created["due"], "status": "done",
        })
        self.assertEqual(self.alice.post("/study/plan").json()["sessions"], [])

    def test_plans_are_private_to_an_account(self):
        self._add(self.alice, "Alice work", 72, subject="Secret", estimated_minutes=120)
        self.alice.post("/study/plan")
        self.assertEqual(self.bob.get("/study/plan").json()["sessions"], [])
        self.assertEqual(self.bob.get("/study/progress").json()["planned_minutes"], 0)

    def test_one_account_cannot_tick_off_anothers_session(self):
        self._add(self.alice, "Alice work", 72, estimated_minutes=60)
        sessions = self.alice.post("/study/plan").json()["sessions"]
        response = self.bob.put(f"/study/sessions/{sessions[0]['id']}?status=done")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.alice.get("/study/plan").json()["sessions"][0]["status"], "planned")

    def test_planning_survives_a_calendar_outage(self):
        """Google being down must not take the planner down with it."""
        class Broken:
            def events(self):
                raise RuntimeError("calendar unavailable")

        self.services["alice"] = Broken()
        self._add(self.alice, "Essay", 72, estimated_minutes=60)
        generated = self.alice.post("/study/plan")
        self.assertEqual(generated.status_code, 200)
        self.assertTrue(generated.json()["sessions"])


if __name__ == "__main__":
    unittest.main()
