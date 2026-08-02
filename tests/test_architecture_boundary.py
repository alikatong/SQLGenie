from __future__ import annotations

import re
import unittest
from pathlib import Path

from backend.main import app


ROOT = Path(__file__).resolve().parents[1]


class ArchitectureBoundaryTests(unittest.TestCase):
    def test_requirements_have_no_target_database_drivers(self) -> None:
        requirements = (ROOT / "backend" / "requirements.txt").read_text(encoding="utf-8").casefold()
        forbidden = ("pymysql", "mysqlclient", "psycopg", "cx_oracle", "oracledb", "pyodbc")
        self.assertFalse([name for name in forbidden if re.search(rf"(?m)^\s*{re.escape(name)}\b", requirements)])

    def test_api_has_no_target_database_connection_or_execution_route(self) -> None:
        paths = {route.path.casefold() for route in app.routes}
        forbidden_segments = ("test-connection", "connect-database", "execute-sql", "query-execute", "explain-sql")
        self.assertFalse([path for path in paths if any(segment in path for segment in forbidden_segments)])


if __name__ == "__main__":
    unittest.main()
