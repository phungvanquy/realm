# Manage relay rules with realmctl

`realmctl` is a Bash utility for named Realm rules on Linux. Each rule has a configuration file, and changes take effect when the Realm service next starts. `realmctl apply` explicitly restarts the service; existing connections are interrupted.

It requires Bash, standard GNU utilities, and `flock` (from `util-linux`). The `apply`, `status`, and `logs` commands also require systemd. File management works without systemd, including in a directory you own.

## Install

The [installer](readme.md#install-on-ubuntu) installs `realmctl` beside `realm` when the selected release bundles it. Both the full and slim release archives include the same script. Older releases still install successfully and leave an existing `realmctl` unchanged.

From a checkout of this repository, install the script directly:

```bash
sudo install -m 0755 realmctl /usr/local/bin/realmctl
realmctl --help
```

For a manually downloaded release, extract its archive and run the same `install` command on the bundled `realmctl` file.

## Start with named rules

Initialize the directory and add a rule:

```bash
sudo realmctl init
sudo realmctl add web --listen 0.0.0.0:8443 --remote example.com:443
realmctl list
realmctl show web
```

`init` creates `/etc/realm/conf.d` and `_global.toml`, with logging to standard output. Repeating it preserves existing settings. It does not install or start a service. For an existing installation, use the migration instructions below before initializing this directory.

For a new systemd installation, create `/etc/systemd/system/realm.service`:

```ini
[Unit]
Description=Realm TCP and UDP relay
Wants=network-online.target
After=network-online.target

[Service]
Type=exec
DynamicUser=yes
ExecStart=/usr/local/bin/realm -c /etc/realm/conf.d
UnsetEnvironment=REALM_CONF
Restart=on-failure
RestartSec=3
LimitNOFILE=1048576
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE

[Install]
WantedBy=multi-user.target
```

Then enable the service at boot and start the configured rules:

```bash
sudo systemctl daemon-reload
sudo systemctl enable realm
sudo realmctl apply
realmctl status
sudo realmctl logs
```

Allow the chosen listening ports in your host and cloud firewall rules. The utility does not change firewall settings.

## Migrate an existing configuration

Start with a new, empty `/etc/realm/conf.d` directory:

```bash
sudo realmctl init --import /etc/realm/config.toml
realmctl show imported
```

This copies the complete configuration to `imported.toml`, preserving its contents, ownership, and permissions. The source file remains unchanged. All existing endpoints initially belong to one rule named `imported`; you can split them into separate files later. `_global.toml` starts with only `endpoints = []`, so it does not override imported global settings.

Update the existing service through a drop-in:

```bash
sudo install -d -m 0755 /etc/systemd/system/realm.service.d
sudo tee /etc/systemd/system/realm.service.d/50-realmctl.conf >/dev/null <<'EOF'
[Service]
ExecStart=
ExecStart=/usr/local/bin/realm -c /etc/realm/conf.d
UnsetEnvironment=REALM_CONF
EOF
sudo systemctl daemon-reload
sudo realmctl apply
```

Use your actual binary path and retain any required command-line options in `ExecStart`. `UnsetEnvironment` clears `REALM_CONF`, which otherwise takes precedence over the configuration path in Realm. Existing service settings such as user, capabilities, and restart policy remain in effect.

## Commands

Global options go before the command:

```bash
realmctl --config-dir /path/to/conf.d --service realm-custom list
```

The directory defaults to `/etc/realm/conf.d`, and the service defaults to `realm.service`. A service name without `.service` gets that suffix automatically. Configure the selected service's `ExecStart` to read the same directory; `--config-dir` alone does not change a systemd unit. Use `sudo` for writes to a system directory and service changes. Reading private rule files or the journal may also require `sudo`.

| Command | Behavior |
| --- | --- |
| `init [--import FILE]` | Initialize an empty configuration directory; optionally copy an existing configuration as `imported`. |
| `add NAME --listen IP:PORT --remote HOST:PORT` | Add a TCP rule. Names must be unique, start with a letter or digit, and contain up to 64 letters, digits, dots, underscores, or hyphens. |
| `list` | Show names and configured enabled/disabled states. This is not a report of live listeners. |
| `show NAME` | Print the complete rule, including comments and advanced settings. |
| `edit NAME` | Edit a temporary copy, then back up and atomically replace the original after a successful editor exit. |
| `enable NAME` / `disable NAME` | Rename the file to change whether Realm loads it on its next start. Repeating the operation is harmless. |
| `remove NAME` | Keep a backup, then delete the rule. |
| `apply` | Restart the selected service. Stop it if no rule files are enabled. |
| `status` | Show the selected service's systemd status and return its exit status. |
| `logs [--follow]` | Show the last 50 journal entries, optionally following new entries. |

Choose a protocol or create a disabled rule:

```bash
sudo realmctl add dns --listen 0.0.0.0:5353 --remote 192.0.2.10:53 --protocol both
sudo realmctl add game --listen 0.0.0.0:27015 --remote 192.0.2.20:27015 --protocol udp
sudo realmctl add internal --listen '[::1]:9000' --remote '[2001:db8::10]:9000' --disabled
```

`--protocol` accepts `tcp` (default), `udp`, or `both`. The script writes protocol options inside each endpoint, so a rule's protocol choice takes precedence over global network settings. Listen addresses must be IPv4 or bracketed IPv6 literals; remote addresses may also use DNS hostnames. Ports must be between 1 and 65535. Scoped IPv6 addresses such as `[fe80::1%eth0]` are not accepted by `add`.

## Editing

```bash
sudo realmctl edit web
sudo realmctl apply
```

The editor is selected from `VISUAL`, then `EDITOR`, then `nano`. To select one explicitly through sudo:

```bash
sudo env VISUAL='vim -f' realmctl edit web
```

The script accepts a command with simple whitespace-separated arguments, such as `code --wait`, or an exact executable path containing spaces. It does not interpret shell quoting, substitutions, pipes, or redirects. Use a wrapper executable for complex editor commands. GUI editors must wait for editing to finish before returning.

The temporary copy lives in a private, hidden directory that Realm ignores. An unsuccessful editor exit, a missing or empty edited file, or a replacement symlink leaves the original untouched. An unchanged edit creates no backup. Successful edits preserve ownership, file permissions, comments, and all advanced settings. Editing a disabled rule keeps it disabled.

If the original changes outside `realmctl` while the editor is open, the script leaves that external change intact and prints the path of the edited copy for recovery. All `realmctl` commands that read or change configuration use a directory lock; another conflicting command fails promptly while an edit or apply is in progress. Direct edits through other tools do not take this lock.

## Files and backups

```text
/etc/realm/conf.d/
  _global.toml                    shared settings, with endpoints = []
  web.toml                        enabled rule
  internal.toml.disabled          disabled rule
  .realmctl-backups/              private copies saved before edit and remove
```

Keep the directory flat. The utility rejects visible subdirectories, symlinks, JSON filenames, and simultaneous enabled/disabled files for the same name. Hidden working directories and backups are skipped by Realm's loader. Unrelated files with other extensions are ignored.

Use `_global.toml` for shared log, DNS, and network settings. Keep its root `endpoints = []` entry empty: `apply` determines whether to stop the service by counting enabled rule files. Put endpoints in named rule files. Avoid repeating global settings across files because Realm's directory traversal does not define an override order.

Generated files are readable by the unprivileged service (`0644`); imported files and edited rules retain ownership and permissions. Backups are stored in a `0700` directory with `0600` files. Their paths are printed when created, and copies are retained until you remove them. To restore a backup, copy it to the desired `NAME.toml` or `NAME.toml.disabled`, set ownership and permissions that allow your service to read it, then apply.

## Validation and applying changes

`add` validates names, address syntax, port ranges, and protocol choices. The Bash utility does **not** parse arbitrary TOML, check duplicate or occupied listeners, resolve DNS, or test destination reachability. Edited and imported configurations remain subject to Realm's own startup checks. There is no `realmctl check` command in this version.

Review edited/imported files before applying. A service restart can fail because of invalid configuration, and `apply` does not automatically roll back files. Backups remain available. A successful systemd restart and active status do not prove every listener started; inspect `realmctl logs` for individual endpoint failures. The [systemctl documentation](https://www.freedesktop.org/software/systemd/man/latest/systemctl.html) describes the service restart and status operations used here.

Changes are written to the directory immediately, so any later service start or automatic restart reads them. `apply` is the explicit restart command; it does not provide a separate pending configuration store. Disabling or removing the last enabled rule and applying stops the service. After adding or enabling a rule, applying starts it again.

## Development checks

```bash
bash -n realmctl
bash -n install.sh
shellcheck realmctl install.sh
python3 -B -m unittest discover -s tests -p 'test_*.py'
```

The tests use temporary directories, local archives, and mocked systemd/editor commands. They do not change system configuration or contact release servers.

To also test actual TCP/UDP forwarding, editing, and disabling against a local Realm build:

```bash
cargo build --locked
REALM_TEST_BINARY="$PWD/target/debug/realm" python3 -B -m unittest discover -s tests -p 'test_realmctl_relay.py'
```

This integration test uses temporary files and loopback ports. Build and release workflows run it against both the full and slim binaries.
