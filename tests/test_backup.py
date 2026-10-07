from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from src.storage.management import initialize_database
from src.storage.backup import backup_database
from src.storage import Database
from src.services.bootstrap import bootstrap_admin
from src.services.identity import IdentityService
from src.services.content import ContentService
from src.services.unit_of_work import ApplicationUnitOfWork

class BackupTest(unittest.TestCase):
    def test_live_wal_snapshot_can_restore_real_document(self):
        with TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'live.sqlite'
            initialize_database(source)
            db = Database(source)
            bootstrap_admin(db, 'admin', 'Admin', 'backup-test-password-123')
            grant = IdentityService(db).login('admin', 'backup-test-password-123', source='local')
            with ApplicationUnitOfWork(db).transaction() as work: scope = work.default_scope()
            # Hold a connection so committed pages remain in WAL during backup.
            with closing(sqlite3.connect(source)) as keeper:
                keeper.execute('PRAGMA wal_autocheckpoint=0')
                doc = ContentService(db).create_document(scope, scope.root_id, 'Backup', content='最新正文\n', session_token=grant.session_token)
                target = backup_database(source, root / 'snapshot.sqlite')
                self.assertEqual(target.stat().st_mode & 0o777, 0o600)
                original = target.read_bytes()
                with self.assertRaises(FileExistsError): backup_database(source, target)
                self.assertEqual(target.read_bytes(), original)
                with self.assertRaises(ValueError): backup_database(source, source)
            restored = root / 'restore.sqlite'; shutil.copyfile(target, restored)
            initialize_database(restored)
            result = ContentService(Database(restored)).read_document(scope, doc.id, session_token=grant.session_token)
            self.assertEqual(result.content, '最新正文\n')
            self.assertEqual(result.revision_id, doc.revision_id)

    def test_missing_source_is_not_created(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / 'missing.sqlite'
            with self.assertRaises(sqlite3.OperationalError): backup_database(source, Path(directory) / 'out.sqlite')
            self.assertFalse(source.exists())
