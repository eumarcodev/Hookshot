"""scripts/preflight.py reads what CI reads: the three header pins of vulkan-dlss.yml, the DLL import rule and run_all's FAIL
lines. A pin or a rule it reads wrongly would pass a build CI refuses.

    python -I -B tests/test_preflight.py
"""
from pathlib import Path
import importlib.util
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("preflight", ROOT / "scripts" / "preflight.py")
pf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pf)


class Preflight(unittest.TestCase):
    def test_pins_are_the_workflows(self):
        pins = pf.header_pins()
        self.assertEqual([p[2] for p in pins], [
            "_deps/cpm/vulkanheaders/v1.4.345", "_deps/cpm/spirv-headers/vulkan-sdk-1.4.341.0",
            "_deps/cpm/vulkanmemoryallocator/v3.3.0"])
        for repo, sha, _ in pins:
            self.assertRegex(sha, r"^[0-9a-f]{40}$")
            self.assertTrue(repo.count("/") == 1)

    def test_import_rule_is_the_workflows(self):
        text = (ROOT / ".github" / "workflows" / "vulkan-dlss.yml").read_text(encoding="utf-8")
        self.assertIn("kernel32|user32|gdi32|advapi32", text)   # (the rule this script repeats)
        for ok in ("KERNEL32.dll", "USER32.dll", "GDI32.dll", "ADVAPI32.dll", "api-ms-win-crt-heap-l1-1-0.dll"):
            self.assertTrue(pf.ALLOWED_IMPORTS.match(ok), ok)
        for bad in ("ole32.dll", "shell32.dll", "vulkan-1.dll", "nvngx_dlss.dll"):
            self.assertFalse(pf.ALLOWED_IMPORTS.match(bad), bad)

    def test_fail_lines(self):
        out = "pass 0.1s  tests/test_a.py\nFAIL       0.2s  tests/test_b.py\nFAIL         12.0s  tests/test_c.py\n"
        self.assertEqual(set(pf.FAIL_LINE.findall(out)), {"tests/test_b.py", "tests/test_c.py"})


if __name__ == "__main__":
    unittest.main()
