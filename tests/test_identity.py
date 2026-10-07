"""Real SQLite identity lifecycle, committed accounting and verification races."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import Event, Thread

from src.core.errors import Conflict, InvalidArgument, RateLimited, Unauthenticated
from src.identity.passwords import PasswordHasher
from src.identity.tokens import token_digest, new_token
from src.identity.validation import normalize_login_name, validate_password
from src.services.identity import IdentityService
from src.services.unit_of_work import ApplicationUnitOfWork
from src.storage.database import Database
from src.storage.management import initialize_database
from src.storage.identity_repository import IdentityRepository, UserRecord, PasswordCredentialRecord, AccountTokenRecord
from tests.helpers import TempPathTestCase

PASSWORD = ' Unicode 密码    '

class IdentityTests(TempPathTestCase):
    def setUp(self):
        super().setUp()
        self.path = self.temp_path()
        initialize_database(self.path)
        self.db = Database(self.path)
        self.now = datetime(2026, 10, 3, tzinfo=timezone.utc)
        self.hasher = PasswordHasher()
        self.service = IdentityService(self.db, clock=lambda: self.now, password_hasher=self.hasher)
        self.seed()

    def seed(self, status='active'):
        with self.db.transaction(write=True) as connection:
            repo = IdentityRepository(connection)
            stamp = self.now.isoformat()
            repo.insert_user(UserRecord('u', 'alice', 'Alice', status, False, 1, 1, stamp, stamp))
            repo.set_password_credential(PasswordCredentialRecord('u', self.hasher.hash(PASSWORD), 1, stamp))

    def grant(self):
        return self.service.login('ALICE', PASSWORD, source='local')

    def account_token(self, purpose):
        token = new_token()
        with self.db.transaction(write=True) as connection:
            repo = IdentityRepository(connection)
            user = repo.get_user('u')
            status = 'invited' if purpose == 'activate' else 'reset_required'
            repo.update_user(replace(user, status=status, version=user.version+1), expected_version=user.version)
            expiry = self.now + timedelta(hours=48 if purpose == 'activate' else 1)
            repo.insert_account_token(AccountTokenRecord(token_digest(token), 'u', purpose, user.credential_version,
                self.now.isoformat(), expiry.isoformat(), None, None))
        return token

    def test_password_and_login_contracts(self):
        self.assertEqual(normalize_login_name('Alice_123'), 'alice_123')
        for size in (8, 128):
            password = ' ' + '密' * (size-2) + ' '
            self.assertEqual(validate_password(password), password)
            encoded = self.hasher.hash(password)
            self.assertTrue(encoded.startswith('$argon2id$v=19$m=19456,t=2,p=1$'))
            self.assertTrue(self.hasher.verify(encoded, password))
            self.assertFalse(self.hasher.verify(encoded, password.strip()))
        for size in (7, 129):
            with self.assertRaises(InvalidArgument):
                validate_password('a'*size)
        for name in ('ab','1alice','älïce','a'*33):
            with self.assertRaises(InvalidArgument):
                normalize_login_name(name)

    def test_session_profile_logout_and_fixed_expiry(self):
        grant = self.grant()
        self.assertEqual(self.service.current_session(session_token=grant.session_token).user.login_name, 'alice')
        with self.db.transaction() as connection:
            repo = IdentityRepository(connection)
            self.assertIsNone(repo.get_session(grant.session_token))
            self.assertIsNotNone(repo.get_session(token_digest(grant.session_token)))
        updated = self.service.change_display_name('New Alice', session_token=grant.session_token, expected_version=1)
        self.assertEqual(updated.version, 2)
        with self.assertRaises(Conflict):
            self.service.change_display_name('New Alice', session_token=grant.session_token, expected_version=1)
        self.now += timedelta(hours=23)
        self.assertEqual(self.service.current_session(session_token=grant.session_token).expires_at, grant.expires_at)
        self.now += timedelta(hours=1)
        with self.assertRaises(Unauthenticated):
            self.service.current_session(session_token=grant.session_token)
        grant = self.grant()
        self.service.logout(session_token=grant.session_token)
        with self.assertRaises(Unauthenticated):
            self.service.current_session(session_token=grant.session_token)
        with ApplicationUnitOfWork(self.db, clock=lambda: self.now).transaction() as work:
            self.assertIsNone(work.resolve_principal(None).user_id)
            for token in ('bad', ''):
                with self.assertRaises(Unauthenticated):
                    work.resolve_principal(token)

    def test_change_password_revokes_all_sessions_and_audits(self):
        first, second = self.grant(), self.grant()
        self.service.change_password(PASSWORD, 'new password of fifteen', session_token=first.session_token, source='local')
        for grant in (first, second):
            with self.assertRaises(Unauthenticated):
                self.service.current_session(session_token=grant.session_token)
        with self.assertRaises(Unauthenticated):
            self.grant()
        self.service.login('alice', 'new password of fifteen', source='local')
        with self.db.transaction() as connection:
            event = connection.execute('SELECT * FROM audit_events').fetchone()
            self.assertEqual(event['event_type'], 'identity.change_password')
            self.assertNotIn(PASSWORD, str(dict(event)))

    def test_activate_reset_single_use_and_expiry_boundary(self):
        for purpose in ('activate','reset'):
            token = self.account_token(purpose)
            method = self.service.activate if purpose == 'activate' else self.service.reset_password
            user = method(token, PASSWORD, source='local')
            self.assertEqual(user.status,'active')
            with self.assertRaises(Unauthenticated):
                method(token, PASSWORD, source='local')
            with self.db.transaction() as connection:
                self.assertEqual(connection.execute('SELECT COUNT(*) FROM sessions').fetchone()[0],0)
            token = self.account_token(purpose)
            self.now += timedelta(hours=48 if purpose == 'activate' else 1)
            with self.assertRaises(Unauthenticated):
                method(token, PASSWORD, source='local')

    def test_throttle_failure_counts_commit_and_window_recovers(self):
        for _ in range(10):
            with self.assertRaises(Unauthenticated):
                self.service.login('alice', 'wrong', source='local')
        with self.assertRaises(RateLimited) as error:
            self.grant()
        self.assertEqual(error.exception.details['retry_after'],900)
        with self.db.transaction() as connection:
            rows = connection.execute('SELECT bucket_type, attempts FROM auth_throttles').fetchall()
            self.assertEqual(dict(rows), {'source':11,'login':11})
        with self.assertRaises(Unauthenticated):
            self.service.change_password(PASSWORD, PASSWORD, session_token=123, source='malformed')
        with self.db.transaction() as connection:
            self.assertEqual(connection.execute("SELECT attempts FROM auth_throttles WHERE bucket_type='source' AND bucket_key='malformed'").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT attempts FROM auth_throttles WHERE bucket_type='password' AND bucket_key=?", (token_digest('invalid'),)).fetchone()[0], 1)
        self.now += timedelta(minutes=15)
        self.grant()
        for index in range(59):
            with self.assertRaises(Unauthenticated):
                self.service.login('unknown'+str(index), 'wrong', source='local')
        with self.assertRaises(RateLimited):
            self.service.login('freshname','wrong',source='local')
        with self.db.transaction() as connection:
            self.assertEqual(connection.execute("SELECT attempts FROM auth_throttles WHERE bucket_type='login' AND bucket_key='freshname'").fetchone()[0],1)

    def test_login_verify_race_cannot_issue_after_disable_or_reset(self):
        original = self.hasher.verify
        for state in ('disabled','reset_required'):
            reached, proceed = Event(), Event()
            def blocked(encoded, password):
                result = original(encoded, password)
                reached.set()
                self.assertTrue(proceed.wait(10))
                return result
            self.hasher.verify = blocked
            outcome=[]
            def login():
                try:
                    outcome.append(self.grant())
                except Exception as exc:
                    outcome.append(exc)
            thread=Thread(target=login)
            thread.start()
            self.assertTrue(reached.wait(10))
            other=Database(self.path)
            with other.transaction(write=True) as connection:
                repo=IdentityRepository(connection)
                user=repo.get_user('u')
                repo.update_user(replace(user,status=state,version=user.version+1,
                    credential_version=user.credential_version+1),expected_version=user.version)
            proceed.set()
            thread.join(10)
            self.assertFalse(thread.is_alive())
            self.assertIsInstance(outcome[0],Unauthenticated)
            with self.db.transaction(write=True) as connection:
                repo=IdentityRepository(connection)
                user=repo.get_user('u')
                repo.update_user(replace(user,status='active',version=user.version+1),expected_version=user.version)
                repo.set_password_credential(PasswordCredentialRecord('u',self.hasher.hash(PASSWORD),user.credential_version,self.now.isoformat()))
            self.hasher.verify=original
        with self.db.transaction() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM sessions').fetchone()[0],0)

    def test_generation_and_status_reject_existing_sessions_uniformly(self):
        grant = self.grant()
        with self.db.transaction(write=True) as connection:
            repo = IdentityRepository(connection)
            user = repo.get_user('u')
            repo.update_user(replace(user, credential_version=2, version=2), expected_version=1)
        with self.assertRaises(Unauthenticated):
            self.service.current_session(session_token=grant.session_token)
        for status in ('invited', 'disabled', 'reset_required'):
            with self.db.transaction(write=True) as connection:
                repo = IdentityRepository(connection)
                user = repo.get_user('u')
                repo.update_user(replace(user, status=status, version=user.version+1), expected_version=user.version)
            with self.assertRaises(Unauthenticated) as failure:
                self.grant()
            self.assertEqual(str(failure.exception), 'authentication failed')
        with self.assertRaises(Unauthenticated) as failure:
            self.service.login('nobody', PASSWORD, source='local')
        self.assertEqual(str(failure.exception), 'authentication failed')
        with self.assertRaises(Unauthenticated):
            self.service.activate('', PASSWORD, source='local')
        with self.db.transaction() as connection:
            self.assertEqual(connection.execute("SELECT attempts FROM auth_throttles WHERE bucket_type='source'").fetchone()[0], 6)

    def test_concurrent_account_token_consumption_has_one_winner(self):
        token=self.account_token('activate')
        reached, proceed=Event(), Event()
        original=self.hasher.hash
        def blocked(password):
            result=original(password)
            reached.set()
            self.assertTrue(proceed.wait(10))
            return result
        self.hasher.hash=blocked
        outcome=[]
        def activate():
            try:
                outcome.append(self.service.activate(token,PASSWORD,source='one'))
            except Exception as exc:
                outcome.append(exc)
        thread=Thread(target=activate)
        thread.start()
        self.assertTrue(reached.wait(10))
        other=IdentityService(Database(self.path),clock=lambda:self.now)
        self.assertEqual(other.activate(token,PASSWORD,source='two').status,'active')
        proceed.set()
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertIsInstance(outcome[0],Unauthenticated)
