"""Tests for scripts/check_release_age.py, run against throwaway repositories with a fake registry."""

import contextlib
import datetime as dt
import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("check_release_age", REPO / "scripts/check_release_age.py")
check_release_age = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_release_age)

NOW = dt.datetime(2026, 11, 20, 12, 0, tzinfo=dt.UTC)
DAYS = check_release_age.COOLDOWN_DAYS


def iso(days_ago):
    return (NOW - dt.timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)


def npm_lock(packages):
    """packages: {name: version}."""
    entries = {"": {"name": "dev", "devDependencies": dict(packages)}}
    for name, version in packages.items():
        entries[f"node_modules/{name}"] = {
            "version": version,
            "resolved": f"https://registry.npmjs.org/{name}/-/{name.split('/')[-1]}-{version}.tgz",
            "dev": True,
        }
    return json.dumps({"name": "dev", "lockfileVersion": 3, "packages": entries}, indent=2)


def uv_lock(packages):
    """packages: {name: (version, upload_days_ago)}."""
    out = ['version = 1\nrevision = 3\nrequires-python = ">=3.11"\n']
    out.append('\n[[package]]\nname = "dev"\nversion = "0"\nsource = { virtual = "." }\n')
    for name, (version, days_ago) in packages.items():
        out.append(
            f'\n[[package]]\nname = "{name}"\nversion = "{version}"\n'
            f'source = {{ registry = "https://pypi.org/simple" }}\n'
            f'sdist = {{ url = "https://files.example/{name}-{version}.tar.gz", upload-time = "{iso(days_ago + 1)}" }}\n'
            f"wheels = [\n"
            f'    {{ url = "https://files.example/{name}-{version}-a.whl", upload-time = "{iso(days_ago)}" }},\n'
            f"]\n"
        )
    return "".join(out)


class Repo:
    """A repository with consistent cooldown settings, an npm lock, a uv lock and an exact .nvmrc."""

    def __init__(self, root):
        self.root = root
        self.write(
            ".github/dependabot.yml", "".join(f"  - x:\n    cooldown:\n      default-days: {DAYS}\n" for _ in range(3))
        )
        self.write(".github/zizmor.yml", f"rules:\n  dependabot-cooldown:\n    config:\n      days: {DAYS}\n")
        self.write(".npmrc", f"min-release-age={DAYS}\n")
        self.write("pyproject.toml", f'[tool.uv]\npackage = false\nexclude-newer = "{DAYS} days"\n')
        self.write(".nvmrc", "26.1.0\n")
        self.write("package-lock.json", npm_lock({"left-pad": "1.0.0"}))
        self.write("uv.lock", uv_lock({"ruff": ("0.1.0", 30)}))

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def init_git(self):
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "config", "user.email", "t@example.com")
        git(self.root, "config", "user.name", "t")
        self.commit("base")
        git(self.root, "checkout", "-q", "-b", "feature")

    def commit(self, message):
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", message)


class FakeRegistry:
    """Serves npm packuments and the Node release index; records which URLs were fetched."""

    def __init__(self):
        self.npm = {"left-pad": {"1.0.0": iso(400)}}
        self.node = {"26.1.0": 40}
        self.fetched = []

    def __call__(self, url):
        self.fetched.append(url)
        if url == check_release_age.NODE_INDEX:
            return [
                {"version": f"v{v}", "date": (NOW - dt.timedelta(days=d)).strftime("%Y-%m-%d")}
                for v, d in self.node.items()
            ]
        name = url.removeprefix(check_release_age.NPM_REGISTRY).replace("%2F", "/").replace("%2f", "/")
        if name not in self.npm:
            raise OSError(f"404 {url}")
        return {"name": name, "time": dict(self.npm[name])}


def run(root, registry, *argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = check_release_age.main(["--root", str(root), *argv], fetch_json=registry, now=NOW)
    return code, out.getvalue(), err.getvalue()


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.repo = Repo(self.root)
        self.registry = FakeRegistry()

    def tearDown(self):
        self._tmp.cleanup()

    def test_consistent_settings_pass_without_network(self):
        code, out, err = run(self.root, self.registry, "check")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.registry.fetched, [])

    def test_each_setting_must_equal_the_cooldown(self):
        cases = {
            ".github/dependabot.yml": "      default-days: 7\n",
            ".npmrc": "min-release-age=3\n",
            "pyproject.toml": '[tool.uv]\nexclude-newer = "9 days"\n',
            ".github/zizmor.yml": "      days: 5\n",
        }
        for rel, text in cases.items():
            with self.subTest(file=rel):
                original = (self.root / rel).read_text()
                self.repo.write(rel, text)
                code, _, err = run(self.root, self.registry, "check")
                self.assertEqual(code, 1)
                self.assertIn(rel, err)
                self.repo.write(rel, original)

    def test_missing_setting_fails(self):
        self.repo.write(".npmrc", "fund=false\n")
        code, _, err = run(self.root, self.registry, "check")
        self.assertEqual(code, 1)
        self.assertIn(".npmrc: no min-release-age", err)

    def test_nvmrc_must_be_an_exact_version(self):
        self.repo.write(".nvmrc", "26\n")
        code, _, err = run(self.root, self.registry, "check")
        self.assertEqual(code, 1)
        self.assertIn("exact Node version", err)


class LockTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.repo = Repo(self.root)
        self.repo.init_git()
        self.registry = FakeRegistry()

    def tearDown(self):
        self._tmp.cleanup()

    def check(self):
        return run(self.root, self.registry, "check", "--base", "main")

    def test_unchanged_locks_fetch_nothing(self):
        code, _, err = self.check()
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.registry.fetched, [])

    def test_new_npm_version_younger_than_cooldown_fails(self):
        self.registry.npm["left-pad"]["1.1.0"] = iso(DAYS - 1)
        self.repo.write("package-lock.json", npm_lock({"left-pad": "1.1.0"}))
        self.repo.commit("bump")
        code, _, err = self.check()
        self.assertEqual(code, 1)
        self.assertIn("left-pad@1.1.0", err)

    def test_new_npm_version_older_than_cooldown_passes(self):
        self.registry.npm["left-pad"]["1.1.0"] = iso(DAYS + 1)
        self.repo.write("package-lock.json", npm_lock({"left-pad": "1.1.0"}))
        self.repo.commit("bump")
        self.assertEqual(self.check()[0], 0)

    def test_scoped_npm_package_is_looked_up_by_encoded_name(self):
        self.registry.npm["@scope/tool"] = {"2.0.0": iso(DAYS - 2)}
        self.repo.write("package-lock.json", npm_lock({"left-pad": "1.0.0", "@scope/tool": "2.0.0"}))
        self.repo.commit("add scoped")
        code, _, err = self.check()
        self.assertEqual(code, 1)
        self.assertIn("@scope/tool@2.0.0", err)
        self.assertIn(check_release_age.NPM_REGISTRY + "@scope%2Ftool", self.registry.fetched)

    def test_unknown_npm_publish_time_fails_closed(self):
        self.repo.write("package-lock.json", npm_lock({"left-pad": "1.0.0", "ghost": "1.0.0"}))
        self.repo.commit("add ghost")
        code, _, err = self.check()
        self.assertEqual(code, 1)
        self.assertIn("ghost@1.0.0", err)

    def test_removed_npm_package_needs_no_check(self):
        self.repo.write("package-lock.json", npm_lock({}))
        self.repo.commit("remove")
        self.assertEqual(self.check()[0], 0)
        self.assertEqual(self.registry.fetched, [])

    def test_new_uv_version_younger_than_cooldown_fails_offline(self):
        self.repo.write("uv.lock", uv_lock({"ruff": ("0.2.0", DAYS - 1)}))
        self.repo.commit("bump ruff")
        code, _, err = self.check()
        self.assertEqual(code, 1)
        self.assertIn("ruff==0.2.0", err)
        self.assertEqual(self.registry.fetched, [])

    def test_young_uv_version_already_on_base_is_not_rechecked(self):
        # e.g. a security update merged through the bypass: only versions this branch adds count.
        git(self.root, "checkout", "-q", "main")
        self.repo.write("uv.lock", uv_lock({"ruff": ("0.1.0", 30), "zizmor": ("1.0.0", 1)}))
        self.repo.commit("security update on main")
        git(self.root, "checkout", "-q", "-b", "later")
        self.repo.write("README.md", "unrelated\n")
        self.repo.commit("unrelated")
        self.assertEqual(self.check()[0], 0)

    def test_new_uv_version_older_than_cooldown_passes(self):
        self.repo.write("uv.lock", uv_lock({"ruff": ("0.2.0", DAYS + 1)}))
        self.repo.commit("bump ruff")
        self.assertEqual(self.check()[0], 0)

    def test_changed_nvmrc_younger_than_cooldown_fails(self):
        self.registry.node["26.2.0"] = DAYS - 2
        self.repo.write(".nvmrc", "26.2.0\n")
        self.repo.commit("bump node")
        code, _, err = self.check()
        self.assertEqual(code, 1)
        self.assertIn("Node 26.2.0", err)

    def test_changed_nvmrc_to_unknown_version_fails(self):
        self.repo.write(".nvmrc", "26.9.9\n")
        self.repo.commit("bump node")
        code, _, err = self.check()
        self.assertEqual(code, 1)
        self.assertIn("not in the Node release index", err)

    def test_changed_nvmrc_older_than_cooldown_passes(self):
        self.registry.node["26.2.0"] = DAYS + 2
        self.repo.write(".nvmrc", "26.2.0\n")
        self.repo.commit("bump node")
        self.assertEqual(self.check()[0], 0)


if __name__ == "__main__":
    unittest.main()
