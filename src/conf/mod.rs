use std::fs;
use std::io::{Result, Error};

use walkdir::WalkDir;
use clap::ArgMatches;
use serde::{Serialize, Deserialize};

mod log;
pub use self::log::{LogLevel, LogConf};

mod dns;
pub use dns::{DnsMode, DnsProtocol, DnsConf};

mod net;
pub use net::{NetConf, NetInfo};

mod endpoint;
pub use endpoint::{EndpointConf, EndpointInfo};

mod legacy;
pub use legacy::LegacyConf;

#[allow(clippy::too_long_first_doc_paragraph)]
/// Conig Architecture
/// cmd | file => LogConf => { level, output }
/// cmd | file => DnsConf => { resolve cinfig, opts }
/// cmd | file => NetConf
///                      \
/// cmd | file => EndpointConf => { [local, remote, conn_opts] }
pub trait Config {
    type Output;

    fn is_empty(&self) -> bool;

    fn build(self) -> Self::Output;

    // override self if other not empty
    // e.g.: cmd argument overrides global and local option
    fn rst_field(&mut self, other: &Self) -> &mut Self;

    // take other only if self empty & other not empty
    // e.g.: local field takes global option
    fn take_field(&mut self, other: &Self) -> &mut Self;

    fn from_cmd_args(matches: &ArgMatches) -> Self;
}

#[derive(Debug, Default)]
pub struct CmdOverride {
    pub log: LogConf,
    pub dns: DnsConf,
    pub network: NetConf,
}

#[derive(Debug, Default, Serialize, Deserialize)]
pub struct FullConf {
    #[serde(default)]
    #[serde(skip_serializing_if = "Config::is_empty")]
    pub log: LogConf,

    #[serde(default)]
    #[serde(skip_serializing_if = "Config::is_empty")]
    pub dns: DnsConf,

    #[serde(default)]
    #[serde(skip_serializing_if = "Config::is_empty")]
    pub network: NetConf,

    pub endpoints: Vec<EndpointConf>,
}

impl FullConf {
    #[allow(unused)]
    pub fn new(log: LogConf, dns: DnsConf, network: NetConf, endpoints: Vec<EndpointConf>) -> Self {
        FullConf {
            log,
            dns,
            network,
            endpoints,
        }
    }

    pub fn from_conf_file(file: &str) -> Self {
        Self::try_from_conf_file(file).unwrap_or_else(|e| panic!("failed to load {}: {}", file, e))
    }

    /// Load configuration without starting listeners or hiding traversal errors.
    /// The check command and normal startup must use exactly the same loader.
    pub fn try_from_conf_file(file: &str) -> Result<Self> {
        let mtd = fs::metadata(file)?;

        if mtd.is_file() {
            return Self::from_conf_str(&fs::read_to_string(file)?);
        }
        if !mtd.is_dir() {
            return Err(Error::other("configuration must be a regular file or directory"));
        }
        // Resolve relative roots such as "." before WalkDir's hidden-entry
        // filter; otherwise the entire directory can be silently skipped.
        let directory = fs::canonicalize(file)?;
        if [std::path::Path::new(file), directory.as_path()].iter().any(|path| {
            path.file_name()
                .is_some_and(|name| name.to_string_lossy().starts_with('.'))
        }) {
            return Err(Error::other("configuration directory name must not start with '.'"));
        }

        let mut full_conf = FullConf::default();
        for entry in WalkDir::new(directory)
            .follow_links(true)
            .sort_by_file_name()
            .into_iter()
            .filter_entry(|e| e.file_name().to_str().is_some_and(|x| !x.starts_with('.')))
        {
            let entry = entry.map_err(Error::other)?;
            if !entry.file_type().is_file() || !entry.path().extension().is_some_and(|s| s == "toml" || s == "json") {
                continue;
            }
            let conf_part = fs::read_to_string(entry.path())?;
            let conf_part = Self::from_conf_str(&conf_part)
                .map_err(|e| Error::other(format!("{}: {}", entry.path().display(), e)))?;
            full_conf.take_fields(conf_part);
        }
        Ok(full_conf)
    }

    pub fn from_conf_str(s: &str) -> Result<Self> {
        let toml_err = match toml::from_str(s) {
            Ok(x) => return Ok(x),
            Err(e) => e,
        };

        let json_err = match serde_json::from_str(s) {
            Ok(x) => return Ok(x),
            Err(e) => e,
        };

        // to be compatible with old version
        let legacy_err = match serde_json::from_str::<LegacyConf>(s) {
            Ok(x) => {
                eprintln!("attention: you are using a legacy config file!");
                return Ok(x.into());
            }
            Err(e) => e,
        };

        Err(Error::other(format!(
            "parse as toml: {0}; parse as json: {1}; parse as legacy: {2}",
            toml_err, json_err, legacy_err
        )))
    }

    fn take_fields(&mut self, other: Self) {
        self.log.take_field(&other.log);
        self.dns.take_field(&other.dns);
        self.network.take_field(&other.network);
        self.endpoints.extend(other.endpoints);
    }

    pub fn add_endpoint(&mut self, endpoint: EndpointConf) -> &mut Self {
        self.endpoints.push(endpoint);
        self
    }

    // override
    pub fn apply_cmd_opts(&mut self, opts: CmdOverride) -> &mut Self {
        let CmdOverride {
            ref log,
            ref dns,
            ref network,
        } = opts;

        self.log.rst_field(log);
        self.dns.rst_field(dns);
        self.endpoints.iter_mut().for_each(|x| {
            x.network.rst_field(network);
        });

        self
    }

    // take inner global opts
    pub fn apply_global_opts(&mut self) -> &mut Self {
        self.endpoints.iter_mut().for_each(|x| {
            x.network.take_field(&self.network);
        });

        self
    }
}

#[macro_export]
macro_rules! rst {
    ($this: ident, $field: ident, $other: ident) => {
        let Self { $field, .. } = $other;
        if $field.is_some() {
            $this.$field = $field;
        }
    };
}

#[macro_export]
macro_rules! take {
    ($this: ident, $field: ident, $other: ident) => {
        let Self { $field, .. } = $other;
        if $this.$field.is_none() && $field.is_some() {
            $this.$field = $field;
        }
    };
}

#[macro_export]
macro_rules! empty {
    ( $this: expr => $( $field: ident ),* ) => {{
        let mut res = true;
        $(
            res = res && $this.$field.is_none();
        )*
        res
    }};
    ( $( $value: expr ),* ) => {{
        let mut res = true;
        $(
            res = res && $value.is_none();
        )*
        res
    }}
}
