"""Tests for scripts/check_plugins.py, run against throwaway marketplace repositories."""

import contextlib
import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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


class GithubAuthorTests(unittest.TestCase):
    def author(self, returncode=0, stdout="", raises=None):
        result = subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")
        err = io.StringIO()
        with (
            mock.patch.object(check_plugins.subprocess, "run", return_value=result, side_effect=raises) as run,
            contextlib.redirect_stderr(err),
        ):
            return check_plugins.github_author("o/r", "abc1234567"), run, err.getvalue()

    def test_returns_the_login_and_whether_it_is_a_bot(self):
        self.assertEqual(self.author(stdout="alice\tUser\n")[0], ("alice", False))
        self.assertEqual(self.author(stdout="renovate\tBot\n")[0], ("renovate", True))

    def test_asks_for_the_commit_author_not_the_committer(self):
        _, run, _ = self.author(stdout="alice\tUser\n")
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[:3], ["gh", "api", "repos/o/r/commits/abc1234567"])
        self.assertTrue(cmd[cmd.index("--jq") + 1].startswith(".author "))
        self.assertEqual(run.call_args.kwargs["timeout"], check_plugins.LOOKUP_TIMEOUT)

    def test_no_linked_account_gives_none_quietly(self):
        found, _, err = self.author(stdout="")
        self.assertEqual((found, err), (None, ""))

    def test_failures_give_none_with_a_warning(self):
        cases = {
            "non-zero exit": {"returncode": 1, "stdout": "partial"},
            "gh missing": {"raises": FileNotFoundError("gh")},
            "hang": {"raises": subprocess.TimeoutExpired(["gh"], 20)},
        }
        for name, kwargs in cases.items():
            with self.subTest(name):
                found, _, err = self.author(**kwargs)
                self.assertIsNone(found)
                self.assertIn("abc1234567", err)


class DefaultRepoTests(unittest.TestCase):
    def repo_for(self, origin, env=None):
        with (
            mock.patch.dict(check_plugins.os.environ, env or {}, clear=True),
            mock.patch.object(check_plugins, "git", return_value=origin + "\n"),
        ):
            return check_plugins.default_repo(Path("."))

    def test_github_repository_wins(self):
        self.assertEqual(self.repo_for("git@github.com:x/y.git", {"GITHUB_REPOSITORY": "o/r"}), "o/r")

    def test_parses_github_origins(self):
        for origin in (
            "https://github.com/o/r.git",
            "https://github.com/o/r",
            "https://github.com/o/r/",
            "git@github.com:o/r.git",
            "ssh://git@github.com/o/r",
        ):
            with self.subTest(origin=origin):
                self.assertEqual(self.repo_for(origin), "o/r")

    def test_other_origins_ask_for_repo(self):
        for origin in ("git@github-work:o/r.git", "https://gitlab.com/o/r.git"):
            with self.subTest(origin=origin), self.assertRaises(SystemExit) as caught:
                self.repo_for(origin)
            self.assertIn("--repo", str(caught.exception))


class ReleaseNotesTests(unittest.TestCase):
    """Contributors come from commits that changed the plugin since its previous release tag."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.mk = Marketplace(self.root, version="1.0.0")
        self.mk.init_git()
        git(self.root, "tag", "demo--v1.0.0")
        sections = "".join(f"## [{v}] - 2026-01-01\n\n- Notes for {v}.\n\n" for v in ("1.1.0", "1.0.1", "1.0.0"))
        (self.mk.plugin / "CHANGELOG.md").write_text(f"# Changelog\n\n{sections}")
        self.found = {}  # commit sha -> (login, is_bot); absent: GitHub links no account
        self.lookups = []
        self.edits = 0

    def tearDown(self):
        self._tmp.cleanup()

    def commit_as(self, name, rel="plugins/demo/x.md", email=None, found=None, message="change"):
        self.edits += 1
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"edit {self.edits}\n")
        git(self.root, "add", "-A")
        email = email or f"{name.split()[0].lower()}@example.com"
        cmd = ["git", "-C", str(self.root), "-c", f"user.name={name}", "-c", f"user.email={email}"]
        subprocess.run([*cmd, "commit", "-q", "-m", message], check=True, capture_output=True)
        sha = subprocess.run(["git", "-C", str(self.root), "rev-parse", "HEAD"], capture_output=True, text=True).stdout
        if found:
            self.found[sha.strip()] = found
        return sha.strip()

    def lookup(self, repo, sha):
        self.lookups.append((repo, sha))
        return self.found.get(sha)

    def people(self, version="1.1.0", *extra):
        code, out, err = self.notes(version, *extra)
        self.assertEqual(code, 0, err)
        if "## Contributors" not in out:
            return []
        return [line[2:] for line in out.split("## Contributors")[1].splitlines() if line.startswith("- ")]

    def notes(self, version, *extra):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = check_plugins.main(
                ["--root", str(self.root), "release-notes", "demo", version, "--repo", "o/r", *extra],
                lookup=self.lookup,
            )
        return code, out.getvalue(), err.getvalue()

    def test_lists_people_who_changed_the_plugin_since_its_previous_release(self):
        self.commit_as("Alice", found=("alice", False))
        self.commit_as("Bob", found=("Bob", False))
        self.commit_as("Carol", rel="README.md", found=("carol", False))  # outside the plugin
        self.assertEqual(self.people(), ["@alice", "@Bob"])
        self.assertTrue(all(repo == "o/r" for repo, _ in self.lookups))

    def test_changelog_section_comes_first(self):
        self.commit_as("Alice", found=("alice", False))
        code, out, _ = self.notes("1.1.0")
        self.assertEqual(code, 0)
        changelog, people = out.split("## Contributors")
        self.assertIn("- Notes for 1.1.0.", changelog)
        self.assertIn("@alice", people)

    def test_commits_before_the_previous_release_are_excluded(self):
        git(self.root, "tag", "-d", "demo--v1.0.0")
        self.commit_as("Early", found=("early", False))
        git(self.root, "tag", "demo--v1.0.0")
        self.commit_as("Later", found=("later", False))
        self.assertEqual(self.people(), ["@later"])

    def test_previous_release_is_chosen_by_version_not_by_name(self):
        for version in ("1.9.0", "1.10.0"):
            git(self.root, "tag", f"demo--v{version}")
        git(self.root, "tag", "other--v9.0.0")
        self.assertEqual(check_plugins.previous_tag(self.root, "demo", "1.11.0"), "demo--v1.10.0")
        self.assertEqual(check_plugins.previous_tag(self.root, "demo", "1.10.0"), "demo--v1.9.0")
        self.assertIsNone(check_plugins.previous_tag(self.root, "demo", "1.0.0"))

    def test_first_release_counts_every_commit_up_to_the_target(self):
        git(self.root, "tag", "-d", "demo--v1.0.0")
        target = self.commit_as("Alice", found=("alice", False))
        self.commit_as("After", found=("after", False))
        self.assertIn("@alice", self.people("1.0.0", "--target", target))
        self.assertNotIn("@after", self.people("1.0.0", "--target", target))

    def test_bots_are_left_out_by_account_type_or_name(self):
        self.commit_as("Renovate", found=("renovate", True))
        self.commit_as("dependabot[bot]", email="49699333+dependabot[bot]@users.noreply.github.com")
        self.commit_as("github-actions[bot]", email="actions@github.com")
        self.commit_as("Alice", found=("alice", False))
        self.assertEqual(self.people(), ["@alice"])

    def test_one_person_is_listed_once_whatever_their_name_or_lookup(self):
        self.commit_as("Alice Smith", email="alice@example.com", found=("alice", False))
        self.commit_as("A. Smith", email="ALICE@example.com")  # lookup failed or no link for this commit
        self.assertEqual(self.people(), ["@alice"])

    def test_two_people_with_the_same_name_are_kept_apart(self):
        self.commit_as("Sam Lee", email="sam1@example.com")
        self.commit_as("Sam Lee", email="sam2@example.com")
        self.assertEqual(self.people(), ["`Sam Lee`", "`Sam Lee`"])

    def test_noreply_emails_become_logins(self):
        self.commit_as("Dana", email="1234+dana-x@users.noreply.github.com")
        self.assertEqual(self.people(), ["@dana-x"])

    def test_co_authors_are_credited(self):
        self.commit_as(
            "Alice",
            found=("alice", False),
            message="squash\n\nCo-authored-by: Bob Builder <bob@example.com>\n"
            "Co-authored-by: Cy <99+cyborg@users.noreply.github.com>\n",
        )
        self.assertEqual(self.people(), ["@alice", "`Bob Builder`", "@cyborg"])

    def test_unlinked_names_cannot_link_or_mention(self):
        self.commit_as("@octocat [click](https://evil.example) `x`", email="evil@example.com")
        self.assertEqual(self.people(), ["`@octocat [click](https://evil.example) 'x'`"])

    def test_a_name_that_is_empty_once_cleaned_still_shows(self):
        self.assertEqual(check_plugins.display_name(" \u2028 "), "`(no name)`")

    def test_names_with_line_separators_do_not_break_the_notes(self):
        self.commit_as("Ann\u2028Lee", email="ann@example.com")
        self.assertEqual(self.people(), ["`Ann Lee`"])

    def test_shallow_clone_is_refused(self):
        with mock.patch.object(check_plugins, "git", side_effect=lambda root, *args: "true\n"):
            with self.assertRaises(SystemExit) as caught:
                check_plugins.contributors(self.root, "demo", "1.1.0", "HEAD", "o/r", self.lookup)
        self.assertIn("shallow", str(caught.exception))

    def test_no_contributors_section_without_plugin_commits(self):
        git(self.root, "checkout", "-q", "--", ".")  # drop the uncommitted changelog edit
        git(self.root, "tag", "-f", "demo--v1.0.0")
        self.mk.set_version("1.0.1")  # edited but not committed
        self.assertNotIn("## Contributors", self.notes("1.0.1")[1])


if __name__ == "__main__":
    unittest.main()
