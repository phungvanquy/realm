"""Opt-in real systemd test using a unique runtime unit and loopback sockets.

Run as root with REALM_TEST_SYSTEMD=1 and REALM_TEST_BINARY=/path/to/realm.
No persistent unit or boot enablement is created. All test resources are removed.
"""

import os
from pathlib import Path
import shutil
import socket
import socketserver
import subprocess
import tempfile
import threading
import time
import unittest
import uuid

from test_realmctl_relay import TcpEcho, UdpEcho


REALMCTL = Path(__file__).resolve().parents[1] / 'realmctl'
BINARY = os.environ.get('REALM_TEST_BINARY')


@unittest.skipUnless(os.environ.get('REALM_TEST_SYSTEMD') == '1' and BINARY,
                     'set REALM_TEST_SYSTEMD=1 and REALM_TEST_BINARY for the real systemd test')
class RealmctlSystemdTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(os.geteuid(), 0, 'Run the opt-in systemd test as root.')
        # /run may be mounted noexec, and DynamicUser gives services a private
        # /tmp. Use a temporary readable tree under /var/lib for the executable.
        self.temp = tempfile.TemporaryDirectory(prefix='realmctl-probe-', dir='/var/lib')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root.chmod(0o755)
        self.config = self.root / 'rules %i $HOME "quoted"\\'
        self.binary = self.root / 'realm %i $HOME'
        shutil.copy2(Path(BINARY).resolve(), self.binary)
        self.binary.chmod(0o755)
        self.service = f'realmctl-probe-{uuid.uuid4().hex[:8]}.service'
        self.unit_dir = Path('/run/systemd/system')
        self.unit = self.unit_dir / self.service
        self.env = {**os.environ, 'REALMCTL_UNIT_DIR': str(self.unit_dir)}
        self.env.pop('REALM_CONF', None)
        self.addCleanup(self.cleanup_service)

    def cleanup_service(self):
        subprocess.run(['systemctl', 'stop', self.service], capture_output=True, timeout=10)
        self.unit.unlink(missing_ok=True)
        shutil.rmtree(self.unit_dir / f'{self.service}.d', ignore_errors=True)
        backup_dir = self.unit_dir / '.realmctl-backups'
        for path in backup_dir.glob(f'{self.service}.*'):
            path.unlink()
        try:
            backup_dir.rmdir()
        except OSError:
            pass
        subprocess.run(['systemctl', 'daemon-reload'], capture_output=True, timeout=20)
        subprocess.run(['systemctl', 'reset-failed', self.service], capture_output=True, timeout=10)

    def ctl(self, *args):
        result = subprocess.run([str(REALMCTL), '--config-dir', str(self.config),
                                 '--service', self.service, *args],
                                env=self.env, text=True, capture_output=True, timeout=30)
        if result.returncode:
            logs = subprocess.run(['journalctl', '--no-pager', '-n', '30', '-u', self.service],
                                  text=True, capture_output=True, timeout=10)
            self.fail(result.stdout + result.stderr + logs.stdout + logs.stderr)
        return result.stdout

    def exchange(self, port):
        deadline = time.monotonic() + 5
        while True:
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=0.3) as client:
                    client.sendall(b'ping')
                    response = bytearray()
                    while len(response) < len(b'live:ping'):
                        chunk = client.recv(1024)
                        if not chunk:
                            break
                        response.extend(chunk)
                    self.assertEqual(response, b'live:ping')
                break
            except (OSError, socket.timeout):
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.settimeout(2)
            client.sendto(b'ping', ('127.0.0.1', port))
            self.assertEqual(client.recvfrom(1024)[0], b'live:ping')

    def backend(self):
        tcp = socketserver.ThreadingTCPServer(('127.0.0.1', 0), TcpEcho)
        tcp.daemon_threads = True
        udp = socketserver.UDPServer(tcp.server_address, UdpEcho)
        for server in (tcp, udp):
            server.prefix = b'live:'
            self.addCleanup(server.server_close)
            threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True).start()
            self.addCleanup(server.shutdown)
        return tcp.server_address[1]

    def test_setup_forwarding_lifecycle_and_remove_with_real_systemd(self):
        destination = self.backend()
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
            self.ctl('setup', '--binary', str(self.binary), '--no-enable')
            self.ctl('add', 'web', '--listen', f'127.0.0.1:{port}',
                     '--remote', f'127.0.0.1:{destination}', '--protocol', 'both')
        self.ctl('start')
        self.exchange(port)
        original_pid = subprocess.check_output(['systemctl', 'show', '--property=MainPID', '--value', self.service])
        self.ctl('setup', '--binary', str(self.binary), '--no-enable')
        self.assertEqual(subprocess.check_output(['systemctl', 'show', '--property=MainPID', '--value', self.service]), original_pid)
        self.ctl('stop')
        self.ctl('restart')
        self.exchange(port)
        self.ctl('disable', 'web')
        self.ctl('apply')
        self.assertNotEqual(subprocess.run(['systemctl', 'is-active', '--quiet', self.service]).returncode, 0)
        self.ctl('enable', 'web')
        self.ctl('apply')
        self.exchange(port)
        self.assertIn('ExecStart=', self.ctl('service', 'show'))
        self.ctl('service', 'remove')
        self.assertFalse(self.unit.exists())
        self.assertTrue((self.config / 'web.toml').exists())
        self.assertTrue(list((self.unit_dir / '.realmctl-backups').glob(f'{self.service}.*')))

    def test_adopt_active_service_and_remove_preserve_original_unit(self):
        destination = self.backend()
        legacy_binary = self.root / 'realm-legacy'
        shutil.copy2(self.binary, legacy_binary)
        source = self.root / 'old.toml'
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
            original = ('[log]\nlevel = "info"\noutput = "stdout"\n'
                        f'[[endpoints]]\nlisten = "127.0.0.1:{port}"\n'
                        f'remote = "127.0.0.1:{destination}"\n'
                        '[endpoints.network]\nuse_udp = true\n')
            source.write_text(original)
            source.chmod(0o644)
            unit_content = ('[Service]\nType=simple\nUser=nobody\n'
                            f'ExecStart="{legacy_binary}" -c "{source}"\n'
                            '[Install]\nWantedBy=multi-user.target\n')
            self.unit.write_text(unit_content)
            self.unit.chmod(0o644)
        subprocess.run(['systemctl', 'daemon-reload'], check=True, capture_output=True, timeout=20)
        subprocess.run(['systemctl', 'start', self.service], check=True, capture_output=True, timeout=10)
        self.exchange(port)
        original_pid = subprocess.check_output(['systemctl', 'show', '--property=MainPID', '--value', self.service])

        # This override must remain intact, and its REALM_CONF must not override
        # the new directory when the adopted service starts again.
        dropins = self.unit_dir / f'{self.service}.d'
        dropins.mkdir(mode=0o755)
        custom = dropins / '50-custom.conf'
        custom_content = '[Service]\nLimitNOFILE=12345\nEnvironment=REALM_CONF=/does-not-exist\n'
        custom.write_text(custom_content)
        custom.chmod(0o644)
        self.ctl('setup', '--import', str(source), '--binary', str(self.binary), '--no-enable')
        self.assertEqual(subprocess.check_output(['systemctl', 'show', '--property=MainPID', '--value', self.service]), original_pid)
        self.assertEqual(self.unit.read_text(), unit_content)
        self.assertEqual(source.read_text(), original)
        self.assertEqual((self.config / 'imported.toml').read_text(), original)
        self.assertEqual(subprocess.check_output(['systemctl', 'show', '--property=User', '--value', self.service]).strip(), b'nobody')
        self.assertEqual(subprocess.check_output(['systemctl', 'show', '--property=LimitNOFILE', '--value', self.service]).strip(), b'12345')
        self.ctl('apply')
        self.exchange(port)
        self.ctl('service', 'remove')
        self.assertEqual(self.unit.read_text(), unit_content)
        self.assertEqual(custom.read_text(), custom_content)
        self.assertFalse((dropins / '90-realmctl.conf').exists())
        self.assertNotEqual(subprocess.run(['systemctl', 'is-active', '--quiet', self.service]).returncode, 0)


if __name__ == '__main__':
    unittest.main()
