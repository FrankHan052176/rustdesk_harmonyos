#!/usr/bin/env bash
# Flutter UI + the pinned wanghd114 ohos-port core, without the legacy rd_engine.
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
export PATH="$HOME/.cargo/bin:$HOME/flutter-ohos/bin:$PATH"

source_only=0
check_only=0
for argument in "$@"; do
  case "$argument" in
    --source-only) source_only=1 ;;
    --check) check_only=1 ;;
    *) echo "Unsupported new-core build option: $argument" >&2; exit 2 ;;
  esac
done
if [[ "$source_only" == 1 ]]; then
  python3 scripts/ohos-port-core.py prepare
  exit 0
fi

# Never create local caches below an unmounted NAS or silently use a different cache.
for cache in "${CARGO_HOME:-$HOME/.cargo}/registry" "${CARGO_HOME:-$HOME/.cargo}/git" "${PUB_CACHE:-$HOME/.pub-cache}"; do
  if [[ -L "$cache" && ! -d "$cache" ]]; then
    echo "Build cache is unavailable: $cache -> $(readlink "$cache"). Mount the configured cache first." >&2
    exit 2
  fi
  cache_target="$cache"
  if [[ -L "$cache" ]]; then cache_target="$(readlink "$cache")"; fi
  if [[ "$cache" == /Volumes/nas/* || "$cache_target" == /Volumes/nas/* ]]; then
    guard="$HOME/.config/rustdesk-build-cache/guard.sh"
    if [[ ! -x "$guard" ]]; then echo "NAS cache guard is unavailable: $guard" >&2; exit 2; fi
    "$guard" --ready
  fi
done
if [[ -d "${PUB_CACHE:-$HOME/.pub-cache}" ]]; then
  export PUB_CACHE="$(cd "${PUB_CACHE:-$HOME/.pub-cache}" && pwd -P)"
fi
core_root="$(python3 scripts/ohos-port-core.py prepare)"
source scripts/ohos-env.sh
export OHOS_DEPS_DIR="${OHOS_DEPS_DIR:-$OHOS_VCPKG_PREFIX}"
export SODIUM_LIB_DIR_aarch64_unknown_linux_ohos="$OHOS_DEPS_DIR/lib"
export CARGO_TARGET_DIR="$repo_root/target/ohos-port-build/cargo"

for command_name in cargo flutter dart; do
  command -v "$command_name" >/dev/null || { echo "Missing required command: $command_name" >&2; exit 2; }
done
codegen="${FLUTTER_RUST_BRIDGE_CODEGEN:-$HOME/.cargo/bin/flutter_rust_bridge_codegen}"
if [[ ! -x "$codegen" || "$("$codegen" --version)" != 'flutter_rust_bridge_codegen 1.80.1' ]]; then
  echo 'flutter_rust_bridge_codegen 1.80.1 is required' >&2
  exit 2
fi

# These existing builders also provide the arm64-linux layout used by magnum-opus.
for dependency in libvpx libyuv libopus libsodium; do
  bash "scripts/build-$dependency-ohos.sh"
done
# build.rs uses hbb_common on the host as well as in the OHOS library.
bash scripts/build-libsodium-ohos.sh --host
host_triple="$(rustc -vV | sed -n 's/^host: //p')"
export "SODIUM_LIB_DIR_${host_triple//-/_}=$repo_root/target/ohos-port-build/host-libsodium/lib"
for dependency in vpx yuv opus sodium; do
  test -s "$OHOS_DEPS_DIR/lib/lib$dependency.a" || {
    echo "OHOS_DEPS_DIR has no lib$dependency.a: $OHOS_DEPS_DIR" >&2; exit 2;
  }
done

backup="$(mktemp -d "${TMPDIR:-/tmp}/rustdesk-ohos-port.XXXXXX")"
cp flutter/pubspec.yaml "$backup/pubspec.yaml"
if [[ -f flutter/pubspec.lock ]]; then cp flutter/pubspec.lock "$backup/pubspec.lock"; fi
cleanup() {
  cp "$backup/pubspec.yaml" "$repo_root/flutter/pubspec.yaml"
  if [[ -f "$backup/pubspec.lock" ]]; then
    cp "$backup/pubspec.lock" "$repo_root/flutter/pubspec.lock"
  else
    rm -f "$repo_root/flutter/pubspec.lock"
  fi
  rm -rf "$backup"
}
trap cleanup EXIT
python3 scripts/prepare-ohos-flutter.py
(cd flutter && flutter pub get)
# FRB 1.x scans cfg-disabled desktop modules too.
if [[ ! -f "$core_root/src/ui/inline.rs" ]]; then
  printf '%s\n' '// FRB source-graph stub; desktop inline UI is not built on OHOS.' > "$core_root/src/ui/inline.rs"
fi
"$codegen" --skip-deps-check --class-name Rustdesk \
  --rust-input "$core_root/src/flutter_ffi.rs" --rust-crate-dir "$core_root" \
  --rust-output "$core_root/src/bridge_generated.rs" \
  --c-output "$repo_root/target/ohos-port-build/bridge_generated.h" \
  --dart-output "$repo_root/flutter/lib/generated_bridge.dart" --skip-add-mod-to-lib \
  --llvm-path "$OHOS_NDK_HOME/native/llvm" \
  --llvm-compiler-opts="--sysroot=$OHOS_NDK_HOME/native/sysroot -isystem $OHOS_NDK_HOME/native/sysroot/usr/include/aarch64-linux-ohos"
python3 scripts/patch-ohos-frb-dart.py flutter/lib/generated_bridge.dart
(cd flutter && dart format lib/generated_bridge.dart && dart analyze lib/generated_bridge.dart)

# Codegen has fetched the locked crates; do not pass exFAT metadata to C builders.
python3 scripts/clean-ohos-cargo-metadata.py "$core_root/Cargo.lock"
# Keep the locked OpenSSL build/install on NAS. Its vendored builder's
# remove_dir_all cleanup fails on the configured exFAT volume after install.
openssl_prefix="$(python3 scripts/build-openssl-ohos.py "$core_root/Cargo.lock" "$(ohos_build_jobs)")"
export AARCH64_UNKNOWN_LINUX_OHOS_OPENSSL_NO_VENDOR=1
export AARCH64_UNKNOWN_LINUX_OHOS_OPENSSL_LIB_DIR="$openssl_prefix/lib"
export AARCH64_UNKNOWN_LINUX_OHOS_OPENSSL_INCLUDE_DIR="$openssl_prefix/include"
export AARCH64_UNKNOWN_LINUX_OHOS_OPENSSL_STATIC=1

operation=build
if [[ "$check_only" == 1 ]]; then operation=check; fi
mkdir -p target/ohos-port-build
messages="$repo_root/target/ohos-port-build/cargo-artifacts.jsonl"
status=0
(cd "$core_root" && cargo "$operation" --locked --lib --release \
  --target aarch64-unknown-linux-ohos --no-default-features --features flutter,use_dasp \
  --message-format=json-render-diagnostics) > "$messages" || status=$?
python3 - "$messages" <<'PY'
import json
import sys
from pathlib import Path
for line in Path(sys.argv[1]).read_text(encoding='utf-8').splitlines():
    message = json.loads(line)
    if message.get('reason') == 'compiler-message':
        print(message['message'].get('rendered', ''), file=sys.stderr, end='')
PY
if [[ "$status" != 0 ]]; then exit "$status"; fi
if [[ "$check_only" == 1 ]]; then exit 0; fi
artifact="$(python3 - "$messages" "$core_root/Cargo.toml" <<'PY'
import json
from pathlib import Path
import sys
artifacts = set()
for line in Path(sys.argv[1]).read_text(encoding='utf-8').splitlines():
    message = json.loads(line)
    if (message.get('reason') == 'compiler-artifact'
            and message.get('target', {}).get('name') == 'librustdesk'
            and Path(message['manifest_path']).resolve() == Path(sys.argv[2]).resolve()):
        artifacts.update(name for name in message['filenames'] if name.endswith('.so'))
if len(artifacts) != 1:
    raise SystemExit(f'Expected one Cargo-reported core cdylib, got {sorted(artifacts)}')
print(artifacts.pop())
PY
)"
exports="$("$OHOS_NDK_HOME/native/llvm/bin/llvm-nm" -D --defined-only "$artifact")"
for symbol in session_get_rgba wire_session_start wire_main_init \
  ohos_init ohos_init_video ohos_deinit_video ohos_server_start \
  ohos_capture_start ohos_capture_stop ohos_capture_running \
  init_frb_dart_api_dl free_WireSyncReturn; do
  if ! grep -qE " [A-Za-z] ${symbol}$" <<< "$exports"; then
    echo "New core is missing required Flutter symbol: $symbol" >&2; exit 1
  fi
done
library_dir="$repo_root/flutter/ohos/entry/libs/arm64-v8a"
libcxx="$OHOS_NDK_HOME/native/llvm/lib/aarch64-linux-ohos/libc++_shared.so"
test -s "$libcxx"
mkdir -p "$library_dir"
cp "$artifact" "$library_dir/liblibrustdesk.so"
cp "$libcxx" "$library_dir/libc++_shared.so"
python3 scripts/ohos-port-core.py record --core-root "$core_root" --artifact "$artifact"
python3 scripts/ohos-port-core.py verify
