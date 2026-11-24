"""What CI runs, run here before a pull request is opened (CONTRIBUTING.md, "Before you open a pull request").

    python -I scripts/preflight.py [--setup] [--skip build|tests|native] [--base origin/develop]

  --setup   Windows: fetch what the renderer's build needs into _deps/ (git-ignored): the pinned llvm-mingw and the three
            header repositories at the commits .github/workflows/vulkan-dlss.yml pins. Needs the network, once.
  build     The renderer alone with AMD FSR 4, as the "Windows Vulkan back end with NVIDIA DLSS and AMD FSR 4" job builds it
            (RX_VK_ONLY=1, HOOKSHOT_FSR=1, through the job queue), then that job's import check: hookshot_vk.dll may import
            only kernel32, user32, gdi32, advapi32 and the UCRT. NVIDIA's SDK is not part of it (its license is yours to
            accept: scripts/ngx_sdk.py); the files DLSS adds are not compiled here.
  tests     tests/run_all.py here and on --base (default origin/develop, in a git worktree under _deps/), and the checks
            that fail here and not there. A check that fails on both is not yours; it is listed, not counted. They run
            as CI's do: Python 3.12 in _deps/venv312 with CI's packages (--setup makes it when `py -3.12` exists; without it
            this Python, and the checks that need a package are skipped or fail on both sides), and llvm-mingw's compilers
            on PATH (CI's Windows job has a compiler; without one most checks skip). About 8 minutes; the base's result
            is kept in _deps/ by its commit, so the next run takes only this side.

Exit 0 when nothing new failed. It says what it could not run (no compiler, no llvm-mingw), and a step it could not run is
not a pass: say so in the pull request. The Linux AppImage job and the Linux runs of the checks are CI's; this does not
stand in for them.
"""
import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEPS = os.path.join(ROOT, "_deps")
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "vulkan-dlss.yml")
CI_PACKAGES = ["zstandard==0.25.0", "pillow==13.0.0", "numpy==2.5.4", "lz4==4.4.5", "unicorn==2.1.4"]   # (source-regressions.yml's; unicorn: tests/test_insn_fixed_convert.py)
ALLOWED_IMPORTS = re.compile(r"^(kernel32|user32|gdi32|advapi32)\.dll$|^api-ms-win-crt-[a-z0-9-]+\.dll$", re.I)
FAIL_LINE = re.compile(r"^FAIL\s+[\d.]+s\s+(tests/\S+)", re.M)
WIN = os.name == "nt"


def say(msg):
    print("preflight: " + msg, flush=True)


def run(cmd, cwd=ROOT, env=None, log=None):
    """Runs cmd, returns (exit code, output). With log, the output goes to that file and its tail is what comes back."""
    e = dict(os.environ, **(env or {}))
    p = subprocess.run(cmd, cwd=cwd, env=e, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = p.stdout.decode("utf-8", "replace")
    if log:
        with open(log, "w", encoding="utf-8") as f:
            f.write(out)
    return p.returncode, out


def mingw_dir():
    """The llvm-mingw folder: HOOKSHOT_MINGW, else the one --setup unpacked under _deps/llvm."""
    if os.environ.get("HOOKSHOT_MINGW") and os.path.isdir(os.environ["HOOKSHOT_MINGW"]):
        return os.environ["HOOKSHOT_MINGW"]
    base = os.path.join(DEPS, "llvm")
    if os.path.isdir(base):
        for d in sorted(os.listdir(base)):
            if os.path.isfile(os.path.join(base, d, "bin", "clang++.exe")):
                return os.path.join(base, d)
    return None


def test_python():
    """The venv --setup made (Python 3.12 and CI's packages), else the Python that runs this."""
    v = os.path.join(DEPS, "venv312", "Scripts" if WIN else "bin", "python.exe" if WIN else "python")
    return v if os.path.isfile(v) else sys.executable


def test_env():
    """As CI's "Every test" step has it: llvm-mingw's compilers on PATH, and the pinned SPIR-V and Vulkan headers."""
    m = mingw_dir()
    env = {"PATH": os.path.join(m, "bin") + os.pathsep + os.environ["PATH"], "HOOKSHOT_MINGW": m} if m else {}   # (HOOKSHOT_MINGW: the a64 checks take the target and runtime flags from it)
    for var, sub in (("HOOKSHOT_SPIRV_HEADERS", "spirv-headers"), ("HOOKSHOT_VULKAN_HEADERS", "vulkanheaders")):
        base = os.path.join(DEPS, "cpm", sub)
        found = sorted(glob.glob(os.path.join(base, "*", "include")))
        if found:
            env[var] = found[-1]
    return env


def header_pins():
    """(repository, commit, path under the clone) of each header repository the DLSS workflow checks out."""
    text = open(WORKFLOW, encoding="utf-8").read()
    pins = re.findall(r"repository: (\S+)\s+ref: ([0-9a-f]{40})[^\n]*\n\s+path: (_deps/cpm/\S+)", text)
    if len(pins) != 3:
        raise SystemExit("preflight: expected 3 pinned header repositories in vulkan-dlss.yml, found %d" % len(pins))
    return pins


def setup():
    if not WIN:
        say("--setup is for Windows (llvm-mingw); on Linux the distro's clang and headers are what the build uses")
        return 0
    os.makedirs(DEPS, exist_ok=True)
    if not mingw_dir():
        say("llvm-mingw (scripts/release_parts.py: the official release, SHA-256 checked)")
        code, out = run([sys.executable, "-I", os.path.join("scripts", "release_parts.py"), "llvm-mingw", os.path.join("_deps", "llvm")])
        if code:
            print(out)
            return code
    for repo, sha, path in header_pins():
        dest = os.path.join(ROOT, *path.split("/"))
        if os.path.isdir(os.path.join(dest, ".git")):
            continue
        say("fetching %s at %s" % (repo, sha[:8]))
        os.makedirs(dest, exist_ok=True)
        for cmd in (["git", "init", "-q"],
                    ["git", "fetch", "-q", "--depth", "1", "https://github.com/%s.git" % repo, sha],
                    ["git", "checkout", "-q", "FETCH_HEAD"]):
            code, out = run(cmd, cwd=dest)
            if code:
                print(out)
                return code
    if not os.path.isfile(test_python()) or test_python() == sys.executable:
        py = ["py", "-3.12"] if WIN else ["python3.12"]
        code, out = run(py + ["-m", "venv", os.path.join("_deps", "venv312")])
        if code:
            say("no Python 3.12 (`py install 3.12`): the checks will run on this Python, which may lack CI's packages")
        else:
            code, out = run([test_python(), "-m", "pip", "install", "-q", "--disable-pip-version-check", "--no-deps"] + CI_PACKAGES)
            if code:
                print(out)
                return code
    say("setup done: " + (mingw_dir() or "?"))
    return 0


def build():
    """0 built and its imports are allowed, 1 failed, 2 could not run."""
    env = {"RX_VK_ONLY": "1", "RX_RELEASE": "1", "HOOKSHOT_FSR": "1", "OOT_WORK": os.path.join(DEPS, "vk-work")}
    if WIN:
        mingw = mingw_dir()
        cpm = os.path.join(DEPS, "cpm")
        if not mingw or not os.path.isdir(cpm):
            say("renderer build NOT RUN: no llvm-mingw or headers under _deps/ (python -I scripts/preflight.py --setup)")
            return 2
        env.update(HOOKSHOT_MINGW=mingw, RX_CPM=cpm)
    elif shutil.which("clang++") is None:
        say("renderer build NOT RUN: no clang++")
        return 2
    log = os.path.join(DEPS, "vk-build.log")
    os.makedirs(DEPS, exist_ok=True)
    say("building the renderer with AMD FSR 4 (log: _deps/vk-build.log)")
    t0 = time.time()
    code, out = run([sys.executable, "-I", os.path.join("scripts", "job_queue.py"), "run", "preflight-vk", "--mode", "build", "--",
                     sys.executable, "-I", os.path.join("nx2", "build.py"), "--standalone", "--jobs", str(max(2, (os.cpu_count() or 4) // 2))],
                    env=env, log=log)
    if code or "with AMD FSR 4" not in out:
        errs = [l for l in out.splitlines() if "error" in l.lower()][:12]
        say("renderer build FAILED (%d s):\n  %s" % (time.time() - t0, "\n  ".join(errs) or out[-800:]))
        return 1
    say("renderer built (%d s)" % (time.time() - t0))
    if not WIN:
        return 0
    dll = os.path.join(DEPS, "vk-work", "sa", "build", "hookshot_vk.dll")
    code, out = run([os.path.join(mingw_dir(), "bin", "llvm-objdump.exe"), "-p", dll])
    names = sorted({m.strip() for m in re.findall(r"DLL Name: (\S+)", out)})
    bad = [n for n in names if not ALLOWED_IMPORTS.match(n)]
    if bad:
        say("imports FAILED: hookshot_vk.dll imports a DLL that is not Windows' own: " + ", ".join(bad) +
            " (load it at its first use, as gpu/vk/vk_menu.cpp share_proc does)")
        return 1
    say("imports ok (%d DLLs, all allowed)" % len(names))
    return 0


def failing(root):
    code, out = run([test_python(), "-I", "-B", os.path.join("tests", "run_all.py"), "--strict", "--jobs", str(max(2, (os.cpu_count() or 4) // 2))],
                    cwd=root, env=test_env())
    m = re.search(r"(\d+) passed, (\d+) failed, (\d+) skipped", out)
    return set(FAIL_LINE.findall(out)), (m.groups() if m else ("?", "?", "?"))


def tests(base):
    code, out = run(["git", "rev-parse", "--verify", "-q", base])
    if code:
        say("tests NOT RUN against %s: no such revision (git fetch)" % base)
        return 2
    sha = out.strip()
    say("checks here (with %s%s)" % (os.path.relpath(test_python(), ROOT) if test_python() != sys.executable else "this Python",
                                     ", llvm-mingw on PATH" if test_env() else ""))
    mine, n_mine = failing(ROOT)
    say("%s passed, %s failed, %s skipped" % n_mine)
    cache = os.path.join(DEPS, "base-failing-strict-%s.txt" % sha[:12])
    if os.path.isfile(cache):
        theirs = set(open(cache, encoding="utf-8").read().split())
        say("checks on %s: its failures from %s" % (base, os.path.relpath(cache, ROOT)))
    else:
        wt = os.path.join(DEPS, "base-worktree")
        if os.path.isdir(wt):
            run(["git", "worktree", "remove", "--force", wt])
        code, out = run(["git", "worktree", "add", "--detach", "-f", wt, base])
        if code:
            print(out)
            return 2
        try:
            say("checks on %s" % base)
            theirs, n_base = failing(wt)
            say("%s passed, %s failed, %s skipped" % n_base)
        finally:
            run(["git", "worktree", "remove", "--force", wt])
        with open(cache, "w", encoding="utf-8") as f:
            f.write("\n".join(sorted(theirs)) + "\n")
    new = sorted(mine - theirs)
    # (a check that fails with eight others running and passes alone is load, not the change: each new failure is run again
    # by itself, and only what fails again counts. The ones that passed are named, so a flaky check is not hidden)
    for name in list(new):
        code, _ = run([test_python(), "-I", "-B", os.path.join("tests", "run_all.py"), "--strict", name], env=test_env())
        if code == 0:
            say("%s failed in the full run and passes alone (load): not counted" % name)
            new.remove(name)
    old = sorted(mine & theirs)
    if old:
        say("%d checks fail here and on %s: not yours (e.g. %s)" % (len(old), base, ", ".join(old[:3])))
    if new:
        say("tests FAILED: %d fail here and not on %s:\n  %s" % (len(new), base, "\n  ".join(new)))
        return 1
    say("tests ok: nothing fails here that passes on %s" % base)
    return 0


def vsdevcmd():
    vw = os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Microsoft Visual Studio", "Installer", "vswhere.exe")
    if not os.path.isfile(vw):
        return None
    code, out = run([vw, "-latest", "-products", "*", "-version", "[17.0,18.0)", "-requires",
                     "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"])
    root = out.strip().splitlines()[0] if code == 0 and out.strip() else ""
    path = os.path.join(root, "Common7", "Tools", "VsDevCmd.bat")
    return path if root and os.path.isfile(path) else None


def native():
    """CI's Windows-only steps (source-regressions.yml, part 1): the MSVC checks and the Builder's .NET checks.
    0 passed, 1 failed, 2 could not run (no Visual Studio 2022 C++ tools, or no dotnet)."""
    if not WIN:
        say("native checks are Windows' (MSVC, .NET): NOT RUN here")
        return 2
    dev = vsdevcmd()
    dotnet = shutil.which("dotnet")
    if not dev or not dotnet:
        say("native checks NOT RUN: %s" % ("no Visual Studio 2022 with the C++ tools" if not dev else "no dotnet"))
        return 2
    tmp = os.path.join(DEPS, "native")
    os.makedirs(tmp, exist_ok=True)
    py = test_python()
    cmd = os.path.join(tmp, "msvc.cmd")
    lines = ["@echo off",
             'call "%s" -arch=x64 -host_arch=x64 >nul' % dev,
             "if errorlevel 1 exit /b 1",
             r'cl /nologo /std:c++17 /W4 /WX /EHsc /Fo"%s\present_rect_test.obj" /Fe:"%s\present_rect_test.exe" '
             r'gpu\vk\tests\present_rect_test.cpp' % (tmp, tmp),
             "if errorlevel 1 exit /b 1",
             r'"%s\present_rect_test.exe"' % tmp,
             "if errorlevel 1 exit /b 1",
             '"%s" -I -B tests/test_invalidate_vcore.py --cxx cl' % py]
    with open(cmd, "w", newline="") as f:
        f.write("\r\n".join(lines) + "\r\n")
    steps = [("MSVC checks (present_rect_test, test_invalidate_vcore)", ["cmd", "/c", cmd])]
    workers = os.path.join("tests", "builder_workers", "BuilderWorkersTests.csproj")
    if os.path.isfile(os.path.join(ROOT, workers)):
        steps.append(("Builder worker delivery", [dotnet, "run", "--project", workers, "--configuration", "Release", "--", py]))
    else:
        say("Builder worker delivery skipped: %s is not in the repository (the workflow step is stale)" % workers)
    steps.append(("Compile Windows Builder", [dotnet, "build", os.path.join("builder", "HookshotBuilder", "HookshotBuilder.csproj"),
                                              "--configuration", "Debug", "-p:NuGetAudit=false"]))
    failed = []
    # (a stray quote in one PATH entry, as some installers leave, breaks VsDevCmd.bat's own parsing: "\Windows was unexpected")
    clean_path = os.pathsep.join(e.replace('"', "") for e in os.environ["PATH"].split(os.pathsep) if e.strip())
    for name, c in steps:
        code, out = run(c, env={"PATH": clean_path})
        say("%s: %s" % (name, "ok" if code == 0 else "FAILED"))
        if code:
            failed.append(name)
            print(out[-1500:])
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--setup", action="store_true")
    ap.add_argument("--skip", action="append", default=[], choices=["build", "tests", "native"])
    ap.add_argument("--base", default="origin/develop")
    a = ap.parse_args()
    if a.setup:
        code = setup()
        if code:
            return code
    results = {}
    if "native" not in a.skip:
        results["MSVC and .NET checks"] = native()
    if "build" not in a.skip:
        results["renderer build"] = build()
    if "tests" not in a.skip:
        results["tests against " + a.base] = tests(a.base)
    print()
    for name, code in results.items():
        print("  %-34s %s" % (name, {0: "ok", 1: "FAILED", 2: "NOT RUN"}[code]))
    if any(c == 1 for c in results.values()):
        return 1
    if any(c == 2 for c in results.values()):
        say("a step could not run: the pull request says so")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
