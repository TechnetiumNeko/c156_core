"""Write coordination uses real services and substitute editors, never a tty."""
import io
from contextlib import contextmanager
from unittest.mock import patch

from src.cli.app import CLI
from src.core.errors import Conflict, StorageBusy
from src.editor import Editor, EditorResult
from src.services.content import ContentService
from src.storage import Database
from src.storage.errors import StorageError
from src.storage.management import initialize_database
from tests.helpers import TempPathTestCase, revision_state


class TrackingDatabase(Database):
    active = 0

    @contextmanager
    def transaction(self, *, write=False):
        with super().transaction(write=write) as connection:
            self.active += 1
            try:
                yield connection
            finally:
                self.active -= 1


class CLIWriteTests(TempPathTestCase):
    def setUp(self):
        super().setUp()
        self.path = self.temp_path()
        self.root_scope = initialize_database(self.path)
        self.database = TrackingDatabase(self.path)
        self.service = ContentService(self.database)
        self.scope = self.service.default_scope()
        self.folder = self.service.create_folder(self.scope, self.scope.root_id, 'products')
        self.doc = self.service.create_document(self.scope, self.folder.id, 'doc', content='original\r\n\r\n')
        self.output = io.StringIO()
        self.inputs = []
        self.opened = []
        self.results = [EditorResult(True, '用户未保存正文', True)]
        self.during_edit = lambda: None
        self.cli = CLI(self.service, self.scope, output=lambda *a, **kw: print(*a, file=self.output, **kw),
                       prompt=self.prompt, editor_factory=self.factory)

    def prompt(self, text):
        self.assertEqual(self.database.active, 0)
        return self.inputs.pop(0) if self.inputs else 'later'

    def factory(self, document_id, title, content, system_command_handler=None):
        self.assertEqual(self.database.active, 0)
        self.opened.append((document_id, title, content))
        test = self
        class Substitute:
            def run(self):
                test.assertEqual(test.database.active, 0)
                test.during_edit()
                return test.results.pop(0)
        return Substitute()

    def test_mkdir_registers_virtual_parent_and_name(self):
        self.cli.execute('mkdir "~/products//中文 空格/"')
        self.assertEqual(self.service.resolve_path(self.scope, '/products/中文 空格').kind, 'folder')

    def test_create_confirm_and_cancel_preserves_empty_document(self):
        self.inputs = ['y']
        self.results = [EditorResult(False, '', False)]
        self.cli.execute('edit "products/new doc"')
        created = self.service.resolve_path(self.scope, '/products/new doc')
        self.assertEqual(self.service.read_document(self.scope, created.id).content, '')
        self.assertEqual(self.opened, [(created.id, '/products/new doc', '')])
        self.inputs = ['N']
        self.cli.execute('edit products/no')
        self.assertEqual(len(self.opened), 1)
        self.assertEqual([x.name for x in self.service.list_children(self.scope, self.folder.id)], ['doc', 'new doc'])

    def test_invalid_document_trailing_slash_never_creates_or_opens(self):
        self.inputs = ['y']
        for value in ('products/new/', 'products/doc/'):
            self.cli.execute('edit ' + value)
        self.assertEqual(self.opened, [])
        self.assertEqual([n.name for n in self.service.list_children(self.scope, self.folder.id)], ['doc'])

    def test_missing_parent_and_document_dotdot_never_create(self):
        self.inputs = ['y']
        for value in ('missing/new', 'products/doc/../new'):
            self.cli.execute('edit ' + value)
        self.assertEqual(self.opened, [])
        self.assertEqual([n.name for n in self.service.list_children(self.scope, self.folder.id)], ['doc'])

    def test_save_uses_first_read_revision_and_pure_editor_inputs(self):
        original_save = self.service.save_document
        with patch.object(self.service, 'save_document', wraps=original_save) as save:
            self.cli.execute('edit products/doc')
        self.assertEqual(self.opened, [(self.doc.id, '/products/doc', self.doc.content)])
        self.assertEqual(save.call_args.kwargs, {'expected_revision_id': self.doc.revision_id})
        self.assertEqual(self.service.read_document(self.scope, self.doc.id).content, '用户未保存正文')
        self.assertIsNone(self.cli.context.pending_edit)

    def test_untouched_real_editor_result_does_not_create_revision(self):
        def factory(*args, **kwargs):
            editor = Editor(*args, **kwargs)
            editor.run = lambda: editor.result()
            editor._execute_command('wq')
            return editor
        self.cli.context.editor_factory = factory
        self.cli.execute('edit products/doc')
        self.assertEqual(len(revision_state(self.path, self.doc.id)), 1)
        self.assertEqual(self.service.read_document(self.scope, self.doc.id).content, self.doc.content)

    def conflict(self):
        self.service.save_document(self.scope, self.doc.id, 'other writer', expected_revision_id=self.doc.revision_id)
        self.during_edit = lambda: None

    def test_conflict_keeps_buffer_and_base_on_reopen(self):
        self.during_edit = self.conflict
        self.cli.execute('edit products/doc')
        self.assert_pending('用户未保存正文')
        self.results = [EditorResult(True, 'continued', True)]
        self.cli.execute('edit products/doc')
        self.assertEqual(self.opened[-1][2], '用户未保存正文')
        self.assert_pending('continued')
        self.assertEqual(self.service.read_document(self.scope, self.doc.id).content, 'other writer')

    def assert_pending(self, content):
        pending = self.cli.context.pending_edit
        self.assertEqual(pending.object_id, self.doc.id)
        self.assertEqual(pending.base_revision_id, self.doc.revision_id)
        self.assertEqual(pending.content, content)
        self.assertEqual(self.database.active, 0)

    def test_busy_and_unknown_storage_failure_keep_buffer(self):
        for error in (StorageBusy('locked'), StorageError('disk failed'), OSError('I/O failure')):
            with self.subTest(error=error):
                self.results = [EditorResult(True, '用户未保存正文', True)]
                with patch.object(self.service, 'save_document', side_effect=error):
                    self.cli.execute('edit products/doc')
                self.assert_pending('用户未保存正文')
                self.assertIn(str(error), self.output.getvalue())

    def test_deleted_document_retains_buffer(self):
        self.during_edit = lambda: self.service.delete_node(self.scope, self.doc.id, expected_version=self.doc.version)
        self.cli.execute('edit products/doc')
        self.assert_pending('用户未保存正文')

    def test_moved_outside_scope_retains_buffer(self):
        self.during_edit = lambda: self.service.move_node(self.root_scope, self.doc.id, self.root_scope.root_id, expected_version=self.doc.version)
        self.cli.execute('edit products/doc')
        self.assert_pending('用户未保存正文')

    def test_failure_offers_view_and_continue_preserving_base(self):
        self.during_edit = self.conflict
        self.inputs = ['view', 'continue', 'later']
        self.results.append(EditorResult(True, 'continued', True))
        self.cli.execute('edit products/doc')
        self.assertIn('用户未保存正文', self.output.getvalue())
        self.assertEqual(self.opened[-1][2], '用户未保存正文')
        self.assert_pending('continued')

    def test_another_document_requires_explicit_discard(self):
        self.during_edit = self.conflict
        self.cli.execute('edit products/doc')
        self.service.create_document(self.scope, self.folder.id, 'other')
        self.cli.execute('edit products/other')
        self.assertEqual(len(self.opened), 1)
        self.assert_pending('用户未保存正文')
        self.inputs = ['discard']
        self.results = [EditorResult(False, '', False)]
        self.cli.execute('edit products/other')
        self.assertEqual(len(self.opened), 2)
        self.assertIsNone(self.cli.context.pending_edit)

    def test_exit_requires_explicit_discard_and_eof_warns(self):
        self.during_edit = self.conflict
        self.cli.execute('edit products/doc')
        self.assertTrue(self.cli.execute('exit'))
        self.assert_pending('用户未保存正文')
        self.cli.context.prompt = lambda _: (_ for _ in ()).throw(EOFError())
        self.cli.run()
        self.assertIn('未保存', self.output.getvalue())
        self.assert_pending('用户未保存正文')
        self.cli.context.prompt = lambda _: 'discard'
        self.assertFalse(self.cli.execute('exit'))
        self.assertIsNone(self.cli.context.pending_edit)

    def test_active_editor_exit_and_nested_edit_are_rejected(self):
        editor = Editor(self.doc.id, self.doc.path, self.doc.content, self.cli._execute_editor_command)
        self.cli.context.active_editor = editor
        editor._handle_normal_key('i')
        editor._handle_insert_key('X')
        for command in ('exit', 'edit products/doc'):
            editor._execute_command(command)
            self.assertFalse(editor.closed)
            self.assertFalse(self.cli.exit_requested)
            self.assertIn('失败', editor.status)
        self.assertTrue(editor.result().changed)

    def test_reopened_pending_q_preserves_failed_save_until_explicit_discard(self):
        self.during_edit = self.conflict
        self.cli.execute('edit products/doc')
        self.results = [EditorResult(False, '用户未保存正文', False)]
        self.cli.execute('edit products/doc')
        self.assert_pending('用户未保存正文')
        self.results = [EditorResult(False, '用户未保存正文', False, discard_requested=True)]
        self.cli.execute('edit products/doc')
        self.assertIsNone(self.cli.context.pending_edit)

    def test_deleted_pending_can_reopen_original_path_without_recreation(self):
        self.during_edit = lambda: self.service.delete_node(self.scope, self.doc.id, expected_version=self.doc.version)
        self.cli.execute('edit products/doc')
        self.during_edit = lambda: None
        self.results = [EditorResult(True, 'continued after deletion', True)]
        self.cli.execute('edit products/doc')
        self.assertEqual(self.opened[-1], (self.doc.id, '/products/doc', '用户未保存正文'))
        self.assert_pending('continued after deletion')
        self.assertEqual(self.service.list_children(self.scope, self.folder.id), [])

    def test_moved_outside_pending_reopens_original_id_and_base(self):
        self.during_edit = lambda: self.service.move_node(self.root_scope, self.doc.id, self.root_scope.root_id, expected_version=self.doc.version)
        self.cli.execute('edit products/doc')
        self.during_edit = lambda: None
        self.results = [EditorResult(True, 'continued outside scope', True)]
        self.cli.execute('edit /products/doc')
        self.assertEqual(self.opened[-1], (self.doc.id, '/products/doc', '用户未保存正文'))
        self.assert_pending('continued outside scope')
        self.assertEqual(self.service.list_children(self.scope, self.folder.id), [])

    def test_replacement_at_old_path_never_receives_pending_text(self):
        self.during_edit = lambda: self.service.delete_node(self.scope, self.doc.id, expected_version=self.doc.version)
        self.cli.execute('edit products/doc')
        self.during_edit = lambda: None
        replacement = self.service.create_document(self.scope, self.folder.id, 'doc', content='replacement')
        self.cli.execute('edit products/doc')
        self.assertEqual(len(self.opened), 1)
        self.assert_pending('用户未保存正文')
        self.inputs = ['discard']
        self.results = [EditorResult(False, 'replacement', False)]
        self.cli.execute('edit products/doc')
        self.assertEqual(self.opened[-1], (replacement.id, '/products/doc', 'replacement'))
        self.assertEqual(self.service.read_document(self.scope, replacement.id).content, 'replacement')
        self.assertIsNone(self.cli.context.pending_edit)

    def cancel_factory(self, *args, **kwargs):
        class CancelEditor:
            def run(self):
                raise KeyboardInterrupt()
        return CancelEditor()

    def test_creation_prompt_interrupt_cancels_without_terminating_cli(self):
        self.cli.context.prompt = lambda _: (_ for _ in ()).throw(KeyboardInterrupt())
        self.assertTrue(self.cli.execute('edit products/new'))
        self.assertEqual([n.name for n in self.service.list_children(self.scope, self.folder.id)], ['doc'])
        self.assertIn('取消', self.output.getvalue())
        self.assertIsNone(self.cli.context.active_editor)
        self.assertTrue(self.cli.execute('pwd'))

    def test_new_empty_document_remains_when_editor_is_interrupted(self):
        self.inputs = ['y']
        self.cli.context.editor_factory = self.cancel_factory
        self.assertTrue(self.cli.execute('edit products/new'))
        created = self.service.resolve_path(self.scope, '/products/new')
        self.assertEqual(self.service.read_document(self.scope, created.id).content, '')
        self.assertIsNone(self.cli.context.active_editor)
        self.assertIn('取消', self.output.getvalue())

    def test_interrupted_editor_keeps_existing_failed_buffer_and_base(self):
        self.during_edit = self.conflict
        self.cli.execute('edit products/doc')
        self.cli.context.editor_factory = self.cancel_factory
        self.assertTrue(self.cli.execute('edit products/doc'))
        self.assert_pending('用户未保存正文')
        self.assertIsNone(self.cli.context.active_editor)
        self.assertIn('未保存', self.output.getvalue())

    def test_interrupted_save_retains_returned_buffer_and_base(self):
        with patch.object(self.service, 'save_document', side_effect=KeyboardInterrupt()):
            self.assertTrue(self.cli.execute('edit products/doc'))
        self.assert_pending('用户未保存正文')
        self.assertIsNone(self.cli.context.active_editor)
        self.assertIn('未保存', self.output.getvalue())
        self.assertEqual(self.service.read_document(self.scope, self.doc.id).content, self.doc.content)

    def test_interrupted_real_editor_retains_current_text_without_a_terminal(self):
        def factory(*args, **kwargs):
            editor = Editor(*args, **kwargs)
            def cancel_run():
                editor._handle_normal_key('i')
                editor._handle_insert_key('X')
                raise KeyboardInterrupt()
            editor.run = cancel_run
            return editor
        self.cli.context.editor_factory = factory
        self.assertTrue(self.cli.execute('edit products/doc'))
        self.assert_pending('Xoriginal\n\n')
        self.assertIsNone(self.cli.context.active_editor)

    def eof_prompt(self, text):
        self.assertEqual(self.database.active, 0)
        raise EOFError()

    def test_creation_confirmation_eof_cancels_without_create_or_editor(self):
        self.cli.context.prompt = self.eof_prompt
        self.assertTrue(self.cli.execute('edit products/new'))
        self.assertEqual([n.name for n in self.service.list_children(self.scope, self.folder.id)], ['doc'])
        self.assertEqual(self.opened, [])
        self.assertIsNone(self.cli.context.active_editor)
        self.assertIn('取消', self.output.getvalue())
        self.assertTrue(self.cli.execute('pwd'))

    def test_eof_while_handling_pending_before_creation_keeps_text_and_base(self):
        self.during_edit = self.conflict
        self.cli.execute('edit products/doc')
        self.cli.context.prompt = self.eof_prompt
        self.assertTrue(self.cli.execute('edit products/new'))
        self.assert_pending('用户未保存正文')
        self.assertEqual(len(self.opened), 1)
        self.assertEqual([n.name for n in self.service.list_children(self.scope, self.folder.id)], ['doc'])
        self.assertIsNone(self.cli.context.active_editor)
        self.assertIn('未保存', self.output.getvalue())

    def test_creation_confirmation_accepts_yes_case_insensitively(self):
        for answer in ('yes', 'YES', 'Y'):
            with self.subTest(answer=answer):
                name = 'new-' + answer
                self.inputs = [answer]
                self.results = [EditorResult(False, '', False)]
                self.assertTrue(self.cli.execute('edit products/' + name))
                created = self.service.resolve_path(self.scope, '/products/' + name)
                self.assertEqual(self.service.read_document(self.scope, created.id).content, '')
