"""A release's player files made on this PC, for the fork's GitHub releases (eumarcodev/Hookshot), where no release workflow
runs: the Windows setup (HookshotBuilder.exe), the Hookshot Builder package split into the parts the setup downloads, and the
Linux AppImage. No game data is involved: what is packaged is the builder, its tools and the play profile, as the release
workflows package them (.github/workflows/windows-builder.yml, linux-appimage.yml).

    python -I scripts/release_fork.py windows <tag> [--reference <installed HookshotBuilder folder>] [--upload]
    python -I scripts/release_fork.py linux <tag> [--wsl <distribution>] [--upload]
    python -I scripts/release_fork.py all <tag> [--upload]
    python -I scripts/release_fork.py windows <tag> --from <commit>   (a trial build, never uploaded)

The tag must be pushed and hold the GitHub download addresses (builder/setup/setup.c, Updater.cs). Each command builds from a
clean checkout of the tag (a detached worktree under .dev/release/<tag>), never from the working tree.

windows: the setup (builder/setup/setup.c with the official llvm-mingw release_parts.py names), the Builder (dotnet publish,
versioned from the tag), the package folder (release_parts.py package), then the parts the CI took from the previous release:
Python, emsdk, CPM and wgpu-native (tools/<name>), the play profile (tools/work) and third_party's prebuilt files, all from
--reference, an installed Builder of an earlier release (default: %LOCALAPPDATA%/HookshotBuilder/versions/<current.txt>). Then
make_setup_parts.py splits it, with the parts served from the builder-parts release. Out: .dev/release/<tag>/dist.

linux: the AppImage built in the Ubuntu 22.04 container (scripts/linux/docker.sh) inside WSL, from a clone of the fork in the
distribution's own file system (~/hookshot-release/src: /mnt is slow, and the deps are kept there between releases). Out: the
AppImage and its .zsync copied to .dev/release/<tag>/dist.

--upload (needs gh signed in with write access to the fork): new parts go to the builder-parts prerelease (created when
missing; a part already there by name is the same file, named by its SHA-256), then the files, a <file>.sha256 beside each and
SHA256SUMS go to the tag's release (created as the latest when missing; a file already there is replaced).
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = "eumarcodev/Hookshot"
PARTS_RELEASE = "builder-parts"
PARTS_BASE = "https://github.com/%s/releases/download/%s" % (REPO, PARTS_RELEASE)
TOOLS_FROM_REFERENCE = ("python", "emsdk", "cpm", "wgpu-native")
PREBUILT_EXT = (".dll", ".lib", ".exe", ".sha1")   # (as release_parts.py: third_party files the repository does not carry)
WSL_DIR = "~/hookshot-release"


def run(cmd, cwd=None, env=None, capture=False):
    print("+ " + " ".join(cmd), file=sys.stderr, flush=True)
    r = subprocess.run(cmd, cwd=cwd, env=env, check=True, text=True, capture_output=capture)
    return r.stdout if capture else None


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main_root():
    """The main checkout (a worktree's .dev is the main checkout's, where the release folders are kept)"""
    common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=ROOT, check=True,
                            text=True, capture_output=True).stdout.strip()
    return os.path.dirname(common)


def release_dir(tag):
    return os.path.join(main_root(), ".dev", "release", tag)


def checkout(tag, ref=None):
    """A clean detached worktree of the tag (or of --from's commit, for a trial build), made again on every run"""
    run(["git", "fetch", "-q", "--tags", "origin"], cwd=ROOT)
    if not ref and not run(["git", "tag", "-l", tag], cwd=ROOT, capture=True).strip():
        raise SystemExit("no tag %s: push it first" % tag)
    src = os.path.join(release_dir(tag), "src")
    if os.path.exists(src):
        run(["git", "worktree", "remove", "--force", src], cwd=ROOT)
        shutil.rmtree(src, ignore_errors=True)
    os.makedirs(release_dir(tag), exist_ok=True)
    run(["git", "worktree", "add", "-q", "--detach", src, ref or tag], cwd=ROOT)
    for f in ("builder/setup/setup.c", "builder/HookshotBuilder/Updater.cs"):
        with open(os.path.join(src, f), encoding="utf-8") as fh:
            if "git.notrexed.dev" in fh.read():
                raise SystemExit("%s at %s still downloads from git.notrexed.dev: its players could not install or update" % (f, tag))
    return src


def default_reference():
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "HookshotBuilder", "versions")
    try:
        with open(os.path.join(base, "current.txt"), encoding="utf-8") as f:
            return os.path.join(base, f.read().strip(), "HookshotBuilder")
    except OSError:
        return None


def windows(a):
    ref = a.reference or default_reference()
    if not ref or not os.path.isdir(os.path.join(ref, "tools")):
        raise SystemExit("no installed Builder to take Python, emsdk, CPM, wgpu-native and the play profile from: pass --reference")
    for name in TOOLS_FROM_REFERENCE + ("work", "third_party"):
        if not os.path.isdir(os.path.join(ref, "tools", name)):
            raise SystemExit("%s has no tools/%s" % (ref, name))
    src = checkout(a.tag, a.ref)
    out = release_dir(a.tag)
    py = sys.executable
    dist = os.path.join(out, "dist")
    os.makedirs(dist, exist_ok=True)
    for f in os.listdir(dist):   # (this platform's files only: dist/linux is the AppImage's)
        if os.path.isfile(os.path.join(dist, f)):
            os.remove(os.path.join(dist, f))

    # the official llvm-mingw, kept between releases
    cache = os.path.join(main_root(), ".dev", "release", "llvm")
    mingw = run([py, "-I", "scripts/release_parts.py", "llvm-mingw", cache], cwd=src, capture=True).strip().splitlines()[-1]
    clang = os.path.join(mingw, "bin", "x86_64-w64-mingw32-clang.exe")
    windres = os.path.join(mingw, "bin", "x86_64-w64-mingw32-windres.exe")

    setup_exe = os.path.join(out, "HookshotBuilder-Setup.exe")
    rc_o = os.path.join(out, "setup_rc.o")
    setup_dir = os.path.join(src, "builder", "setup")
    run([windres, "setup.rc", "-O", "coff", "-o", rc_o], cwd=setup_dir)
    run([clang, "-O2", "-mwindows", "-municode", "setup.c", rc_o, "-lwinhttp", "-lbcrypt", "-lcomctl32", "-lshell32",
         "-lole32", "-Wl,--dependent-load-flag=0x800", "-o", setup_exe], cwd=setup_dir)

    version = a.tag[1:] if a.tag.startswith("v") else a.tag
    publish = os.path.join(out, "publish")
    shutil.rmtree(publish, ignore_errors=True)
    run(["dotnet", "publish", "builder/HookshotBuilder/HookshotBuilder.csproj", "--configuration", "Release",
         "-p:Version=" + version, "-p:NuGetAudit=false", "--output", publish], cwd=src)

    package = os.path.join(out, "package")
    run([py, "-I", "scripts/release_parts.py", "package", package, "--publish", publish, "--setup", setup_exe], cwd=src)

    # (what the CI's --list / --reuse took from the previous release: from the installed reference instead)
    tools = os.path.join(package, "tools")
    for name in TOOLS_FROM_REFERENCE:
        print("tools/%s from %s" % (name, ref), file=sys.stderr)
        shutil.copytree(os.path.join(ref, "tools", name), os.path.join(tools, name), dirs_exist_ok=False,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.rmtree(os.path.join(tools, "work"), ignore_errors=True)
    shutil.copytree(os.path.join(ref, "tools", "work"), os.path.join(tools, "work"))
    tracked = set(run(["git", "ls-files", "third_party"], cwd=src, capture=True).split("\n"))
    n = 0
    ref_tp = os.path.join(ref, "tools")
    for d, _, fs in os.walk(os.path.join(ref_tp, "third_party")):
        for f in fs:
            rel = os.path.relpath(os.path.join(d, f), ref_tp).replace(os.sep, "/")
            if rel not in tracked and rel.lower().endswith(PREBUILT_EXT):
                dst = os.path.join(tools, *rel.split("/"))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(os.path.join(d, f), dst)
                n += 1
    print("%d prebuilt third_party files from %s" % (n, ref), file=sys.stderr)

    run([py, "-I", "scripts/make_setup_parts.py", package, a.tag, dist, "--base", PARTS_BASE], cwd=src)
    shutil.copyfile(setup_exe, os.path.join(dist, "HookshotBuilder.exe"))   # (Updater.cs: a release holds the setup so)
    with zipfile.ZipFile(os.path.join(dist, "Hookshot-%s-Windows-x64.zip" % a.tag), "w", zipfile.ZIP_DEFLATED) as z:
        z.write(os.path.join(dist, "HookshotBuilder.exe"), "HookshotBuilder.exe")
        z.write(os.path.join(src, "recomplauncher.json"), "recomplauncher.json")
    print("windows files in %s" % dist, file=sys.stderr)
    if a.upload:
        upload_parts(dist)
        upload_release(a.tag, [os.path.join(dist, f) for f in ("HookshotBuilder.exe", "hookshot-setup.txt",
                                                               "Hookshot-%s-Windows-x64.zip" % a.tag)])
    return 0


def wsl(distro, script):
    return run(["wsl.exe", "-d", distro, "--", "bash", "-lc", script])


def linux(a):
    ref = a.reference or default_reference()
    out = os.path.join(release_dir(a.tag), "dist", "linux")
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(os.path.join(out, "parts"))
    to_wsl = lambda p: run(["wsl.exe", "-d", a.wsl, "--", "wslpath", "-a", p.replace("\\", "/")], capture=True).strip()
    win_out = to_wsl(out)
    # (the play profile into work/, as the workflow's "Play profile" step: from the reference instead of the previous release)
    profile = "cp -a '%s/.' work/; " % to_wsl(os.path.join(ref, "tools", "work")) if ref and os.path.isdir(
        os.path.join(ref, "tools", "work")) else "echo 'no reference: built without the play profile'; "
    wsl(a.wsl, "set -e; mkdir -p %(d)s; cd %(d)s; [ -d src/.git ] || git clone -q https://github.com/%(r)s.git src; "
               "cd src; git fetch -q --tags --force origin '+refs/heads/*:refs/remotes/origin/*'; git checkout -q --force %(c)s; "
               "git clean -qfdx -e build-linux; rm -rf build-linux/parts build-linux/*.AppImage build-linux/*.zsync; "
               "mkdir -p work; %(p)s"
               "HOOKSHOT_VERSION=%(t)s scripts/linux/docker.sh; "
               "cp -v build-linux/*.AppImage build-linux/*.AppImage.zsync '%(o)s/'; cp -v build-linux/parts/* '%(o)s/parts/'"
        % {"d": WSL_DIR, "r": REPO, "t": a.tag, "c": a.ref or a.tag, "p": profile, "o": win_out})
    images = [os.path.join(out, f) for f in sorted(os.listdir(out)) if f.endswith((".AppImage", ".AppImage.zsync"))]
    listing = os.path.join(out, "parts", "hookshot-linux.txt")
    if len(images) != 2 or not os.path.isfile(listing):
        raise SystemExit("the AppImage build left %s and %s" % (images, "hookshot-linux.txt" if os.path.isfile(listing) else "no list"))
    print("linux files in %s" % out, file=sys.stderr)
    if a.upload:
        upload_parts(os.path.join(out, "parts"), "linux-parts", "HookshotLinux-", "The Linux builder's update parts.")
        upload_release(a.tag, images + [listing])
    return 0


def gh(*args, capture=False):
    return run(["gh", *args, "-R", REPO], capture=capture)


def upload_parts(dist, release=PARTS_RELEASE, prefix="HookshotBuilder-",
                 notes="The parts HookshotBuilder.exe (the setup) downloads, named by each release's hookshot-setup.txt."):
    """The parts the release lacks to its parts release (a prerelease, never latest). Named by their SHA-256: one already
    there by name is the same file"""
    have = ""
    try:
        have = gh("release", "view", release, "--json", "assets", "--jq", ".assets[].name", capture=True)
    except subprocess.CalledProcessError:
        gh("release", "create", release, "--prerelease", "--latest=false", "--title", release, "--notes", notes)
    have = set(have.split())
    for f in sorted(os.listdir(dist)):
        if f.startswith(prefix) and f.endswith(".zip"):
            if f in have:
                print("%s: already there" % f, file=sys.stderr)
            else:
                gh("release", "upload", release, os.path.join(dist, f))


def upload_release(tag, files):
    try:
        gh("release", "view", tag, "--json", "tagName", capture=True)
    except subprocess.CalledProcessError:
        gh("release", "create", tag, "--verify-tag", "--latest", "--title", tag, "--generate-notes")
    sums = []
    side = []
    for f in files:
        h = sha256(f)
        s = f + ".sha256"
        with open(s, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("%s  %s\n" % (h, os.path.basename(f)))
        side.append(s)
        sums.append("%s  %s" % (h, os.path.basename(f)))
    gh("release", "upload", tag, "--clobber", *files, *side)
    # (SHA256SUMS of every player file on the release, the other platform's too)
    names = gh("release", "view", tag, "--json", "assets", "--jq", ".assets[].name", capture=True).split()
    lines = {}
    for n in names:
        if n.endswith(".sha256"):
            txt = run(["gh", "release", "download", tag, "-R", REPO, "-p", n, "-O", "-"], capture=True)
            w = txt.split()
            if len(w) >= 2:
                lines[w[1]] = w[0]
    path = os.path.join(os.path.dirname(files[0]), "SHA256SUMS")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for n in sorted(lines):
            fh.write("%s  %s\n" % (lines[n], n))
    gh("release", "upload", tag, "--clobber", path)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("windows", "linux", "all"):
        s = sub.add_parser(name)
        s.add_argument("tag")
        s.add_argument("--upload", action="store_true")
        s.add_argument("--reference")
        s.add_argument("--wsl", default="ubuntu")
        s.add_argument("--from", dest="ref", help="a trial build of this commit under the tag's name (no --upload)")
    a = p.parse_args()
    if a.ref and a.upload:
        raise SystemExit("--from is for a trial build: a release is built from its pushed tag")
    if a.cmd in ("windows", "all"):
        windows(a)
    if a.cmd in ("linux", "all"):
        linux(a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
