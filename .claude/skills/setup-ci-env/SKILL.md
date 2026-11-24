---
name: setup-ci-env
description: Set up this PC so Hookshot's CI can be run locally - llvm-mingw, the pinned header repositories, Python 3.12 with CI's packages, Visual Studio and .NET checks on Windows, and Docker for the Linux jobs. Use on a new PC or clone, when scripts/preflight.py or scripts/ci_check.py reports a step NOT RUN, when Docker or a tool is missing, or when a pinned version in .github/workflows changed.
---

# Set up the environment CI runs in

The goal: a pull request never fails in CI for a reason this PC could have shown. CI has two kinds of jobs, and each has
its own place here ([docs/development.md](../../../docs/development.md) has the build details):

| CI job | Runs here as | Needs |
|---|---|---|
| Windows: renderer with FSR 4, import check | `scripts/preflight.py` (native) | llvm-mingw, pinned headers in `_deps/cpm` |
| Windows: `tests/run_all.py --strict`, MSVC checks, Builder compile | `scripts/preflight.py` (native) | Python 3.12 venv, Visual Studio 2022 C++ tools, .NET SDK |
| Linux: checks, SPIR-V fixtures, lavapipe fixtures, actionlint | `scripts/ci_local.py` (Docker) | Docker Desktop (Linux containers) |
| NVIDIA DLSS link, Linux AppImage, PR title, hardware gate | not here | say so in the PR |

`python -I scripts/ci_check.py` runs both sides. Everything this skill makes is in `_deps/` (git-ignored), nothing of the
game is in it.

## 1. Check what is there

Run each and note what is missing:

```powershell
py -3.12 --version                      # Python 3.12 (install: py install 3.12)
dotnet --list-sdks                      # an SDK; the Builder targets net8.0
& "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe" -latest -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
docker --version; docker info --format '{{.OSType}}'   # must print: linux
```

## 2. Windows side

```powershell
python -I scripts/preflight.py --setup --skip build --skip tests
```

It unpacks the pinned llvm-mingw into `_deps/llvm`, fetches the three header repositories at the commits
`.github/workflows/vulkan-dlss.yml` pins into `_deps/cpm`, and makes `_deps/venv312` (Python 3.12 with the packages
`source-regressions.yml` installs). Needs the network once. Visual Studio and .NET are installed by you (Visual Studio
Installer, "Desktop development with C++"; .NET SDK from Microsoft).

## 3. Linux side (Docker)

Docker Desktop with Linux containers. Then:

```powershell
python -I scripts/ci_local.py --rebuild-image --job actionlint
```

builds `scripts/ci/linux.Dockerfile` (Ubuntu 24.04 with g++, clang, cmake, spirv-tools, lavapipe, glslang 16.6.0 and
actionlint at the versions the workflows pin) and runs the quickest job to prove it. The first build takes a few minutes.

### Things that bite

- **`docker` not found from a tool or agent session:** Docker Desktop installed per user puts it in
  `%LOCALAPPDATA%\Programs\DockerDesktop\resources\bin`, which an already-open session does not have in PATH.
  `ci_local.py` looks there itself; a new terminal also fixes it.
- **"error getting credentials ... docker-credential-desktop":** the credential helper is not on PATH. `ci_local.py` uses
  `_deps/docker-config` (no credential store; the images are public). Do the same by hand with `DOCKER_CONFIG`.
- **`\Windows was unexpected at this time` from VsDevCmd.bat:** one PATH entry has a stray quote (some installers leave
  `...Performance Toolkit"`). `preflight.py` cleans PATH for that step; fix the entry in the system PATH to stop it
  everywhere.
- **Pinned versions moved:** the headers' commits, llvm-mingw's name, glslang's and actionlint's versions and checksums are
  read from, or copied from, `.github/workflows/*.yml`. When one changes there, run `--setup` again and
  `ci_local.py --rebuild-image`, and update `scripts/ci/linux.Dockerfile`'s `ARG`s to match.
- **Containers are slow on bind mounts:** `ci_local.py` copies the working tree (`git ls-files -co --exclude-standard`)
  into the container's own file system instead. Do not add a bind mount of the repository.

## 4. Prove it

```powershell
python -I scripts/ci_check.py
```

Report which steps ran and which did not. A step that did not run is not a pass.
