"""Check a commit message's subject is a Conventional Commit.

Same types as .github/workflows/pr-title.yml. Merge, revert-by-git, fixup! and squash! subjects pass: git writes
them and a pull request's squash title is what release-please reads (docs/versioning.md).
"""
import re
import sys

TYPES = ("feat", "fix", "perf", "refactor", "docs", "test", "ci", "build", "chore", "revert")
SUBJECT = re.compile(r"^(?:%s)(?:\([a-z0-9][a-z0-9._/-]*\))?!?: \S.*$" % "|".join(TYPES))
GIT_MADE = re.compile(r"^(?:Merge |Revert \"|fixup! |squash! |amend! )")


def check(subject):
    """True when the subject is acceptable."""
    return bool(SUBJECT.match(subject) or GIT_MADE.match(subject))


def subject_of(text):
    for line in text.splitlines():
        if line.strip() and not line.startswith("#"):
            return line.rstrip()
    return ""


def main(argv):
    with open(argv[1], encoding="utf-8", errors="replace") as f:
        subject = subject_of(f.read())
    if check(subject):
        return 0
    sys.stderr.write(
        "commit subject is not a Conventional Commit: %r\n"
        "  expected: type(scope)!: description, type one of %s\n"
        "  see docs/versioning.md\n" % (subject, ", ".join(TYPES)))
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
