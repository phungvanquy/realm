# Realm

A fast TCP and UDP relay written in Rust. This fork focuses on Ubuntu AMD64 and ARM64 and is based on [zhboner/realm](https://github.com/zhboner/realm).

![Realm](assets/realm.png)

[![ci](https://github.com/phungvanquy/realm/actions/workflows/ci.yml/badge.svg)](https://github.com/phungvanquy/realm/actions/workflows/ci.yml)
[![build](https://github.com/phungvanquy/realm/actions/workflows/build.yml/badge.svg)](https://github.com/phungvanquy/realm/actions/workflows/build.yml)
[![docker](https://github.com/phungvanquy/realm/actions/workflows/docker.yml/badge.svg)](https://github.com/phungvanquy/realm/actions/workflows/docker.yml)

[Install on Ubuntu](#install-on-ubuntu) · [First relay](#run-your-first-relay) · [Run at boot](#run-at-boot-with-systemd) · [Docker / Compose](readme.container.md) · [Build from source](#build-from-source) · [Configuration](#configuration)

## Install on Ubuntu

Use a prebuilt binary on 64-bit Ubuntu 22.04 or newer. The commands below select AMD64 or ARM64 automatically and install `realm` to `/usr/local/bin`.

### One-line install

```bash
curl -fsSL https://raw.githubusercontent.com/phungvanquy/realm/master/install.sh | bash
```

The [installer](install.sh) detects your CPU, downloads the latest public release, verifies its SHA-256 checksum, and installs the full binary. No GitHub account, token, or GitHub CLI is needed. It uses `sudo` if required to write to `/usr/local/bin`.

If `curl` is missing, install it first:

```bash
sudo apt update
sudo apt install -y curl ca-certificates
```

Check the installation:

```bash
realm --version
```

You can choose a version, install the smaller TCP/UDP build, or install for your own user without sudo:

```bash
# A specific release (with or without the v prefix)
curl -fsSL https://raw.githubusercontent.com/phungvanquy/realm/master/install.sh | bash -s -- --version v2.9.7

# Slim build, installed as realm
curl -fsSL https://raw.githubusercontent.com/phungvanquy/realm/master/install.sh | bash -s -- --slim

# Install for the current user
curl -fsSL https://raw.githubusercontent.com/phungvanquy/realm/master/install.sh | bash -s -- --install-dir "$HOME/.local/bin"
export PATH="$HOME/.local/bin:$PATH"
```

Options can be combined. For a user installation, add the `export PATH` line to `~/.bashrc` to keep it in future terminals. The systemd example below uses the default `/usr/local/bin/realm` path.

<details>
<summary>Download in your browser instead</summary>

Open the [latest release](https://github.com/phungvanquy/realm/releases/latest) and download the matching archive and its `.sha256` file. These downloads are public:

| CPU | `uname -m` | Full binary archive |
| --- | --- | --- |
| AMD64 / x86-64 | `x86_64` | `realm-x86_64-unknown-linux-gnu.tar.gz` |
| ARM64 | `aarch64` | `realm-aarch64-unknown-linux-gnu.tar.gz` |

In the download directory, verify and extract the archive. For AMD64:

```bash
sha256sum --check realm-x86_64-unknown-linux-gnu.tar.gz.sha256 &&
tar -xzf realm-x86_64-unknown-linux-gnu.tar.gz &&
sudo install -m 0755 realm /usr/local/bin/realm
realm --version
```

For ARM64, replace `x86_64` with `aarch64`. The `realm-slim-*` archives contain a smaller `realm-slim` binary for basic TCP/UDP forwarding; install that file as `realm` with `sudo install -m 0755 realm-slim /usr/local/bin/realm`.

</details>

### Prefer Docker?

With [Docker installed](https://docs.docker.com/engine/install/ubuntu/), replace `192.168.1.100:443` with your destination and run:

```bash
sudo docker run -d --name realm --restart unless-stopped \
  -p 5000:5000/tcp \
  ghcr.io/phungvanquy/realm:master \
  -l 0.0.0.0:5000 -r 192.168.1.100:443
```

Docker selects AMD64 or ARM64 automatically. See the [container guide](readme.container.md) for logs, UDP, configuration files, Compose, and updates.

## Run your first relay

Replace `192.168.1.100:443` with the address and port of the service you want to reach:

```bash
realm -l 0.0.0.0:5000 -r 192.168.1.100:443
```

Connections to port `5000` on this machine are forwarded to that destination. This command runs in the foreground; press **Ctrl+C** to stop it. Use `127.0.0.1:5000` to accept connections only from this machine. For remote clients, allow the listening port in your host and cloud firewall rules.

TCP is enabled by default. Add `-u` to forward UDP as well, or `-u -t` for UDP only.

For a persistent configuration, create `/etc/realm/config.toml`:

```bash
sudo install -d -m 0755 /etc/realm
sudo tee /etc/realm/config.toml >/dev/null <<'EOF'
[log]
level = "info"
output = "stdout"

[network]
use_udp = false

[[endpoints]]
listen = "0.0.0.0:5000"
remote = "192.168.1.100:443"
EOF
```

Edit the destination before starting. Set `use_udp = true` if you also need UDP:

```bash
sudo nano /etc/realm/config.toml
realm -c /etc/realm/config.toml
```

Check that the relay starts, then press **Ctrl+C** before setting up the service below so port `5000` is free.

## Run at boot with systemd

After installing the binary and creating the configuration above, create a service:

```bash
sudo tee /etc/systemd/system/realm.service >/dev/null <<'EOF'
[Unit]
Description=Realm TCP and UDP relay
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
DynamicUser=yes
ExecStart=/usr/local/bin/realm -c /etc/realm/config.toml
Restart=on-failure
RestartSec=3
LimitNOFILE=1048576
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now realm
sudo systemctl status realm --no-pager
```

The service runs as a temporary unprivileged user and can also listen on ports below `1024`. Logs go to the journal:

```bash
sudo journalctl -u realm -n 50 --no-pager
sudo journalctl -u realm -f
```

After editing `/etc/realm/config.toml`, apply the changes with `sudo systemctl restart realm`.

### Update or remove

To update, repeat the [one-line install](#one-line-install), including any options such as `--slim` or `--install-dir` used for your installation. The installer verifies the new binary before replacing the old one. Your configuration stays in `/etc/realm/config.toml`. Restart a running systemd service to use the new binary:

```bash
sudo systemctl restart realm
```

To remove a systemd installation:

```bash
sudo systemctl disable --now realm
sudo rm -f /etc/systemd/system/realm.service /usr/local/bin/realm
sudo systemctl daemon-reload
```

This keeps `/etc/realm/config.toml` for reuse. For a standalone binary installation, just remove `/usr/local/bin/realm` after stopping the process.

### Troubleshooting

| Symptom | What to check |
| --- | --- |
| `realm: command not found` | Run `/usr/local/bin/realm --version` and ensure `/usr/local/bin` is in your `PATH`. |
| `Exec format error` | Use the installer to select the binary matching `uname -m`; 32-bit ARM builds are not provided by this fork. |
| Release download fails | Check access to GitHub and confirm the requested version exists in [Releases](https://github.com/phungvanquy/realm/releases). |
| Checksum verification fails | Retry the download. The installer leaves the existing binary intact on a verification failure. |
| `Address already in use` | Stop the foreground relay before starting systemd, or choose a free listening port. |
| Service does not start | Run `sudo journalctl -u realm -n 50 --no-pager`; check the binary path and configuration. |
| Remote clients cannot connect | Check the listening address, TCP/UDP selection, firewall rules, and reachability of the destination from the relay host. |

## Build from source

Install build tools on Ubuntu:

```bash
sudo apt update
sudo apt install -y build-essential ca-certificates cmake curl git perl pkg-config
```

Install Rust using [rustup](https://rustup.rs/) if it is not already installed:

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source "$HOME/.cargo/env"
```

Clone, build, and install. The repository's `rust-toolchain.toml` selects Rust nightly automatically:

```bash
git clone https://github.com/phungvanquy/realm.git &&
cd realm &&
cargo build --locked --release &&
sudo install -m 0755 target/release/realm /usr/local/bin/realm &&
realm --version
```

The built binary is also available in `target/release/realm`. Continue with [your first relay](#run-your-first-relay) or [systemd setup](#run-at-boot-with-systemd).

For a build optimized for the current machine's CPU:

```bash
RUSTFLAGS='-C target-cpu=native' cargo build --locked --release
```

### Build Options

- ~~udp: enable udp relay~~ builtin.
- ~~tfo: enable tcp-fast-open~~ deprecated.
- ~~trust-dns: enable trust-dns's async dns resolver~~ builtin.
- ~~zero-copy: enable zero-copy on linux~~ builtin.
- brutal-shutdown: see [realm_io/brutal-shutdown](realm_io/README.md#about-brutal-shutdown).
- hook: see [realm_hook](realm_hook/README.md).
- proxy: enable proxy-protocol.
- balance: enable load balance.
- transport: enable ws/tls/wss.
- transport-tls-ring: use [ring](https://github.com/briansmith/ring) as rustls backend.
- transport-tls-awslc: use [aws-lc](https://github.com/aws/aws-lc-rs) as rustls backend.
- batched-udp: enable more efficient udp on linux.
- multi-thread: enable tokio's multi-threaded IO scheduler.
- mi-malloc: custom memory allocator.
- jemalloc: custom memory allocator.
- page-alloc: custom memory allocator.

Default: proxy + balance + transport + transport-tls-awslc + batched-udp + brutal-shutdown + multi-thread.

Default-Slim: batched-udp + brutal-shutdown + multi-thread.

See also: [Cargo.toml](Cargo.toml).

Examples:

```shell
# simple tcp
cargo build --release --no-default-features

# enable other options
cargo build --release --features 'jemalloc'

# fully customized
cargo build --release \
    --no-default-features \
    --features 'transport, multi-thread, jemalloc'

# (since v2.9) use ring as rustls backend
cargo build --release \
    --no-default-features \
    --features 'multi-thread, brutal-shutdown' \
    --features 'proxy, balance, batched-udp' \
    --features 'transport, transport-tls-ring'
# equals
cargo build --release --no-default-features --features default-ring
```

### Cross Compile

Please refer to [https://rust-lang.github.io/rustup/cross-compilation.html](https://rust-lang.github.io/rustup/cross-compilation.html). You may need to install cross-compilers or other SDKs, and specify them when building the project.

Or have a look at [Cross](https://github.com/cross-rs/cross), it makes things easier.

## Usage

```shell
A high efficiency relay tool

Usage: realm [FLAGS] [OPTIONS]

Commands:
  convert  convert your legacy configuration into an advanced one

FLAGS:
  -h, --help     show help
  -v, --version  show version
  -d, --daemon   run as a unix daemon
  -u, --udp      force enable udp forward
  -m, --mtcp     force enable mptcp protocol
  -t, --ntcp     force disable tcp forward
  -6, --ipv6     force disable ipv6 mapped ipv4
  -f, --tfo      force enable tcp fast open -- deprecated
  -z, --splice   force enable tcp zero copy -- deprecated

OPTIONS:
  -c, --config <path>               use config file
  -l, --listen <address>            listen address
  -r, --remote <address>            remote address
  -x, --through <address>           send through ip or address
  -i, --interface <device>          send through interface
  -e, --listen-interface <device>   listen interface
  -a, --listen-transport <options>  listen transport
  -b, --remote-transport <options>  remote transport

SYS OPTIONS:
  -n, --nofile <limit>        set nofile limit
  -p, --pipe-page <number>    set pipe capacity
  -j, --pre-conn-hook <path>  set pre-connect hook

LOG OPTIONS:
      --log-level <level>  override log level
      --log-output <path>  override log output

DNS OPTIONS:
      --dns-mode <mode>          override dns mode
      --dns-min-ttl <second>     override dns min ttl
      --dns-max-ttl <second>     override dns max ttl
      --dns-cache-size <number>  override dns cache size
      --dns-protocol <protocol>  override dns protocol
      --dns-servers <servers>    override dns servers

PROXY OPTIONS:
      --send-proxy <send_proxy>        send proxy protocol header
      --send-proxy-version <version>   send proxy protocol version
      --accept-proxy <accept_proxy>    accept proxy protocol header
      --accept-proxy-timeout <second>  accept proxy protocol timeout

TIMEOUT OPTIONS:
      --tcp-timeout <second>         override tcp timeout(5s)
      --udp-timeout <second>         override udp timeout(30s)
      --tcp-keepalive <second>       override default tcp keepalive interval(15s)
      --tcp-keepalive-probe <count>  override default tcp keepalive count(3)
```

Start from command line arguments:

```shell
realm -l 0.0.0.0:5000 -r 1.1.1.1:443
```

Start with config file:

```shell
# use toml
realm -c config.toml

# use json
realm -c config.json

# use configs in folder (since v2.6.2)
# all toml and json files are recursively included (e.g.: config/log.toml, config/node/n1.toml)
# hidden ones are recursively excluded (e.g.: config/.hidden_file, config/.hidden_dir/)
realm -c config/
```

Start with environment variables:

```shell
REALM_CONF='{"endpoints":[{"local":"127.0.0.1:5000","remote":"1.1.1.1:443"}]}' realm

# or
export REALM_CONF=`cat config.json | jq -c `
realm
```

Convert a legacy config file:

```shell
realm convert old.json
```

## Configuration

TOML Example

```toml
[log]
level = "warn"
output = "realm.log"

[network]
no_tcp = false
use_udp = true

[[endpoints]]
listen = "0.0.0.0:5000"
remote = "1.1.1.1:443"

[[endpoints]]
listen = "0.0.0.0:10000"
remote = "www.google.com:443"

```

<details>
<summary>JSON Example</summary>
<p>

```json
{
  "log": {
    "level": "warn",
    "output": "realm.log"
  },
  "network": {
    "no_tcp": false,
    "use_udp": true
  },
  "endpoints": [
    {
      "listen": "0.0.0.0:5000",
      "remote": "1.1.1.1:443"
    },
    {
      "listen": "0.0.0.0:10000",
      "remote": "www.google.com:443"
    }
  ]
}

```

</p>
</details>

[See more examples here](./examples).

## Overview

```shell
├── log
│   ├── level
│   └── output
├── dns
│   ├── mode
│   ├── protocol
│   ├── nameservers
│   ├── min_ttl
│   ├── max_ttl
│   └── cache_size
├── network
│   ├── no_tcp
│   ├── use_udp
│   ├── ipv6_only
│   ├── tcp_timeout
│   ├── udp_timeout
│   ├── tcp_keepalive
│   ├── tcp_keepalive_probe
│   ├── send_mptcp
│   ├── accept_mptcp
│   ├── send_proxy
│   ├── send_proxy_version
│   ├── accept_proxy
│   └── accept_proxy_timeout
└── endpoints
    ├── listen
    ├── remote
    ├── extra_remotes
    ├── balance
    ├── through
    ├── interface
    ├── listen_interface
    ├── listen_transport
    ├── remote_transport
    └── network->
```

You should provide at least [endpoint.listen](#endpointlisten-string) and [endpoint.remote](#endpointremote-string), the left fields will take their default values.

Option priority: cmd override > endpoint config > global config.

### endpoint

#### endpoint.listen: string

Local address, supported formats:

- ipv4:port
- ipv6:port

#### endpoint.remote: string

Remote address, supported formats:

- ipv4:port
- ipv6:port
- example.com:port

#### endpoint.extra_remotes: string array

Extra remote address, same as endpoint.remote above.

#### endpoint.balance: string

Require `balance` feature.

Load balance strategy and weights of remote peers.

Format:

```bash
$strategy: $weight1, $weight2, ...
```

Where `remote` is used as default backend server, and `extra_remotes` are used as backups.

Available algorithms (provided by [realm_lb](./realm_lb/)):

- iphash

- roundrobin

Example:

```toml
[[endpoints]]
remote = "a:443"
extra_remotes = ["b:443", "c:443"]
balance = "roundrobin: 4, 2, 1"
```

The weight of [a, b, c] is [4, 2, 1] in turn.

#### endpoint.through: string

TCP: Bind a specific `ip` before opening a connection.

UDP: Bind a specific `ip` or `address` before sending packet.

Supported formats:

- ipv4/ipv6 (tcp/udp)
- ipv4/ipv6:port (udp)

#### endpoint.interface: string

Bind to a specific interface for outgoing traffics.

#### endpoint.listen_interface: string

Bind to a specific interface for incoming traffics.

#### endpoint.listen_transport: string

Require `transport` feature.

See [Kaminari Options](https://github.com/zephyrchien/kaminari#options).

#### endpoint.remote_transport: string

Require `transport` feature.

See [Kaminari Options](https://github.com/zephyrchien/kaminari#options).

#### endpoint.network

The same as [network](#network), override global options.

### log

#### log.level: string

values:

- off
- error
- warn
- info
- debug
- trace

default: off

#### log.output: string

values:

- stdout
- stderr
- path (e.g. `/var/log/realm.log`)

default: stdout

### dns

Require `trust-dns` feature.

#### dns.mode: string

Dns resolve strategy.

values:

- ipv4_only
- ipv6_only
- ipv4_then_ipv6
- ipv6_then_ipv4
- ipv4_and_ipv6

default: ipv4_and_ipv6

#### dns.protocol: string

Dns transport protocol.

values:

- tcp
- udp
- tcp_and_udp

default: tcp_and_udp

#### dns.nameservers: string array

Custom upstream servers.

format: ["server1", "server2" ...]

default:

If on **unix/windows**, read from the default location.(e.g. `/etc/resolv.conf`).

Otherwise, use google's public dns(`8.8.8.8:53`, `8.8.4.4:53` and `2001:4860:4860::8888:53`, `2001:4860:4860::8844:53`).

#### dns.min_ttl: unsigned int

The minimum lifetime of a positive dns cache.

default: 0

#### dns.max_ttl: unsigned int

The maximum lifetime of a positive dns cache.

default: 86400 (1 day)

#### dns.cache_size: unsigned int

The maximum count of dns cache.

default: 32

### network

#### network.no_tcp: bool

Do not start a tcp relay.

default: false

#### network.use_udp: bool

~~Require `udp` feature~~

Start listening on a udp endpoint and forward packets to the remote peer.

It will dynamically allocate local endpoints and establish udp associations. Once timeout, the endpoints will be deallocated and the association will be terminated. See also: [network.udp_timeout](#networkudp_timeout-unsigned-int).

Due to the receiver side not limiting access to the association, the relay works like a full-cone NAT.

default: false

#### network.ipv6_only: bool

Disable ipv4-mapped-ipv6 when binding to an ipv6 address.

E.g.:
`[::0]:port` with (ipv6_only=false) binds to `*:port`

`[::0]:port` with (ipv6_only=true) binds to `[::]:port`

default: false

#### ~~network.zero_copy: bool~~ deprecated

~~Require `zero-copy` feature.~~

~~Use `splice` instead of `send/recv` while handing tcp connection. This will save a lot of memory copies and context switches.~~

~~default: false~~

#### ~~network.fast_open: bool~~ deprecated

~~Require `fast-open` feature.~~

~~It is not recommended to enable this option, see [The Sad Story of TCP Fast Open](https://squeeze.isobar.com/2019/04/11/the-sad-story-of-tcp-fast-open/).~~

~~default: false~~

#### network.tcp_timeout: unsigned int

This is **connect** timeout. An attempt to connect to a remote peer fails after waiting for a period of time.

To disable timeout, you need to explicitly set timeout value to 0.

default: 5

#### network.udp_timeout: unsigned int

Terminate udp association after `timeout`.

The timeout value must be properly configured in case of memory leak. Do not use a large `timeout`!

default: 30

#### network.tcp_keepalive: unsigned int

TCP Keepalive interval.

On Linux, this is equivalent to setting both `net.ipv4.tcp_keepalive_time` and `net.ipv4.tcp_keepalive_intvl`.

To use system's tcp keepalive interval, you need to explicitly set this option to 0.

default: 15

#### network.tcp_keepalive_probe: unsigned int

TCP Keepalive retries.

On Linux, this is equivalent to `ipv4.tcp_keepalive_probes`.

default: 3

#### network.send_mptcp: bool

Enable MPTCP outbound connections on Linux.

Requires a higher kernel version(>5.6) with `net.mptcp.enabled=1`.

See also [Path Manager](https://www.mptcp.dev/pm.html) guidelines.

default: false

#### network.accept_mptcp: bool

Enable MPTCP inbound connections on Linux.

default: false

#### network.send_proxy: bool

Require `proxy` feature.

Send haproxy PROXY header once the connection established. Both `v1` and `v2` are supported, see [send_proxy_version](#networksend_proxy_version-unsigned-int).

You should make sure the remote peer also speaks proxy-protocol.

default: false

#### network.send_proxy_version: unsigned int

Require `proxy` feature.

This option has no effect unless [send_proxy](#networksend_proxy-bool) is enabled.

value:

- 1
- 2

default: 2

#### network.accept_proxy: bool

Require `proxy` feature.

Wait for a PROXY header once the connection established.

If the remote sender does not send a `v1` or `v2` header before other contents, the connection will be closed.

default: false

#### network.accept_timeout: unsigned int

Require `proxy` feature.

Wait for a PROXY header within a period of time, otherwise close the connection.

default: 5.

## Libraries

| Library | Documentation |
| --- | --- |
| realm-core | [crates.io](https://crates.io/crates/realm_core) · [API docs](https://docs.rs/realm_core) |
| realm-io | [crates.io](https://crates.io/crates/realm_io) · [API docs](https://docs.rs/realm_io) |
| realm-lb | [crates.io](https://crates.io/crates/realm_lb) · [API docs](https://docs.rs/realm_lb) |
| realm-hook | [crates.io](https://crates.io/crates/realm_hook) · [API docs](https://docs.rs/realm_hook) |
| realm-syscall | [crates.io](https://crates.io/crates/realm_syscall) · [API docs](https://docs.rs/realm_syscall) |
