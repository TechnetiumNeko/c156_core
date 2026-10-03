"""Typed storage records and SQL only; caller owns connection and transaction."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, astuple

@dataclass(frozen=True)
class AuditEventRecord:
    id: str
    actor_id: str|None
    workspace_id: str|None
    event_type: str
    target_type: str
    target_id: str
    before_json: str|None
    after_json: str|None
    created_at: str


class AuditRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def append(self, event: AuditEventRecord) -> None:
        self.connection.execute('INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?,?)', astuple(event))
