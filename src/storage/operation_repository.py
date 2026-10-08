"""Success receipts; the application owns the shared SQLite transaction."""
from dataclasses import dataclass, astuple
import sqlite3


@dataclass(frozen=True)
class OperationRecord:
    actor_id: str
    operation_id: str
    workspace_id: str
    branch_id: str
    object_id: str
    operation_type: str
    request_digest: str
    result_revision_id: str
    changed: bool
    created_at: str


class OperationRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get(self, actor_id: str, operation_id: str) -> OperationRecord | None:
        row = self.connection.execute(
            "SELECT actor_id, operation_id, workspace_id, branch_id, object_id, "
            "operation_type, request_digest, result_revision_id, changed, created_at "
            "FROM document_operations WHERE actor_id=? AND operation_id=?",
            (actor_id, operation_id)).fetchone()
        if row is None:
            return None
        values = list(row)
        values[8] = bool(values[8])
        return OperationRecord(*values)

    def insert(self, record: OperationRecord) -> None:
        self.connection.execute(
            "INSERT INTO document_operations (actor_id, operation_id, workspace_id, "
            "branch_id, object_id, operation_type, request_digest, result_revision_id, "
            "changed, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)", astuple(record))
