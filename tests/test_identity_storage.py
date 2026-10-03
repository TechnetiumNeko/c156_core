"""Schema scope, immutable records and committed fixed-window accounting."""
import sqlite3
from datetime import datetime, timedelta, timezone

from src.storage.database import Database
from src.storage.errors import SchemaError
from src.storage.management import initialize_database
from src.storage.identity_repository import IdentityRepository, UserRecord
from src.storage.access_repository import AccessRepository, MembershipRecord, AccessRuleRecord, PrivacyRecord, LockRecord
from src.storage.auth_throttle_repository import AuthThrottleRepository
from tests.helpers import TempPathTestCase, FIXTURE_TIME


class IdentityStorageTests(TempPathTestCase):
    def setUp(self):
        super().setUp()
        self.path = self.temp_path()
        self.scope = initialize_database(self.path)
        self.db = Database(self.path)

    def seed_user(self, connection):
        user = UserRecord('u','alice','Alice','active',False,1,1,FIXTURE_TIME,FIXTURE_TIME)
        identity = IdentityRepository(connection)
        identity.insert_user(user)
        access = AccessRepository(connection,workspace_id=self.scope.workspace_id,branch_id=self.scope.branch_id)
        access.insert_membership(MembershipRecord(self.scope.workspace_id,'u','editor','active',1,FIXTURE_TIME,FIXTURE_TIME))
        return identity, access

    def test_initial_seed_and_v1_rejection_preserve_file(self):
        with self.db.transaction() as c:
            a = AccessRepository(c,workspace_id=self.scope.workspace_id,branch_id=self.scope.branch_id)
            self.assertEqual(a.get_settings().read_scope,'members')
            self.assertEqual(a.get_settings().version,1)
            self.assertEqual(IdentityRepository(c).list_users(),())
            self.assertEqual(c.execute('SELECT COUNT(*) FROM content_ownership WHERE creator_id IS NULL').fetchone()[0],5)
        with self.db.management_connection() as c:
            c.execute('PRAGMA user_version=1')
            c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        before = self.path.read_bytes()
        with self.assertRaises(SchemaError):
            with self.db.transaction():
                pass
        self.assertEqual(self.path.read_bytes(),before)

    def test_rules_scope_unique_and_member_removal_preserves_privacy(self):
        with self.db.transaction(write=True) as c:
            _,a = self.seed_user(c)
            rule = AccessRuleRecord(self.scope.workspace_id,self.scope.branch_id,self.scope.root_id,'user','u','u','read','allow')
            a.insert_rule(rule)
            with self.assertRaises(sqlite3.IntegrityError):
                a.insert_rule(rule)
            for table,tail in [('access_rules',"'user','u','u','edit','allow'"),('content_privacy',"'u','now'")]:
                with self.assertRaises(sqlite3.IntegrityError):
                    c.execute(f"INSERT INTO {table} VALUES (?,?,?,{tail})", (self.scope.workspace_id,'other',self.scope.root_id))
                with self.assertRaises(sqlite3.IntegrityError):
                    c.execute(f"INSERT INTO {table} VALUES (?,?,?,{tail})", ('other',self.scope.branch_id,self.scope.root_id))
            a.insert_privacy(PrivacyRecord(self.scope.workspace_id,self.scope.branch_id,self.scope.root_id,'u',FIXTURE_TIME))
            a.delete_user_rules('u')
            a.update_membership(MembershipRecord(self.scope.workspace_id,'u','editor','removed',2,FIXTURE_TIME,FIXTURE_TIME),expected_version=1)
            self.assertEqual(a.list_rules(),())
            self.assertEqual(a.get_privacy(self.scope.root_id).owner_id,'u')
            self.assertIsNone(a.get_ownership(self.scope.root_id).creator_id)

    def test_document_only_locks_and_immutable_identity_ownership_audit(self):
        with self.db.transaction(write=True) as c:
            _,a = self.seed_user(c)
            with self.assertRaises(sqlite3.IntegrityError):
                a.insert_lock(LockRecord(self.scope.workspace_id,self.scope.branch_id,self.scope.root_id,'u',FIXTURE_TIME))
            # Reuse a real object and entry shape to add a document.
            c.execute('INSERT INTO objects VALUES (?,?,?,?)',('doc',self.scope.workspace_id,'document',FIXTURE_TIME))
            c.execute('INSERT INTO entries SELECT workspace_id,branch_id,?,object_id,?,10,1,NULL,metadata_json,created_at,modified_at,NULL FROM entries WHERE object_id=?',('doc','doc',self.scope.root_id))
            a.insert_lock(LockRecord(self.scope.workspace_id,self.scope.branch_id,'doc','u',FIXTURE_TIME))
            a.update_membership(MembershipRecord(self.scope.workspace_id,'u','editor','removed',2,FIXTURE_TIME,FIXTURE_TIME),expected_version=1)
            self.assertEqual(a.get_lock('doc').locked_by,'u')
            for sql in ["UPDATE content_locks SET object_id='"+self.scope.root_id+"'", "UPDATE users SET login_name='bob'", "UPDATE content_ownership SET creator_id='u'"]:
                with self.assertRaises(sqlite3.IntegrityError):
                    c.execute(sql)
            c.execute("INSERT INTO audit_events VALUES ('a','u',NULL,'test','user','u',NULL,NULL,'now')")
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("DELETE FROM audit_events")

    def test_repository_updates_and_accounting_roll_back_with_caller(self):
        with self.db.transaction(write=True) as c:
            identity,a = self.seed_user(c)
            user=identity.get_user_by_login_name('alice')
            self.assertEqual(user.id,'u')
            self.assertFalse(a.update_settings(expected_version=9,read_scope='everyone'))
            self.assertTrue(a.update_settings(expected_version=1,read_scope='everyone'))
        with self.assertRaises(RuntimeError):
            with self.db.transaction(write=True) as c:
                a=AccessRepository(c,workspace_id=self.scope.workspace_id,branch_id=self.scope.branch_id)
                a.update_settings(expected_version=2,read_scope='members')
                raise RuntimeError('abort')
        with self.db.transaction() as c:
            self.assertEqual(c.execute('SELECT read_scope,version FROM workspace_access_settings').fetchone()[:],('everyone',2))

    def test_fixed_window_denial_commits_attempt_and_resets_at_boundary(self):
        now=datetime(2026,1,1,tzinfo=timezone.utc)
        for i in range(11):
            with self.db.transaction(write=True) as c:
                result=AuthThrottleRepository(c).consume('login','alice',limit=10,now=now+timedelta(seconds=i))
            self.assertEqual(result.allowed,i<10)
        self.assertEqual(result.retry_after,890)
        with self.db.transaction() as c:
            row=c.execute('SELECT * FROM auth_throttles').fetchone()
            self.assertEqual(row['attempts'],11)
            self.assertEqual(row['window_started_at'],now.isoformat())
        with self.db.transaction(write=True) as c:
            self.assertTrue(AuthThrottleRepository(c).consume('login','alice',limit=10,now=now+timedelta(seconds=900)).allowed)
            self.assertEqual(c.execute('SELECT attempts FROM auth_throttles').fetchone()[0],1)

    def test_cleanup_budget_shared_by_buckets_and_full_capacity_keeps_existing(self):
        now=datetime(2026,1,1,tzinfo=timezone.utc)
        expired=(now-timedelta(seconds=900)).isoformat()
        with self.db.transaction(write=True) as c:
            c.executemany('INSERT INTO auth_throttles VALUES (?,?,?,1)', [('login',str(i),expired) for i in range(150)])
            repo=AuthThrottleRepository(c)
            repo.consume('source','local',limit=60,now=now)
            repo.consume('token','token',limit=10,now=now)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM auth_throttles WHERE window_started_at=?',(expired,)).fetchone()[0],50)
            c.execute('DELETE FROM auth_throttles')
            c.executemany('INSERT INTO auth_throttles VALUES (?,?,?,1)', [('login',str(i),now.isoformat()) for i in range(10000)])
            self.assertTrue(repo.consume('login','0',limit=10,now=now).allowed)
            result=repo.consume('source','new',limit=60,now=now)
            self.assertFalse(result.allowed)
            self.assertEqual(result.retry_after,900)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM auth_throttles').fetchone()[0],10000)

            # 100-row cleanup leaves this newer expired bucket behind.
            c.executemany('INSERT INTO auth_throttles VALUES (?,?,?,1)', [
                ('token',str(i),(now-timedelta(seconds=901)).isoformat()) for i in range(100)
            ])
            c.execute('INSERT INTO auth_throttles VALUES (?,?,?,7)', ('token','surviving',expired))
            repo=AuthThrottleRepository(c)
            result=repo.consume('token','surviving',limit=10,now=now)
            self.assertFalse(result.allowed)
            self.assertEqual(result.retry_after,900)
            self.assertEqual(c.execute('SELECT window_started_at,attempts FROM auth_throttles WHERE bucket_type=? AND bucket_key=?', ('token','surviving')).fetchone()[:], (expired,7))
            self.assertEqual(c.execute('SELECT COUNT(*) FROM auth_throttles WHERE window_started_at>?', (expired,)).fetchone()[0],10000)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM auth_throttles WHERE window_started_at<=?', (expired,)).fetchone()[0],1)
            self.assertTrue(repo.consume('login','0',limit=10,now=now).allowed)
            # Once a slot is free, reusing the stale key may start a new window.
            c.execute("DELETE FROM auth_throttles WHERE bucket_type='login' AND bucket_key='1'")
            self.assertTrue(repo.consume('token','surviving',limit=10,now=now).allowed)
            self.assertEqual(c.execute('SELECT window_started_at,attempts FROM auth_throttles WHERE bucket_type=? AND bucket_key=?', ('token','surviving')).fetchone()[:], (now.isoformat(),1))
