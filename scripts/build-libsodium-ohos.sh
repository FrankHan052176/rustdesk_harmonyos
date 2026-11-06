#!/usr/bin/env bash
# The fork's libsodium-sys deliberately has no bundled C sources.
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
build_host=0
case "${1:-}" in
  "") ;;
  --host) build_host=1 ;;
  *) echo "Unsupported libsodium build option: $1" >&2; exit 2 ;;
esac
if [[ -z "${OHOS_NDK_HOME:-}" ]] || ! declare -F ohos_build_jobs >/dev/null; then
  source "$repo_root/scripts/ohos-env.sh"
fi
prefix="${OHOS_LIBSODIUM_ROOT:-$OHOS_VCPKG_PREFIX}"
label=OHOS
if [[ "$build_host" == 1 ]]; then
  prefix="$repo_root/target/ohos-port-build/host-libsodium"
  label=host
fi
if [[ -s "$prefix/lib/libsodium.a" && -s "$prefix/include/sodium.h" ]]; then
  echo "Using cached $label libsodium: $prefix"
  exit 0
fi
version=1.0.20
sha256=ebb65ef6ca439333c2bb41a0c1990587288da07f6c7fd07cb3a18cc18d30ce19
archive="$repo_root/target/libsodium-$version.tar.gz"
work="$repo_root/target/ohos-libsodium-$version"
build_dir="$work/build"
if [[ "$build_host" == 1 ]]; then build_dir="$work/host-build"; fi
mkdir -p "$work/source" "$build_dir" "$prefix"
if [[ ! -f "$archive" ]]; then
  curl --fail --location "https://download.libsodium.org/libsodium/releases/libsodium-$version.tar.gz" \
    --output "$archive"
fi
python3 - "$archive" "$sha256" <<'PY'
import hashlib
from pathlib import Path
import sys
if hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest() != sys.argv[2]:
    raise SystemExit('libsodium archive checksum mismatch')
PY
if [[ ! -x "$work/source/configure" ]]; then
  tar -xzf "$archive" --strip-components=1 -C "$work/source"
fi
if [[ "$build_host" == 1 ]]; then
  configure_host="$(rustc -vV | sed -n 's/^host: //p')"
else
  # This release's config.sub predates OHOS; use its musl host alias only for
  # Autoconf checks. CC/CFLAGS still select the real OHOS target and SDK sysroot.
  configure_host=aarch64-linux-musl
  export CC="$CC_aarch64_unknown_linux_ohos"
  export AR="$AR_aarch64_unknown_linux_ohos"
  export RANLIB="$RANLIB_aarch64_unknown_linux_ohos"
  export CFLAGS="$CFLAGS_aarch64_unknown_linux_ohos -fPIC"
fi
(
  cd "$build_dir"
  "$work/source/configure" --host="$configure_host" --prefix="$prefix" \
    --disable-shared --enable-static --with-pic --disable-pie
  make -j"$(ohos_build_jobs)"
  make install
)
test -s "$prefix/lib/libsodium.a"
echo "Built $label libsodium: $prefix"
