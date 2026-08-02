from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.config import settings
from backend.database import init_db
from backend.main import app
from backend.rate_limit import SlidingWindowRateLimiter


class LoginRateLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.patch = patch.object(settings, "db_path", Path(self.temp.name) / "test.db")
        self.patch.start()
        init_db()

    def tearDown(self) -> None:
        self.patch.stop()
        self.temp.cleanup()

    def test_login_returns_429_after_too_many_attempts(self) -> None:
        with patch(
            "backend.main.login_limiter",
            SlidingWindowRateLimiter(max_requests=2, window_seconds=60),
        ):
            with TestClient(app) as client:
                first = client.post("/api/login", json={"username": "missing", "password": "wrong"})
                second = client.post("/api/login", json={"username": "missing", "password": "wrong"})
                third = client.post("/api/login", json={"username": "missing", "password": "wrong"})

        self.assertEqual(first.status_code, 401)
        self.assertEqual(second.status_code, 401)
        self.assertEqual(third.status_code, 429)
        self.assertIn("Retry-After", third.headers)


if __name__ == "__main__":
    unittest.main()
