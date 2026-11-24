---
name: pre-pr-review
description: Run every job CI runs on a pull request here, locally, and do not open or push the pull request until each step passes or is named as not run - Windows (renderer build with FSR 4, imports, strict checks against develop, MSVC and .NET) and Linux in Docker (checks, SPIR-V and lavapipe fixtures, actionlint). Use before opening a Hookshot pull request, before each push to one, when asked to review a change before it goes up, or after a CI job failed and you want to see it fail here first.
---

# Run CI before CI does

The rule ([CONTRIBUTING.md](../../../CONTRIBUTING.md), [AGENTS.md](../../../AGENTS.md)): a pull request is tested before it
is opened. This skill is that test, for the part a PC can run. The environment it needs is
[setup-ci-env](../setup-ci-env/SKILL.md); if a step says NOT RUN for a missing tool, do that first.

## 1. Before the run

- Commit what you want tested, or leave it in the working tree: the Linux side copies tracked and not-ignored files, the
  Windows side uses the tree as it is.
- `git fetch origin` so `origin/develop` is current: the checks that fail on it are not yours, and that list is cached by
  its commit.
- The change itself: [prior-work](../prior-work/SKILL.md) said nobody else is on it; the title is a Conventional Commit.

## 2. Run it all

```powershell
python -I scripts/ci_check.py
```

About 15 to 20 minutes the first time, less after: the base's result and the Docker image and ccache are kept. Narrow it
while you iterate, and run it whole before the push:

```powershell
python -I scripts/ci_check.py --skip linux                  # Windows only
python -I scripts/ci_check.py --skip windows --job linux-tests   # one Linux job
python -I scripts/preflight.py --skip tests --skip native   # only the renderer build and imports
```

Each job's full output is in `_deps/` (`ci-<job>.log`, `vk-build.log`). Never run a game or a build outside the job queue
([docs/development.md](../../../docs/development.md#sharing-a-pc-the-job-queue)); `preflight.py` already goes through it.

## 3. Read the result

| Result | Meaning | You |
|---|---|---|
| `ok` | The step ran and passed | nothing |
| `FAILED` | New: it fails here and not on `origin/develop`, or a build, import, fixture or lint failed | fix it, run that job again, then the whole run |
| `NOT RUN` | A tool is missing (Docker, Visual Studio, llvm-mingw) | fix the environment ([setup-ci-env](../setup-ci-env/SKILL.md)) or say in the PR that it did not run. It is not a pass. |
| "N checks fail here and on origin/develop" | Not yours; develop's own failures | list them in the PR, do not fix them in it |

A check that fails only in Linux and not in Windows (or the reverse) is real: CI runs both. Read the log; do not retry
until it passes. For a test that reads source text, change the check in the same PR rather than weaken it
([AGENTS.md](../../../AGENTS.md)).

## 4. What this does not run

NVIDIA DLSS's link (its license is the owner's to accept: `scripts/ngx_sdk.py`), the Linux AppImage (hours of
dependencies), the PR title and the hardware-test gate. If the change touches `gpu/vk/vk_dlss*`, `vk_temporal*`, the
AppImage scripts (`scripts/linux/`) or `nx2/build.py`, say in the description that those jobs were left to CI, and watch
them there.

## 5. Put it in the description

- The summary `ci_check.py` printed, with what ran, what did not and why.
- The failing checks shared with `origin/develop`, by name, when there are any.
- The game run, the numbers and the screenshots the pull request needs
  ([measure](../measure/SKILL.md), [fix-with-test](../fix-with-test/SKILL.md)): this skill does not replace them.

Run it again before every push to the pull request, and if CI fails on something this said was fine, find the difference
(a version, a flag, a path) and put it into `scripts/ci_local.py`, `scripts/preflight.py` or the Dockerfile in the same PR.
