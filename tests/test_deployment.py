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
