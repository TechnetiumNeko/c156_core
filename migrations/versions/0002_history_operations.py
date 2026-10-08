"""Immutable revision provenance, history permission and atomic operation receipts.

Management disables foreign keys before BEGIN for populated table rebuilds;
all constraints and revision immutability triggers are recreated explicitly.
"""
from alembic import op

revision = "0002_history_operations"
down_revision = "0001_protocol2_baseline"
branch_labels = None
depends_on = None

_STATEMENTS = (
    """
CREATE TABLE _new_document_revisions (
        id TEXT NOT NULL PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        object_id TEXT NOT NULL,
        parent_revision_id TEXT,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL,
        actor_id TEXT REFERENCES users(id),
        source_kind TEXT NOT NULL DEFAULT 'unknown'
            CHECK(source_kind IN ('unknown','save','restore','import')),
        restored_from_revision_id TEXT,
        FOREIGN KEY (workspace_id, object_id, restored_from_revision_id)
            REFERENCES document_revisions (workspace_id, object_id, id),
        UNIQUE (workspace_id, object_id, id),
        FOREIGN KEY (workspace_id, object_id)
            REFERENCES objects (workspace_id, id),
        FOREIGN KEY (workspace_id, object_id, parent_revision_id)
            REFERENCES document_revisions (workspace_id, object_id, id)
    )
    """,
    """
INSERT INTO _new_document_revisions
(id,workspace_id,object_id,parent_revision_id,content,created_at)
SELECT id,workspace_id,object_id,parent_revision_id,content,created_at FROM document_revisions
    """,
    """
DROP TABLE document_revisions
    """,
    """
ALTER TABLE _new_document_revisions RENAME TO document_revisions
    """,
    """
CREATE TRIGGER trg_document_revisions_no_update
    BEFORE UPDATE ON document_revisions
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'document_revisions are append-only');
    END
    """,
    """
CREATE TRIGGER trg_document_revisions_no_delete
    BEFORE DELETE ON document_revisions
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'document_revisions are append-only');
    END
    """,
    """
CREATE TABLE _new_access_rules (
        workspace_id TEXT NOT NULL,
        branch_id TEXT NOT NULL,
        object_id TEXT NOT NULL,
        subject_type TEXT NOT NULL CHECK(subject_type IN ('user','role','authenticated','everyone')),
        subject_key TEXT NOT NULL,
        subject_user_id TEXT,
        action TEXT NOT NULL CHECK(action IN ('read','history_read','edit','create','rename','move','delete','review','publish')),
        effect TEXT NOT NULL CHECK(effect IN ('allow','deny')),
        PRIMARY KEY(workspace_id,branch_id,object_id,subject_type,subject_key,action),
        FOREIGN KEY(workspace_id,branch_id,object_id) REFERENCES entries(workspace_id,branch_id,object_id),
        FOREIGN KEY(workspace_id,subject_user_id) REFERENCES workspace_memberships(workspace_id,user_id),
        CHECK((subject_type='user' AND subject_user_id IS NOT NULL AND subject_user_id=subject_key) OR (subject_type='role' AND subject_user_id IS NULL AND subject_key IN ('reader','editor','admin','owner')) OR (subject_type IN ('authenticated','everyone') AND subject_user_id IS NULL AND subject_key='' AND action='read'))
    )
    """,
    """
INSERT INTO _new_access_rules SELECT * FROM access_rules
    """,
    """
DROP TABLE access_rules
    """,
    """
ALTER TABLE _new_access_rules RENAME TO access_rules
    """,
    """
CREATE TABLE document_operations (
    actor_id TEXT NOT NULL REFERENCES users(id),
    operation_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    object_id TEXT NOT NULL,
    operation_type TEXT NOT NULL CHECK(operation_type IN ('save','restore')),
    request_digest TEXT NOT NULL,
    result_revision_id TEXT NOT NULL,
    changed INTEGER NOT NULL CHECK(typeof(changed)='integer' AND changed IN (0,1)),
    created_at TEXT NOT NULL,
    PRIMARY KEY(actor_id, operation_id),
    FOREIGN KEY(workspace_id,branch_id,object_id)
        REFERENCES entries(workspace_id,branch_id,object_id),
    FOREIGN KEY(workspace_id,object_id,result_revision_id)
        REFERENCES document_revisions(workspace_id,object_id,id)
)
    """,
    """
PRAGMA user_version = 3
    """,
)


def upgrade():
    for statement in _STATEMENTS:
        op.execute(statement)


def downgrade():
    raise RuntimeError("Restore an explicit pre-upgrade backup instead of downgrading")
