"""Tests for scripts/check_plugins.py, run against throwaway marketplace repositories."""

import contextlib
import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("check_plugins", REPO / "scripts/check_plugins.py")
check_plugins = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_plugins)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)


class Marketplace:
    """A minimal two-catalog marketplace with one plugin, in a temp directory."""

    def __init__(self, root, name="demo", version="1.0.0"):
        self.root = root
        self.name = name
        write_json(
            root / ".claude-plugin/marketplace.json",
            {"name": "mk", "owner": {"name": "t"}, "plugins": [{"name": name, "source": f"./plugins/{name}"}]},
        )
        write_json(
            root / ".agents/plugins/marketplace.json",
            {
                "name": "mk",
                "plugins": [
                    {
                        "name": name,
                        "source": {"source": "local", "path": f"./plugins/{name}"},
                        "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                        "category": "Developer Tools",
                    }
                ],
            },
        )
        self.set_version(version)
        (self.plugin / "skills/a").mkdir(parents=True, exist_ok=True)
        (self.plugin / "skills/a/SKILL.md").write_text("one\n")

    @property
    def plugin(self):
        return self.root / "plugins" / self.name

    def set_version(self, version, codex_version=None, changelog=True):
        write_json(self.plugin / ".claude-plugin/plugin.json", {"name": self.name, "version": version})
        write_json(self.plugin / ".codex-plugin/plugin.json", {"name": self.name, "version": codex_version or version})
        if changelog:
            (self.plugin / "CHANGELOG.md").write_text(
                f"# Changelog\n\n## [Unreleased]\n\n## [{version}] - 2026-01-01\n\n- Notes for {version}.\n\n"
                f"## [0.0.1] - 2025-01-01\n\n- Old.\n\n[{version}]: https://example.com\n"
            )

    def init_git(self):
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "config", "user.email", "t@example.com")
        git(self.root, "config", "user.name", "t")
        self.commit("base")
        git(self.root, "checkout", "-q", "-b", "feature")

    def commit(self, message):
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", message)


def run(root, *argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = check_plugins.main(["--root", str(root), *argv])
    return code, out.getvalue(), err.getvalue()


class CheckTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.mk = Marketplace(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def assert_error(self, fragment):
        code, _, err = run(self.root, "check")
        self.assertEqual(code, 1, err)
        self.assertIn(fragment, err)

    def test_valid_marketplace_passes(self):
        code, out, err = run(self.root, "check")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("1 plugin(s) checked", out)

    def test_codex_manifest_version_must_match(self):
        self.mk.set_version("1.0.0", codex_version="1.0.1")
        self.assert_error("Codex manifest version '1.0.1' differs from '1.0.0'")

    def test_version_must_be_plain_semver(self):
        self.mk.set_version("1.0")
        self.assert_error("is not MAJOR.MINOR.PATCH")

    def test_changelog_needs_a_section_for_the_version(self):
        self.mk.set_version("1.1.0", changelog=False)
        self.assert_error("CHANGELOG.md has no '## [1.1.0]' section")

    def test_catalog_entry_must_not_carry_a_version(self):
        catalog = self.root / ".claude-plugin/marketplace.json"
        data = json.loads(catalog.read_text())
        data["plugins"][0]["version"] = "1.0.0"
        write_json(catalog, data)
        self.assert_error("set the version in plugin.json only")

    def test_catalogs_must_list_the_same_plugins(self):
        catalog = self.root / ".agents/plugins/marketplace.json"
        data = json.loads(catalog.read_text())
        data["plugins"][0]["name"] = "other"
        write_json(catalog, data)
        self.assert_error("catalogs list different plugins")

    def test_marketplace_names_must_match(self):
        catalog = self.root / ".agents/plugins/marketplace.json"
        data = json.loads(catalog.read_text())
        data["name"] = "different"
        write_json(catalog, data)
        self.assert_error("marketplace names differ")

    def test_codex_entry_needs_policy_and_category(self):
        catalog = self.root / ".agents/plugins/marketplace.json"
        data = json.loads(catalog.read_text())
        del data["plugins"][0]["policy"]["authentication"]
        del data["plugins"][0]["category"]
        write_json(catalog, data)
        code, _, err = run(self.root, "check")
        self.assertEqual(code, 1)
        self.assertIn("needs policy.authentication", err)
        self.assertIn("needs a category", err)

    def test_stale_codex_build_fails(self):
        script = self.mk.plugin / "scripts/build_codex_skills.py"
        script.parent.mkdir(parents=True)
        script.write_text("import sys\nprint('codex-skills/ is stale')\nsys.exit(1)\n")
        self.assert_error("Codex skills are stale")


class BumpTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.mk = Marketplace(self.root)
        self.mk.init_git()

    def tearDown(self):
        self._tmp.cleanup()

    def check_against_main(self):
        return run(self.root, "check", "--base", "main")

    def test_shipped_change_without_bump_fails(self):
        (self.mk.plugin / "skills/a/SKILL.md").write_text("two\n")
        self.mk.commit("change skill")
        code, _, err = self.check_against_main()
        self.assertEqual(code, 1)
        self.assertIn("version is still 1.0.0 (base 1.0.0)", err)

    def test_shipped_change_with_bump_passes(self):
        (self.mk.plugin / "skills/a/SKILL.md").write_text("two\n")
        self.mk.set_version("1.1.0")
        self.mk.commit("change skill and bump")
        code, _, err = self.check_against_main()
        self.assertEqual((code, err), (0, ""))

    def test_version_going_backwards_fails(self):
        (self.mk.plugin / "skills/a/SKILL.md").write_text("two\n")
        self.mk.set_version("0.9.0")
        self.mk.commit("downgrade")
        code, _, err = self.check_against_main()
        self.assertEqual(code, 1)
        self.assertIn("version is still 0.9.0", err)

    def test_readme_and_changelog_changes_need_no_bump(self):
        (self.mk.plugin / "README.md").write_text("docs\n")
        with (self.mk.plugin / "CHANGELOG.md").open("a") as fh:
            fh.write("\n")
        self.mk.commit("docs only")
        code, _, err = self.check_against_main()
        self.assertEqual((code, err), (0, ""))

    def test_changes_outside_plugins_need_no_bump(self):
        (self.root / "README.md").write_text("root docs\n")
        self.mk.commit("root docs")
        self.assertEqual(self.check_against_main()[0], 0)

    def test_new_plugin_needs_no_bump(self):
        git(self.root, "checkout", "-q", "main")
        git(self.root, "rm", "-r", "-q", "plugins")
        self.mk.commit("no plugin on main")
        git(self.root, "checkout", "-q", "-b", "add-plugin")
        Marketplace(self.root)
        self.mk.commit("add plugin")
        self.assertEqual(self.check_against_main()[0], 0)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.mk = Marketplace(self.root, version="1.2.0")
        self.mk.init_git()

    def tearDown(self):
        self._tmp.cleanup()

    def test_untagged_version_is_pending(self):
        code, out, _ = run(self.root, "releases")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), [{"plugin": "demo", "version": "1.2.0", "tag": "demo--v1.2.0"}])

    def test_tagged_version_is_not_pending(self):
        git(self.root, "tag", "demo--v1.2.0")
        self.assertEqual(json.loads(run(self.root, "releases")[1]), [])

    def test_notes_print_only_that_versions_section(self):
        code, out, _ = run(self.root, "notes", "demo", "1.2.0")
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "- Notes for 1.2.0.")

    def test_notes_for_unknown_version_fail(self):
        code, _, err = run(self.root, "notes", "demo", "3.0.0")
        self.assertEqual(code, 1)
        self.assertIn("no changelog section", err)


if __name__ == "__main__":
    unittest.main()
