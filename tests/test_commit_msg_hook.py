"""The local commit-msg hook accepts Conventional Commit subjects and nothing else (scripts/hooks/)."""
import importlib.util
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("commit_msg_check", ROOT / "scripts/hooks/commit_msg_check.py")
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


class Subjects(unittest.TestCase):
    def test_accepts(self):
        for s in ("feat: x", "fix(vk): a fix", "feat(builder)!: breaking", "chore(ci-x): y",
                  "Merge branch 'develop'", "fixup! fix: x"):
            self.assertTrue(hook.check(s), s)

    def test_rejects(self):
        for s in ("", "update stuff", "Fix: x", "feat:x", "feat(): x", "wip: x", "feat(vk) x"):
            self.assertFalse(hook.check(s), s)

    def test_comment_lines_skipped(self):
        self.assertEqual(hook.subject_of("# c\n\nfix: a\nbody"), "fix: a")

    def test_types_match_pr_title_workflow(self):
        wf = (ROOT / ".github/workflows/pr-title.yml").read_text(encoding="utf-8")
        block = re.search(r"types: \|\n((?:\s+\w+\n)+)", wf).group(1).split()
        self.assertEqual(tuple(block), hook.TYPES)


if __name__ == "__main__":
    unittest.main()
