"""Account lifecycle exercised through real SQLite and authentication."""
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from src.core.errors import Conflict, Forbidden, Unauthenticated
from src.services.accounts import AccountService
from src.services.bootstrap import bootstrap_admin
from src.services.identity import IdentityService
from src.storage import Database
from src.storage.management import initialize_database
from src.storage.audit_repository import AuditRepository
from tests.helpers import TempPathTestCase

PASSWORD = 'a secure password 123'


class AccountTests(TempPathTestCase):
    def setUp(self):
        super().setUp()
        self.path = self.temp_path()
        self.scope = initialize_database(self.path)
        self.db = Database(self.path)
        self.admin = bootstrap_admin(self.db, 'admin', 'Administrator', PASSWORD)
        self.identity = IdentityService(self.db)
        self.token = self.identity.login('admin', PASSWORD, source='local').session_token
        self.accounts = AccountService(self.db)

    def create(self, name='member'):
        return self.accounts.create_user(name, name, session_token=self.token)

    def test_last_admin_and_owner_retention_and_normal_password_change(self):
        for call, extra in ((self.accounts.reset_user, {}), (self.accounts.disable_user, {}), (self.accounts.set_site_admin, {'enabled': False})):
            with self.assertRaises(Forbidden):
                call(self.admin.id, session_token=self.token, expected_version=1, **extra)
        self.identity.change_password(PASSWORD, 'another secure password', session_token=self.token, source='local')
        self.identity.login('admin', 'another secure password', source='local')

    def test_invitation_resend_disable_enable_and_one_use(self):
        grant = self.create()
        replacement = self.accounts.resend_activation(grant.user.id, session_token=self.token, expected_version=1)
        with self.assertRaises(Unauthenticated):
            self.identity.activate(grant.token, PASSWORD, source='local')
        active = self.identity.activate(replacement.token, PASSWORD, source='local')
        session = self.identity.login('member', PASSWORD, source='local').session_token
        reset = self.accounts.reset_user(active.id, session_token=self.token, expected_version=active.version)
        with self.assertRaises(Unauthenticated):
            self.identity.current_session(session_token=session)
        disabled = self.accounts.disable_user(active.id, session_token=self.token, expected_version=reset.user.version)
        with self.assertRaises(Unauthenticated):
            self.identity.reset_password(reset.token, PASSWORD, source='local')
        enabled = self.accounts.enable_user(active.id, session_token=self.token, expected_version=disabled.version)
        user = self.identity.reset_password(enabled.token, PASSWORD, source='local')
        with self.assertRaises(Unauthenticated):
            self.identity.reset_password(enabled.token, PASSWORD, source='local')
        with self.assertRaises(Conflict):
            self.accounts.set_site_admin(user.id, session_token=self.token, expected_version=1, enabled=False)
        self.assertEqual(user.status, 'active')

    def test_guest_and_non_admin_cannot_manage_and_audit_uses_actor(self):
        grant = self.create()
        user = self.identity.activate(grant.token, PASSWORD, source='local')
        token = self.identity.login('member', PASSWORD, source='local').session_token
        for session_token in (None, token):
            with self.assertRaises(Forbidden):
                self.accounts.list_users(session_token=session_token)
            with self.assertRaises(Forbidden):
                self.accounts.disable_user(user.id, session_token=session_token, expected_version=user.version)
        promoted = self.accounts.set_site_admin(user.id, session_token=self.token, expected_version=user.version, enabled=True)
        demoted = self.accounts.set_site_admin(user.id, session_token=self.token, expected_version=promoted.version, enabled=False)
        with self.db.transaction() as connection:
            events = connection.execute("SELECT before_json,after_json FROM audit_events WHERE event_type='accounts.set_site_admin' AND target_id=? ORDER BY rowid", (user.id,)).fetchall()
            states = [(json.loads(event['before_json']), json.loads(event['after_json'])) for event in events]
            self.assertEqual(states, [
                ({'status': 'active', 'site_admin': False, 'version': user.version},
                 {'status': 'active', 'site_admin': True, 'version': promoted.version}),
                ({'status': 'active', 'site_admin': True, 'version': promoted.version},
                 {'status': 'active', 'site_admin': False, 'version': demoted.version}),
            ])
        self.accounts.disable_user(user.id, session_token=self.token, expected_version=demoted.version)
        with self.db.transaction() as connection:
            event = connection.execute("SELECT actor_id,target_id FROM audit_events WHERE event_type='accounts.disable'").fetchone()
            self.assertEqual(tuple(event), (self.admin.id, user.id))

    def second_admin(self):
        grant = self.create('second')
        user = self.identity.activate(grant.token, PASSWORD, source='local')
        user = self.accounts.set_site_admin(user.id, session_token=self.token, expected_version=user.version, enabled=True)
        token = self.identity.login('second', PASSWORD, source='local').session_token
        return user, token

    def test_owner_reset_remains_effective_and_disable_is_refused(self):
        second, token = self.second_admin()
        reset = self.accounts.reset_user(self.admin.id, session_token=token, expected_version=1)
        with self.assertRaises(Forbidden):
            self.accounts.disable_user(self.admin.id, session_token=token, expected_version=reset.user.version)
        with self.db.transaction() as connection:
            self.assertEqual(connection.execute('SELECT status FROM workspace_memberships WHERE user_id=?', (self.admin.id,)).fetchone()[0], 'active')

    def test_concurrent_admin_demotion_retains_one_and_audit_actor(self):
        second, token = self.second_admin()
        def demote(user, actor_token):
            try:
                AccountService(Database(self.path)).set_site_admin(user.id, session_token=actor_token, expected_version=user.version, enabled=False)
                return True
            except Forbidden:
                return False
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda args: demote(*args), [(self.admin, self.token), (second, token)]))
        self.assertEqual(sum(results), 1)
        with self.db.transaction() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM users WHERE site_admin=1 AND status='active'").fetchone()[0], 1)
            actor = connection.execute("SELECT actor_id FROM audit_events WHERE event_type='accounts.set_site_admin' ORDER BY rowid LIMIT 1").fetchone()[0]
            self.assertEqual(actor, self.admin.id)

    def test_audit_failure_rolls_back_user_and_tokens(self):
        grant = self.create()
        with self.db.transaction() as connection:
            before = tuple(connection.execute('SELECT * FROM users WHERE id=?', (grant.user.id,)).fetchone())
        with patch.object(AuditRepository, 'append', side_effect=RuntimeError('audit failure')):
            with self.assertRaises(RuntimeError):
                self.accounts.resend_activation(grant.user.id, session_token=self.token, expected_version=1)
        with self.db.transaction() as connection:
            self.assertEqual(tuple(connection.execute('SELECT * FROM users WHERE id=?', (grant.user.id,)).fetchone()), before)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM account_tokens WHERE user_id=? AND revoked_at IS NULL', (grant.user.id,)).fetchone()[0], 1)
        self.identity.activate(grant.token, PASSWORD, source='local')

    def test_bootstrap_concurrency_and_rollback(self):
        other = self.temp_path('other.sqlite')
        initialize_database(other)
        def create(name):
            try:
                return bootstrap_admin(Database(other), name, name, PASSWORD)
            except Forbidden:
                return None
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sum(user is not None for user in pool.map(create, ['first', 'second'])), 1)
        empty = self.temp_path('empty.sqlite')
        initialize_database(empty)
        with patch.object(AuditRepository, 'append', side_effect=RuntimeError('audit failure')):
            with self.assertRaises(RuntimeError):
                bootstrap_admin(Database(empty), 'first', 'First', PASSWORD)
        with Database(empty).transaction() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM users').fetchone()[0], 0)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM workspace_memberships').fetchone()[0], 0)
