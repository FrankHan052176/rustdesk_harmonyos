#!/usr/bin/env python3
"""Fetch the pinned OHOS core and bind packaged native/Dart outputs to one build."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PORT = ROOT / "native/ohos_port"
LOCK = PORT / "source.lock.json"
RECEIPT = ROOT / "target/ohos-port-build/core-build.json"
LIBRARY = ROOT / "flutter/ohos/entry/libs/arm64-v8a/liblibrustdesk.so"
DART = ROOT / "flutter/lib/generated_bridge.dart"
BUILD_INPUTS = (
    "native/ohos_port/source.lock.json",
    "native/ohos_port/core_patch.py",
    "native/ohos_port/flutter_video.py",
    "scripts/build-ohos-port-core.sh",
    "scripts/build-libsodium-ohos.sh",
    "scripts/build-openssl-ohos.py",
    "scripts/clean-ohos-cargo-metadata.py",
    "scripts/ohos-env.sh",
    "scripts/ohos-port-core.py",
    "scripts/patch-ohos-frb-dart.py",
    "scripts/prepare-ohos-flutter.py",
)


def git(core, *args):
    return subprocess.check_output(["git", "-C", str(core), *args], text=True).strip()


def run(*args):
    subprocess.run([str(arg) for arg in args], check=True, stdout=sys.stderr)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inputs():
    return {name: digest(ROOT / name) for name in BUILD_INPUTS}


def validate_core(core, lock):
    actual = git(core, "rev-parse", "HEAD")
    if actual != lock["revision"]:
        raise ValueError(f"Core is {actual}, expected {lock['revision']}; use a separate checkout, not reset/overwrite")
    expected_submodule = git(core, "ls-tree", "HEAD", "libs/hbb_common").split()[2]
    submodule = core / "libs/hbb_common"
    if not (submodule / "Cargo.toml").is_file():
        raise ValueError(f"Core submodule is missing: {submodule}")
    if git(submodule, "rev-parse", "HEAD") != expected_submodule:
        raise ValueError("hbb_common is not at the core's pinned gitlink")
    changes = git(submodule, "status", "--porcelain")
    if changes:
        raise ValueError("hbb_common must remain at the unmodified upstream gitlink")
    run(sys.executable, PORT / "core_patch.py", core, "--check")


def prepare(core, lock, check):
    if not (core / "Cargo.toml").is_file():
        if check:
            raise ValueError(f"Pinned core has not been prepared: {core}")
        if core.exists() and any(core.iterdir()):
            raise ValueError(f"Core directory is not an empty checkout: {core}")
        core.parent.mkdir(parents=True, exist_ok=True)
        run("git", "clone", "--no-checkout", "--depth", "1", "--single-branch",
            "--branch", lock["branch"], lock["repository"], core)
        run("git", "-C", core, "fetch", "--depth", "1", "origin", lock["revision"])
        run("git", "-C", core, "checkout", "--detach", lock["revision"])
    if git(core, "rev-parse", "HEAD") != lock["revision"]:
        raise ValueError("Existing core checkout does not match source.lock.json; it was not changed")
    if not (core / "libs/hbb_common/Cargo.toml").is_file() and not check:
        run("git", "-C", core, "submodule", "update", "--init", "--recursive", "--", "libs/hbb_common")
    validate_core(core, lock)
    if not check:
        run(sys.executable, PORT / "core_patch.py", core)
    print(core)


def record(core, lock, artifact):
    validate_core(core, lock)
    if not artifact.is_file() or not LIBRARY.is_file() or not DART.is_file():
        raise ValueError("Native build or matching generated Dart output is missing")
    if digest(artifact) != digest(LIBRARY):
        raise ValueError("Packaged library differs from the just-built core artifact")
    data = {
        "repository": lock["repository"], "revision": git(core, "rev-parse", "HEAD"),
        "artifact": str(artifact.resolve()), "library_sha256": digest(LIBRARY),
        "dart_sha256": digest(DART), "inputs": inputs(),
    }
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    temporary = RECEIPT.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    temporary.replace(RECEIPT)
    print(f"Recorded OHOS Flutter core {data['revision']}")


def verify(lock):
    if not RECEIPT.is_file():
        raise ValueError("New-core build receipt is missing; run bash scripts/build-ohos-ffi.sh before packaging")
    data = json.loads(RECEIPT.read_text(encoding="utf-8"))
    if data.get("revision") != lock["revision"] or data.get("repository") != lock["repository"]:
        raise ValueError("Packaged OHOS core does not match source.lock.json")
    if data.get("inputs") != inputs():
        raise ValueError("OHOS integration changed after the native build; rebuild the core")
    if digest(LIBRARY) != data.get("library_sha256") or digest(DART) != data.get("dart_sha256"):
        raise ValueError("Native library and generated Dart bindings are not from the recorded new-core build")
    print(f"Verified pinned OHOS Flutter core {data['revision']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "record", "verify"))
    parser.add_argument("--core-root", type=Path,
                        default=Path(os.environ.get("RUSTDESK_CORE_ROOT", ROOT / "target/ohos-port-upstream-d53391e")))
    parser.add_argument("--check", action="store_true", help="prepare: check without fetching or applying changes")
    parser.add_argument("--artifact", type=Path)
    args = parser.parse_args()
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    core = args.core_root.resolve()
    if args.action == "prepare":
        prepare(core, lock, args.check)
    elif args.action == "record":
        if args.artifact is None:
            parser.error("record requires --artifact")
        record(core, lock, args.artifact)
    else:
        verify(lock)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"OHOS core: {error}", file=sys.stderr)
        sys.exit(1)
