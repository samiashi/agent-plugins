"""The plugin scripts refuse Pythons older than 3.11 with a clear message."""

import ast
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = sorted(REPO.glob("plugins/*/scripts/*.py"))


def load(path):
    spec = importlib.util.spec_from_file_location(f"floor_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PythonFloorTests(unittest.TestCase):
    def test_there_are_plugin_scripts(self):
        self.assertTrue(SCRIPTS)

    def test_each_script_refuses_python_older_than_3_11(self):
        for script in SCRIPTS:
            for version in ((3, 9, 6), (3, 10, 16)):
                with self.subTest(script=script.name, version=version):
                    with mock.patch.object(sys, "version_info", version), self.assertRaises(SystemExit) as caught:
                        load(script)
                    self.assertIn("needs Python 3.11 or later", str(caught.exception.code))
                    self.assertIn("brew install python", str(caught.exception.code))

    def test_each_script_parses_with_python_3_9_grammar(self):
        # Partial and local only (ast's feature_version is best effort): CI's "Old Pythons get a
        # clear error" step runs the scripts on real 3.9 and 3.10. This catches `match` and similar early.
        for script in SCRIPTS:
            with self.subTest(script=script.name):
                ast.parse(script.read_text(), filename=str(script), feature_version=(3, 9))

    def test_each_script_loads_on_python_3_11_and_later(self):
        for script in SCRIPTS:
            with self.subTest(script=script.name), mock.patch.object(sys, "version_info", (3, 11, 0)):
                load(script)


if __name__ == "__main__":
    unittest.main()
