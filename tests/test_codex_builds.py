"""Every plugin's real Codex build must pass `--check` on each Python that CI tests.

The unit-test matrix covers every supported Python down to macOS's python3 (3.9), so this catches a builder that
uses a newer standard-library API, which ruff's py39 syntax target cannot see.
"""

import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BUILDERS = sorted(REPO.glob("plugins/*/scripts/build_codex_skills.py"))


class CodexBuildTests(unittest.TestCase):
    def test_there_is_a_builder_to_check(self):
        self.assertTrue(BUILDERS, "no plugins/*/scripts/build_codex_skills.py found")

    def test_each_builder_check_passes_on_this_python(self):
        for builder in BUILDERS:
            with self.subTest(builder=builder.relative_to(REPO).as_posix()):
                proc = subprocess.run(
                    [sys.executable, "-B", str(builder), "--check"], capture_output=True, text=True, timeout=60
                )
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
