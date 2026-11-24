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
