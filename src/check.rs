//! Read-only configuration, systemd command, and Linux listener checks.
//!
//! No shell parsing is used for systemd's command arguments: busctl supplies
//! typed JSON. Socket ownership is checked against the selected process's FDs,
//! so a port held by another application cannot satisfy readiness.

use std::collections::HashSet;
use std::fs;
use std::io::{Error, Result};
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr, SocketAddr};
use std::process::Command as Process;

use clap::{Arg, ArgAction, ArgMatches, Command};
use serde_json::Value;

use crate::conf::{Config, FullConf};

pub fn command() -> Command {
    Command::new("check")
        .about("validate configuration without starting a relay")
        .version("1")
        .disable_version_flag(true)
        .disable_help_flag(true)
        .arg(Arg::new("check_version").long("version").action(ArgAction::Version))
        .arg(Arg::new("check_help").long("help").short('h').action(ArgAction::Help))
        .arg(
            Arg::new("config")
                .short('c')
                .long("config")
                .required(true)
                .value_name("PATH"),
        )
        .arg(
            Arg::new("pid")
                .long("pid")
                .value_parser(clap::value_parser!(u32).range(1..)),
        )
        .arg(Arg::new("service").long("service").requires("binary"))
        .arg(Arg::new("binary").long("binary").requires("service"))
        .arg(
            Arg::new("service_only")
                .long("service-only")
                .requires("service")
                .conflicts_with("pid")
                .action(ArgAction::SetTrue),
        )
        .arg(Arg::new("quiet").long("quiet").short('q').action(ArgAction::SetTrue))
}

pub fn run(args: &ArgMatches) -> Result<()> {
    let path = args.get_one::<String>("config").unwrap();
    if let Some(service) = args.get_one::<String>("service") {
        verify_service(service, args.get_one::<String>("binary").unwrap(), path)?;
    }
    if args.get_flag("service_only") {
        return Ok(());
    }
    let mut conf = FullConf::try_from_conf_file(path)?;
    conf.apply_global_opts();
    let hook = std::panic::take_hook();
    std::panic::set_hook(Box::new(|_| {}));
    let listeners = validate(conf);
    std::panic::set_hook(hook);
    let listeners = listeners?;
    if let Some(pid) = args.get_one::<u32>("pid") {
        verify_process(*pid, path, &listeners)?;
    }
    if !args.get_flag("quiet") {
        println!("Configuration valid: {} listener(s).", listeners.len());
    }
    Ok(())
}

#[derive(Debug, Clone, Copy, Eq, PartialEq, Hash)]
struct Listener {
    udp: bool,
    address: SocketAddr,
}

fn validate(conf: FullConf) -> Result<Vec<Listener>> {
    // The existing builders also validate transport, balancing, and DNS
    // settings. Convert their parse panics to a failing check; never open the
    // log output or bind a listening socket during preflight.
    let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| -> Result<Vec<Listener>> {
        #[cfg(feature = "transport")]
        {
            static TLS_PROVIDER: std::sync::Once = std::sync::Once::new();
            TLS_PROVIDER.call_once(crate::core::kaminari::install_tls_provider);
        }
        conf.dns.build();
        let mut listeners = Vec::new();
        let mut seen = HashSet::new();
        for endpoint in conf.endpoints {
            #[cfg(not(feature = "transport"))]
            if endpoint.listen_transport.is_some() || endpoint.remote_transport.is_some() {
                return Err(Error::other("transport settings require the full Realm build"));
            }
            #[cfg(not(feature = "balance"))]
            if endpoint.balance.is_some() || !endpoint.extra_remotes.is_empty() {
                return Err(Error::other("balancing settings require the full Realm build"));
            }
            #[cfg(not(feature = "proxy"))]
            if endpoint.network.send_proxy == Some(true) || endpoint.network.accept_proxy == Some(true) {
                return Err(Error::other("proxy protocol settings require the full Realm build"));
            }
            if endpoint.network.send_proxy_version.is_some_and(|v| v != 1 && v != 2) {
                return Err(Error::other("send_proxy_version must be 1 or 2"));
            }
            let info = endpoint.build();
            let address = info.endpoint.laddr;
            if address.port() == 0 {
                return Err(Error::other("listener port must be between 1 and 65535"));
            }
            if info.no_tcp && !info.use_udp {
                return Err(Error::other(format!("{} disables both TCP and UDP", address)));
            }
            for udp in [false, true] {
                if (udp && !info.use_udp) || (!udp && info.no_tcp) {
                    continue;
                }
                let listener = Listener { udp, address };
                if !seen.insert(listener) {
                    return Err(Error::other(format!(
                        "duplicate {} listener: {}",
                        if udp { "UDP" } else { "TCP" },
                        address
                    )));
                }
                listeners.push(listener);
            }
        }
        Ok(listeners)
    }));
    outcome.unwrap_or_else(|panic| {
        let message = panic
            .downcast_ref::<String>()
            .map(String::as_str)
            .or_else(|| panic.downcast_ref::<&str>().copied())
            .unwrap_or("invalid configuration");
        Err(Error::other(message))
    })
}

fn bus(args: &[&str]) -> Result<Value> {
    let output = Process::new("busctl")
        .args(["--system", "--json=short", "--timeout=5s"])
        .args(args)
        .output()
        .map_err(|e| Error::other(format!("cannot run busctl: {}", e)))?;
    if !output.status.success() {
        return Err(Error::other(format!(
            "cannot inspect systemd: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        )));
    }
    let json: Value = serde_json::from_slice(&output.stdout).map_err(Error::other)?;
    json.get("data")
        .cloned()
        .ok_or_else(|| Error::other("busctl returned no data"))
}

fn property(path: &str, name: &str) -> Result<Value> {
    bus(&[
        "get-property",
        "org.freedesktop.systemd1",
        path,
        "org.freedesktop.systemd1.Service",
        name,
    ])
}

fn verify_service(service: &str, binary: &str, config: &str) -> Result<()> {
    let object = bus(&[
        "call",
        "org.freedesktop.systemd1",
        "/org/freedesktop/systemd1",
        "org.freedesktop.systemd1.Manager",
        "LoadUnit",
        "s",
        service,
    ])?;
    let object = object
        .get(0)
        .and_then(Value::as_str)
        .ok_or_else(|| Error::other("systemd returned no unit path"))?;
    let start = property(object, "ExecStartEx")?;
    let kind = property(object, "Type")?;
    let unset = property(object, "UnsetEnvironment")?;
    verify_service_values(&start, &kind, &unset, binary, config)
}

fn verify_service_values(start: &Value, kind: &Value, unset: &Value, binary: &str, config: &str) -> Result<()> {
    let commands = start
        .as_array()
        .ok_or_else(|| Error::other("invalid ExecStartEx property"))?;
    let expected = serde_json::json!([binary, "-c", config]);
    let matches = commands.len() == 1
        && commands[0].get(0).and_then(Value::as_str) == Some(binary)
        && commands[0].get(1) == Some(&expected)
        && commands[0]
            .get(2)
            .and_then(Value::as_array)
            .is_some_and(|flags| flags.len() == 1 && flags[0].as_str() == Some("no-env-expand"));
    if !matches
        || kind.as_str() != Some("exec")
        || !unset
            .as_array()
            .is_some_and(|values| values.iter().any(|v| v.as_str() == Some("REALM_CONF")))
    {
        return Err(Error::other(
            "effective service command differs from realmctl setup; inspect service show for conflicting overrides",
        ));
    }
    Ok(())
}

fn process_start(pid: u32) -> Result<String> {
    let stat = fs::read_to_string(format!("/proc/{}/stat", pid))?;
    stat.rsplit_once(')')
        .and_then(|(_, fields)| fields.split_whitespace().nth(19))
        .map(str::to_owned)
        .ok_or_else(|| Error::other("cannot read process start time"))
}

#[cfg(target_os = "linux")]
fn verify_process(pid: u32, config: &str, listeners: &[Listener]) -> Result<()> {
    let started = process_start(pid)?;
    let root = format!("/proc/{}", pid);
    let cmdline = fs::read(format!("{}/cmdline", root))?;
    let args: Vec<&[u8]> = cmdline.split(|b| *b == 0).filter(|v| !v.is_empty()).collect();
    if args.len() != 3 || args[1] != b"-c" || args[2] != config.as_bytes() {
        return Err(Error::other(
            "running process does not use the selected configuration directory",
        ));
    }
    let mut sockets = HashSet::new();
    for fd in fs::read_dir(format!("{}/fd", root))? {
        match fs::read_link(fd?.path()) {
            Ok(link) => {
                if let Some(inode) = link
                    .to_str()
                    .and_then(|s| s.strip_prefix("socket:["))
                    .and_then(|s| s.strip_suffix(']'))
                {
                    sockets.insert(inode.to_owned());
                }
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {} // An unrelated FD closed during the scan.
            Err(e) => return Err(e),
        }
    }
    let mut actual = HashSet::new();
    for (file, udp, ipv6) in [
        ("tcp", false, false),
        ("tcp6", false, true),
        ("udp", true, false),
        ("udp6", true, true),
    ] {
        if !listeners.iter().any(|l| l.udp == udp && l.address.is_ipv6() == ipv6) {
            continue;
        }
        let table = fs::read_to_string(format!("{}/net/{}", root, file))?;
        for line in table.lines().skip(1) {
            let fields: Vec<_> = line.split_whitespace().collect();
            if fields.len() < 10 {
                return Err(Error::other("invalid kernel socket table"));
            }
            if fields[3] != if udp { "07" } else { "0A" } || !sockets.contains(fields[9]) {
                continue;
            }
            actual.insert(Listener {
                udp,
                address: parse_address(fields[1])?,
            });
        }
    }
    for listener in listeners {
        let address = SocketAddr::new(listener.address.ip(), listener.address.port());
        if !actual.contains(&Listener { address, ..*listener }) {
            return Err(Error::other(format!(
                "PID {} has no {} listener on {}",
                pid,
                if listener.udp { "UDP" } else { "TCP" },
                address
            )));
        }
    }
    if process_start(pid)? != started {
        return Err(Error::other("service process changed during the readiness check"));
    }
    Ok(())
}

#[cfg(not(target_os = "linux"))]
fn verify_process(_pid: u32, _config: &str, _listeners: &[Listener]) -> Result<()> {
    Err(Error::other("process listener checks require Linux"))
}

fn parse_address(value: &str) -> Result<SocketAddr> {
    let (address, port) = value
        .split_once(':')
        .ok_or_else(|| Error::other("invalid socket address"))?;
    let port = u16::from_str_radix(port, 16).map_err(Error::other)?;
    let mut bytes = Vec::new();
    if address.len() != 8 && address.len() != 32 {
        return Err(Error::other("invalid socket address length"));
    }
    for offset in (0..address.len()).step_by(8) {
        bytes.extend(
            u32::from_str_radix(&address[offset..offset + 8], 16)
                .map_err(Error::other)?
                .to_ne_bytes(),
        );
    }
    let ip = if bytes.len() == 4 {
        IpAddr::V4(Ipv4Addr::from(<[u8; 4]>::try_from(bytes).unwrap()))
    } else {
        IpAddr::V6(Ipv6Addr::from(<[u8; 16]>::try_from(bytes).unwrap()))
    };
    Ok(SocketAddr::new(ip, port))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn effective_service_uses_typed_arguments_and_rejects_later_overrides() {
        let binary = "/opt/realm % $ with spaces";
        let config = "/etc/rules \"quoted\"\\";
        let start = serde_json::json!([[binary, [binary, "-c", config], ["no-env-expand"]]]);
        let kind = serde_json::json!("exec");
        let unset = serde_json::json!(["REALM_CONF"]);
        assert!(verify_service_values(&start, &kind, &unset, binary, config).is_ok());
        assert!(verify_service_values(&start, &kind, &unset, binary, "/old/config.toml").is_err());
        assert!(verify_service_values(&start, &kind, &serde_json::json!([]), binary, config).is_err());
    }

    #[test]
    fn invalid_semantics_and_duplicate_listeners_are_rejected() {
        for config in [
            "[[endpoints]]\nlisten='127.0.0.1:0'\nremote='127.0.0.1:80'",
            "[[endpoints]]\nlisten='127.0.0.1:8000'\nremote='invalid'",
            "[[endpoints]]\nlisten='127.0.0.1:8000'\nremote='127.0.0.1:80'\n[endpoints.network]\nno_tcp=true\nuse_udp=false",
            "[[endpoints]]\nlisten='127.0.0.1:8000'\nremote='127.0.0.1:80'\n[[endpoints]]\nlisten='127.0.0.1:8000'\nremote='127.0.0.1:81'",
        ] {
            let mut conf = FullConf::from_conf_str(config).unwrap();
            conf.apply_global_opts();
            assert!(validate(conf).is_err());
        }
        let config = "[[endpoints]]\nlisten='127.0.0.1:8000'\nremote='127.0.0.1:80'";
        assert_eq!(validate(FullConf::from_conf_str(config).unwrap()).unwrap().len(), 1);
    }

    #[test]
    #[cfg(target_endian = "little")]
    fn kernel_ipv4_and_ipv6_addresses_are_decoded() {
        assert_eq!(
            parse_address("0100007F:1F90").unwrap(),
            "127.0.0.1:8080".parse().unwrap()
        );
        assert_eq!(
            parse_address("00000000000000000000000001000000:0035").unwrap(),
            "[::1]:53".parse().unwrap()
        );
        assert!(parse_address("BAD:0035").is_err());
    }
}
