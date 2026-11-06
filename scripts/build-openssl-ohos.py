#!/usr/bin/env python3
"""Build the locked OpenSSL on the configured NAS without vendored tree cleanup."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib

lock = tomllib.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
packages = [item for item in lock["package"] if item["name"] == "openssl-src"]
if len(packages) != 1:
    raise SystemExit("Expected one locked openssl-src package")
package = packages[0]
package_name = f"openssl-src-{package['version']}"
cargo_home = Path(os.environ.get("CARGO_HOME", Path.home() / ".cargo"))
sources = list((cargo_home / "registry/src").glob(f"*/{package_name}/openssl/Configure"))
if len(sources) != 1:
    raise SystemExit(f"Expected one fetched {package_name} source tree, found {len(sources)}")
source = sources[0].resolve().parent
cache_root = Path(os.environ["RUSTDESK_BUILD_CACHE_ROOT"])
if cache_root.is_relative_to("/Volumes/nas"):
    subprocess.run([str(Path.home() / ".config/rustdesk-build-cache/guard.sh"), "--ready"], check=True)

target = "aarch64-unknown-linux-ohos"
compiler = os.environ["CC_aarch64_unknown_linux_ohos"]
env = os.environ.copy()
env.update({
    "CC": compiler,
    "AR": os.environ["AR_aarch64_unknown_linux_ohos"],
    "RANLIB": os.environ["RANLIB_aarch64_unknown_linux_ohos"],
    "CFLAGS": "-O2 -ffunction-sections -fdata-sections -fPIC " + os.environ["CFLAGS_aarch64_unknown_linux_ohos"],
})
env.pop("CROSS_COMPILE", None)
# Match openssl-src's current static/legacy build and disabled weak ciphers.
options = [
    "--openssldir=/usr/local/ssl", "no-shared", "no-module", "no-tests",
    "no-comp", "no-zlib", "no-zlib-dynamic", "--libdir=lib",
    "no-ssl3", "no-md2", "no-rc5", "no-weak-ssl-ciphers",
    "no-camellia", "no-idea", "no-seed", "linux-aarch64",
]
inputs = {
    "package": package_name, "checksum": package["checksum"], "target": target,
    "compiler": compiler,
    "compiler_version": subprocess.check_output([compiler, "--version"], text=True),
    "ar": env["AR"], "ranlib": env["RANLIB"], "cflags": env["CFLAGS"],
    "cppflags": env.get("CPPFLAGS", ""), "ldflags": env.get("LDFLAGS", ""),
    "options": options,
    "builder_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
}
serialized = json.dumps(inputs, sort_keys=True, indent=2) + "\n"
key = hashlib.sha256(serialized.encode()).hexdigest()[:16]
work = cache_root / "native-openssl" / package["version"] / target / key
build = work / "build"
prefix = work / "install"
stamp = prefix / "build-inputs.json"
required = [prefix / name for name in ("lib/libcrypto.a", "lib/libssl.a", "include/openssl/ssl.h")]
if stamp.is_file() and stamp.read_text() == serialized and all(path.is_file() and path.stat().st_size for path in required):
    print(f"Using cached OHOS OpenSSL: {prefix}", file=sys.stderr)
    print(prefix)
    sys.exit(0)

build.mkdir(parents=True, exist_ok=True)
prefix.mkdir(parents=True, exist_ok=True)
stamp.unlink(missing_ok=True)
perl = env.get("OPENSSL_SRC_PERL", env.get("PERL", "perl"))

def run(*command):
    subprocess.run(command, cwd=build, env=env, check=True, stdout=sys.stderr)

print(f"Building locked {package_name} out of tree: {build}", file=sys.stderr)
run(perl, str(source / "Configure"), f"--prefix={prefix}", *options)
run("make", "depend")
run("make", f"-j{sys.argv[2]}", "build_libs")
run("make", "install_dev")
if not all(path.is_file() and path.stat().st_size for path in required):
    raise SystemExit("OpenSSL installation did not produce the required static libraries and headers")
temporary = stamp.with_suffix(".tmp")
temporary.write_text(serialized, encoding="utf-8")
temporary.replace(stamp)
print(f"Built OHOS OpenSSL: {prefix}", file=sys.stderr)
print(prefix)
