"""Real SQLite workspace management and atomic authorization changes."""
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from src.access.models import AccessRule
from src.core.errors import Conflict, Forbidden, InvalidArgument, NotFound
from src.services.access import AccessService
from src.services.accounts import AccountService
from src.services.bootstrap import bootstrap_admin
from src.services.identity import IdentityService
from src.services.unit_of_work import ApplicationUnitOfWork
from src.storage.access_repository import PrivacyRecord, LockRecord, OwnershipRecord
from src.storage import Database
from src.storage.management import initialize_database
from src.storage.audit_repository import AuditRepository
from tests.helpers import TempPathTestCase

PASSWORD = 'a secure password 123'


class AccessManagementTests(TempPathTestCase):
    def setUp(self):
        super().setUp()
        self.db = Database(self.temp_path())
        self.scope = initialize_database(self.db.path)
        self.owner = bootstrap_admin(self.db, 'owner', 'Owner', PASSWORD)
        self.identity = IdentityService(self.db)
        self.token = self.identity.login('owner', PASSWORD, source='local').session_token
        self.accounts = AccountService(self.db)
        self.access = AccessService(self.db)

    def user(self, name):
        grant = self.accounts.create_user(name, name, session_token=self.token)
        user = self.identity.activate(grant.token, PASSWORD, source='local')
        token = self.identity.login(name, PASSWORD, source='local').session_token
        return user, token

    def version(self):
        return self.access.workspace_access(self.scope, session_token=self.token).version

    def add(self, name, role='editor'):
        user, token = self.user(name)
        self.access.add_member(self.scope, name.upper(), role, session_token=self.token, expected_version=self.version())
        return user, token

    def test_escalation_and_site_admin_nonmember(self):
        admin, token = self.add('manager', 'admin')
        target, _ = self.user('target')
        version = self.version()
        for role, error in (('admin', Forbidden), ('owner', InvalidArgument)):
            with self.assertRaises(error):
                self.access.add_member(self.scope, 'target', role, session_token=token, expected_version=version)
        with self.assertRaises(Forbidden):
            self.access.set_member_role(self.scope, self.owner.id, 'editor', session_token=token, expected_version=version)
        self.accounts.set_site_admin(target.id, enabled=True, session_token=self.token, expected_version=target.version)
        site_token = self.identity.login('target', PASSWORD, source='local').session_token
        with self.assertRaises(Forbidden):
            self.access.workspace_access(self.scope, session_token=site_token)
        with self.assertRaises(Forbidden):
            self.access.object_access(self.scope, self.scope.root_id, session_token=None)
        self.assertEqual(self.version(), version)

    def test_versions_noop_and_rule_inheritance_remove_rejoin(self):
        user, _ = self.add('member')
        v = self.version()
        rule = AccessRule(self.scope.root_id, 'user', user.id, 'read', 'deny')
        result = self.access.put_rule(self.scope, rule, session_token=self.token, expected_version=v)
        self.assertEqual(result.rules, (rule,))
        self.assertEqual(result.version, v+1)
        result = self.access.put_rule(self.scope, rule, session_token=self.token, expected_version=v+1)
        self.assertEqual(result.version, v+1)
        with self.assertRaises(Conflict):
            self.access.put_rule(self.scope, rule, session_token=self.token, expected_version=v)
        removed = self.access.remove_rule(self.scope, rule, session_token=self.token, expected_version=v+1)
        self.assertEqual(removed.rules, ())
        self.access.put_rule(self.scope, rule, session_token=self.token, expected_version=removed.version)
        with ApplicationUnitOfWork(self.db).transaction(write=True) as work:
            document = work.content(self.scope).create_document(self.scope, self.scope.root_id, 'private')
            repo = work.access(self.scope)
            now = work.now.isoformat()
            repo.insert_privacy(PrivacyRecord(self.scope.workspace_id, self.scope.branch_id, document.id, user.id, now))
            repo.insert_lock(LockRecord(self.scope.workspace_id, self.scope.branch_id, document.id, user.id, now))
            repo.insert_ownership(OwnershipRecord(self.scope.workspace_id, document.id, user.id))
        inherited = self.access.object_access(self.scope, document.id, session_token=self.token)
        self.assertEqual(inherited.inherited_rules, (rule,))
        self.assertEqual(inherited.rules, ())
        self.assertEqual(inherited.decisions['edit'].reason, 'management_override')
        with self.assertRaises(InvalidArgument):
            self.access.put_rule(self.scope, AccessRule(document.id, 'role', 'editor', 'create', 'allow'), session_token=self.token, expected_version=self.version())
        v = self.version()
        with patch.object(AuditRepository, 'append', side_effect=RuntimeError('audit failed')):
            with self.assertRaises(RuntimeError):
                self.access.remove_member(self.scope, user.id, session_token=self.token, expected_version=v)
        self.assertEqual(self.version(), v)
        self.assertEqual(self.access.object_access(self.scope, self.scope.root_id, session_token=self.token).rules, (rule,))
        view = self.access.remove_member(self.scope, user.id, session_token=self.token, expected_version=self.version())
        self.assertEqual(next(m.status for m in view.members if m.user.id == user.id), 'removed')
        self.assertEqual(self.access.remove_member(self.scope, user.id, session_token=self.token, expected_version=view.version).version, view.version)
        with self.assertRaises(Conflict):
            self.access.remove_member(self.scope, user.id, session_token=self.token, expected_version=view.version-1)
        self.access.add_member(self.scope, 'member', 'reader', session_token=self.token, expected_version=view.version)
        self.assertEqual(self.access.object_access(self.scope, self.scope.root_id, session_token=self.token).rules, ())
        with ApplicationUnitOfWork(self.db).transaction() as work:
            repo = work.access(self.scope)
            self.assertEqual(repo.get_privacy(document.id).owner_id, user.id)
            self.assertEqual(repo.get_lock(document.id).locked_by, user.id)
            self.assertEqual(repo.get_ownership(document.id).creator_id, user.id)
        with self.db.transaction() as connection:
            event = connection.execute("SELECT before_json,after_json FROM audit_events WHERE event_type='access.remove_member'").fetchone()
            before, after = json.loads(event[0]), json.loads(event[1])
            self.assertEqual(before['members'][0]['status'], 'active')
            self.assertEqual(after['members'][0]['status'], 'removed')
            self.assertEqual(len(before['rules']), 1)
            self.assertEqual(after['rules'], [])
        v = self.version()
        self.assertEqual(self.access.set_read_scope(self.scope, 'members', session_token=self.token, expected_version=v).version, v)
        for stale in (v-1, None):
            with self.assertRaises(Conflict):
                self.access.set_read_scope(self.scope, 'members', session_token=self.token, expected_version=stale)

    def test_transfer_and_last_owner_concurrent_writers(self):
        target, token = self.add('successor')
        with self.assertRaises(InvalidArgument):
            self.access.transfer_ownership(self.scope, self.owner.id, session_token=self.token, expected_version=self.version())
        self.access.transfer_ownership(self.scope, target.id, session_token=self.token, expected_version=self.version())
        view = self.access.workspace_access(self.scope, session_token=token)
        self.assertEqual({m.user.id:m.role for m in view.members}, {self.owner.id:'admin', target.id:'owner'})
        with self.assertRaises(Forbidden):
            self.access.remove_member(self.scope, target.id, session_token=token, expected_version=view.version)
        # Seed a second owner to exercise the retention invariant under competing writes.
        with self.db.transaction(write=True) as connection:
            connection.execute("UPDATE workspace_memberships SET role='owner' WHERE user_id=?", (self.owner.id,))
        version = self.version()
        def demote(user_id, session):
            try:
                self.access.set_member_role(self.scope, user_id, 'admin', session_token=session, expected_version=version)
                return 'ok'
            except (Conflict, Forbidden):
                return 'refused'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda pair:demote(*pair), ((self.owner.id,self.token),(target.id,token))))
        self.assertEqual(sorted(results), ['ok','refused'])
        with self.db.transaction() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workspace_memberships WHERE role='owner' AND status='active'").fetchone()[0], 1)

    def test_invalid_transfer_rule_and_atomic_audit_states(self):
        target, _ = self.add('member')
        invited = self.accounts.create_user('invited', 'Invited', session_token=self.token)
        with self.assertRaises(NotFound):
            self.access.transfer_ownership(self.scope, invited.user.id, session_token=self.token, expected_version=self.version())
        with self.assertRaises(InvalidArgument):
            self.access.put_rule(self.scope, AccessRule(self.scope.root_id,'everyone','','edit','allow'), session_token=self.token, expected_version=self.version())
        with self.db.transaction(write=True) as connection:
            connection.execute("UPDATE users SET status='reset_required' WHERE id=?", (target.id,))
        with self.assertRaises(InvalidArgument):
            self.access.transfer_ownership(self.scope, target.id, session_token=self.token, expected_version=self.version())
        version = self.version()
        with patch.object(AuditRepository, 'append', side_effect=RuntimeError('audit failed')):
            with self.assertRaises(RuntimeError):
                self.access.set_read_scope(self.scope, 'everyone', session_token=self.token, expected_version=version)
        view = self.access.workspace_access(self.scope, session_token=self.token)
        self.assertEqual((view.version,view.read_scope), (version,'members'))
        self.access.set_read_scope(self.scope, 'everyone', session_token=self.token, expected_version=version)
        with self.db.transaction() as connection:
            row = connection.execute("SELECT * FROM audit_events WHERE event_type='access.set_read_scope'").fetchone()
            self.assertEqual(row['actor_id'], self.owner.id)
            before, after = json.loads(row['before_json']), json.loads(row['after_json'])
            self.assertEqual((before['settings']['read_scope'],after['settings']['read_scope']), ('members','everyone'))
            self.assertEqual(after['settings']['version'], version+1)
