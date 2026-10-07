"""Preflight rejection runs the actual deployment entrypoint without replacing tools."""
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'deploy' / 'deploy.sh'

class DeploymentPreflightTest(unittest.TestCase):
    def test_stale_release_keeps_success_pointer_and_data(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'config.env').write_text(f'SITE_DOMAIN=docs.example.com\nDEPLOY_ROOT={root}\nNGINX_MANAGED=0\n')
            release = root / 'releases' / 'old'; release.mkdir(parents=True)
            image = 'registry.example.com/team/app@sha256:' + 'a' * 64
            (release / 'release.env').write_text(f'BUILD_SHA={"b" * 40}\nBACKEND_IMAGE={image}\nFRONTEND_IMAGE={image}\nDEPLOY_SEQUENCE=1\n')
            current = root / 'releases' / 'current'; current.mkdir()
            (root / 'current').symlink_to(current)
            (root / 'sequence').write_text('2\n')
            data = root / 'data'; data.mkdir(); (data / 'c156.sqlite').write_bytes(b'keep')
            result = subprocess.run(['bash', str(SCRIPT), str(root), str(release)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('stale deployment rejected', result.stderr)
            self.assertEqual((root / 'current').resolve(), current)
            self.assertEqual((data / 'c156.sqlite').read_bytes(), b'keep')

    def test_mutable_image_is_rejected_before_any_deployment(self):
        with TemporaryDirectory() as directory:
            root = Path(directory); release = root / 'releases' / 'bad'; release.mkdir(parents=True)
            (root / 'config.env').write_text(f'SITE_DOMAIN=docs.example.com\nDEPLOY_ROOT={root}\nNGINX_MANAGED=0\n')
            (release / 'release.env').write_text(f'BUILD_SHA={"b" * 40}\nBACKEND_IMAGE=registry.example.com/app:latest\nFRONTEND_IMAGE=registry.example.com/app:latest\nDEPLOY_SEQUENCE=1\n')
            result = subprocess.run(['bash', str(SCRIPT), str(root), str(release)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('registry digest', result.stderr)
            self.assertFalse((root / 'current').exists())

    def test_first_install_has_no_previous_release_alias(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            common = SCRIPT.with_name('common.sh')
            result = subprocess.run(['bash', '-c', 'source "$1"; DEPLOY_ROOT=$2; release_pointer "$2/current"', 'check', str(common), str(root)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, '')
            target = root / 'releases' / 'first'; target.mkdir(parents=True)
            (root / 'current').symlink_to(target)
            result = subprocess.run(['bash', '-c', 'source "$1"; DEPLOY_ROOT=$2; release_pointer "$2/current"', 'check', str(common), str(root)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), str(target))

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
