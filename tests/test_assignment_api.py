"""HTTP contract for assignment routes, including authentication boundaries."""

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


def _due(hours):
    return (local_now() + timedelta(hours=hours)).isoformat()


class AssignmentApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = UserStore(self.temp.name, Fernet.generate_key())
        for uid in ("alice", "bob"):
            self.store.save_user(uid, {"id": uid, "email": uid + "@example.com"},
                                 {"refresh_token": "private-" + uid})
        runtime = UserRuntime(
            self.store,
            lambda: Model(Planner(QueryPlan(intent="list"))),
            lambda uid: FakeService(list_result={"items": []}),
        )
        self.app = create_multiuser_app(self.store, runtime, origin="http://127.0.0.1:5173")
        self.alice = self._client("alice")
        self.bob = self._client("bob")

    def _client(self, user_id):
        client = TestClient(self.app)
        client.cookies.set(COOKIE, self.store.session(user_id))
        client.headers.update({"origin": "http://127.0.0.1:5173", "x-task-pilot": "1"})
        return client

    def test_requires_sign_in(self):
        anonymous = TestClient(self.app)
        self.assertEqual(anonymous.get("/assignments").status_code, 401)
        self.assertEqual(
            anonymous.post("/assignments", json={"title": "X", "due": _due(5)}).status_code,
            401,
        )

    def test_rejects_cross_site_writes(self):
        """A logged-in cookie alone must not let another site post for you."""
        client = TestClient(self.app)
        client.cookies.set(COOKIE, self.store.session("alice"))
        response = client.post(
            "/assignments",
            json={"title": "Forged", "due": _due(5)},
            headers={"origin": "https://evil.example"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.store.assignments("alice"), [])

    def test_create_and_list(self):
        created = self.alice.post("/assignments", json={
            "title": "Compiler design", "subject": "CS", "due": _due(48),
            "priority": "high", "estimated_minutes": 240,
        })
        self.assertEqual(created.status_code, 200)
        body = created.json()
        self.assertEqual(body["title"], "Compiler design")
        self.assertEqual(body["priority"], "high")

        listed = self.alice.get("/assignments").json()
        self.assertEqual([row["title"] for row in listed], ["Compiler design"])

    def test_validation_errors_are_reported(self):
        bad = self.alice.post("/assignments", json={"title": "X", "due": _due(5),
                                                   "priority": "whenever"})
        self.assertEqual(bad.status_code, 422)

    def test_update_and_complete(self):
        created = self.alice.post("/assignments", json={"title": "Draft", "due": _due(30)}).json()
        updated = self.alice.put(f"/assignments/{created['id']}", json={
            "title": "Draft", "due": _due(30), "status": "done",
        })
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["status"], "done")
        self.assertEqual(self.alice.get("/assignments?include_done=false").json(), [])

    def test_delete(self):
        created = self.alice.post("/assignments", json={"title": "Temp", "due": _due(9)}).json()
        self.assertEqual(self.alice.delete(f"/assignments/{created['id']}").status_code, 200)
        self.assertEqual(self.alice.delete(f"/assignments/{created['id']}").status_code, 404)

    def test_one_account_cannot_touch_another(self):
        mine = self.alice.post("/assignments", json={"title": "Secret", "due": _due(12)}).json()
        self.assertEqual(self.bob.get("/assignments").json(), [])
        self.assertEqual(
            self.bob.put(f"/assignments/{mine['id']}",
                         json={"title": "Hijacked", "due": _due(12)}).status_code, 404)
        self.assertEqual(self.bob.delete(f"/assignments/{mine['id']}").status_code, 404)
        self.assertEqual(
            self.alice.get("/assignments").json()[0]["title"], "Secret")


if __name__ == "__main__":
    unittest.main()
