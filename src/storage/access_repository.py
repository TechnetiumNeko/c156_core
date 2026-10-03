"""Typed storage records and SQL only; caller owns connection and transaction."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, astuple

@dataclass(frozen=True)
class MembershipRecord:
    workspace_id: str
    user_id: str
    role: str
    status: str
    version: int
    created_at: str
    modified_at: str


@dataclass(frozen=True)
class AccessSettingsRecord:
    workspace_id: str
    read_scope: str
    version: int


@dataclass(frozen=True)
class AccessRuleRecord:
    workspace_id: str
    branch_id: str
    object_id: str
    subject_type: str
    subject_key: str
    subject_user_id: str|None
    action: str
    effect: str


@dataclass(frozen=True)
class OwnershipRecord:
    workspace_id: str
    object_id: str
    creator_id: str|None


@dataclass(frozen=True)
class PrivacyRecord:
    workspace_id: str
    branch_id: str
    object_id: str
    owner_id: str
    created_at: str


@dataclass(frozen=True)
class LockRecord:
    workspace_id: str
    branch_id: str
    object_id: str
    locked_by: str
    created_at: str


class AccessRepository:
    def __init__(self, connection: sqlite3.Connection, *, workspace_id: str, branch_id: str) -> None:
        self.connection = connection
        self.workspace_id = workspace_id
        self.branch_id = branch_id

    def get_settings(self) -> AccessSettingsRecord | None:
        row = self.connection.execute('SELECT * FROM workspace_access_settings WHERE workspace_id=?', (self.workspace_id,)).fetchone()
        return AccessSettingsRecord(**dict(row)) if row else None

    def update_settings(self, *, expected_version: int, read_scope: str | None = None) -> bool:
        cursor = self.connection.execute('UPDATE workspace_access_settings SET version=version+1,read_scope=COALESCE(?,read_scope) WHERE workspace_id=? AND version=?', (read_scope,self.workspace_id,expected_version))
        return cursor.rowcount == 1

    def get_membership(self, user_id: str) -> MembershipRecord | None:
        row = self.connection.execute('SELECT * FROM workspace_memberships WHERE workspace_id=? AND user_id=?', (self.workspace_id,user_id)).fetchone()
        return MembershipRecord(**dict(row)) if row else None

    def list_memberships(self) -> tuple[MembershipRecord, ...]:
        return tuple(MembershipRecord(**dict(r)) for r in self.connection.execute('SELECT * FROM workspace_memberships WHERE workspace_id=? ORDER BY user_id', (self.workspace_id,)))

    def insert_membership(self, record: MembershipRecord) -> None:
        self._check_workspace(record.workspace_id)
        self.connection.execute('INSERT INTO workspace_memberships VALUES (?,?,?,?,?,?,?)', astuple(record))

    def update_membership(self, record: MembershipRecord, *, expected_version: int) -> bool:
        self._check_workspace(record.workspace_id)
        cursor = self.connection.execute('UPDATE workspace_memberships SET role=?,status=?,version=?,modified_at=? WHERE workspace_id=? AND user_id=? AND version=?', (record.role,record.status,record.version,record.modified_at,self.workspace_id,record.user_id,expected_version))
        return cursor.rowcount == 1

    def count_effective_owners(self, *, excluding_user_id: str | None = None) -> int:
        return self.connection.execute("SELECT COUNT(*) FROM workspace_memberships m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=? AND m.role='owner' AND m.status='active' AND u.status IN ('active','reset_required') AND (? IS NULL OR u.id<>?)", (self.workspace_id,excluding_user_id,excluding_user_id)).fetchone()[0]

    def has_any_owner(self) -> bool:
        return self.connection.execute("SELECT 1 FROM workspace_memberships WHERE role='owner' LIMIT 1").fetchone() is not None

    def count_effective_owners_in_workspace(self, workspace_id: str, *, excluding_user_id: str) -> int:
        return self.connection.execute("SELECT COUNT(*) FROM workspace_memberships m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=? AND m.role='owner' AND m.status='active' AND u.status IN ('active','reset_required') AND u.id<>?", (workspace_id, excluding_user_id)).fetchone()[0]

    def owner_workspace_ids(self, user_id: str) -> tuple[str, ...]:
        return tuple(r[0] for r in self.connection.execute("SELECT workspace_id FROM workspace_memberships WHERE user_id=? AND role='owner' AND status='active' ORDER BY workspace_id", (user_id,)))

    def list_rules(self, object_id: str | None = None) -> tuple[AccessRuleRecord, ...]:
        return tuple(AccessRuleRecord(**dict(r)) for r in self.connection.execute('SELECT * FROM access_rules WHERE workspace_id=? AND branch_id=? AND (? IS NULL OR object_id=?) ORDER BY object_id,subject_type,subject_key,action', (self.workspace_id,self.branch_id,object_id,object_id)))

    def list_workspace_rules(self) -> tuple[AccessRuleRecord, ...]:
        return tuple(AccessRuleRecord(**dict(r)) for r in self.connection.execute(
            'SELECT * FROM access_rules WHERE workspace_id=? ORDER BY branch_id,object_id,subject_type,subject_key,action',
            (self.workspace_id,)))

    def set_read_scope(self, read_scope: str) -> None:
        self.connection.execute('UPDATE workspace_access_settings SET read_scope=? WHERE workspace_id=?',
                                (read_scope, self.workspace_id))

    def upsert_rule(self, record: AccessRuleRecord) -> None:
        self._check_scope(record.workspace_id, record.branch_id)
        self.connection.execute('INSERT INTO access_rules VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(workspace_id,branch_id,object_id,subject_type,subject_key,action) DO UPDATE SET effect=excluded.effect', astuple(record))

    def delete_rule(self, record: AccessRuleRecord) -> None:
        self._check_scope(record.workspace_id, record.branch_id)
        self.connection.execute('DELETE FROM access_rules WHERE workspace_id=? AND branch_id=? AND object_id=? AND subject_type=? AND subject_key=? AND action=?',
            (record.workspace_id, record.branch_id, record.object_id, record.subject_type, record.subject_key, record.action))

    def insert_rule(self, record: AccessRuleRecord) -> None:
        self._check_scope(record.workspace_id,record.branch_id)
        self.connection.execute('INSERT INTO access_rules VALUES (?,?,?,?,?,?,?,?)', astuple(record))

    def delete_rules(self, object_id: str) -> None:
        self.connection.execute('DELETE FROM access_rules WHERE workspace_id=? AND branch_id=? AND object_id=?', (self.workspace_id,self.branch_id,object_id))

    def delete_user_rules(self, user_id: str) -> None:
        self.connection.execute("DELETE FROM access_rules WHERE workspace_id=? AND subject_type='user' AND subject_user_id=?", (self.workspace_id,user_id))

    def get_ownership(self, object_id: str) -> OwnershipRecord | None:
        row = self.connection.execute('SELECT * FROM content_ownership WHERE workspace_id=? AND object_id=?', (self.workspace_id,object_id)).fetchone()
        return OwnershipRecord(**dict(row)) if row else None

    def insert_ownership(self, record: OwnershipRecord) -> None:
        self._check_workspace(record.workspace_id)
        self.connection.execute('INSERT INTO content_ownership VALUES (?,?,?)', astuple(record))

    def get_privacy(self, object_id: str) -> PrivacyRecord | None:
        row = self.connection.execute('SELECT * FROM content_privacy WHERE workspace_id=? AND branch_id=? AND object_id=?', (self.workspace_id,self.branch_id,object_id)).fetchone()
        return PrivacyRecord(**dict(row)) if row else None

    def list_privacy(self) -> tuple[PrivacyRecord, ...]:
        return tuple(PrivacyRecord(**dict(r)) for r in self.connection.execute('SELECT * FROM content_privacy WHERE workspace_id=? AND branch_id=? ORDER BY object_id', (self.workspace_id,self.branch_id)))

    def insert_privacy(self, record: PrivacyRecord) -> None:
        self._check_scope(record.workspace_id,record.branch_id)
        self.connection.execute('INSERT INTO content_privacy VALUES (?,?,?,?,?)', astuple(record))

    def delete_privacy(self, object_id: str) -> None:
        self.connection.execute('DELETE FROM content_privacy WHERE workspace_id=? AND branch_id=? AND object_id=?', (self.workspace_id,self.branch_id,object_id))

    def get_lock(self, object_id: str) -> LockRecord | None:
        row = self.connection.execute('SELECT * FROM content_locks WHERE workspace_id=? AND branch_id=? AND object_id=?', (self.workspace_id,self.branch_id,object_id)).fetchone()
        return LockRecord(**dict(row)) if row else None

    def list_locks(self) -> tuple[LockRecord, ...]:
        return tuple(LockRecord(**dict(r)) for r in self.connection.execute('SELECT * FROM content_locks WHERE workspace_id=? AND branch_id=? ORDER BY object_id', (self.workspace_id,self.branch_id)))

    def insert_lock(self, record: LockRecord) -> None:
        self._check_scope(record.workspace_id,record.branch_id)
        self.connection.execute('INSERT INTO content_locks VALUES (?,?,?,?,?)', astuple(record))

    def delete_lock(self, object_id: str) -> None:
        self.connection.execute('DELETE FROM content_locks WHERE workspace_id=? AND branch_id=? AND object_id=?', (self.workspace_id,self.branch_id,object_id))

    def _check_workspace(self, workspace_id: str) -> None:
        if workspace_id != self.workspace_id:
            raise ValueError('record is outside repository workspace')

    def _check_scope(self, workspace_id: str, branch_id: str) -> None:
        self._check_workspace(workspace_id)
        if branch_id != self.branch_id:
            raise ValueError('record is outside repository branch')
