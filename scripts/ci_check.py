"""Every job CI runs on a pull request, run here: the one command before you open or push one.

    python -I scripts/ci_check.py [--setup] [--skip windows|linux] [--base origin/develop] [--job NAME ...]

  windows   scripts/preflight.py: the MSVC and .NET checks, the renderer built with AMD FSR 4 and its import check, and
            tests/run_all.py --strict here and on --base (only what fails here and not there counts).
  linux     scripts/ci_local.py, in Docker: the checks on Ubuntu 24.04, the SPIR-V fixtures, the lavapipe fixtures and actionlint.
  --setup   makes the environment first (llvm-mingw, pinned headers, Python 3.12 with CI's packages, the Docker image).

Not here, and said in the summary: NVIDIA DLSS's link (its license is yours to accept), the Linux AppImage (hours of
dependencies; scripts/ci_local.py has no job for it) and the checks of the title and the hardware gate (GitHub's).
Exit 0 only when nothing failed and nothing was left unrun; a step that could not run is not a pass.
"""
import argparse
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(ROOT, "_deps", "venv312", "Scripts" if os.name == "nt" else "bin", "python.exe" if os.name == "nt" else "python")


def run(label, cmd):
    print("\n=== %s: %s" % (label, " ".join(os.path.relpath(c, ROOT) if os.path.isabs(c) and c.startswith(ROOT) else c for c in cmd)), flush=True)
    t0 = time.time()
    code = subprocess.run(cmd, cwd=ROOT).returncode
    return code, time.time() - t0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--setup", action="store_true")
    ap.add_argument("--skip", action="append", default=[], choices=["windows", "linux"])
    ap.add_argument("--base", default="origin/develop")
    ap.add_argument("--job", action="append", help="only these Linux jobs (see scripts/ci_local.py)")
    a = ap.parse_args()
    py = VENV_PY if os.path.isfile(VENV_PY) else sys.executable
    results = {}
    if "windows" not in a.skip:
        cmd = [py, "-I", os.path.join("scripts", "preflight.py"), "--base", a.base] + (["--setup"] if a.setup else [])
        results["windows (preflight)"] = run("windows", cmd)
    if "linux" not in a.skip:
        cmd = [sys.executable, "-I", os.path.join("scripts", "ci_local.py")]
        for j in a.job or []:
            cmd += ["--job", j]
        results["linux (docker)"] = run("linux", cmd)
    print("\nci_check summary")
    names = {0: "ok", 1: "FAILED", 2: "NOT RUN"}
    for name, (code, secs) in results.items():
        print("  %-22s %-8s %4d s" % (name, names.get(code, "exit %d" % code), secs))
    print("  %-22s %s" % ("not covered here", "NVIDIA DLSS link, Linux AppImage, PR title and hardware gate (say so in the PR)"))
    if any(c == 1 for c, _ in (v for v in results.values())):
        return 1
    return 2 if any(c == 2 for c, _ in results.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
