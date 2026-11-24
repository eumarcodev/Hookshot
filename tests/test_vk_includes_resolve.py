"""Every #include "..." in the renderer's own files (gpu/vk/*.cpp, *.h, not eden or shaders) names a file the repo tracks.

A header that exists only in another tree (upstream's menu/output.h, which Hookshot never had) builds nowhere, yet no
game-free check compiles the whole renderer: this reads the includes instead. An include resolves against the including
file's folder, or to a tracked file whose path ends with it (the build's include folders are many: host, eden, the
vendored SDKs). GENERATED names are made or downloaded by the build (nx2/build.py), so they are not tracked.

    python -I -B tests/test_vk_includes_resolve.py
"""
from pathlib import Path
import posixpath
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
VK = ROOT / "gpu" / "vk"
INCLUDE = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.M)
# (made by nx2/build.py, or the NVIDIA SDK scripts/ngx_sdk.py fetches: never tracked)
GENERATED = ("hookshot_logo.h", "nvsdk_ngx")


def tracked():
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=True).stdout
    return {p for p in out.decode("utf-8").split("\0") if p}


def unresolved(files):
    bad = []
    for rel in sorted(files):
        if not rel.startswith("gpu/vk/") or rel.count("/") != 2 or not rel.endswith((".cpp", ".h")):
            continue
        text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        for name in INCLUDE.findall(text):
            if name.startswith(GENERATED):
                continue
            near = posixpath.normpath(posixpath.join("gpu/vk", name))
            if near in files or any(f.endswith("/" + name.lstrip("./")) for f in files):
                continue
            bad.append(f"{rel}: #include \"{name}\"")
    return bad


def main():
    bad = unresolved(tracked())
    for b in bad:
        print("FAIL (no tracked file):", b)
    if bad:
        return 1
    print("ok: every quoted #include in gpu/vk resolves to a tracked file")
    return 0


if __name__ == "__main__":
    sys.exit(main())
