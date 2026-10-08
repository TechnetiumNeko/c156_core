"""Frozen complete protocol 2 schema; never import runtime schema builders."""
from alembic import op

revision = "0001_protocol2_baseline"
down_revision = None
branch_labels = None
depends_on = None

_TABLE_STATEMENTS = (
    """
    CREATE TABLE workspaces (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE objects (
        id TEXT NOT NULL PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (
            kind IN ('folder', 'document', 'executable', 'resource')
        ),
        created_at TEXT NOT NULL,
        UNIQUE (workspace_id, id),
        FOREIGN KEY (workspace_id) REFERENCES workspaces (id)
    )
    """,
    """
    CREATE TABLE branches (
        id TEXT NOT NULL PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        name TEXT NOT NULL,
        root_object_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (workspace_id, id),
        UNIQUE (workspace_id, name),
        FOREIGN KEY (workspace_id) REFERENCES workspaces (id),
        FOREIGN KEY (workspace_id, root_object_id)
            REFERENCES objects (workspace_id, id)
    )
    """,
    """
    CREATE TABLE document_revisions (
        id TEXT NOT NULL PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        object_id TEXT NOT NULL,
        parent_revision_id TEXT,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (workspace_id, object_id, id),
        FOREIGN KEY (workspace_id, object_id)
            REFERENCES objects (workspace_id, id),
        FOREIGN KEY (workspace_id, object_id, parent_revision_id)
            REFERENCES document_revisions (workspace_id, object_id, id)
    )
    """,
    """
    CREATE TABLE entries (
        workspace_id TEXT NOT NULL,
        branch_id TEXT NOT NULL,
        object_id TEXT NOT NULL,
        parent_id TEXT,
        name TEXT NOT NULL,
        position INTEGER NOT NULL CHECK (
            typeof(position) = 'integer' AND position >= 0
        ),
        version INTEGER NOT NULL CHECK (
            typeof(version) = 'integer' AND version >= 1
        ),
        current_revision_id TEXT,
        metadata_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        modified_at TEXT NOT NULL,
        deleted_at TEXT,
        PRIMARY KEY (workspace_id, branch_id, object_id),
        FOREIGN KEY (workspace_id, branch_id)
            REFERENCES branches (workspace_id, id),
        FOREIGN KEY (workspace_id, object_id)
            REFERENCES objects (workspace_id, id),
        FOREIGN KEY (workspace_id, branch_id, parent_id)
            REFERENCES entries (workspace_id, branch_id, object_id),
        FOREIGN KEY (workspace_id, object_id, current_revision_id)
            REFERENCES document_revisions (workspace_id, object_id, id)
    )
    """,
    """
    CREATE TABLE legacy_imports (
        source_digest TEXT PRIMARY KEY,
        imported_at TEXT NOT NULL,
        object_count INTEGER NOT NULL,
        document_count INTEGER NOT NULL,
        report_json TEXT NOT NULL
    )
    """,
)

_INDEX_STATEMENTS = (
    """
    CREATE UNIQUE INDEX idx_entries_active_sibling_name
        ON entries (workspace_id, branch_id, parent_id, name)
        WHERE deleted_at IS NULL
    """,
    """
    CREATE UNIQUE INDEX idx_entries_active_sibling_position
        ON entries (workspace_id, branch_id, parent_id, position)
        WHERE deleted_at IS NULL
    """,
    """
    CREATE UNIQUE INDEX idx_entries_active_root
        ON entries (workspace_id, branch_id)
        WHERE parent_id IS NULL AND deleted_at IS NULL
    """,
)

_TRIGGER_STATEMENTS = (
    """
    CREATE TRIGGER trg_objects_identity_immutable
    BEFORE UPDATE ON objects
    FOR EACH ROW
    WHEN NEW.id <> OLD.id
        OR NEW.workspace_id <> OLD.workspace_id
        OR NEW.kind <> OLD.kind
    BEGIN
        SELECT RAISE(ABORT, 'objects identity is immutable');
    END
    """,
    """
    CREATE TRIGGER trg_entries_identity_immutable
    BEFORE UPDATE ON entries
    FOR EACH ROW
    WHEN NEW.workspace_id <> OLD.workspace_id
        OR NEW.branch_id <> OLD.branch_id
        OR NEW.object_id <> OLD.object_id
    BEGIN
        SELECT RAISE(ABORT, 'entries identity is immutable');
    END
    """,
    """
    CREATE TRIGGER trg_branches_identity_immutable
    BEFORE UPDATE ON branches
    FOR EACH ROW
    WHEN NEW.id <> OLD.id OR NEW.workspace_id <> OLD.workspace_id
    BEGIN
        SELECT RAISE(ABORT, 'branches identity is immutable');
    END
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
)

_IDENTITY_STATEMENTS = (
    """
    CREATE TABLE users (
        id TEXT PRIMARY KEY NOT NULL,
        login_name TEXT UNIQUE NOT NULL,
        display_name TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('invited','active','reset_required','disabled')),
        site_admin INTEGER NOT NULL CHECK(typeof(site_admin)='integer' AND site_admin IN (0,1)),
        version INTEGER NOT NULL CHECK(typeof(version)='integer' AND version>=1),
        credential_version INTEGER NOT NULL CHECK(typeof(credential_version)='integer' AND credential_version>=1),
        created_at TEXT NOT NULL,
        modified_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE password_credentials (
        user_id TEXT PRIMARY KEY NOT NULL REFERENCES users(id),
        password_hash TEXT NOT NULL,
        credential_version INTEGER NOT NULL CHECK(typeof(credential_version)='integer' AND credential_version>=1),
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE account_tokens (
        token_digest TEXT PRIMARY KEY NOT NULL,
        user_id TEXT NOT NULL REFERENCES users(id),
        purpose TEXT NOT NULL CHECK(purpose IN ('activate','reset')),
        credential_version INTEGER NOT NULL CHECK(typeof(credential_version)='integer' AND credential_version>=1),
        issued_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        consumed_at TEXT,
        revoked_at TEXT
    )
    """,
    """
    CREATE TABLE sessions (
        token_digest TEXT PRIMARY KEY NOT NULL,
        user_id TEXT NOT NULL REFERENCES users(id),
        credential_version INTEGER NOT NULL CHECK(typeof(credential_version)='integer' AND credential_version>=1),
        csrf_token TEXT NOT NULL,
        issued_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        revoked_at TEXT
    )
    """,
    """
    CREATE TABLE workspace_memberships (
        workspace_id TEXT NOT NULL REFERENCES workspaces(id),
        user_id TEXT NOT NULL REFERENCES users(id),
        role TEXT NOT NULL CHECK(role IN ('reader','editor','admin','owner')),
        status TEXT NOT NULL CHECK(status IN ('active','removed')),
        version INTEGER NOT NULL CHECK(typeof(version)='integer' AND version>=1),
        created_at TEXT NOT NULL,
        modified_at TEXT NOT NULL,
        PRIMARY KEY(workspace_id,user_id)
    )
    """,
    """
    CREATE TABLE workspace_access_settings (
        workspace_id TEXT PRIMARY KEY NOT NULL REFERENCES workspaces(id),
        read_scope TEXT NOT NULL DEFAULT 'members' CHECK(read_scope IN ('members','authenticated','everyone')),
        version INTEGER NOT NULL DEFAULT 1 CHECK(typeof(version)='integer' AND version>=1)
    )
    """,
    """
    CREATE TABLE access_rules (
        workspace_id TEXT NOT NULL,
        branch_id TEXT NOT NULL,
        object_id TEXT NOT NULL,
        subject_type TEXT NOT NULL CHECK(subject_type IN ('user','role','authenticated','everyone')),
        subject_key TEXT NOT NULL,
        subject_user_id TEXT,
        action TEXT NOT NULL CHECK(action IN ('read','edit','create','rename','move','delete','review','publish')),
        effect TEXT NOT NULL CHECK(effect IN ('allow','deny')),
        PRIMARY KEY(workspace_id,branch_id,object_id,subject_type,subject_key,action),
        FOREIGN KEY(workspace_id,branch_id,object_id) REFERENCES entries(workspace_id,branch_id,object_id),
        FOREIGN KEY(workspace_id,subject_user_id) REFERENCES workspace_memberships(workspace_id,user_id),
        CHECK((subject_type='user' AND subject_user_id IS NOT NULL AND subject_user_id=subject_key) OR (subject_type='role' AND subject_user_id IS NULL AND subject_key IN ('reader','editor','admin','owner')) OR (subject_type IN ('authenticated','everyone') AND subject_user_id IS NULL AND subject_key='' AND action='read'))
    )
    """,
    """
    CREATE TABLE content_ownership (
        workspace_id TEXT NOT NULL,
        object_id TEXT NOT NULL,
        creator_id TEXT REFERENCES users(id),
        PRIMARY KEY(workspace_id,object_id),
        FOREIGN KEY(workspace_id,object_id) REFERENCES objects(workspace_id,id)
    )
    """,
    """
    CREATE TABLE content_privacy (
        workspace_id TEXT NOT NULL,
        branch_id TEXT NOT NULL,
        object_id TEXT NOT NULL,
        owner_id TEXT NOT NULL REFERENCES users(id),
        created_at TEXT NOT NULL,
        PRIMARY KEY(workspace_id,branch_id,object_id),
        FOREIGN KEY(workspace_id,branch_id,object_id) REFERENCES entries(workspace_id,branch_id,object_id)
    )
    """,
    """
    CREATE TABLE content_locks (
        workspace_id TEXT NOT NULL,
        branch_id TEXT NOT NULL,
        object_id TEXT NOT NULL,
        locked_by TEXT NOT NULL REFERENCES users(id),
        created_at TEXT NOT NULL,
        PRIMARY KEY(workspace_id,branch_id,object_id),
        FOREIGN KEY(workspace_id,branch_id,object_id) REFERENCES entries(workspace_id,branch_id,object_id)
    )
    """,
    """
    CREATE TABLE audit_events (
        id TEXT PRIMARY KEY NOT NULL,
        actor_id TEXT REFERENCES users(id),
        workspace_id TEXT REFERENCES workspaces(id),
        event_type TEXT NOT NULL,
        target_type TEXT NOT NULL,
        target_id TEXT NOT NULL,
        before_json TEXT,
        after_json TEXT,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE auth_throttles (
        bucket_type TEXT NOT NULL CHECK(bucket_type IN ('login','token','source','password')),
        bucket_key TEXT NOT NULL,
        window_started_at TEXT NOT NULL,
        attempts INTEGER NOT NULL CHECK(typeof(attempts)='integer' AND attempts>=1),
        PRIMARY KEY(bucket_type,bucket_key)
    )
    """,
    """
    CREATE INDEX idx_account_tokens_user_purpose ON account_tokens(user_id,purpose)
    """,
    """
    CREATE INDEX idx_sessions_user ON sessions(user_id)
    """,
    """
    CREATE INDEX idx_auth_throttles_window ON auth_throttles(window_started_at)
    """,
    """
    CREATE TRIGGER trg_audit_events_no_update BEFORE UPDATE ON audit_events BEGIN SELECT RAISE(ABORT, 'audit_events are immutable'); END
    """,
    """
    CREATE TRIGGER trg_audit_events_no_delete BEFORE DELETE ON audit_events BEGIN SELECT RAISE(ABORT, 'audit_events are immutable'); END
    """,
    """
    CREATE TRIGGER trg_content_ownership_no_update BEFORE UPDATE ON content_ownership BEGIN SELECT RAISE(ABORT, 'content_ownership are immutable'); END
    """,
    """
    CREATE TRIGGER trg_content_ownership_no_delete BEFORE DELETE ON content_ownership BEGIN SELECT RAISE(ABORT, 'content_ownership are immutable'); END
    """,
    """
    CREATE TRIGGER trg_users_identity_immutable BEFORE UPDATE ON users WHEN NEW.id<>OLD.id OR NEW.login_name<>OLD.login_name BEGIN SELECT RAISE(ABORT, 'users identity is immutable'); END
    """,
    """
    CREATE TRIGGER trg_content_locks_document_insert BEFORE INSERT ON content_locks WHEN COALESCE((SELECT kind FROM objects WHERE workspace_id=NEW.workspace_id AND id=NEW.object_id),'')<>'document' BEGIN SELECT RAISE(ABORT, 'only documents may be locked'); END
    """,
    """
    CREATE TRIGGER trg_content_locks_document_update BEFORE UPDATE ON content_locks WHEN COALESCE((SELECT kind FROM objects WHERE workspace_id=NEW.workspace_id AND id=NEW.object_id),'')<>'document' BEGIN SELECT RAISE(ABORT, 'only documents may be locked'); END
    """,
)

_SCHEMA_STATEMENTS = _TABLE_STATEMENTS + _INDEX_STATEMENTS + _TRIGGER_STATEMENTS + _IDENTITY_STATEMENTS



def upgrade():
    for statement in _SCHEMA_STATEMENTS:
        op.execute(statement)
    op.execute("PRAGMA user_version = 2")


def downgrade():
    raise RuntimeError("Restore an explicit pre-upgrade backup instead of downgrading")
