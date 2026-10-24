"""Optional loopback integration check: set REALM_TEST_BINARY to a built realm."""

import os
from pathlib import Path
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import unittest


REALMCTL = Path(__file__).resolve().parents[1] / "realmctl"
REALM_BINARY = os.environ.get("REALM_TEST_BINARY")


class TcpEcho(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(3)
        data = self.request.recv(1024)
        if data:
            self.request.sendall(self.server.prefix + data)


class UdpEcho(socketserver.BaseRequestHandler):
    def handle(self):
        data, sock = self.request
        sock.sendto(self.server.prefix + data, self.client_address)


@unittest.skipUnless(REALM_BINARY, "set REALM_TEST_BINARY to run the loopback relay test")
class RealmctlRelayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "rules"
        self.binary = str(Path(REALM_BINARY).resolve())
        self.env = {key: value for key, value in os.environ.items() if key != "REALM_CONF"}
        self.env["VISUAL"] = ""
        self.process = None
        self.output = None
        self.addCleanup(self.stop_relay)

    def ctl(self, *args):
        result = subprocess.run([str(REALMCTL), "--config-dir", str(self.config), *args],
                                env=self.env, cwd=self.root, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def backend(self, prefix):
        tcp = socketserver.ThreadingTCPServer(("127.0.0.1", 0), TcpEcho)
        tcp.daemon_threads = True
        udp = socketserver.UDPServer(tcp.server_address, UdpEcho)
        for server in (tcp, udp):
            server.prefix = prefix
            self.addCleanup(server.server_close)
            threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
            self.addCleanup(server.shutdown)
        return tcp.server_address[1]

    def reserve_port(self):
        # Hold both protocols until launch so the two rules cannot pick the same
        # ephemeral port and another test cannot use a still-reserved listener.
        tcp = socket.socket()
        tcp.bind(("127.0.0.1", 0))
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.bind(tcp.getsockname())
        self.addCleanup(tcp.close)
        self.addCleanup(udp.close)
        return tcp.getsockname()[1], (tcp, udp)

    def start_relay(self):
        self.output = (self.root / "relay.log").open("wb")
        self.process = subprocess.Popen([self.binary, "-c", str(self.config)], env=self.env,
                                        cwd=self.root, stdout=self.output, stderr=subprocess.STDOUT)

    def stop_relay(self):
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
            self.process = None
        if self.output is not None:
            self.output.close()
            self.output = None

    def assert_tcp(self, port, expected):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self.fail((self.root / "relay.log").read_text())
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2) as client:
                    client.sendall(b"hello")
                    response = bytearray()
                    while len(response) < len(expected):
                        chunk = client.recv(1024)
                        if not chunk:
                            break
                        response.extend(chunk)
                    self.assertEqual(response, expected)
                    return
            except (ConnectionRefusedError, ConnectionResetError, socket.timeout):
                time.sleep(0.02)
        self.fail(f"Listener {port} did not become ready:\n" + (self.root / "relay.log").read_text())

    def assert_udp(self, port, expected):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.settimeout(2)
            client.sendto(b"hello", ("127.0.0.1", port))
            self.assertEqual(client.recvfrom(1024)[0], expected)

    def test_multiple_rules_edit_disable_and_loader_exclusions(self):
        first = self.backend(b"first:")
        second = self.backend(b"second:")
        alpha, alpha_sockets = self.reserve_port()
        beta, beta_sockets = self.reserve_port()
        self.ctl("init")
        self.ctl("add", "alpha", "--listen", f"127.0.0.1:{alpha}",
                 "--remote", f"127.0.0.1:{first}", "--protocol", "both")
        self.ctl("add", "beta", "--listen", f"127.0.0.1:{beta}",
                 "--remote", f"127.0.0.1:{second}")
        self.ctl("add", "disabled", "--listen", "127.0.0.1:1", "--remote", "127.0.0.1:1", "--disabled")
        # These deliberately invalid files would prevent startup if the loader
        # included either disabled rules or hidden working/backup directories.
        (self.config / "disabled.toml.disabled").write_text("invalid configuration [[\n")
        hidden = self.config / ".realmctl-backups"
        hidden.mkdir()
        (hidden / "bad.toml").write_text("invalid configuration [[\n")
        for sock in (*alpha_sockets, *beta_sockets):
            sock.close()
        self.start_relay()
        self.assert_tcp(alpha, b"first:hello")
        self.assert_udp(alpha, b"first:hello")
        self.assert_tcp(beta, b"second:hello")

        editor = self.root / "editor"
        editor.write_text(f"#!{sys.executable}\nfrom pathlib import Path\nimport sys\np = Path(sys.argv[1])\np.write_text(p.read_text().replace('127.0.0.1:{first}', '127.0.0.1:{second}'))\n")
        editor.chmod(0o755)
        self.env["EDITOR"] = str(editor)
        self.ctl("edit", "alpha")
        self.ctl("disable", "beta")
        # On-disk changes take effect at startup, not in the running process.
        self.assert_tcp(alpha, b"first:hello")
        self.assert_tcp(beta, b"second:hello")
        self.stop_relay()
        self.start_relay()
        self.assert_tcp(alpha, b"second:hello")
        self.assert_udp(alpha, b"second:hello")
        with socket.socket() as client:
            client.settimeout(1)
            self.assertNotEqual(client.connect_ex(("127.0.0.1", beta)), 0)


if __name__ == "__main__":
    unittest.main()
