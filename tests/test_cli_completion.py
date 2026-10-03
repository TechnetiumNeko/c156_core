"""Readline completion using temporary virtual content, never a terminal."""
import shlex
from unittest.mock import patch

from src.cli.completion import Completer
from tests.test_cli_reads import CLIReadFixture


class CLICompletionTests(CLIReadFixture):
    # Reuse fixture setup only, not the read test methods.
    def setUp(self):
        super().setUp()
        self.names = ['space folder', "single'quote", 'double"quote', '.folder', 'money dollar$cash', 'money tick`name']
        for name in self.names:
            self.service.create_folder(self.scope, self.scope.root_id, name, session_token=self.token)
        self.service.create_document(self.scope, self.scope.root_id, 'space document', session_token=self.token)

    def complete(self, line, begidx=None):
        if begidx is None:
            begidx = line.rfind(' ') + 1
        with patch('readline.get_line_buffer', return_value=line), patch('readline.get_begidx', return_value=begidx), patch('readline.get_endidx', return_value=len(line)):
            completer = Completer(self.cli)
            values = []
            for state in range(100):
                value = completer(line[begidx:], state)
                if value is None:
                    break
                values.append(value)
        return values

    def test_candidates_absolute_home_relative_empty_and_type(self):
        fs = self.cli.fs
        for prefix in ('', '/', '~/'):
            candidates = fs.completion_candidates(prefix)
            self.assertIn(prefix + '.folder/', candidates)
            self.assertIn(prefix + 'space document', candidates)
            self.assertNotIn(prefix + 'space document', fs.completion_candidates(prefix, directories_only=True))
        self.cli.execute('cd products')
        self.assertIn('../space folder/', fs.completion_candidates('../spa'))
        self.assertEqual(fs.completion_candidates('../../'), [])
        self.assertEqual(fs.completion_candidates('/missing/'), [])
        self.assertEqual(fs.completion_candidates('/space document/'), [])

    def test_returned_paths_are_shell_arguments(self):
        matches = self.complete('cd ')
        decoded = [shlex.split('cd ' + value) for value in matches]
        for name in self.names:
            self.assertIn(['cd', name + '/'], decoded)
        self.assertNotIn(['cd', 'space document'], decoded)

    def test_readline_open_quote_and_spaces_preserve_prefix(self):
        for line, expected in [("cd 'space f", 'space folder/'), ('cd "space f', 'space folder/'), ("cd 'single", "single'quote/"), ('cd "double', 'double"quote/'), ('cd space\\ f', 'space folder/'), ('cd "money d', 'money dollar$cash/'), ('cd "money t', 'money tick`name/')]:
            with self.subTest(line=line):
                begidx = line.rfind(' ') + 1
                matches = self.complete(line, begidx)
                self.assertTrue(matches)
                self.assertIn(['cd', expected], [shlex.split(line[:begidx] + value) for value in matches])

    def test_command_help_and_tree_options(self):
        self.assertEqual(self.complete('c'), ['cd', 'cat'])
        self.assertEqual(self.complete('help ca'), ['cat'])
        for line in ('tree -d ', 'tree --max-depth 2 ', 'tree products ', 'pwd ', 'unknown '):
            self.assertEqual(self.complete(line), [])
        self.assertTrue(self.complete('tree /spa'))

    def test_ideographic_space_stays_inside_one_shell_path_argument(self):
        name = '中文\u3000目录'
        self.service.create_folder(self.scope, self.scope.root_id, name, session_token=self.token)
        line = 'cd 中文\u3000目'
        self.assertEqual(shlex.split(line), ['cd', '中文\u3000目'])
        begidx = len('cd ')
        matches = self.complete(line, begidx)
        self.assertEqual([shlex.split(line[:begidx] + value) for value in matches],
                         [['cd', name + '/']])
        self.cli.execute(line[:begidx] + matches[0])
        self.assertEqual(self.cli.fs.display(), '/' + name)

    def test_private_option_preserves_path_completion(self):
        self.assertEqual(self.complete('mkdir --private pro'), ['products/'])
        self.assertEqual(self.complete('edit --private pro'), ['products/'])
        self.assertEqual(self.complete('cd --private pro'), [])
