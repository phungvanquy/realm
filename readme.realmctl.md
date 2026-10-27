# Manage Realm with realmctl

Use `realmctl` to set up the service, manage named relay rules, edit shared settings, and control forwarding. You do not need to write a systemd unit or run `systemctl` for the normal workflow.

[New server](#first-time-setup) · [Existing Realm configuration](#migrate-an-existing-configuration) · [Everyday commands](#commands) · [Safe apply and recovery](#validation-and-recovery) · [Troubleshooting](#troubleshooting)

## First-time setup

These steps are for a new installation on Ubuntu 22.04 or newer with systemd. If you already have a Realm service and configuration, use [migration](#migrate-an-existing-configuration) instead.

### 1. Install both commands

This guide describes the repository version. Its validation and recovery features require **both `realm` and `realmctl` from the same build**. They are not included in v2.9.8. Until a release includes them, [build and install both from source](#install-from-source-and-development-checks); replacing only the Bash script is insufficient.

For packaged releases, the installer installs both commands:

```bash
curl -fsSL https://raw.githubusercontent.com/phungvanquy/realm/main/install.sh | bash
realm --version
realmctl --help
```

The installer selects AMD64 or ARM64 automatically and installs both commands to `/usr/local/bin`. If `curl` is missing, run `sudo apt update` and `sudo apt install -y curl ca-certificates` first.

Before following this guide, confirm `realmctl --help` lists `check` and `recover`, and `realm check --version` prints checker version `1`. Older binaries are rejected before service setup or restart. For an existing managed service, run `sudo realmctl setup` once after upgrading to record its binary path; use `setup --adopt` for a manually created service.

### 2. Set up the service and add a rule

Replace `192.168.1.100:443` with the destination service's address and port:

```bash
sudo realmctl setup
sudo realmctl add web --listen 0.0.0.0:5000 --remote 192.168.1.100:443
realmctl list
```

The rule is named **`web`**. It will forward TCP connections arriving on this server's port **5000** to **192.168.1.100:443**. Use `127.0.0.1:5000` to accept connections only from this machine.

`setup` finds the installed Realm binary, initializes `/etc/realm/conf.d`, creates the service, reloads systemd, and enables startup at boot. It keeps existing rules and does not start or restart forwarding. Repeat it when you need to update the service definition. Use `setup --no-enable` to skip boot enablement; it does not disable an already enabled service.

`list` should show `web` as `enabled`. This means the rule will run on the next service start.

### 3. Start forwarding

```bash
sudo realmctl apply
realmctl status
sudo realmctl logs
```

`apply` reports success only after the service process owns every expected TCP/UDP listener. The status should show **`active (running)`**. For remote clients, allow TCP port **5000** in your host and cloud firewall rules. Listener readiness does not test the destination or firewall.

Setup is complete. From now on, add or change a rule, then run `sudo realmctl apply`. Applying changes restarts the service and interrupts existing connections.

## Commands

Use these commands after the one-time setup above. Replace `web` with your rule's name.

| Task | Command |
| --- | --- |
| Create or update the service | `sudo realmctl setup` |
| List rules | `realmctl list` |
| Show a rule's configuration | `realmctl show web` |
| Edit a rule | `sudo realmctl edit web` |
| Disable a rule | `sudo realmctl disable web` |
| Enable it again | `sudo realmctl enable web` |
| Remove a rule, keeping a backup | `sudo realmctl remove web` |
| Show shared settings | `realmctl config show` |
| Edit shared settings | `sudo realmctl config edit` |
| Validate enabled rules without restarting | `sudo realmctl check` |
| Apply changes | `sudo realmctl apply` |
| Recover an interrupted apply | `sudo realmctl recover` |
| Start forwarding | `sudo realmctl start` |
| Stop forwarding | `sudo realmctl stop` |
| Restart forwarding | `sudo realmctl restart` |
| Start automatically at boot | `sudo realmctl service enable` |
| Turn off automatic startup | `sudo realmctl service disable` |
| Inspect the service definition | `realmctl service show` |
| Remove the managed service | `sudo realmctl service remove` |
| Check the service | `realmctl status` |
| Read recent logs | `sudo realmctl logs` |
| Watch logs live | `sudo realmctl logs --follow` |

After **add, edit, enable, disable, or remove**, run `sudo realmctl apply` to change the running service. If no rules are enabled, `apply` stops the service. Enabling a rule and applying starts it again.

`start` leaves an already running service alone. If its configuration is changed or has never been verified, it asks you to use `apply`. `restart` uses the same validation and recovery as `apply`. `service enable` and `service disable` only change startup at boot. They do not start or stop the running service. Rule commands such as `disable web` change one rule, independently of boot settings.

`service remove` stops and disables the service, backs up its managed files, and removes them. It keeps your rules and binaries. If you adopted an existing service, its original unit and other overrides remain, stopped and disabled. Run `setup` again to restore a service created by realmctl, or `setup --adopt` to manage an existing one again.

### Add more rules

Replace the example destination addresses with your own services. Each name must be unique:

```bash
# TCP is the default
sudo realmctl add app --listen 0.0.0.0:8080 --remote 192.168.1.100:80

# TCP and UDP on the same port
sudo realmctl add dns --listen 0.0.0.0:5353 --remote 192.168.1.100:53 --protocol both

# UDP only
sudo realmctl add game --listen 0.0.0.0:27015 --remote 192.168.1.100:27015 --protocol udp

sudo realmctl apply
```

`--protocol` accepts `tcp`, `udp`, or `both`. Add `--disabled` to save a rule without enabling it. Rule names start with a letter or digit and can contain up to 64 letters, digits, dots, underscores, or hyphens.

## Editing

Open a rule in your editor:

```bash
sudo realmctl edit web
```

To change its destination, edit the `remote = "..."` line. In **nano**, press **Ctrl+O**, **Enter** to save, then **Ctrl+X** to exit. Exit without saving to discard your changes.

Then check and apply the saved rule. `apply` also checks automatically:

```bash
sudo realmctl check
sudo realmctl apply
sudo realmctl logs
```

`edit` uses `VISUAL`, then `EDITOR`, then `nano`. To choose an editor explicitly:

```bash
sudo env VISUAL='vim -f' realmctl edit web
```

Saving changes creates a backup and preserves ownership, permissions, comments, and advanced settings. An unsuccessful editor exit or an empty file leaves the original unchanged. Editing a disabled rule keeps it disabled.

To edit shared logging, DNS, or network settings, use the same workflow:

```bash
sudo realmctl config edit
sudo realmctl apply
```

This edits `_global.toml` with the same backup and locking behavior. Keep its root `endpoints = []` entry empty; put listeners in named rules. See the [configuration reference](readme.md#configuration) for supported settings.

## Validation and recovery

`realmctl check` uses Realm's own configuration loader and endpoint builders to validate enabled rules and shared settings. It catches malformed TOML/JSON, invalid endpoint settings, unsupported build features, and duplicate listeners. It does not open listening ports or create log files. Disabled rules are checked after you enable them and run `check` or `apply`. Use absolute paths for certificate, key, and log files so checks and the service refer to the same files.

Checks use the binary recorded by `setup` for the selected directory and service. For configuration-only use, `check` finds `realm` beside the script or on `PATH`. Override either choice with `realmctl check --binary /absolute/path/to/realm`.

`apply`, `restart`, and `start` follow these steps:

1. Verify that systemd's effective start command uses the selected binary and configuration directory, with the expected service type and environment handling.
2. Save a private configuration snapshot and validate the configuration before changing the running service. Validation failure leaves the current process running.
3. Start or restart the service, then verify that its process owns every configured TCP and UDP listener. A port held by another process cannot satisfy this check. A successful check records a verified snapshot.
4. If startup or listener checks fail, restore the previous verified configuration, including disabled rules and file permissions. If the service was running before the attempt, start it again and verify its listeners. The command still exits with an error and prints the saved location of the rejected changes.

**The first successful `start` or `apply` establishes the rollback snapshot.** Before that, there is no verified configuration to restore: a failed initial start keeps your files and stops the failed service. Configuration recovery does not restore an older executable or undo systemd changes made outside realmctl. If rollback itself fails, the recovery record and saved files remain available.

After an interrupted apply, or if recovery could not finish, fix the reported service problem and run:

```bash
sudo realmctl recover
sudo realmctl check
realmctl status
```

Use the same `--config-dir` and `--service` options as the interrupted operation. Rule changes are blocked while recovery is pending. Repeating `apply` also attempts recovery, then exits for you to review the restored rules before applying again. Interrupted initialization/import resumes when you repeat `init` or `setup`; an interrupted service-file update is recovered by the next service-changing command.

Readiness is a startup check, not continuous monitoring or a test of DNS resolution, remote connectivity, or firewall access. The default readiness wait is 10 seconds; set `REALMCTL_START_TIMEOUT` to 1–60 seconds if needed, for example `sudo env REALMCTL_START_TIMEOUT=30 realmctl apply`. Separate configuration checks and systemd jobs have their own time limits.

File changes are saved immediately. Any later service start, including an automatic restart, can read them before you run `apply`. Run `check` promptly after editing; realmctl does not provide a separate pending configuration directory.

## Migrate an existing configuration

If you already use realmctl rules but created the service manually, adopt it once:

```bash
sudo realmctl setup --adopt
sudo realmctl apply
```

If your service still reads a single file such as `/etc/realm/config.toml`, import it instead. This needs a new configuration directory, so run it **before** plain `realmctl setup` or `init`:

### 1. Import and set up the service

```bash
sudo realmctl setup --import /etc/realm/config.toml
sudo realmctl show imported
```

This copies the whole configuration into one rule named **`imported`**. Your original file stays unchanged, and its contents, ownership, and permissions are preserved in the copy. Existing endpoints and global settings stay together; you can split them into named rules later.

`--import` also adopts an existing service, or creates one if none exists. Adoption adds `90-realmctl.conf` while keeping the original unit and other overrides. It replaces the start command with `realm -c /etc/realm/conf.d`, uses `Type=exec`, and clears `REALM_CONF`. Other settings, such as the service user and resource limits, stay in place. Move any required extra command-line settings into your configuration before applying. For a nonstandard binary location, add `--binary /absolute/path/to/realm`.

Imported files retain their permissions. Make sure the service user can read them; new services run as a temporary unprivileged user. Setup leaves the running process alone until you apply.

### 2. Apply and check

```bash
sudo realmctl apply
realmctl status
sudo realmctl logs
```

You can now use `sudo realmctl edit imported` or add new rules with their own names.

Import does **not** overwrite or merge existing rules. It requires a new directory, except that repeating the same unchanged import is safe. If setup fails or is interrupted after importing, repeat the same command to resume. Once you edit the imported copy, omit `--import` on future setup runs to keep your changes.

## Troubleshooting

| What you see | What to do |
| --- | --- |
| `realmctl: command not found` | Repeat the [one-line install](readme.md#one-line-install). v2.9.8 introduced `realmctl`. If `/usr/local/bin/realmctl --help` works, add `/usr/local/bin` to your `PATH`. |
| `Unknown command: setup`, or binary lacks native `check` | [Install both commands from the same source build](#install-from-source-and-development-checks); v2.9.8 does not include these service safety features. |
| Run setup before applying rules | Run `setup` once to register the service's binary; use `setup --adopt` for a manual service. |
| Configuration directory is missing | Run `sudo realmctl setup` for a new setup, or `sudo realmctl setup --import /etc/realm/config.toml` to migrate. |
| Import requires a new configuration directory | Keep your existing rules. Use `--config-dir` with another empty directory during setup; see [custom paths](#custom-paths-and-services). |
| Service already exists | Use `setup --adopt` for existing realmctl rules, or `setup --import FILE` for an older single-file configuration. |
| Boot state is `static` | The existing unit has no boot installation target. Inspect `realmctl service show`; use `setup --no-enable` if another service controls its startup. |
| `Unit realm.service could not be found` | Run `sudo realmctl setup`, then apply. |
| `Required command not found: nano` | Install it with `sudo apt install nano`, or [choose another editor](#editing). |
| Another realmctl command is using the directory | Finish or close the other editor/command, then retry. |
| Rule changes have no effect | Run `sudo realmctl apply`. Use `realmctl service show` to confirm `ExecStart` reads `/etc/realm/conf.d`. |
| Service uses a different configuration directory | Use the matching `--config-dir` on every command. Run `setup` with a new directory only when you intend to switch the service to it. |
| Effective service differs from requested setup | Inspect `realmctl service show` for conflicting overrides, including a later `override.conf`. Resolve the conflict and repeat setup. Your other overrides are preserved; realmctl restores its previous service files on failure. |
| Configuration validation failed | Run `sudo realmctl check`, correct the reported file, then apply. The existing process was kept. |
| Not all listeners became ready | Read `sudo realmctl logs`; check occupied ports and service-user permissions. A previous verified configuration is restored when available. The failed command prints where rejected changes were saved. |
| Recovery is pending | Fix the reported service problem, then run `sudo realmctl recover` with the same directory and service options. |
| `Address already in use` in the logs | Stop the foreground relay if it is still running, or choose a free listening port. Check for overlapping rules. |
| No previously verified snapshot is available | Correct the initial configuration and apply again. The first successful start/apply establishes recovery history. |
| Remote clients cannot connect | Check the listening address, selected protocol, host/cloud firewall rules, and destination reachability from this server. |

## Files and backups

```text
/etc/realm/conf.d/
  _global.toml            shared log, DNS, and network settings
  web.toml                enabled rule
  game.toml.disabled      disabled rule
  .realmctl-backups/      copies saved before edits and removals
  .realmctl-state/        private verified snapshots and apply recovery records
```

`realmctl` prints the backup path when it edits or removes a rule. Backups remain until you delete them. To restore one, copy it back to the rule's filename, restore ownership and permissions that let the service read it, then apply. New rules use mode `0644`; backup files are private (`0600` inside a `0700` directory).

Keep `_global.toml`'s root `endpoints = []` entry empty, and place endpoints in named rule files. When splitting an imported configuration, move shared settings into `_global.toml` and avoid duplicating them across files. Directory loading uses filename order; keeping shared settings in one file avoids order-dependent settings.

Snapshots are stored per service under `.realmctl-state` in private directories, with the original configuration file modes and ownership. Failed candidates remain there for inspection; they are not automatically reapplied or pruned. Keep the snapshot named by `last-good`, and never delete a pending `transaction` or its referenced snapshots. Other old snapshots can be removed when no realmctl command is running. Recovery records handle process interruption, including SIGKILL; they do not replace an external backup against disk failure or power loss.

New services use `/etc/systemd/system/realm.service`; adopted services use `/etc/systemd/system/realm.service.d/90-realmctl.conf`. Service updates and removal save private copies in `/etc/systemd/system/.realmctl-backups`, using a service-name hash to keep filenames bounded. A failed systemd reload or effective-command check restores the previous service files. Configuration files remain available after a setup failure so you can retry. Temporary recovery records live in `.realmctl-init` for initialization and `/etc/systemd/system/.realmctl-transactions` for service-file updates.

Use your own systemd override for advanced service settings, while retaining the start command, `Type=exec`, and `REALM_CONF` handling installed by realmctl. Conflicting effective settings cause setup/apply to fail. Files marked as managed by realmctl are regenerated by `setup`.

<details>
<summary>File layout, editing, and validation details</summary>

- Keep the directory flat and give it a basename that does not start with a dot, such as `conf.d`. Visible subdirectories, symlinks, JSON filenames, and enabled/disabled copies of the same rule are rejected. Realm ignores hidden working directories and backups.
- `add` checks rule names, address syntax, port ranges, and protocol choices. Use `check` after editing/importing; it validates the complete enabled configuration with Realm's parser.
- `check` does not reserve ports. Port conflicts and service-user access failures are detected by startup/readiness checks, with [recovery](#validation-and-recovery) when a verified snapshot exists.
- Edits use a temporary copy and a directory lock. If another tool changes the original during editing, `realmctl` preserves that change and prints the saved location of your edited copy.
- Editor commands accept simple whitespace-separated arguments or an exact executable path. Use a wrapper for complex shell commands, and use a waiting option such as `code --wait` for GUI editors.

</details>

## Custom paths and services

Put global options **before** the command:

```bash
sudo realmctl --config-dir /etc/realm/custom.d --service realm-custom setup
sudo realmctl --config-dir /etc/realm/custom.d --service realm-custom add web \
  --listen 0.0.0.0:5000 --remote 192.168.1.100:443
sudo realmctl --config-dir /etc/realm/custom.d --service realm-custom apply
```

Use the same options for every command that manages those rules. `setup` configures the selected service to read that directory. Other commands refuse to change a managed service associated with a different directory. The default service is `realm.service`; `.service` is added automatically if omitted. The complete service name can contain up to 253 characters.

`setup` finds `realm` beside `realmctl`, then searches `PATH`. Use `setup --binary /opt/realm/realm` to select another executable. Keep it in a location the service user can access. New services can listen on ports below `1024` and write logs to the journal by default.

The utility needs Bash, GNU utilities, and `flock` from `util-linux`. Service verification needs `busctl`, a running systemd system manager, Linux `/proc` access, and the matching Realm binary. For configuration-only use, run `init` instead of `setup`; file management and `check` work in a directory you own without systemd. Use `sudo` for system-directory writes, service changes, and reading private files or logs.

<details>
<summary>IPv6 and advanced relay options</summary>

Listen addresses must be IPv4 or bracketed IPv6 literals. Remote addresses can also be DNS hostnames. Ports must be between 1 and 65535. For example:

```bash
sudo realmctl add internal --listen '[::1]:9000' --remote '[2001:db8::10]:9000' --disabled
```

`add` does not accept scoped IPv6 addresses such as `[fe80::1%eth0]`. Each generated rule stores its protocol choice in `[endpoints.network]`, overriding global network settings. Use `edit` for other options from the [configuration reference](readme.md#configuration).

</details>

## Install from source and development checks

From a checkout of this repository, with the [build prerequisites](readme.md#build-from-source) installed, build and install both commands together:

```bash
cargo build --locked --release
sudo install -m 0755 target/release/realm /usr/local/bin/realm
sudo install -m 0755 realmctl /usr/local/bin/realmctl
realm check --version
realmctl --help
```

For the slim build, add `--no-default-features --features default-slim` to `cargo build`. Run `setup` once for an existing managed service after upgrading, then `apply` to load and verify its configuration. Use `setup --adopt` if its unit was created manually.

<details>
<summary>Run the utility tests</summary>

```bash
bash -n realmctl
bash -n install.sh
shellcheck realmctl install.sh
python3 -B -m unittest discover -s tests -p 'test_*.py'
```

These tests use temporary directories and mocked service commands. To test real TCP/UDP forwarding, editing, and disabling against a local build:

```bash
cargo build --locked
REALM_TEST_BINARY="$PWD/target/debug/realm" python3 -B -m unittest discover -s tests -p 'test_realm_check.py'
REALM_TEST_BINARY="$PWD/target/debug/realm" python3 -B -m unittest discover -s tests -p 'test_realmctl_relay.py'
```

The integration test uses loopback ports. Build and release workflows run it against both full and slim binaries.

To also test actual service creation, forwarding, restarts, and removal on a development machine with systemd:

```bash
sudo env REALM_TEST_SYSTEMD=1 REALM_TEST_BINARY="$PWD/target/debug/realm" \
  python3 -B -m unittest discover -s tests -p 'test_realmctl_systemd.py'
```

These opt-in tests create uniquely named runtime services, do not enable them at boot, and remove their resources afterward. They cover migration of an active manual service, conflicting overrides, invalid edits, and rollback after a failed listener. The mocked tests also interrupt import, setup, and apply with signals to exercise recovery. Build and release workflows run native and service tests against both full and slim binaries. `REALMCTL_UNIT_DIR` lets the tests select `/run/systemd/system`; normal installations use `/etc/systemd/system`.

</details>
