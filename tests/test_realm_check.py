"""Native configuration validation and process ownership regression checks."""

import os
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest


BINARY = os.environ.get('REALM_TEST_BINARY')


@unittest.skipUnless(BINARY, 'set REALM_TEST_BINARY to run native checker tests')
class RealmCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / 'config.toml'
        self.binary = str(Path(BINARY).resolve())

    def check(self, content, *args, ok=True):
        self.config.write_text(content)
        result = subprocess.run([self.binary, 'check', '--config', str(self.config), *args],
                                env={**os.environ, 'REALM_CONF': 'endpoints = []'},
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return result

    def test_version_is_available_without_config_and_ignores_realm_conf(self):
        result = subprocess.run([self.binary, 'check', '--version'],
                                env={**os.environ, 'REALM_CONF': 'endpoints = []'},
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('check 1', result.stdout)

    def test_valid_configuration_does_not_bind_ports_or_create_log_files(self):
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1', 0))
            occupied.listen()
            port = occupied.getsockname()[1]
            log = self.root / 'not-created.log'
            self.check(f'[log]\noutput="{log}"\n[[endpoints]]\nlisten="127.0.0.1:{port}"\nremote="127.0.0.1:80"\n')
            self.assertFalse(log.exists())

    def test_invalid_syntax_addresses_and_duplicate_listeners_fail(self):
        for content in ('[[endpoints]\n',
                        '[[endpoints]]\nlisten="127.0.0.1:8000"\nremote="missing-port"\n',
                        ('[[endpoints]]\nlisten="127.0.0.1:8000"\nremote="127.0.0.1:80"\n' * 2)):
            with self.subTest(content=content):
                self.check(content, ok=False)

    def test_hidden_root_and_unreadable_directory_entries_fail(self):
        hidden = self.root / '.rules'
        hidden.mkdir()
        (hidden / 'rule.toml').write_text('endpoints = []\n')
        result = subprocess.run([self.binary, 'check', '--config', str(hidden)], capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('must not start', result.stderr)
        result = subprocess.run([self.binary, 'check', '--config', '.'], cwd=hidden, capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('must not start', result.stderr)
        rules = self.root / 'rules'
        rules.mkdir()
        (rules / 'broken.toml').symlink_to(rules / 'missing.toml')
        result = subprocess.run([self.binary, 'check', '--config', str(rules)], capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)

    def test_relative_directory_does_not_skip_invalid_configuration(self):
        self.config.write_text('[[endpoints]\n')
        result = subprocess.run([self.binary, 'check', '--config', '.'], cwd=self.root,
                                capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('config.toml', result.stderr)

    def test_an_unrelated_process_cannot_satisfy_readiness(self):
        with socket.socket() as owned_by_test:
            owned_by_test.bind(('127.0.0.1', 0))
            owned_by_test.listen()
            port = owned_by_test.getsockname()[1]
            result = self.check(f'[[endpoints]]\nlisten="127.0.0.1:{port}"\nremote="127.0.0.1:80"\n',
                                '--pid', str(os.getpid()), ok=False)
            self.assertIn('running process does not use', result.stderr)


if __name__ == '__main__':
    unittest.main()
