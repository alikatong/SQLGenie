from __future__ import annotations

from dataclasses import dataclass

ROLE_ADMIN = "admin"
ROLE_USER = "user"
SUPPORTED_DB_TYPES = ("mysql", "pg", "oracle")


@dataclass(slots=True)
class AuthenticatedUser:
    id: int
    username: str
    role: str

