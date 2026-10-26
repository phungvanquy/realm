# Manage relay rules with realmctl

Use `realmctl` to give each relay a name, edit its destination, and enable or disable it. It is included with Realm starting in **v2.9.8**, in both the full and slim releases.

[New server](#first-time-setup) · [Existing Realm configuration](#migrate-an-existing-configuration) · [Everyday commands](#commands) · [Edit a rule](#editing) · [Troubleshooting](#troubleshooting)

## First-time setup

These steps are for a new installation on Ubuntu 22.04 or newer with systemd. If you already have a Realm service and configuration, use [migration](#migrate-an-existing-configuration) instead.

### 1. Install both commands

Skip this step if `realm` and `realmctl` are already installed.

```bash
curl -fsSL https://raw.githubusercontent.com/phungvanquy/realm/main/install.sh | bash
realm --version
realmctl --help
```

The installer selects AMD64 or ARM64 automatically and installs both commands to `/usr/local/bin`. If `curl` is missing, run `sudo apt update` and `sudo apt install -y curl ca-certificates` first.

### 2. Add your first rule

Replace `192.168.1.100:443` with the destination service's address and port:

```bash
sudo realmctl init
sudo realmctl add web --listen 0.0.0.0:5000 --remote 192.168.1.100:443
realmctl list
```

The rule is named **`web`**. It will forward TCP connections arriving on this server's port **5000** to **192.168.1.100:443**. Use `127.0.0.1:5000` to accept connections only from this machine.

`list` should show `web` as `enabled`. This means the rule is configured to run; forwarding starts in step 4. `init` creates the configuration directory but does not start a service.

### 3. Create the service

Run this once on a new installation so Realm can run in the background and start at boot:

```bash
sudo tee /etc/systemd/system/realm.service >/dev/null <<'EOF'
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
EOF

sudo systemctl daemon-reload
sudo systemctl enable realm
```

### 4. Start forwarding

```bash
sudo realmctl apply
realmctl status
sudo realmctl logs
```

The status should show **`active (running)`**. Check the logs for listener errors. For remote clients, allow TCP port **5000** in your host and cloud firewall rules.

Setup is complete. From now on, add or change a rule, then run `sudo realmctl apply`. Applying changes restarts the service and interrupts existing connections.

## Commands

Use these commands after the one-time setup above. Replace `web` with your rule's name.

| Task | Command |
| --- | --- |
| List rules | `realmctl list` |
| Show a rule's configuration | `realmctl show web` |
| Edit a rule | `sudo realmctl edit web` |
| Disable a rule | `sudo realmctl disable web` |
| Enable it again | `sudo realmctl enable web` |
| Remove a rule, keeping a backup | `sudo realmctl remove web` |
| Apply changes | `sudo realmctl apply` |
| Check the service | `realmctl status` |
| Read recent logs | `sudo realmctl logs` |
| Watch logs live | `sudo realmctl logs --follow` |

After **add, edit, enable, disable, or remove**, run `sudo realmctl apply` to change the running service. If no rules are enabled, `apply` stops the service. Enabling a rule and applying starts it again.

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

Then apply the saved rule:

```bash
sudo realmctl apply
sudo realmctl logs
```

`edit` uses `VISUAL`, then `EDITOR`, then `nano`. To choose an editor explicitly:

```bash
sudo env VISUAL='vim -f' realmctl edit web
```

Saving changes creates a backup and preserves ownership, permissions, comments, and advanced settings. An unsuccessful editor exit or an empty file leaves the original unchanged. Editing a disabled rule keeps it disabled.

## Migrate an existing configuration

Use this path if your current service reads `/etc/realm/config.toml`. Run it **before** plain `realmctl init`: migration needs a new configuration directory.

### 1. Import your configuration

```bash
sudo realmctl init --import /etc/realm/config.toml
sudo realmctl show imported
```

This copies the whole configuration into one rule named **`imported`**. Your original file stays unchanged, and its contents, ownership, and permissions are preserved in the copy. Existing endpoints and global settings stay together; you can split them into named rules later.

### 2. Point the service at the rules directory

For the service from the main README, run:

```bash
sudo install -d -m 0755 /etc/systemd/system/realm.service.d
sudo tee /etc/systemd/system/realm.service.d/50-realmctl.conf >/dev/null <<'EOF'
[Service]
ExecStart=
ExecStart=/usr/local/bin/realm -c /etc/realm/conf.d
UnsetEnvironment=REALM_CONF
EOF

sudo systemctl daemon-reload
```

If you use a different binary path or extra command-line options, adjust `ExecStart` above to keep them. The file overrides the start command while retaining your other service settings. `UnsetEnvironment` clears `REALM_CONF`, which would otherwise override the configuration directory.

### 3. Apply and check

```bash
sudo realmctl apply
realmctl status
sudo realmctl logs
```

You can now use `sudo realmctl edit imported` or add new rules with their own names.

## Troubleshooting

| What you see | What to do |
| --- | --- |
| `realmctl: command not found` | Repeat the [one-line install](readme.md#one-line-install). v2.9.8 introduced `realmctl`. If `/usr/local/bin/realmctl --help` works, add `/usr/local/bin` to your `PATH`. |
| Configuration directory is missing | Run `sudo realmctl init` for a new setup, or `sudo realmctl init --import /etc/realm/config.toml` to migrate. |
| Import requires a new configuration directory | Keep your existing rules. Import into another empty directory with `--config-dir`, then point the service at that directory; see [custom paths](#custom-paths-and-services). |
| `Unit realm.service could not be found` | Complete [step 3: create the service](#3-create-the-service), then apply. |
| `Required command not found: nano` | Install it with `sudo apt install nano`, or [choose another editor](#editing). |
| Another realmctl command is using the directory | Finish or close the other editor/command, then retry. |
| Rule changes have no effect | Run `sudo realmctl apply`. Use `systemctl cat realm` to confirm `ExecStart` reads `/etc/realm/conf.d`. |
| `Address already in use` in the logs | Stop the foreground relay if it is still running, or choose a free listening port. Check for overlapping rules. |
| Service fails after an edit | Run `sudo realmctl logs`, correct the rule, or restore its [backup](#files-and-backups), then apply again. |
| Remote clients cannot connect | Check the listening address, selected protocol, host/cloud firewall rules, and destination reachability from this server. |

## Files and backups

```text
/etc/realm/conf.d/
  _global.toml            shared log, DNS, and network settings
  web.toml                enabled rule
  game.toml.disabled      disabled rule
  .realmctl-backups/      copies saved before edits and removals
```

`realmctl` prints the backup path when it edits or removes a rule. Backups remain until you delete them. To restore one, copy it back to the rule's filename, restore ownership and permissions that let the service read it, then apply. New rules use mode `0644`; backup files are private (`0600` inside a `0700` directory).

Keep `_global.toml`'s root `endpoints = []` entry empty, and place endpoints in named rule files. When splitting an imported configuration, move shared settings into `_global.toml` and avoid duplicating them across files.

<details>
<summary>File layout, editing, and validation details</summary>

- Keep the directory flat. Visible subdirectories, symlinks, JSON filenames, and enabled/disabled copies of the same rule are rejected. Realm ignores hidden working directories and backups.
- `add` checks rule names, address syntax, port ranges, and protocol choices. The utility does not validate edited TOML, detect listener conflicts, resolve DNS, or test destination reachability. There is no `realmctl check` command.
- Review edited or imported files before applying. A failed restart does not automatically roll back files. An active service does not prove every listener started; inspect its logs.
- Changes are saved immediately and read on the next service start, including an automatic restart. `apply` requests that restart explicitly; there is no separate pending configuration store.
- Edits use a temporary copy and a directory lock. If another tool changes the original during editing, `realmctl` preserves that change and prints the saved location of your edited copy.
- Editor commands accept simple whitespace-separated arguments or an exact executable path. Use a wrapper for complex shell commands, and use a waiting option such as `code --wait` for GUI editors.

</details>

## Custom paths and services

Put global options **before** the command:

```bash
realmctl --config-dir /path/to/conf.d --service realm-custom list
```

Use the same options for every command that manages those rules. Configure the selected service to run `realm -c /path/to/conf.d`; `--config-dir` does not change a systemd unit. The default service is `realm.service`; `.service` is added automatically if omitted.

The utility needs Bash, GNU utilities, and `flock` from `util-linux`. File management works in a directory you own without systemd. `apply`, `status`, and `logs` use systemd. Use `sudo` for system-directory writes, service changes, and reading private files or logs.

<details>
<summary>IPv6 and advanced relay options</summary>

Listen addresses must be IPv4 or bracketed IPv6 literals. Remote addresses can also be DNS hostnames. Ports must be between 1 and 65535. For example:

```bash
sudo realmctl add internal --listen '[::1]:9000' --remote '[2001:db8::10]:9000' --disabled
```

`add` does not accept scoped IPv6 addresses such as `[fe80::1%eth0]`. Each generated rule stores its protocol choice in `[endpoints.network]`, overriding global network settings. Use `edit` for other options from the [configuration reference](readme.md#configuration).

</details>

## Install from source and development checks

From a checkout of this repository, install the script directly:

```bash
sudo install -m 0755 realmctl /usr/local/bin/realmctl
```

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
REALM_TEST_BINARY="$PWD/target/debug/realm" python3 -B -m unittest discover -s tests -p 'test_realmctl_relay.py'
```

The integration test uses loopback ports. Build and release workflows run it against both full and slim binaries.

</details>
