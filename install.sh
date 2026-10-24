#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Install Realm and its bundled realmctl utility from public phungvanquy/realm releases.

Usage: bash install.sh [options]
  --version VERSION    Install a tag such as v2.9.7 (default: latest release)
  --slim               Install the smaller TCP/UDP build
  --install-dir DIR    Installation directory (default: /usr/local/bin)
  -h, --help           Show this help

Examples:
  bash install.sh
  bash install.sh --version v2.9.7 --slim
  bash install.sh --install-dir "$HOME/.local/bin"
EOF
}

die() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

realm_version=latest
realm_binary=realm
realm_install_dir=/usr/local/bin
realm_tmp=
realm_stage=
realm_ctl_stage=
realm_has_ctl=false
realm_sudo=()

cleanup() {
  if [[ -n "$realm_stage" ]]; then
    "${realm_sudo[@]}" rm -f -- "$realm_stage" || true
  fi
  if [[ -n "$realm_ctl_stage" ]]; then
    "${realm_sudo[@]}" rm -f -- "$realm_ctl_stage" || true
  fi
  if [[ -n "$realm_tmp" ]]; then
    rm -rf -- "$realm_tmp"
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version|--install-dir)
      [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || die "$1 requires a value."
      if [[ "$1" == --version ]]; then
        realm_version="$2"
      else
        realm_install_dir="$2"
      fi
      shift 2
      ;;
    --slim) realm_binary=realm-slim; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown option: $1. Run with --help for usage." ;;
  esac
done

[[ "$realm_install_dir" == /* ]] || die "--install-dir must be an absolute path."
if [[ "$realm_version" != latest ]]; then
  realm_version="v${realm_version#v}"
  [[ "$realm_version" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$ ]] ||
    die "Use a version such as v2.9.7, or latest."
fi

[[ "$(uname -s)" == Linux ]] || die "This installer supports Linux only."
case "$(uname -m)" in
  x86_64|amd64) realm_target=x86_64-unknown-linux-gnu ;;
  aarch64|arm64) realm_target=aarch64-unknown-linux-gnu ;;
  *) die "Supported architectures: AMD64 and ARM64." ;;
esac

for realm_command in curl tar sha256sum mktemp chmod install mkdir mv rm; do
  command -v "$realm_command" >/dev/null 2>&1 || die "Required command not found: $realm_command."
done

realm_releases=https://github.com/phungvanquy/realm/releases
realm_curl=(curl --fail --silent --show-error --location --retry 3
  --connect-timeout 15 --max-time 300 --proto '=https' --proto-redir '=https')

if [[ "$realm_version" == latest ]]; then
  realm_latest_url="$("${realm_curl[@]}" --head --output /dev/null \
    --write-out '%{url_effective}' "$realm_releases/latest")" ||
    die "Could not find the latest public release. Check $realm_releases."
  case "$realm_latest_url" in
    "$realm_releases/tag/"*) realm_version="${realm_latest_url##*/}" ;;
    *) die "Unexpected release URL: $realm_latest_url" ;;
  esac
  [[ "$realm_version" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$ ]] ||
    die "Unexpected release version: $realm_version"
fi

realm_archive="$realm_binary-$realm_target.tar.gz"
realm_download="$realm_releases/download/$realm_version"
realm_tmp="$(mktemp -d)"
printf 'Downloading Realm %s (%s, %s)...\n' "$realm_version" "$realm_target" "$realm_binary"

"${realm_curl[@]}" --output "$realm_tmp/$realm_archive" "$realm_download/$realm_archive" ||
  die "Could not download $realm_archive from release $realm_version."
"${realm_curl[@]}" --output "$realm_tmp/$realm_archive.sha256" "$realm_download/$realm_archive.sha256" ||
  die "Could not download the checksum. The existing installation was not changed."

read -r realm_expected_hash realm_checksum_file < "$realm_tmp/$realm_archive.sha256" ||
  die "Invalid checksum file."
[[ "$realm_expected_hash" =~ ^[0-9a-fA-F]{64}$ && "$realm_checksum_file" == "$realm_archive" ]] ||
  die "Invalid checksum file."
(
  cd "$realm_tmp"
  printf '%s  %s\n' "$realm_expected_hash" "$realm_archive" | sha256sum --check --status
) || die "Checksum verification failed. The existing installation was not changed."

tar --extract --gzip --file "$realm_tmp/$realm_archive" --directory "$realm_tmp" \
  --no-same-owner -- "$realm_binary" || die "Could not extract $realm_archive."
[[ -f "$realm_tmp/$realm_binary" && ! -L "$realm_tmp/$realm_binary" ]] ||
  die "The archive does not contain a regular $realm_binary binary."
chmod 0755 "$realm_tmp/$realm_binary"
realm_binary_version="$("$realm_tmp/$realm_binary" --version)" ||
  die "The downloaded binary cannot run on this system. The existing installation was not changed."

# Older releases contain only the relay binary. Take the companion exclusively
# from the same verified archive when present, never from a separate moving URL.
if tar --list --gzip --file "$realm_tmp/$realm_archive" -- realmctl >/dev/null 2>&1; then
  tar --extract --gzip --file "$realm_tmp/$realm_archive" --directory "$realm_tmp" \
    --no-same-owner -- realmctl || die 'Could not extract realmctl.'
  [[ -s "$realm_tmp/realmctl" && -f "$realm_tmp/realmctl" && ! -L "$realm_tmp/realmctl" ]] ||
    die 'The archive does not contain a nonempty regular realmctl script.'
  bash -n "$realm_tmp/realmctl" || die 'The bundled realmctl script has invalid Bash syntax.'
  realm_has_ctl=true
fi

if mkdir -p -- "$realm_install_dir" 2>/dev/null && [[ -w "$realm_install_dir" ]]; then
  realm_sudo=()
elif [[ $EUID -ne 0 ]] && command -v sudo >/dev/null 2>&1; then
  realm_sudo=(sudo)
else
  die "Cannot write to $realm_install_dir. Use sudo or --install-dir \"\$HOME/.local/bin\"."
fi

"${realm_sudo[@]}" mkdir -p -- "$realm_install_dir"
realm_stage="$("${realm_sudo[@]}" mktemp "$realm_install_dir/.realm-install.XXXXXX")"
"${realm_sudo[@]}" install -m 0755 -- "$realm_tmp/$realm_binary" "$realm_stage"
if [[ "$realm_has_ctl" == true ]]; then
  realm_ctl_stage="$("${realm_sudo[@]}" mktemp "$realm_install_dir/.realmctl-install.XXXXXX")"
  "${realm_sudo[@]}" install -m 0755 -- "$realm_tmp/realmctl" "$realm_ctl_stage"
fi
"${realm_sudo[@]}" mv -fT -- "$realm_stage" "$realm_install_dir/realm"
realm_stage=
if [[ "$realm_has_ctl" == true ]]; then
  "${realm_sudo[@]}" mv -fT -- "$realm_ctl_stage" "$realm_install_dir/realmctl"
  realm_ctl_stage=
  printf 'Installed %s/realmctl\n' "$realm_install_dir"
else
  printf 'This release does not bundle realmctl; any existing copy is unchanged.\n'
fi

printf 'Installed %s/realm\n%s\n' "$realm_install_dir" "$realm_binary_version"
case ":$PATH:" in
  *":$realm_install_dir:"*) ;;
  *) printf 'Add %s to your PATH to run realm by name.\n' "$realm_install_dir" ;;
esac
printf 'If Realm is running as a service, restart it to use the installed version.\n'
