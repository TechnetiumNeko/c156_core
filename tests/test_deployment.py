"""Actual deployment entrypoints and local Git remotes; no replacement tools."""
from pathlib import Path
import os
import subprocess
from tempfile import TemporaryDirectory
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'deploy' / 'deploy.sh'

class DeploymentPreflightTest(unittest.TestCase):
    def git(self, directory, *args):
        return subprocess.check_output(['git', '-C', str(directory), *args], text=True, stderr=subprocess.DEVNULL).strip()

    def checkout_fixture(self, directory):
        remote = Path(directory) / 'upstream'; remote.mkdir()
        self.git(remote, 'init', '-b', 'main')
        self.git(remote, 'config', 'user.email', 'test@example.com')
        self.git(remote, 'config', 'user.name', 'Deployment test')
        (remote / '.gitignore').write_text('/config.env\n/images.env\n/data/\n/deploy.lock\n/.deploy-sequence\n')
        deploy = remote / 'deploy'; deploy.mkdir()
        (deploy / 'common.sh').write_bytes(SCRIPT.with_name('common.sh').read_bytes())
        (remote / 'version').write_text('initial')
        self.git(remote, 'add', '.'); self.git(remote, 'commit', '-m', 'initial')
        root = Path(directory) / 'c156'
        self.git(remote, 'clone', str(remote), str(root))
        (root / 'config.env').write_text(f'SITE_DOMAIN=docs.example.com\nDEPLOY_ROOT={root}\nNGINX_MANAGED=0\n')
        (root / 'data').mkdir(); (root / 'data/c156.sqlite').write_bytes(b'keep')
        return remote, root

    def deploy(self, root, sha, sequence='1', image=None):
        image = image or 'registry.example.com/team/app@sha256:' + 'a' * 64
        # Stream the actual entrypoint, as Actions does, to an existing checkout.
        return subprocess.run(['bash', '-s', '--', str(root), sha, image, image, sequence, '--prepare'],
                              input=SCRIPT.read_text(), capture_output=True, text=True)

    def test_stale_deployment_preserves_checkout_images_and_data(self):
        with TemporaryDirectory() as directory:
            _, root = self.checkout_fixture(directory)
            sha = self.git(root, 'rev-parse', 'HEAD')
            (root / '.deploy-sequence').write_text('2\n')
            (root / 'images.env').write_text('keep images')
            result = self.deploy(root, sha)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('stale deployment rejected', result.stderr)
            self.assertEqual(self.git(root, 'rev-parse', 'HEAD'), sha)
            self.assertEqual((root / 'images.env').read_text(), 'keep images')
            self.assertEqual((root / 'data/c156.sqlite').read_bytes(), b'keep')

    def test_mutable_image_is_rejected_before_any_deployment(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.deploy(root, 'b' * 40, image='registry.example.com/app:latest')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('registry digest', result.stderr)
            self.assertFalse((root / 'images.env').exists())

    def test_fetch_checks_out_tested_sha_instead_of_latest_main(self):
        with TemporaryDirectory() as directory:
            remote, root = self.checkout_fixture(directory)
            (remote / 'version').write_text('tested')
            self.git(remote, 'commit', '-am', 'tested')
            tested_sha = self.git(remote, 'rev-parse', 'HEAD')
            (remote / 'version').write_text('newer main')
            self.git(remote, 'commit', '-am', 'newer')
            result = self.deploy(root, tested_sha)
            # Invalid placeholder domain stops before Docker, after the real Git update.
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('set your real SITE_DOMAIN first', result.stderr)
            self.assertEqual(self.git(root, 'rev-parse', 'HEAD'), tested_sha)
            self.assertEqual((root / 'version').read_text(), 'tested')
            self.assertEqual((root / 'data/c156.sqlite').read_bytes(), b'keep')
            self.assertTrue((root / 'config.env').is_file())

    def test_local_source_changes_are_preserved(self):
        with TemporaryDirectory() as directory:
            _, root = self.checkout_fixture(directory)
            sha = self.git(root, 'rev-parse', 'HEAD')
            (root / 'version').write_text('local edit')
            result = self.deploy(root, sha)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('tracked files have local changes', result.stderr)
            self.assertEqual((root / 'version').read_text(), 'local edit')
            self.assertEqual(self.git(root, 'rev-parse', 'HEAD'), sha)

    def test_setup_records_selected_directory_without_manual_root_edit(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / 'c156'
            setup = SCRIPT.with_name('setup.sh')
            result = subprocess.run(['bash', str(setup), str(root)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            config = root / 'config.env'
            self.assertTrue(config.is_file())
            check = subprocess.run(['bash', '-c', 'source "$1"; load_config "$2"; printf "%s" "$DEPLOY_ROOT"', 'check', str(SCRIPT.with_name('common.sh')), str(root)], capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, check.stderr)
            self.assertEqual(check.stdout, str(root))
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)

    def test_setup_rejects_root_aliases_before_creating_files(self):
        common = SCRIPT.with_name('common.sh')
        for path in ('/', '/.', '//', '/./', '/tmp/../', '/tmp/c156/', '/tmp/./c156'):
            with self.subTest(path=path):
                result = subprocess.run(['bash', '-c', 'source "$1"; validate_root "$2"', 'check', str(common), path], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('invalid deployment root', result.stderr)

    def test_setup_fills_copied_template_root_without_resetting_settings(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / 'c156'; root.mkdir()
            config = root / 'config.env'
            config.write_text(SCRIPT.with_name('config.env.example').read_text())
            result = subprocess.run(['bash', str(SCRIPT.with_name('setup.sh')), str(root)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('set your real SITE_DOMAIN first', result.stderr)
            check = subprocess.run(['bash', '-c', 'source "$1"; load_config "$2"; printf "%s" "$DEPLOY_ROOT"', 'check', str(SCRIPT.with_name('common.sh')), str(root)], capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, check.stderr)
            self.assertEqual(check.stdout, str(root))
            self.assertIn('SITE_DOMAIN=docs.example.com', config.read_text())
            expected_uid = os.getuid() or 10001
            expected_gid = os.getgid() if os.getuid() else 10001
            self.assertIn(f'APP_UID={expected_uid}\n', config.read_text())
            self.assertIn(f'APP_GID={expected_gid}\n', config.read_text())
