"""Exercise the installer without network access or changes to system directories."""

import hashlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest


INSTALLER = Path(__file__).resolve().parents[1] / "install.sh"
RELEASES = "https://github.com/phungvanquy/realm/releases"
TARGETS = {
    "x86_64": "x86_64-unknown-linux-gnu",
    "aarch64": "aarch64-unknown-linux-gnu",
}


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.assets = self.root / "assets"
        self.assets.mkdir()
        self.bin = self.root / "mock-bin"
        self.bin.mkdir()
        self.install_dir = self.root / "new directory" / "bin"
        self.installed = self.install_dir / "realm"
        self.log = self.root / "requests"
        self.env = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "REALM_TEST_ASSETS": str(self.assets),
            "REALM_TEST_LOG": str(self.log),
            "REALM_TEST_ARCH": "aarch64",
            "REALM_TEST_OS": "Linux",
        }
        self.mock("uname", """
import os, sys
print(os.environ['REALM_TEST_OS' if sys.argv[1] == '-s' else 'REALM_TEST_ARCH'])
""")
        self.mock("curl", f"""
import os, shutil, sys
from pathlib import Path
args = sys.argv[1:]
url = args[-1]
with open(os.environ['REALM_TEST_LOG'], 'a') as log:
    log.write(url + '\\n')
if '--head' in args and url == '{RELEASES}/latest':
    print(os.environ.get('REALM_TEST_LATEST', '{RELEASES}/tag/v2.9.6'), end='')
elif url.startswith('{RELEASES}/download/v2.9.6/'):
    source = Path(os.environ['REALM_TEST_ASSETS']) / url.rsplit('/', 1)[1]
    if not source.is_file():
        sys.exit(22)
    shutil.copyfile(source, args[args.index('--output') + 1])
else:
    sys.exit(22)
""")
        for arch in TARGETS:
            for binary in ("realm", "realm-slim"):
                self.package(arch, binary)

    def mock(self, name, code):
        path = self.bin / name
        path.write_text(f"#!{sys.executable}\n" + code)
        path.chmod(0o755)

    def archive(self, arch="aarch64", binary="realm"):
        return self.assets / f"{binary}-{TARGETS[arch]}.tar.gz"

    def checksum(self, archive):
        checksum = Path(f"{archive}.sha256")
        checksum.write_text(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n")
        return checksum

    def package(self, arch="aarch64", binary="realm", content=None, symlink=False,
                companion=None, companion_symlink=False):
        archive = self.archive(arch, binary)
        content = content or f"#!/bin/sh\necho 'Realm 2.9.6 ({arch}, {binary})'\n".encode()
        with tarfile.open(archive, "w:gz") as tar:
            member = tarfile.TarInfo(binary)
            member.mode = 0o755
            if symlink:
                member.type = tarfile.SYMTYPE
                member.linkname = "/bin/true"
                tar.addfile(member)
            else:
                member.size = len(content)
                tar.addfile(member, io.BytesIO(content))
            if companion is not None or companion_symlink:
                member = tarfile.TarInfo("realmctl")
                member.mode = 0o755
                if companion_symlink:
                    member.type = tarfile.SYMTYPE
                    member.linkname = "/bin/true"
                    tar.addfile(member)
                else:
                    member.size = len(companion)
                    tar.addfile(member, io.BytesIO(companion))
        self.checksum(archive)
        return archive

    def run_installer(self, *args):
        return subprocess.run(
            ["bash", str(INSTALLER), "--install-dir", str(self.install_dir), *args],
            env=self.env, text=True, capture_output=True, timeout=15,
        )

    def assert_success(self, result, arch, binary):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.installed.stat().st_mode & 0o777, 0o755)
        version = subprocess.check_output([str(self.installed), "--version"], text=True)
        self.assertEqual(version.strip(), f"Realm 2.9.6 ({arch}, {binary})")
        self.assertEqual(list(self.install_dir.iterdir()), [self.installed])

    def test_latest_selects_arm64_and_downloads_matching_checksum(self):
        self.assert_success(self.run_installer(), "aarch64", "realm")
        archive = self.archive().name
        self.assertEqual(self.log.read_text().splitlines(), [
            f"{RELEASES}/latest",
            f"{RELEASES}/download/v2.9.6/{archive}",
            f"{RELEASES}/download/v2.9.6/{archive}.sha256",
        ])

    def test_pinned_amd64_slim_installs_as_realm(self):
        self.env["REALM_TEST_ARCH"] = "x86_64"
        result = self.run_installer("--version", "2.9.6", "--slim")
        self.assert_success(result, "x86_64", "realm-slim")
        self.assertNotIn(f"{RELEASES}/latest", self.log.read_text())

    def test_update_atomically_replaces_existing_binary(self):
        self.install_dir.mkdir(parents=True)
        self.installed.write_text("old binary")
        self.installed.chmod(0o555)
        with self.installed.open() as old:
            self.assert_success(self.run_installer("--version", "v2.9.6"), "aarch64", "realm")
            self.assertEqual(old.read(), "old binary")
            self.assertNotEqual(os.fstat(old.fileno()).st_ino, self.installed.stat().st_ino)

    def test_installs_bundled_realmctl_with_full_and_slim_builds(self):
        companion = (INSTALLER.parent / "realmctl").read_bytes()
        for binary, args in (("realm", ()), ("realm-slim", ("--slim",))):
            with self.subTest(binary=binary):
                self.package(binary=binary, companion=companion)
                result = self.run_installer(*args)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                installed_ctl = self.install_dir / "realmctl"
                self.assertEqual(installed_ctl.read_bytes(), companion)
                self.assertEqual(installed_ctl.stat().st_mode & 0o777, 0o755)
                help_text = subprocess.check_output([str(installed_ctl), "--help"], text=True)
                self.assertIn("edit NAME", help_text)
                self.assertEqual(set(self.install_dir.iterdir()), {self.installed, installed_ctl})

    def test_old_release_preserves_existing_realmctl(self):
        self.install_dir.mkdir(parents=True)
        installed_ctl = self.install_dir / "realmctl"
        installed_ctl.write_text("existing utility\n")
        inode = installed_ctl.stat().st_ino
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("does not bundle realmctl", result.stdout)
        self.assertEqual(installed_ctl.read_text(), "existing utility\n")
        self.assertEqual(installed_ctl.stat().st_ino, inode)

    def test_invalid_companion_preserves_both_installed_files(self):
        self.install_dir.mkdir(parents=True)
        self.installed.write_text("old binary\n")
        installed_ctl = self.install_dir / "realmctl"
        installed_ctl.write_text("old utility\n")
        for companion, symlink in ((b"#!/bin/bash\nif then\n", False), (b"", False), (None, True)):
            with self.subTest(companion=companion, symlink=symlink):
                self.package(companion=companion, companion_symlink=symlink)
                result = self.run_installer()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Error:", result.stderr)
                self.assertEqual(self.installed.read_text(), "old binary\n")
                self.assertEqual(installed_ctl.read_text(), "old utility\n")
                self.assertEqual(set(self.install_dir.iterdir()), {self.installed, installed_ctl})

    def test_failed_download_or_validation_preserves_installation(self):
        self.install_dir.mkdir(parents=True)
        self.installed.write_text("old binary")
        old_inode = self.installed.stat().st_ino
        for failure in ("missing archive", "missing checksum", "bad hash", "bad filename",
                        "invalid tar", "symlink", "broken binary"):
            with self.subTest(failure=failure):
                archive = self.package()
                checksum = Path(f"{archive}.sha256")
                if failure == "missing archive":
                    archive.unlink()
                elif failure == "missing checksum":
                    checksum.unlink()
                elif failure == "bad hash":
                    archive.write_bytes(archive.read_bytes() + b"corrupted")
                elif failure == "bad filename":
                    checksum.write_text(checksum.read_text().replace(archive.name, "other.tar.gz"))
                elif failure == "invalid tar":
                    archive.write_bytes(b"not a tar archive")
                    self.checksum(archive)
                elif failure == "symlink":
                    self.package(symlink=True)
                elif failure == "broken binary":
                    self.package(content=b"#!/bin/sh\nexit 126\n")
                result = self.run_installer()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Error:", result.stderr)
                self.assertEqual(self.installed.read_text(), "old binary")
                self.assertEqual(self.installed.stat().st_ino, old_inode)
                self.assertEqual(list(self.install_dir.iterdir()), [self.installed])

    def test_unsupported_platform_fails_before_downloading(self):
        for system, arch in (("Darwin", "aarch64"), ("Linux", "armv7l")):
            with self.subTest(system=system, arch=arch):
                self.env.update(REALM_TEST_OS=system, REALM_TEST_ARCH=arch)
                self.assertNotEqual(self.run_installer().returncode, 0)
                self.assertFalse(self.log.exists())

    def test_invalid_arguments_fail_before_downloading(self):
        for args in (("--unknown",), ("--version",), ("--version", "../bad"),
                     ("--install-dir",), ("--install-dir", "relative/path")):
            with self.subTest(args=args):
                self.assertNotEqual(self.run_installer(*args).returncode, 0)
                self.assertFalse(self.log.exists())

    def test_unexpected_latest_redirect_fails_before_downloading_assets(self):
        self.env["REALM_TEST_LATEST"] = "https://example.com/tag/v2.9.6"
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unexpected release URL", result.stderr)
        self.assertEqual(self.log.read_text().splitlines(), [f"{RELEASES}/latest"])
        self.assertFalse(self.installed.exists())

    def test_help_does_not_download_or_install(self):
        result = self.run_installer("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Usage:", result.stdout)
        self.assertFalse(self.log.exists())
        self.assertFalse(self.installed.exists())


if __name__ == "__main__":
    unittest.main()
