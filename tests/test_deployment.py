"""Actual deployment entrypoints and local Git remotes; no replacement tools."""
from pathlib import Path
import os
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
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

    def test_fetch_recovers_when_real_remote_becomes_available(self):
        with TemporaryDirectory() as directory:
            remote, root = self.checkout_fixture(directory)
            sha = self.git(remote, 'rev-parse', 'HEAD')
            offline = remote.with_name('offline')
            remote.rename(offline)
            process = subprocess.Popen(['bash', '-s', '--', str(root), sha,
                                        'registry.example.com/app@sha256:' + 'a' * 64,
                                        'registry.example.com/app@sha256:' + 'a' * 64,
                                        '1', '--prepare'], stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                # Restore only after the real entrypoint reports its first failure.
                process.stdin.write(SCRIPT.read_text())
                process.stdin.close()
                process.stdin = None
                stderr_prefix = ''
                for line in process.stderr:
                    stderr_prefix += line
                    if 'Fetch failed; retrying in 5s' in line:
                        break
                self.assertIn('Fetch failed; retrying in 5s', stderr_prefix)
                offline.rename(remote)
                stdout, stderr = process.communicate(timeout=20)
                stderr = stderr_prefix + stderr
                self.assertIn('attempt 2/3', stdout)
                self.assertIn('set your real SITE_DOMAIN first', stderr)
                self.assertEqual(self.git(root, 'rev-parse', 'HEAD'), sha)
                self.assertEqual((root / 'data/c156.sqlite').read_bytes(), b'keep')
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()

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


class DeploymentHealthTest(unittest.TestCase):
    def check_health(self, recover):
        sha = 'a' * 40
        seen = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append((self.server.server_port, self.path))
                payload = {'build_sha': sha}
                if self.path == '/api/healthz':
                    payload = {'status': 'ok', 'build_sha': sha}
                if self.server is public and (not recover or len([x for x in seen if x[0] == public.server_port]) == 1):
                    payload['build_sha'] = 'b' * 40
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode())
            def log_message(self, *args):
                pass
        local = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        public = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        servers = (local, public)
        workers = [threading.Thread(target=server.serve_forever) for server in servers]
        for worker in workers:
            worker.start()
        try:
            result = subprocess.run(['bash', '-c',
                'source "$1"; SITE_DOMAIN=docs.example.com; poll_deployment_health "$2" "$3" "$4" 3 1',
                'health', str(SCRIPT.with_name('common.sh')), sha,
                f'http://127.0.0.1:{local.server_port}', f'http://127.0.0.1:{public.server_port}'],
                capture_output=True, text=True, timeout=10,
                env={**os.environ, "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"})
            return result, seen, public.server_port
        finally:
            for server in servers:
                server.shutdown()
                server.server_close()
            for worker in workers:
                worker.join()

    def test_poll_waits_for_public_sha_then_checks_all_four_endpoints(self):
        result, seen, public_port = self.check_health(recover=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('retry in 1s', result.stderr)
        self.assertIn('local and public frontend/API match', result.stdout)
        self.assertIn((public_port, '/api/healthz'), seen)
        self.assertGreaterEqual(len(seen), 7)

    def test_old_public_sha_times_out_even_when_local_sha_matches(self):
        result, seen, public_port = self.check_health(recover=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('Health verified', result.stdout)
        self.assertGreaterEqual(sum(port == public_port for port, _ in seen), 2)


class DeploymentMaintenanceTest(unittest.TestCase):
    """Run the production maintenance helper and real SQLite commands without Docker.

    The Compose boundary models stopped/running services only; this is not an
    image, container shutdown, or production deployment acceptance test.
    """
    def run_maintenance(self, root, fail_upgrade=False):
        import sys
        adapter = r'''
source "$1"
compose_for() {
  local root=$1; shift
  case "$1" in
    stop) rm -f "$root/running"; printf 'stop\n' >> "$root/events" ;;
    ps) [[ ! -f "$root/running" ]] || printf 'service-id\n' ;;
    up) touch "$root/running"; printf 'start\n' >> "$root/events" ;;
    run)
      shift; while [[ $1 != python ]]; do shift; done
      shift
      [[ ! -f "$root/running" ]] || return 99
      local args=() arg
      for arg in "$@"; do
        arg=${arg//\/data\/c156.sqlite/$root\/data\/c156.sqlite}
        arg=${arg//\/backups\//$root\/backups\/}
        args+=("$arg")
      done
      if [[ ${args[2]:-} == upgrade && $FAIL_UPGRADE == 1 ]]; then
        "$PYTHON_BIN" -c 'import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute("UPDATE alembic_version SET version_num=?", ("unsupported",)); c.commit(); c.close()' "$root/data/c156.sqlite"
      fi
      "$PYTHON_BIN" "${args[@]}"
      ;;
    *) return 98 ;;
  esac
}
maintain_and_start "$2" before.sqlite
'''
        return subprocess.run(['bash', '-c', adapter, 'maintenance', str(SCRIPT.with_name('common.sh')), str(root)],
                              capture_output=True, text=True,
                              env={**os.environ, 'PYTHON_BIN': sys.executable, 'FAIL_UPGRADE': '1' if fail_upgrade else '0'})

    def test_stop_backup_upgrade_verify_start_and_failed_migration_stays_stopped(self):
        import sqlite3
        from src.storage.management import initialize_database
        from src.storage.migrations import HEAD_REVISION
        for failed in (False, True):
            with self.subTest(failed=failed), TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'data').mkdir(); (root / 'backups').mkdir()
                initialize_database(root / 'data/c156.sqlite')
                (root / 'running').touch()
                result = self.run_maintenance(root, failed)
                self.assertEqual(result.returncode == 0, not failed, result.stderr)
                self.assertEqual((root / 'running').exists(), not failed)
                self.assertEqual((root / 'events').read_text(), 'stop\n' if failed else 'stop\nstart\n')
                with sqlite3.connect(root / 'backups/before.sqlite') as backup:
                    self.assertEqual(backup.execute('SELECT version_num FROM alembic_version').fetchone()[0], HEAD_REVISION)
                    self.assertGreater(backup.execute('SELECT count(*) FROM objects').fetchone()[0], 0)
                if failed:
                    self.assertIn('migration failed; services remain stopped', result.stderr)
                    with sqlite3.connect(root / 'data/c156.sqlite') as source:
                        self.assertEqual(source.execute('SELECT version_num FROM alembic_version').fetchone()[0], 'unsupported')
