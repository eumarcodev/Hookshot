# Git Flow on the fork

This fork (`eumarcodev/Hookshot`) uses Git Flow. Upstream contributions still follow [CONTRIBUTING.md](../CONTRIBUTING.md),
which branches from `main`: for an upstream PR, branch from `upstream/main`, not from `develop`.

| Branch | Role | Cut from | Merges into |
|---|---|---|---|
| `main` | What players run. Every release is a tag `vX.Y.Z` on it. | | |
| `develop` | Integration: the next release. | `main` | |
| `feat/<name>` | One feature. | `develop` | `develop` |
| `fix/<name>` | One bug fix that is not urgent. | `develop` | `develop` |
| `release/X.Y.Z` | Stabilise a release: version, notes, last fixes. | `develop` | `main` and `develop` |
| `hotfix/X.Y.Z` | A fix a released build needs now. | `main` | `main` and `develop` |

The prefixes match the repository's names (`feat/`, `fix/`) and tags carry a `v` (`git config gitflow.*`).

## Setup on a new clone

```powershell
git fetch origin
git switch develop
git flow init -d
git config gitflow.prefix.versiontag v
git config gitflow.prefix.feature feat/
git config gitflow.prefix.bugfix fix/
```

## Everyday commands

```powershell
git flow feature start my-change     # branch feat/my-change from develop
git flow feature finish my-change    # merge into develop
git flow release start 3.1.0         # branch release/3.1.0 from develop
git flow release finish 3.1.0        # merge into main, tag v3.1.0, merge back into develop
git flow hotfix start 3.0.7          # branch hotfix/3.0.7 from main
git flow hotfix finish 3.0.7         # merge into main, tag v3.0.7, merge back into develop
```

Finish through a pull request when the change needs CI or review: push the branch, open a PR into `develop` (or into
`main` for a release or hotfix), merge it, then tag and back-merge. A release is published with
`scripts/release_fork.py` ([versioning](versioning.md)).

## CI

`source-regressions` and `vulkan-dlss` run on pushes to `main` and `develop`. Builds (`linux-appimage`,
`windows-builder`) run for pull requests and for pushes to `main` and tags. `release.yml` stays on `main`.

## Conventional Commits and versions

The version rules are in [versioning.md](versioning.md): release-please reads the Conventional Commit subjects on
`main` (`feat` minor, `fix` and `perf` patch, `!` major) and writes the changelog and the tag.

- **Into `develop`: squash**, with a Conventional Commit title (the *PR title* check enforces it). One commit per
  change, so each is a changelog line.
- **Into `main`: fast-forward** (`git merge --ff-only develop` or the release/hotfix branch), never a merge commit and
  never a squash of the whole release. A merge commit's subject is not read, and a squash would turn a release into one
  changelog line. The commits from `develop` arrive on `main` as they are.
- **Back-merge `main` into `develop`** after a release or hotfix (a merge commit is fine there).
- Do not run `git flow release finish` with its default merge commit and its own tag: the tag comes from the Release
  workflow. Finish by hand as above.

For local commits, enable the subject check once per clone:

```powershell
git config core.hooksPath scripts/hooks
```

It rejects a subject that is not `type(scope)!: description`, with the same types as the PR title check
(`tests/test_commit_msg_hook.py` keeps the two lists equal).

## Branch protection (GitHub)

Set on `eumarcodev/Hookshot` for `main` and `develop`:

| Rule | `main` | `develop` |
|---|---|---|
| Pull request required (0 approvals: one maintainer) | yes | yes |
| Required check `Conventional Commit title` | yes | yes |
| Conversations resolved before merge | yes | yes |
| Linear history (no merge commits: fast-forward only) | yes | no (back-merges are merge commits) |
| Force push and deletion | blocked | blocked |
| Applies to administrators | no, so a release can be fast-forwarded from `develop` | no |

The game-free checks are not required yet: `main` has known failures on Windows (see the triage note), and a required
check that cannot pass would block every merge. Add them once `main` is green.
