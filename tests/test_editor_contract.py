"""Pure editor contract, exercised without a terminal."""
import unittest
from src.editor import Editor, EditorResult


class EditorContractTests(unittest.TestCase):
    def editor(self, content='正文\r\n\r\n'):
        return Editor('document-id', '/products/doc', content)

    def test_untouched_crlf_and_blank_lines_are_exact(self):
        for command in ('q', 'wq', 'q!'):
            with self.subTest(command=command):
                editor = self.editor()
                editor._execute_command(command)
                self.assertTrue(editor.closed)
                self.assertEqual(editor.result(), EditorResult(command == 'wq', '正文\r\n\r\n', False, command == 'q!'))

    def test_changed_q_requires_explicit_save_or_discard(self):
        editor = self.editor('old')
        editor._handle_normal_key('i')
        editor._handle_insert_key('X')
        editor._handle_insert_key('ESC')
        editor._execute_command('q')
        self.assertFalse(editor.closed)
        editor._execute_command('wq')
        self.assertEqual(editor.result(), EditorResult(True, 'Xold', True))

    def test_help_is_read_only_and_result_is_document(self):
        editor = self.editor()
        editor._execute_command('h')
        before = editor.buffer.text
        for key in ('i', 'o', 'd', 'd'):
            editor._handle_normal_key(key)
        editor._execute_command('wq')
        self.assertFalse(editor.closed)
        self.assertEqual(editor.buffer.text, before)
        self.assertEqual(editor.result().content, '正文\r\n\r\n')
        editor._execute_command('q')
        editor._execute_command('q')
        self.assertTrue(editor.closed)
        self.assertFalse(editor.result().changed)

    def test_delegated_command_receives_only_command(self):
        seen = []
        editor = Editor('id', '/title', 'text', system_command_handler=seen.append)
        editor._execute_command('pwd')
        self.assertEqual(seen, ['pwd'])
        self.assertEqual(editor.result().content, 'text')

    def test_q_bang_marks_explicit_discard(self):
        editor = self.editor('text')
        editor._execute_command('q!')
        self.assertTrue(editor.result().discard_requested)
