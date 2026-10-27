"""Service management tests use a private unit directory and a fake manager."""

import base64
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


REALMCTL = Path(__file__).resolve().parents[1] / "realmctl"
SYSTEMCTL = r'''
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
root = Path(os.environ['REALMCTL_TEST_ROOT'])
with (root / 'calls').open('a') as log:
    log.write(json.dumps(args) + '\n')
failures = root / 'failures.json'
if failures.exists():
    pending = json.loads(failures.read_text())
    if pending.get(args[0], 0):
        pending[args[0]] -= 1
        failures.write_text(json.dumps(pending))
        print('simulated systemctl failure', file=sys.stderr)
        sys.exit(1)
state_file = root / 'state.json'
state = json.loads(state_file.read_text()) if state_file.exists() else {}
unit = Path(os.environ['REALMCTL_UNIT_DIR']) / args[-1]
if args[0] == 'show':
    fragment = os.environ.get('REALMCTL_TEST_FRAGMENT', str(unit) if unit.exists() else '')
    load = os.environ.get('REALMCTL_TEST_LOAD', 'loaded' if fragment else 'not-found')
    canonical = os.environ.get('REALMCTL_TEST_ID', args[-1])
    print(f'Id={canonical}\nLoadState={load}\nFragmentPath={fragment}')
elif args[0] in ('start', 'restart'):
    state['active'] = True
elif args[0] == 'stop':
    state['active'] = False
elif args[0] == 'is-active':
    sys.exit(0 if state.get('active') else 3)
elif args[0] == 'is-enabled':
    print(os.environ.get('REALMCTL_TEST_BOOT_STATE', 'enabled' if state.get('enabled') else 'disabled'))
    sys.exit(0)
elif args[0] == 'enable':
    state['enabled'] = True
elif args[0] == 'disable':
    state['enabled'] = False
    if '--now' in args:
        state['active'] = False
elif args[0] == 'cat':
    if unit.exists():
        print(unit.read_text(), end='')
    for path in sorted(Path(str(unit) + '.d').glob('*.conf')):
        print(path.read_text(), end='')
state_file.write_text(json.dumps(state))
'''


class RealmctlServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "config with spaces"
        self.units = self.root / "systemd"
        self.units.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.env = {
            **os.environ,
            'PATH': f'{self.bin}:{os.environ["PATH"]}',
            'REALMCTL_UNIT_DIR': str(self.units),
            'REALMCTL_TEST_ROOT': str(self.root),
        }
        self.realm = self.mock('realm', "import os, sys\nassert 'REALM_CONF' not in os.environ\nassert sys.argv[1:] == ['--version']\nprint('Realm 2.9.8 [test]')\n")
        self.mock('systemctl', SYSTEMCTL)
        self.unit = self.units / 'realm.service'
        self.override = self.units / 'realm.service.d/90-realmctl.conf'

    def mock(self, name, code):
        path = self.bin / name
        path.write_text(f'#!{sys.executable}\n' + code)
        path.chmod(0o755)
        return path

    def ctl(self, *args, ok=True, config=None):
        result = subprocess.run([str(REALMCTL), '--config-dir', str(config or self.config), *args],
                                env=self.env, cwd=self.root, text=True, capture_output=True, timeout=10)
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(list(self.units.glob('.realmctl-work.*')), [])
        return result

    def setup(self, *args, **kwargs):
        return self.ctl('setup', *args, **kwargs)

    def add(self):
        self.ctl('add', 'web', '--listen', '127.0.0.1:8443', '--remote', '127.0.0.1:443')

    def calls(self):
        log = self.root / 'calls'
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def state(self):
        path = self.root / 'state.json'
        return json.loads(path.read_text()) if path.exists() else {}

    def fail_next(self, action):
        (self.root / 'failures.json').write_text(json.dumps({action: 1}))

    def backups(self):
        return list((self.units / '.realmctl-backups').glob('*'))

    def existing_service(self, path=None):
        path = path or self.unit
        path.parent.mkdir(parents=True, exist_ok=True)
        content = ('[Unit]\nDescription=Custom Realm\n[Service]\nUser=existing-user\n'
                   f'ExecStart={self.realm} -c /etc/realm/config.toml\n'
                   'LimitNOFILE=4096\nRestart=always\n[Install]\nWantedBy=multi-user.target\n')
        path.write_text(content)
        return content

    def test_setup_creates_readable_unit_and_enables_boot_without_starting(self):
        self.env['REALM_CONF'] = 'must not be passed to the version check'
        self.setup()
        content = self.unit.read_text()
        self.assertIn(f'ExecStart=":{self.realm}" -c "{self.config}"\n', content)
        self.assertIn('DynamicUser=yes\n', content)
        self.assertIn('UnsetEnvironment=REALM_CONF\n', content)
        self.assertIn('AmbientCapabilities=CAP_NET_BIND_SERVICE\n', content)
        self.assertIn(base64.b64encode(str(self.config).encode()).decode(), content)
        self.assertEqual(self.unit.stat().st_mode & 0o777, 0o644)
        self.assertEqual(self.state(), {'enabled': True})
        self.assertEqual([call[0] for call in self.calls()], ['show', 'daemon-reload', 'enable', 'is-enabled'])
        self.assertTrue((self.config / '_global.toml').is_file())

    def test_setup_is_idempotent_and_keeps_existing_rules(self):
        self.setup()
        self.add()
        rule = self.config / 'web.toml'
        contents = rule.read_text() + '# custom configuration\n'
        rule.write_text(contents)
        unit_inode = self.unit.stat().st_ino
        global_inode = (self.config / '_global.toml').stat().st_ino
        self.setup()
        self.assertEqual(self.unit.stat().st_ino, unit_inode)
        self.assertEqual((self.config / '_global.toml').stat().st_ino, global_inode)
        self.assertEqual(rule.read_text(), contents)
        self.assertEqual(self.backups(), [])
        self.assertNotIn('restart', [call[0] for call in self.calls()])

    def test_setup_no_enable_and_explicit_binary(self):
        selected = self.root / 'custom realm'
        shutil.copy2(self.realm, selected)
        self.setup('--binary', str(selected), '--no-enable')
        self.assertIn(f'ExecStart=":{selected}"', self.unit.read_text())
        self.assertNotIn('enable', [call[0] for call in self.calls()])

    def test_invalid_binary_or_systemd_failure_leaves_configuration_uncreated(self):
        for path in ('relative', str(self.root / 'missing'), str(self.root)):
            self.setup('--binary', path, ok=False)
            self.assertFalse(self.config.exists())
        self.realm.write_text('#!/bin/sh\nprintf "Not Realm\\n"\n')
        self.setup(ok=False)
        self.assertFalse(self.config.exists())
        self.mock('realm', "print('Realm 2.9.8')")
        self.fail_next('show')
        self.setup(ok=False)
        self.assertFalse(self.config.exists())
        self.assertFalse(self.unit.exists())

    def test_unmanaged_service_requires_adoption_and_original_unit_is_preserved(self):
        original = self.existing_service()
        self.setup(ok=False)
        self.assertFalse(self.config.exists())
        self.assertEqual(self.unit.read_text(), original)
        self.setup('--adopt', ok=False)
        self.assertFalse(self.config.exists())
        self.ctl('init')
        other_override = self.units / 'realm.service.d/50-realmctl.conf'
        other_override.parent.mkdir()
        other_override.write_text('[Service]\nEnvironment=CUSTOM=yes\n')
        self.setup('--adopt')
        self.assertEqual(self.unit.read_text(), original)
        self.assertEqual(other_override.read_text(), '[Service]\nEnvironment=CUSTOM=yes\n')
        self.assertIn('Type=exec\nExecStart=\nExecStart=', self.override.read_text())
        self.assertNotIn('User=', self.override.read_text())
        self.assertNotIn('LimitNOFILE=', self.override.read_text())
        inode = self.override.stat().st_ino
        self.setup()
        self.assertEqual(self.override.stat().st_ino, inode)
        self.assertEqual(self.unit.read_text(), original)

    def test_vendor_unit_is_adopted_without_copying_or_modifying_it(self):
        vendor = self.root / 'vendor/realm.service'
        original = self.existing_service(vendor)
        self.env['REALMCTL_TEST_FRAGMENT'] = str(vendor)
        self.ctl('init')
        self.setup('--adopt')
        self.assertFalse(self.unit.exists())
        self.assertTrue(self.override.is_file())
        self.assertEqual(vendor.read_text(), original)

    def test_static_service_does_not_claim_successful_boot_enablement(self):
        original = self.existing_service().split('[Install]', 1)[0]
        self.unit.write_text(original)
        self.ctl('init')
        self.env['REALMCTL_TEST_BOOT_STATE'] = 'static'
        result = self.setup('--adopt', ok=False)
        self.assertIn('Boot state for realm.service is static', result.stderr)
        self.assertNotIn('Automatic startup at boot is enabled', result.stdout)
        self.assertEqual(self.unit.read_text(), original)
        self.assertTrue(self.override.is_file())
        self.assertTrue((self.config / '_global.toml').is_file())
        self.ctl('service', 'enable', ok=False)
        self.setup('--no-enable')

    def test_import_adopts_service_and_is_retryable_after_enable_failure(self):
        original_unit = self.existing_service()
        source = self.root / 'old.toml'
        content = '[[endpoints]]\nlisten = "127.0.0.1:8443"\nremote = "127.0.0.1:443"\n'
        source.write_text(content)
        self.fail_next('enable')
        self.setup('--import', str(source), ok=False)
        imported = self.config / 'imported.toml'
        inode = imported.stat().st_ino
        self.setup('--import', str(source))
        self.assertEqual(imported.stat().st_ino, inode)
        self.assertEqual(imported.read_text(), content)
        self.assertEqual(source.read_text(), content)
        self.assertEqual(self.unit.read_text(), original_unit)
        self.assertTrue(self.state()['enabled'])
        imported.write_text(content + '# changed locally\n')
        self.setup('--import', str(source), ok=False)
        self.assertTrue(imported.read_text().endswith('# changed locally\n'))

    def test_failed_reload_removes_new_unit_and_retry_preserves_config(self):
        self.fail_next('daemon-reload')
        self.setup(ok=False)
        self.assertFalse(self.unit.exists())
        global_inode = (self.config / '_global.toml').stat().st_ino
        self.assertNotIn('enable', [call[0] for call in self.calls()])
        self.setup()
        self.assertEqual((self.config / '_global.toml').stat().st_ino, global_inode)
        self.assertTrue(self.unit.exists())

    def test_failed_reload_restores_changed_managed_unit_and_keeps_backup(self):
        self.setup()
        original = self.unit.read_bytes()
        alternative = self.root / 'other-realm'
        shutil.copy2(self.realm, alternative)
        self.fail_next('daemon-reload')
        self.setup('--binary', str(alternative), ok=False)
        self.assertEqual(self.unit.read_bytes(), original)
        self.assertEqual(self.backups()[0].read_bytes(), original)
        self.assertEqual(self.backups()[0].stat().st_mode & 0o777, 0o600)

    def test_masked_and_alias_services_are_not_replaced(self):
        self.unit.symlink_to('/dev/null')
        self.env['REALMCTL_TEST_LOAD'] = 'masked'
        self.setup('--adopt', ok=False)
        self.assertTrue(self.unit.is_symlink())
        self.assertFalse(self.config.exists())
        self.unit.unlink()
        self.env.pop('REALMCTL_TEST_LOAD')
        self.env['REALMCTL_TEST_ID'] = 'different.service'
        self.setup('--adopt', ok=False)
        self.assertFalse(self.unit.exists())

    def test_setup_rejects_symlinks_and_foreign_overrides(self):
        outside = self.root / 'outside'
        outside.write_text('keep me\n')
        self.unit.symlink_to(outside)
        self.setup('--adopt', ok=False)
        self.unit.unlink()
        directory = self.units / 'realm.service.d'
        directory.symlink_to(self.root, target_is_directory=True)
        self.setup('--adopt', ok=False)
        directory.unlink()
        directory.mkdir()
        self.existing_service()
        self.override.symlink_to(outside)
        self.setup('--adopt', ok=False)
        self.override.unlink()
        self.override.write_text('[Service]\nEnvironment=OTHER=1\n')
        self.setup('--adopt', ok=False)
        self.assertEqual(outside.read_text(), 'keep me\n')
        self.assertEqual(self.override.read_text(), '[Service]\nEnvironment=OTHER=1\n')
        self.assertFalse(self.config.exists())

    def test_runtime_and_boot_controls_have_distinct_effects(self):
        self.setup()
        self.ctl('start', ok=False)
        self.add()
        self.ctl('start')
        self.assertTrue(self.state()['active'])
        self.ctl('service', 'disable')
        self.assertFalse(self.state()['enabled'])
        self.assertTrue(self.state()['active'])
        self.ctl('stop')
        self.assertFalse(self.state()['active'])
        self.ctl('restart')
        self.assertTrue(self.state()['active'])
        self.assertFalse(self.state()['enabled'])
        self.ctl('service', 'enable')
        self.assertTrue(self.state()['enabled'])
        self.assertIn('ExecStart=', self.ctl('service', 'show').stdout)
        self.ctl('disable', 'web')
        self.ctl('apply')
        self.assertFalse(self.state()['active'])

    def test_managed_service_rejects_wrong_config_directory(self):
        self.setup()
        for args in (('apply',), ('start',), ('stop',), ('restart',), ('service', 'enable'), ('service', 'remove')):
            with self.subTest(args=args):
                result = self.ctl(*args, config=self.root / 'wrong', ok=False)
                self.assertIn('different directory', result.stderr)
        self.assertTrue(self.unit.exists())

    def test_remove_stops_service_keeps_rules_and_backs_up_owned_files(self):
        self.setup()
        self.add()
        self.ctl('start')
        original = self.unit.read_bytes()
        self.ctl('service', 'remove')
        self.assertFalse(self.unit.exists())
        self.assertEqual(self.state(), {'enabled': False, 'active': False})
        self.assertTrue((self.config / 'web.toml').exists())
        self.assertEqual(self.backups()[0].read_bytes(), original)
        self.assertIn(['disable', '--now', '--', 'realm.service'], self.calls())

    def test_remove_adopted_service_preserves_original_and_other_overrides(self):
        original = self.existing_service()
        self.ctl('init')
        self.setup('--adopt')
        custom = self.units / 'realm.service.d/50-custom.conf'
        custom.write_text('[Service]\nEnvironment=KEEP=yes\n')
        self.ctl('service', 'remove')
        self.assertEqual(self.unit.read_text(), original)
        self.assertEqual(custom.read_text(), '[Service]\nEnvironment=KEEP=yes\n')
        self.assertFalse(self.override.exists())
        self.ctl('service', 'remove', ok=False)
        self.assertEqual(self.unit.read_text(), original)

    def test_remove_failure_restores_files_without_restarting(self):
        self.setup()
        original = self.unit.read_bytes()
        self.fail_next('disable')
        self.ctl('service', 'remove', ok=False)
        self.assertEqual(self.unit.read_bytes(), original)
        self.fail_next('daemon-reload')
        self.ctl('service', 'remove', ok=False)
        self.assertEqual(self.unit.read_bytes(), original)
        self.assertEqual(self.state(), {'enabled': False, 'active': False})

    def test_remove_rejects_symlinked_override_directory(self):
        self.existing_service()
        self.ctl('init')
        self.setup('--adopt')
        directory = self.override.parent
        outside = self.root / 'moved-overrides'
        directory.rename(outside)
        directory.symlink_to(outside, target_is_directory=True)
        original = (outside / '90-realmctl.conf').read_bytes()
        self.ctl('service', 'remove', ok=False)
        self.assertEqual((outside / '90-realmctl.conf').read_bytes(), original)

    def test_unit_lock_blocks_setup_and_runtime_even_with_another_config(self):
        self.setup()
        descriptor = os.open(self.units, os.O_RDONLY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for args in (('setup',), ('stop',), ('service', 'disable'), ('service', 'remove')):
                result = self.ctl(*args, config=self.root / 'other', ok=False)
                self.assertIn('Another realmctl', result.stderr)
        finally:
            os.close(descriptor)
        self.assertFalse((self.root / 'other').exists())

    def test_invalid_service_arguments_have_no_side_effects(self):
        for args in (('setup', '--bad'), ('setup', '--binary'), ('setup', '--import'),
                     ('setup', '--binary', str(self.realm), '--binary', str(self.realm)),
                     ('start', 'web'), ('stop', 'extra'), ('restart', 'extra'),
                     ('service',), ('service', 'bad'), ('service', 'enable', 'extra'),
                     ('--service', 'realm@.service', 'setup'), ('--service', 'a' * 256, 'setup')):
            self.ctl(*args, ok=False)
            self.assertFalse(self.unit.exists())
            self.assertFalse(self.config.exists())
        self.assertEqual(self.calls(), [])

    @unittest.skipUnless(shutil.which('systemd-analyze'), 'systemd-analyze is not installed')
    def test_generated_unit_handles_special_characters_and_passes_systemd_validation(self):
        unusual = self.root / 'realm %i $HOME with spaces'
        shutil.copy2(self.realm, unusual)
        config = self.root / 'rules %i $HOME "quoted"\\'
        self.ctl('setup', '--binary', str(unusual), config=config)
        content = self.unit.read_text()
        self.assertIn('%%i', content)
        self.assertIn('$HOME', content)
        self.assertNotIn('$$HOME', content)
        self.assertIn('\\"quoted\\"', content)
        result = subprocess.run(['systemd-analyze', 'verify', str(self.unit)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_binary_paths_unsupported_by_systemd_are_rejected_before_writes(self):
        for name in ('realm"quoted', "realm'quoted", 'realm\\slash'):
            path = self.root / name
            shutil.copy2(self.realm, path)
            self.setup('--binary', str(path), ok=False)
            self.assertFalse(self.unit.exists())
            self.assertFalse(self.config.exists())


if __name__ == '__main__':
    unittest.main()
