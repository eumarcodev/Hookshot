"""The Linux jobs of CI, run here in Docker, as .github/workflows/source-regressions.yml runs them.

    python -I scripts/ci_local.py [--job NAME ...] [--rebuild-image] [--jobs N] [--base origin/develop]

  Jobs (all by default):
    linux-tests       tests/run_all.py --strict, as the "Python source checks (ubuntu-24.04)" jobs run it (one run, not two shards)
    spirv-fixtures    the three shader recompiler fixtures built with cmake and validated with spirv-val
    vulkan-fixtures   tests/run_vulkan_fixtures.py on lavapipe with validation layers
    actionlint        every workflow, with the release CI uses

  The Windows jobs run natively: scripts/preflight.py (the renderer, the import check, tests/run_all.py --strict, MSVC and .NET).
  scripts/ci_check.py runs both.

  The image (scripts/ci/linux.Dockerfile) is built once and kept. The source goes into the container as a copy of the working
  tree's tracked and not-ignored files (git ls-files), so what is tested is what would be pushed, committed or not; the
  container's work folder is its own file system (not a bind mount: those are slow on Windows). Pinned header
  repositories are read from the workflow and kept in _deps/ci-headers/. A ccache volume (hookshot-ccache) keeps the compiled
  objects between runs. Nothing of the game is involved.

Exit 0 when every job passed.
"""
import argparse
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEPS = os.path.join(ROOT, "_deps")
HEADERS = os.path.join(DEPS, "ci-headers")
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "source-regressions.yml")
IMAGE = "hookshot-ci-linux"
DOCKERFILE = os.path.join(ROOT, "scripts", "ci", "linux.Dockerfile")
WIN = os.name == "nt"


def say(msg):
    print("ci_local: " + msg, flush=True)


def docker_exe():
    exe = shutil.which("docker")
    if exe:
        return exe
    for base in (os.environ.get("LOCALAPPDATA", ""), os.environ.get("ProgramFiles", "")):
        for sub in ("Programs/DockerDesktop", "Docker/Docker"):
            p = os.path.join(base, *sub.split("/"), "resources", "bin", "docker.exe")
            if os.path.isfile(p):
                return p
    return None


def docker_env():
    """Docker Desktop's credential helper may be missing from PATH here; public images need none, so use a config without one."""
    env = dict(os.environ)
    exe = docker_exe()
    if exe:
        env["PATH"] = os.path.dirname(exe) + os.pathsep + env.get("PATH", "")
    cfg = os.path.join(DEPS, "docker-config")
    os.makedirs(cfg, exist_ok=True)
    if not os.path.isfile(os.path.join(cfg, "config.json")):
        with open(os.path.join(cfg, "config.json"), "w") as f:
            f.write('{"auths":{}}\n')
    env["DOCKER_CONFIG"] = cfg
    env["MSYS_NO_PATHCONV"] = "1"
    return env


def docker(*args, **kw):
    return subprocess.run([docker_exe()] + list(args), env=docker_env(), **kw)


def ensure_image(rebuild):
    have = docker("image", "inspect", IMAGE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    stale = have and os.path.getmtime(DOCKERFILE) > _image_time()
    if have and not rebuild and not stale:
        return True
    say("building the image %s (scripts/ci/linux.Dockerfile; a few minutes the first time)" % IMAGE)
    return docker("build", "-q", "-t", IMAGE, "-f", DOCKERFILE, os.path.dirname(DOCKERFILE)).returncode == 0


def _image_time():
    p = docker("image", "inspect", "-f", "{{.Created}}", IMAGE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        from datetime import datetime
        s = p.stdout.decode().strip()[:19]
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=__import__("datetime").timezone.utc).timestamp()
    except Exception:
        return 0


def pins(job):
    """(repository, ref, path) of the header checkouts of one job of the workflow (its text between 'job:' and the next job)."""
    text = open(WORKFLOW, encoding="utf-8").read()
    m = re.search(r"^  %s:\n(.*?)(?=^  \S[^\n]*:\n|\Z)" % re.escape(job), text, re.M | re.S)
    if not m:
        raise SystemExit("ci_local: job %r not found in source-regressions.yml" % job)
    return re.findall(r"repository: (\S+)\s+ref: ([0-9a-f]{40})[^\n]*\n\s+path: (_deps/\S+)", m.group(1))


def fetch_headers(job):
    """The job's pinned header repositories under _deps/ci-headers/<job>/ ; returns the folder."""
    out = os.path.join(HEADERS, job)
    for repo, sha, path in pins(job):
        dest = os.path.join(out, *path.split("/")[1:])
        stamp = os.path.join(dest, ".pinned")
        if os.path.isfile(stamp) and open(stamp).read().strip() == sha:
            continue
        say("fetching %s at %s" % (repo, sha[:8]))
        shutil.rmtree(dest, ignore_errors=True)
        os.makedirs(dest, exist_ok=True)
        for cmd in (["git", "init", "-q"], ["git", "fetch", "-q", "--depth", "1", "https://github.com/%s.git" % repo, sha],
                    ["git", "checkout", "-q", "FETCH_HEAD"]):
            if subprocess.run(cmd, cwd=dest).returncode:
                raise SystemExit("ci_local: %s failed" % " ".join(cmd))
        with open(stamp, "w") as f:
            f.write(sha)
    return out


def source_tar(job_script, ref=None):
    """The working tree as a tar stream: tracked and untracked files git does not ignore, as the PR would carry them."""
    buf = io.BytesIO()
    if ref:   # (a commit's own files, for the base the checks are compared with)
        buf.write(subprocess.run(["git", "archive", "--format=tar", ref], cwd=ROOT, stdout=subprocess.PIPE, check=True).stdout)
        buf.seek(0)
        t = tarfile.open(fileobj=buf, mode="r")
        members = [(m, t.extractfile(m).read() if m.isfile() else None) for m in t.getmembers()]
        t.close()
        buf = io.BytesIO()
        t = tarfile.open(fileobj=buf, mode="w")
        for m, data in members:
            t.addfile(m, io.BytesIO(data) if data is not None else None)
    else:
        names = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=ROOT, stdout=subprocess.PIPE, check=True)
        t = tarfile.open(fileobj=buf, mode="w")
        for n in names.stdout.decode("utf-8").split("\0"):
            p = os.path.join(ROOT, n)
            if n and os.path.isfile(p) and not os.path.islink(p):
                t.add(p, arcname=n)
    with t:
        info = tarfile.TarInfo(".ci_job.sh")
        info.size = len(job_script)
        info.mode = 0o755
        t.addfile(info, io.BytesIO(job_script))
    return buf.getvalue()


# (what each job runs inside the container, in /work; the steps are source-regressions.yml's)
CCACHE_SETUP = r"""
mkdir -p /ccache-bin && for c in cc c++ gcc g++ clang clang++; do ln -sf "$(command -v ccache)" /ccache-bin/$c; done
export PATH=/ccache-bin:$PATH
"""
SCRIPTS = {
    "linux-tests": (["python"], CCACHE_SETUP + r"""
export HOOKSHOT_SPIRV_HEADERS=/work/_deps/spirv-headers/include HOOKSHOT_VULKAN_HEADERS=/work/_deps/vulkan-headers/include
python -I -B tests/run_all.py --strict --jobs "$JOBS"
"""),
    "spirv-fixtures": (["spirv-fixtures"], CCACHE_SETUP + r"""
export CMAKE_CXX_COMPILER_LAUNCHER=ccache
R=/tmp/fx; S=/work/_deps/spirv-headers; fail=0
step() { echo "--- $1"; shift; "$@" || { echo "FAILED: $*"; fail=1; }; }
for f in global_load_16:global_load_16_ir native_suatom:native_suatom f2i_immediate:f2i_immediate; do
  d=${f%%:*}; b=${f##*:}
  CC=clang CXX=clang++ cmake -S tests/$d -B $R/$d -DREPO_ROOT=/work -DSPIRV_HEADERS_ROOT=$S >/dev/null || { echo "FAILED configure $d"; fail=1; continue; }
  cmake --build $R/$d --parallel "$JOBS" >/dev/null || { echo "FAILED build $d"; fail=1; continue; }
  case $d in
    global_load_16) $R/$d/$b $R/$d/modules || fail=1; RX_VK_FOLLOW_STORES=1 $R/$d/$b --cas-loops $R/$d/modules || fail=1; flags="--uniform-buffer-standard-layout" ;;
    native_suatom) $R/$d/$b $R/$d/modules || fail=1; flags="" ;;
    f2i_immediate) $R/$d/$b $R/$d/modules || fail=1; flags="--uniform-buffer-standard-layout" ;;
  esac
  for m in $R/$d/modules/*.spv; do spirv-val --target-env vulkan1.3 $flags "$m" || fail=1; done
  echo "$d done"
done
ccache --show-stats | head -4
exit $fail
"""),
    "vulkan-fixtures": ([], r"""
shopt -s nullglob
icds=(/usr/share/vulkan/icd.d/lvp_icd*.json)
[ "${#icds[@]}" -eq 1 ] || { echo "expected exactly one lavapipe ICD, found ${#icds[@]}"; exit 1; }
export VK_DRIVER_FILES="${icds[0]}" VK_ICD_FILENAMES="${icds[0]}"
python3 -I -B tests/run_vulkan_fixtures.py --cxx g++ --headers /usr/include --device llvmpipe
"""),
    "actionlint": ([], "actionlint -color\n"),
}


def run_job(name, jobs, ref=None):
    header_jobs, script = SCRIPTS[name]
    t0 = time.time()
    headers = fetch_headers(header_jobs[0]) if header_jobs else None
    log = os.path.join(DEPS, "ci-%s%s.log" % (name, "-base" if ref else ""))
    cmd = [docker_exe(), "run", "--rm", "-i", "-e", "JOBS=%d" % jobs, "-v", "hookshot-ccache:/ccache"]
    if headers:
        cmd += ["-v", "%s:/headers:ro" % headers]
    prep = "mkdir -p /work && tar -xf - -C /work && cd /work && git init -q && "
    if headers:
        prep += "mkdir -p _deps && cp -r /headers/* _deps/ && "
    cmd += [IMAGE, "bash", "-c", prep + "bash /work/.ci_job.sh"]
    # (the script travels in the tar stream as a file, so quoting never matters)
    stream = source_tar(("set -o pipefail\n" + script).encode("utf-8"))
    say("%s: running%s" % (name, " on " + ref if ref else ""))
    p = subprocess.run(cmd, input=stream, env=docker_env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = p.stdout.decode("utf-8", "replace")
    with open(log, "w", encoding="utf-8") as f:
        f.write(out)
    tail = "\n".join(out.strip().splitlines()[-6:])
    say("%s: %s in %d s (log: %s)" % (name, "ok" if p.returncode == 0 else "FAILED", time.time() - t0, os.path.relpath(log, ROOT)))
    if p.returncode and not ref:
        print(tail)
    return p.returncode, out


FAIL_LINE = re.compile(r"^FAIL\s+[\d.]+s\s+(tests/\S+)", re.M)


def compare_with_base(out, base, jobs):
    """The checks that fail here and not on base (its failures kept in _deps/ by its commit): 1 if there are any, else 0."""
    p = subprocess.run(["git", "rev-parse", "--verify", "-q", base], cwd=ROOT, stdout=subprocess.PIPE)
    if p.returncode:
        say("no %s to compare with (git fetch): the failures count" % base)
        return 1
    sha = p.stdout.decode().strip()
    cache = os.path.join(DEPS, "ci-linux-base-%s.txt" % sha[:12])
    if os.path.isfile(cache):
        theirs = set(open(cache, encoding="utf-8").read().split())
        say("checks on %s: its failures from %s" % (base, os.path.relpath(cache, ROOT)))
    else:
        _, bout = run_job("linux-tests", jobs, ref=sha)
        theirs = set(FAIL_LINE.findall(bout))
        with open(cache, "w", encoding="utf-8") as f:
            f.write("\n".join(sorted(theirs)) + "\n")
    mine = set(FAIL_LINE.findall(out))
    new, old = sorted(mine - theirs), sorted(mine & theirs)
    if old:
        say("%d checks fail here and on %s: not yours (e.g. %s)" % (len(old), base, ", ".join(old[:3])))
    if new:
        say("linux-tests FAILED: %d fail here and not on %s:\n  %s" % (len(new), base, "\n  ".join(new)))
        return 1
    say("linux-tests ok: nothing fails here that passes on %s" % base)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--job", action="append", choices=list(SCRIPTS))
    ap.add_argument("--rebuild-image", action="store_true")
    ap.add_argument("--base", default="origin/develop", help="the checks that fail here are compared with this revision's")
    ap.add_argument("--jobs", type=int, default=max(2, (os.cpu_count() or 4) // 2))
    a = ap.parse_args()
    if not docker_exe():
        say("Docker not found: install Docker Desktop (the Linux jobs run in it)")
        return 2
    if docker("info", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        say("the Docker daemon is not running: start Docker Desktop")
        return 2
    if not ensure_image(a.rebuild_image):
        say("the image did not build")
        return 1
    results = {}
    for n in (a.job or list(SCRIPTS)):
        code, out = run_job(n, a.jobs)
        if code and n == "linux-tests":
            code = compare_with_base(out, a.base, a.jobs)
        results[n] = code
    print()
    for n, c in results.items():
        print("  %-18s %s" % (n, "ok" if c == 0 else "FAILED"))
    return 1 if any(results.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
