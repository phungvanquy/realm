"""Exercise realmctl in private directories with mocked service commands."""

import fcntl
import base64
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from realmctl_fakes import CHECKER


REALMCTL = Path(__file__).resolve().parents[1] / "realmctl"


class RealmctlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "config with spaces"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.units = self.root / 'systemd'
        self.units.mkdir()
        self.log = self.root / "service-calls"
        self.env = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "VISUAL": "",
            "EDITOR": "",
            "REALMCTL_TEST_LOG": str(self.log),
            "REALMCTL_UNIT_DIR": str(self.units),
            "REALMCTL_TEST_ROOT": str(self.root),
            "REALMCTL_START_TIMEOUT": "1",
        }
        realm = self.mock('realm', CHECKER)
        for name in ('realm.service', 'realm-test.service'):
            (self.units / name).write_text('# Managed by realmctl. Use realmctl setup to update this file.\n'
                                          '# realmctl-config-base64: ' + base64.b64encode(str(self.config).encode()).decode() + '\n'
                                          '# realmctl-binary-base64: ' + base64.b64encode(str(realm).encode()).decode() + '\n')
        self.mock("systemctl", """
import json, os, sys
from pathlib import Path
with open(os.environ['REALMCTL_TEST_LOG'], 'a') as log:
    log.write(json.dumps(sys.argv) + '\\n')
failure = int(os.environ.get('REALMCTL_TEST_' + sys.argv[1].upper().replace('-', '_'), '0'))
if failure: sys.exit(failure)
state = Path(os.environ['REALMCTL_TEST_ROOT']) / 'active'
if sys.argv[1] in ('start', 'restart'): state.touch()
if sys.argv[1] == 'stop': state.unlink(missing_ok=True)
if sys.argv[1] == 'show': print('123' if state.exists() else '0')
if sys.argv[1] == 'is-active': sys.exit(0 if state.exists() else 3)
""")
        self.mock("journalctl", """
import json, os, sys
with open(os.environ['REALMCTL_TEST_LOG'], 'a') as log:
    log.write(json.dumps(sys.argv) + '\\n')
""")

    def mock(self, name, code):
        path = self.bin / name
        path.write_text(f"#!{sys.executable}\n" + code)
        path.chmod(0o755)
        return path

    def run_ctl(self, *args, ok=True, config=None, timeout=10):
        result = subprocess.run(
            [str(REALMCTL), "--config-dir", str(config or self.config), *args],
            env=self.env, cwd=self.root, text=True, capture_output=True, timeout=timeout,
        )
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def init(self):
        self.run_ctl("init")

    def add(self, name="web", *args, **kwargs):
        return self.run_ctl("add", name, "--listen", "127.0.0.1:8443",
                            "--remote", "example.com:443", *args, **kwargs)

    def backups(self):
        return list((self.config / ".realmctl-backups").glob("*"))

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line)[1:] for line in self.log.read_text().splitlines()]

    def assert_clean(self):
        self.assertEqual(list(self.config.glob(".realmctl-work.*")), [])

    def test_help_has_no_side_effects(self):
        for args in ((), ("--help",)):
            self.assertIn("edit NAME", self.run_ctl(*args).stdout)
        self.assertFalse(self.config.exists())
        self.assertEqual(self.calls(), [])

    def test_init_is_idempotent_and_sets_readable_defaults(self):
        self.init()
        global_config = self.config / "_global.toml"
        self.assertEqual(global_config.read_text(), 'endpoints = []\n\n[log]\nlevel = "info"\noutput = "stdout"\n')
        self.assertEqual(global_config.stat().st_mode & 0o777, 0o644)
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o755)
        global_config.write_text("endpoints = []\n# custom settings\n")
        old_inode = global_config.stat().st_ino
        self.init()
        self.assertEqual(global_config.stat().st_ino, old_inode)
        self.assertIn("custom settings", global_config.read_text())
        self.assertEqual(self.calls(), [])
        self.assert_clean()

    def test_import_preserves_source_settings_and_permissions(self):
        source = self.root / "old.toml"
        content = '[log]\nlevel = "debug"\noutput = "stdout"\n[[endpoints]]\nlisten = "127.0.0.1:8000"\nremote = "example.com:80"\n'
        source.write_text(content)
        source.chmod(0o640)
        if os.geteuid() == 0:
            os.chown(source, 65534, 65534)
        self.run_ctl("init", "--import", str(source))
        self.assertEqual(source.read_text(), content)
        self.assertEqual((self.config / "imported.toml").read_text(), content)
        self.assertEqual((self.config / "imported.toml").stat().st_mode & 0o777, 0o640)
        imported_stat = (self.config / "imported.toml").stat()
        self.assertEqual((imported_stat.st_uid, imported_stat.st_gid), (source.stat().st_uid, source.stat().st_gid))
        self.assertEqual((self.config / "_global.toml").read_text(), "endpoints = []\n")
        self.run_ctl("init", "--import", str(source))
        self.assertEqual(self.calls(), [])

    def test_bad_import_does_not_initialize(self):
        source = self.root / "empty"
        source.touch()
        for path in (source, self.root / "missing", self.root):
            self.run_ctl("init", "--import", str(path), ok=False)
            self.assertFalse(self.config.exists())

    def test_init_refuses_to_merge_existing_rules(self):
        self.config.mkdir()
        rule = self.config / "existing.toml"
        rule.write_text("endpoints = []\n")
        self.run_ctl("init", ok=False)
        self.assertEqual(rule.read_text(), "endpoints = []\n")
        self.assertFalse((self.config / "_global.toml").exists())

    def test_import_can_resume_after_sigkill_between_visible_files(self):
        source = self.root / 'old.toml'
        source.write_text('endpoints = []\n')
        marker = self.root / 'interrupted'
        self.mock('mv', f"""
import os, signal, subprocess, sys
from pathlib import Path
result = subprocess.run(['/usr/bin/mv', *sys.argv[1:]])
marker = Path({str(marker)!r})
if result.returncode == 0 and sys.argv[-1].endswith('/imported.toml') and not marker.exists():
    marker.touch()
    os.kill(os.getppid(), signal.SIGKILL)
sys.exit(result.returncode)
""")
        self.run_ctl('init', '--import', str(source), ok=False)
        self.assertTrue((self.config / 'imported.toml').exists())
        self.assertFalse((self.config / '_global.toml').exists())
        self.run_ctl('init', '--import', str(source))
        self.assertEqual((self.config / 'imported.toml').read_text(), source.read_text())
        self.assertTrue((self.config / '_global.toml').exists())
        self.assertFalse((self.config / '.realmctl-init').exists())

    def test_hidden_configuration_directory_is_rejected(self):
        hidden = self.root / '.rules'
        self.run_ctl('init', config=hidden, ok=False)
        self.assertFalse(hidden.exists())

    def test_all_protocols_and_disabled_add(self):
        self.init()
        for protocol, no_tcp, use_udp in (("tcp", "false", "false"), ("udp", "true", "true"), ("both", "false", "true")):
            with self.subTest(protocol=protocol):
                self.add(protocol, "--protocol", protocol)
                content = self.run_ctl("show", protocol).stdout
                self.assertEqual(content, f'[[endpoints]]\nlisten = "127.0.0.1:8443"\nremote = "example.com:443"\n\n[endpoints.network]\nno_tcp = {no_tcp}\nuse_udp = {use_udp}\n')
                self.assertEqual((self.config / f"{protocol}.toml").stat().st_mode & 0o777, 0o644)
        self.add("off", "--disabled")
        self.assertTrue((self.config / "off.toml.disabled").is_file())
        output = self.run_ctl("list").stdout
        self.assertIn("off                              disabled", output)
        self.assertNotIn("_global", output)
        self.assertEqual(self.calls(), [])
        self.assert_clean()

    def test_valid_address_forms(self):
        self.init()
        for index, (listen, remote) in enumerate((
            ("0.0.0.0:1", "localhost:65535"),
            ("255.255.255.255:65535", "192.0.2.1:1"),
            ("[::]:8443", "[2001:db8::1]:443"),
            ("[::1]:53", "example.com.:53"),
            ("[1:2:3:4:5:6:7:8]:443", "[::ffff:192.0.2.1]:443"),
        )):
            self.run_ctl("add", f"rule{index}", "--listen", listen, "--remote", remote)

    def test_invalid_inputs_cannot_create_files_or_inject_toml(self):
        self.init()
        for address in ("0.0.0.0:0", "127.0.0.1:65536", "127.0.0.1:-1", "127.0.0.1:abc",
                        "256.0.0.1:80", "01.0.0.1:80", "host:80", "::1:80", "[1::2::3]:80",
                        "[:::]:80", "[1:2:3:4:5:6:7]:80", "[1:2:3:4:5:6:7:8::]:80",
                        "[::ffff:999.1.2.3]:80", "[fe80::1%eth0]:80", "127.0.0.1:9999999999999999999"):
            with self.subTest(listen=address):
                self.run_ctl("add", "bad", "--listen", address, "--remote", "example.com:443", ok=False)
        for address in ('host"\n[[endpoints]]:80', "$(touch nope):80", "host;touch nope:80", "host:0",
                        "-host:80", "foo..bar:80", "foo..:80", "999.0.0.1:80", f"{'a' * 64}.com:80"):
            with self.subTest(remote=address):
                self.run_ctl("add", "bad", "--listen", "127.0.0.1:80", "--remote", address, ok=False)
        for name in ("../escape", "a/b", ".hidden", "_global", "-x", "a\nb", "a" * 65):
            self.add(name, ok=False)
        self.add("bad", "--protocol", "sctp", ok=False)
        self.add("bad", "--listen", "127.0.0.1:1", ok=False)
        self.assertEqual(list(self.config.iterdir()), [self.config / "_global.toml"])

    def test_duplicate_add_never_overwrites_enabled_or_disabled_rules(self):
        self.init()
        self.add()
        original = (self.config / "web.toml").read_text()
        self.add(ok=False)
        self.assertEqual((self.config / "web.toml").read_text(), original)
        self.run_ctl("disable", "web")
        self.add(ok=False)
        self.assertEqual((self.config / "web.toml.disabled").read_text(), original)

    def test_enable_disable_are_idempotent_and_preserve_content(self):
        self.init()
        self.add()
        original = (self.config / "web.toml").read_bytes()
        for command, filename in (("disable", "web.toml.disabled"), ("enable", "web.toml")):
            self.run_ctl(command, "web")
            inode = (self.config / filename).stat().st_ino
            self.run_ctl(command, "web")
            self.assertEqual((self.config / filename).stat().st_ino, inode)
            self.assertEqual((self.config / filename).read_bytes(), original)
        self.assertEqual(self.calls(), [])

    def test_edit_uses_visual_and_preserves_advanced_fields_mode_and_disabled_state(self):
        self.init()
        self.add("web", "--disabled")
        rule = self.config / "web.toml.disabled"
        original = rule.read_text() + "# advanced configuration stays intact\ntcp_timeout = 17\n"
        rule.write_text(original)
        rule.chmod(0o640)
        if os.geteuid() == 0:
            os.chown(rule, 65534, 65534)
        owner = (rule.stat().st_uid, rule.stat().st_gid)
        editor = self.mock("editor with spaces", """
from pathlib import Path
import sys
p = Path(sys.argv[1])
p.write_text(p.read_text().replace('example.com:443', 'new.example:443'))
""")
        self.env.update(VISUAL=str(editor), EDITOR="does-not-exist")
        with rule.open() as old:
            self.run_ctl("edit", "web")
            self.assertEqual(old.read(), original)
            self.assertNotEqual(os.fstat(old.fileno()).st_ino, rule.stat().st_ino)
        self.assertEqual(rule.read_text(), original.replace("example.com:443", "new.example:443"))
        self.assertEqual(rule.stat().st_mode & 0o777, 0o640)
        self.assertEqual((rule.stat().st_uid, rule.stat().st_gid), owner)
        self.assertFalse((self.config / "web.toml").exists())
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual(self.backups()[0].read_text(), original)
        self.assertEqual(self.backups()[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.calls(), [])
        self.assert_clean()

    def test_editor_arguments_are_passed_without_shell_evaluation(self):
        self.init()
        self.add()
        self.mock("editor", """
from pathlib import Path
import sys
assert sys.argv[1:-1] == ['--wait', '$(touch', 'INJECTED)']
p = Path(sys.argv[-1])
p.write_text(p.read_text() + '# edited\\n')
""")
        self.env["EDITOR"] = "editor --wait $(touch INJECTED)"
        self.run_ctl("edit", "web")
        self.assertTrue((self.config / "web.toml").read_text().endswith("# edited\n"))
        self.assertFalse((self.root / "INJECTED").exists())

    def test_failed_empty_deleted_symlink_and_unchanged_edits_preserve_original(self):
        self.init()
        self.add()
        rule = self.config / "web.toml"
        original = rule.read_bytes()
        inode = rule.stat().st_ino
        outside = self.root / "outside"
        outside.write_text("outside\n")
        cases = (
            ("p.write_text('changed'); sys.exit(1)", False),
            ("p.write_text('')", False),
            ("p.unlink()", False),
            (f"p.unlink(); p.symlink_to({str(outside)!r})", False),
            ("pass", True),
        )
        for code, ok in cases:
            with self.subTest(code=code):
                self.mock("editor", "from pathlib import Path\nimport sys\np = Path(sys.argv[1])\n" + code)
                self.env["EDITOR"] = "editor"
                self.run_ctl("edit", "web", ok=ok)
                self.assertEqual(rule.read_bytes(), original)
                self.assertEqual(rule.stat().st_ino, inode)
                self.assertEqual(self.backups(), [])
                self.assert_clean()
        self.assertEqual(outside.read_text(), "outside\n")

    def test_fallback_editor_and_missing_editor(self):
        self.init()
        self.add()
        self.mock("nano", "from pathlib import Path\nimport sys\np=Path(sys.argv[1]); p.write_text(p.read_text() + '# nano\\n')")
        self.run_ctl("edit", "web")
        self.assertIn("# nano", (self.config / "web.toml").read_text())
        self.env["EDITOR"] = "no-such-editor"
        self.run_ctl("edit", "web", ok=False)
        self.assert_clean()

    def test_external_edit_is_not_overwritten_and_candidate_is_recoverable(self):
        self.init()
        self.add()
        rule = self.config / "web.toml"
        self.mock("editor", f"""
from pathlib import Path
import sys
Path(sys.argv[1]).write_text('my edited copy\\n')
Path({str(rule)!r}).write_text('external change\\n')
""")
        self.env["EDITOR"] = "editor"
        self.run_ctl("edit", "web", ok=False)
        self.assertEqual(rule.read_text(), "external change\n")
        candidates = list(self.config.glob(".realmctl-work.*/web.toml"))
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].read_text(), "my edited copy\n")

    def test_remove_keeps_private_backup(self):
        self.init()
        self.add()
        original = (self.config / "web.toml").read_bytes()
        self.run_ctl("remove", "web")
        self.assertFalse((self.config / "web.toml").exists())
        self.assertEqual(self.backups()[0].read_bytes(), original)
        self.assertEqual((self.config / ".realmctl-backups").stat().st_mode & 0o777, 0o700)
        self.run_ctl("remove", "web", ok=False)
        self.assertEqual(self.calls(), [])

    def test_shared_configuration_can_be_shown_and_edited_with_backup(self):
        self.init()
        self.add()
        original = self.run_ctl('config', 'show').stdout
        rule = (self.config / 'web.toml').read_bytes()
        self.mock('editor', "from pathlib import Path\nimport sys\np=Path(sys.argv[1]); p.write_text(p.read_text().replace('info', 'debug'))")
        self.env['EDITOR'] = 'editor'
        self.run_ctl('config', 'edit')
        self.assertEqual(self.run_ctl('config', 'show').stdout, original.replace('info', 'debug'))
        self.assertEqual(self.backups()[0].read_text(), original)
        self.assertEqual((self.config / 'web.toml').read_bytes(), rule)
        self.assertEqual(self.calls(), [])

    def test_backup_or_atomic_replace_failure_keeps_original(self):
        self.init()
        self.add()
        rule = self.config / "web.toml"
        original = rule.read_bytes()
        self.mock("editor", "from pathlib import Path\nimport sys\np=Path(sys.argv[1]); p.write_text('changed\\n')")
        self.env["EDITOR"] = "editor"
        (self.config / ".realmctl-backups").write_text("not a directory")
        self.run_ctl("edit", "web", ok=False)
        self.run_ctl("remove", "web", ok=False)
        self.assertEqual(rule.read_bytes(), original)
        (self.config / ".realmctl-backups").unlink()
        self.mock("mv", "import sys\nsys.exit(1)")
        self.run_ctl("edit", "web", ok=False)
        self.assertEqual(rule.read_bytes(), original)
        self.assertEqual(self.backups()[0].read_bytes(), original)
        self.assert_clean()

    def test_symlinks_and_ambiguous_layouts_are_rejected(self):
        self.init()
        outside = self.root / "outside"
        outside.write_text("untouched\n")
        for path in (self.config / "bad.toml", self.config / "bad.toml.disabled", self.config / "unrelated"):
            path.symlink_to(outside)
            self.add(ok=False)
            self.run_ctl("apply", ok=False)
            path.unlink()
        link = self.root / "linked-config"
        link.symlink_to(self.config, target_is_directory=True)
        self.run_ctl("init", config=f"{link}/", ok=False)
        self.add()
        (self.config / "web.toml.disabled").write_text("duplicate\n")
        self.run_ctl("enable", "web", ok=False)
        self.run_ctl("apply", ok=False)
        (self.config / "web.toml.disabled").unlink()
        for path in (self.config / "nested", self.config / "external.json"):
            if path.suffix:
                path.write_text('{}')
            else:
                path.mkdir()
            self.run_ctl("apply", ok=False)
            if path.is_dir():
                path.rmdir()
            else:
                path.unlink()
        (self.config / ".realmctl-backups").symlink_to(self.root, target_is_directory=True)
        self.run_ctl("remove", "web", ok=False)
        self.assertEqual(outside.read_text(), "untouched\n")
        self.assertEqual(self.calls(), [])

    def test_lock_serializes_writers_and_apply(self):
        self.init()
        self.add()
        fd = os.open(self.config, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for args in (("edit", "web"), ("disable", "web"), ("remove", "web"), ("apply",), ("init",)):
                result = self.run_ctl(*args, ok=False)
                self.assertIn("Another realmctl", result.stderr)
            self.add("other", ok=False)
        finally:
            os.close(fd)
        self.run_ctl("disable", "web")
        self.assertEqual(self.calls(), [])

    def test_editor_holds_lock_and_interrupt_leaves_rule_unchanged(self):
        self.init()
        self.add()
        rule = self.config / "web.toml"
        original = rule.read_bytes()
        marker = self.root / "editing"
        self.mock("editor", f"""
from pathlib import Path
import sys, time
Path(sys.argv[1]).write_text('unsaved\\n')
Path({str(marker)!r}).touch()
time.sleep(30)
""")
        self.env["EDITOR"] = "editor"
        proc = subprocess.Popen([str(REALMCTL), "--config-dir", str(self.config), "edit", "web"],
                                env=self.env, cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, start_new_session=True)
        try:
            deadline = time.monotonic() + 5
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(marker.exists())
            self.run_ctl("disable", "web", ok=False)
            self.run_ctl("apply", ok=False)
        finally:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.communicate(timeout=5)
        self.assertEqual(rule.read_bytes(), original)
        self.assert_clean()
        self.run_ctl("disable", "web")

    def test_apply_restarts_configured_service_and_stops_when_no_rules_enabled(self):
        self.init()
        self.add()
        self.run_ctl("--service", "realm-test", "apply")
        mutations = [call for call in self.calls() if call[0] in ('start', 'restart', 'stop')]
        self.assertEqual(mutations, [["restart", "--", "realm-test.service"]])
        self.run_ctl("disable", "web")
        self.run_ctl("apply")
        self.assertEqual(self.calls()[-1], ["stop", "--", "realm.service"])

    def test_apply_propagates_service_failures_and_rejects_empty_rules(self):
        self.init()
        self.add()
        original = (self.config / "web.toml").read_text()
        for variable in ("REALMCTL_TEST_RESTART", "REALMCTL_TEST_IS_ACTIVE"):
            self.env[variable] = "1"
            self.run_ctl("apply", ok=False)
            self.env.pop(variable)
            self.assertEqual((self.config / "web.toml").read_text(), original)
        self.log.unlink()
        (self.config / "web.toml").write_text("")
        self.run_ctl("apply", ok=False)
        self.assertEqual(self.calls(), [])
        self.run_ctl("disable", "web")
        self.env["REALMCTL_TEST_STOP"] = "1"
        self.run_ctl("apply", ok=False)

    def test_apply_rejects_empty_shared_configuration(self):
        self.init()
        self.add()
        (self.config / "_global.toml").write_text("")
        self.run_ctl("apply", ok=False)
        self.assertEqual(self.calls(), [])

    def test_status_and_logs_work_without_initialization(self):
        self.run_ctl("status")
        self.run_ctl("logs")
        self.run_ctl("--service", "realm@home", "logs", "--follow")
        self.assertEqual(self.calls(), [
            ["status", "--no-pager", "--", "realm.service"],
            ["--unit=realm.service", "--lines=50", "--no-pager"],
            ["--unit=realm@home.service", "--lines=50", "--no-pager", "--follow"],
        ])
        self.env["REALMCTL_TEST_STATUS"] = "3"
        self.assertEqual(self.run_ctl("status", ok=False).returncode, 3)
        self.assertFalse(self.config.exists())

    def test_suggested_apply_command_retains_custom_directory_and_service(self):
        self.init()
        result = self.run_ctl("--service", "realm-custom", "add", "web",
                              "--listen", "127.0.0.1:8443", "--remote", "example.com:443")
        command = result.stdout.split("Run ", 1)[1].split(" when ready.", 1)[0]
        self.assertEqual(shlex.split(command), ["realmctl", "--config-dir", str(self.config),
                                               "--service", "realm-custom.service", "apply"])

    def test_invalid_commands_and_arguments_have_no_side_effects(self):
        for args in (("wat",), ("edit",), ("edit", "web", "extra"), ("list", "extra"),
                     ("apply", "extra"), ("status", "extra"), ("logs", "--bad"),
                     ("--service", "-bad", "status"), ("--service", "a/b", "apply"),
                     ("--config-dir", "relative", "init"), ("--config-dir", "/", "init"),
                     ("--config-dir", "/tmp/../etc", "init"), ("--config-dir", "/tmp/.", "init"),
                     ("--config-dir",), ("init", "--import"), ("add", "web", "--listen")):
            with self.subTest(args=args):
                self.run_ctl(*args, ok=False)
                self.assertFalse(self.config.exists())
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
