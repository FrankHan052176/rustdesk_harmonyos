#!/usr/bin/env python3
"""Keep macOS AppleDouble metadata out of the locked native C dependency trees."""
import os
from pathlib import Path
import stat
import sys
import tomllib

# zstd-sys treats every *.c entry as source; openssl-src recursively copies its
# source tree. AppleDouble entries on exFAT are filesystem metadata, not inputs.
lock = tomllib.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
packages = {
    f"{item['name']}-{item['version']}": subdirectory
    for item in lock["package"]
    for name, subdirectory in (("zstd-sys", "zstd"), ("openssl-src", "openssl"))
    if item["name"] == name and item.get("source", "").startswith("registry+")
}
registry = Path(os.environ.get("CARGO_HOME", Path.home() / ".cargo")) / "registry/src"
removed = 0
for index in registry.iterdir():
    if not index.is_dir() or index.is_symlink():
        continue
    for package, subdirectory in packages.items():
        root = index / package / subdirectory
        if not root.is_dir() or root.is_symlink():
            continue
        for folder, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [name for name in dirs if not (Path(folder) / name).is_symlink()]
            for name in files:
                if not name.startswith("._"):
                    continue
                path = Path(folder) / name
                before = path.lstat()
                if not stat.S_ISREG(before.st_mode):
                    continue
                with path.open("rb") as stream:
                    opened = os.fstat(stream.fileno())
                    header = stream.read(26)
                identity = lambda entry: (entry.st_dev, entry.st_ino, entry.st_size, entry.st_mtime_ns)
                if identity(opened) != identity(before):
                    raise SystemExit(f"Cache entry changed while reading: {path}")
                if len(header) != 26 or header[:8] != bytes.fromhex("0005160700020000"):
                    continue
                if identity(path.lstat()) != identity(before):
                    raise SystemExit(f"Cache entry changed before deletion: {path}")
                path.unlink()
                removed += 1
print(f"Native C source cache: removed {removed} AppleDouble metadata entries")
