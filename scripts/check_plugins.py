#!/usr/bin/env python3
"""Check the marketplace catalogs and plugins, and find releases to publish.

Commands:
  check [--base REF]     Check every plugin. With --base, also require a version bump
                         for each plugin whose shipped files changed since the merge base
                         with REF, so users actually receive the change.
  releases               Print JSON [{plugin, version, tag}] for versions without a tag.
  notes PLUGIN VERSION   Print that version's changelog section.
  release-notes PLUGIN VERSION [--target REF] [--repo OWNER/NAME]
                         Print the release notes: the changelog section, then a Contributors
                         list. The list has everyone whose commits (co-authors included, bots
                         excluded) changed the plugin's directory since its previous release
                         tag, up to REF (default HEAD): @login for a linked account, otherwise
                         the git name.

A plugin's shipped files are everything under its directory except README.md and
CHANGELOG.md. Tags look like <plugin>--v<version>.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLAUDE_CATALOG = Path(".claude-plugin/marketplace.json")
CODEX_CATALOG = Path(".agents/plugins/marketplace.json")
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
UNSHIPPED = {"README.md", "CHANGELOG.md"}


def load(root, path):
    return json.loads((root / path).read_text())


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


def version_key(version):
    return tuple(int(part) for part in version.split("."))


def tag_for(plugin, version):
    return f"{plugin}--v{version}"


def plugin_dirs(root):
    """Map each plugin name in the Claude catalog to its directory."""
    catalog = load(root, CLAUDE_CATALOG)
    result = {}
    for entry in catalog.get("plugins", []):
        source = entry.get("source")
        if isinstance(source, str):
            result[entry["name"]] = root / source
    return result


def changelog_section(path, version):
    """Return the body of the `## [version]` section, or None."""
    text = path.read_text() if path.exists() else ""
    match = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## |^\[[^\]]+\]: |\Z)", text, re.M | re.S)
    return match.group(1).strip() if match else None


def check_catalogs(root):
    errors = []
    claude = load(root, CLAUDE_CATALOG)
    codex = load(root, CODEX_CATALOG)
    if claude.get("name") != codex.get("name"):
        errors.append(f"marketplace names differ: Claude {claude.get('name')!r}, Codex {codex.get('name')!r}")

    claude_entries = {e["name"]: e for e in claude.get("plugins", [])}
    codex_entries = {e["name"]: e for e in codex.get("plugins", [])}
    if set(claude_entries) != set(codex_entries):
        errors.append(
            f"catalogs list different plugins: Claude {sorted(claude_entries)}, Codex {sorted(codex_entries)}"
        )

    for name, entry in claude_entries.items():
        if "version" in entry:
            errors.append(f"{name}: set the version in plugin.json only, not in the Claude catalog entry")
        if not isinstance(entry.get("source"), str):
            errors.append(f"{name}: Claude catalog source must be a relative path inside this repository")
    for name, entry in codex_entries.items():
        source = entry.get("source") or {}
        if source.get("source") != "local" or not str(source.get("path", "")).startswith("./"):
            errors.append(f"{name}: Codex catalog source must be local with a ./ path")
        elif name in claude_entries and Path(source["path"]) != Path(claude_entries[name].get("source", "")):
            errors.append(f"{name}: Claude and Codex catalogs point at different directories")
        policy = entry.get("policy") or {}
        for field in ("installation", "authentication"):
            if not policy.get(field):
                errors.append(f"{name}: Codex catalog entry needs policy.{field}")
        if not entry.get("category"):
            errors.append(f"{name}: Codex catalog entry needs a category")
    return errors


def check_plugin(name, directory):
    errors = []
    if not directory.is_dir():
        return [f"{name}: source directory {directory} does not exist"]
    claude_manifest = directory / ".claude-plugin/plugin.json"
    if not claude_manifest.exists():
        return [f"{name}: missing .claude-plugin/plugin.json"]
    manifest = json.loads(claude_manifest.read_text())
    version = manifest.get("version", "")
    if manifest.get("name") != name:
        errors.append(f"{name}: plugin.json name is {manifest.get('name')!r}")
    if not SEMVER.match(version):
        errors.append(f"{name}: version {version!r} is not MAJOR.MINOR.PATCH")

    codex_manifest = directory / ".codex-plugin/plugin.json"
    if codex_manifest.exists():
        codex = json.loads(codex_manifest.read_text())
        if codex.get("name") != name:
            errors.append(f"{name}: .codex-plugin/plugin.json name is {codex.get('name')!r}")
        if codex.get("version") != version:
            errors.append(f"{name}: Codex manifest version {codex.get('version')!r} differs from {version!r}")
    else:
        errors.append(f"{name}: missing .codex-plugin/plugin.json")

    if SEMVER.match(version) and changelog_section(directory / "CHANGELOG.md", version) is None:
        errors.append(f"{name}: CHANGELOG.md has no '## [{version}]' section")

    build = directory / "scripts/build_codex_skills.py"
    if build.exists():
        proc = subprocess.run([sys.executable, str(build), "--check"], capture_output=True, text=True)
        if proc.returncode:
            errors.append(f"{name}: Codex skills are stale or fail to build: {(proc.stdout + proc.stderr).strip()}")
    return errors


def base_version(root, base, directory):
    path = directory.relative_to(root) / ".claude-plugin/plugin.json"
    try:
        return json.loads(git(root, "show", f"{base}:{path.as_posix()}")).get("version")
    except subprocess.CalledProcessError:
        return None  # the plugin is new on this branch


def check_bumps(root, base_ref, dirs):
    errors = []
    base = git(root, "merge-base", base_ref, "HEAD").strip()
    changed = [Path(p) for p in git(root, "diff", "--name-only", base, "HEAD").splitlines()]
    for name, directory in dirs.items():
        rel = directory.relative_to(root)
        shipped = [p for p in changed if p.is_relative_to(rel) and p.relative_to(rel).as_posix() not in UNSHIPPED]
        if not shipped:
            continue
        before = base_version(root, base, directory)
        now = json.loads((directory / ".claude-plugin/plugin.json").read_text()).get("version", "")
        if before is None or not (SEMVER.match(before) and SEMVER.match(now)):
            continue
        if version_key(now) <= version_key(before):
            errors.append(
                f"{name}: {len(shipped)} shipped file(s) changed but the version is still {now} "
                f"(base {before}); bump it in both manifests and add a changelog entry"
            )
    return errors


def cmd_check(root, base):
    dirs = plugin_dirs(root)
    errors = check_catalogs(root)
    for name, directory in dirs.items():
        errors += check_plugin(name, directory)
    if base:
        errors += check_bumps(root, base, dirs)
    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    if not errors:
        print(f"ok: {len(dirs)} plugin(s) checked")
    return 1 if errors else 0


def cmd_releases(root):
    tags = set(git(root, "tag", "--list").split())
    pending = []
    for name, directory in plugin_dirs(root).items():
        version = json.loads((directory / ".claude-plugin/plugin.json").read_text())["version"]
        if tag_for(name, version) not in tags:
            pending.append({"plugin": name, "version": version, "tag": tag_for(name, version)})
    print(json.dumps(pending))
    return 0


def cmd_notes(root, plugin, version):
    directory = plugin_dirs(root).get(plugin)
    section = changelog_section(directory / "CHANGELOG.md", version) if directory else None
    if section is None:
        print(f"error: no changelog section for {plugin} {version}", file=sys.stderr)
        return 1
    print(section)
    return 0


def previous_tag(root, plugin, version):
    """The plugin's newest release tag older than `version`, or None for its first release."""
    prefix = f"{plugin}--v"
    older = [
        (version_key(tag[len(prefix) :]), tag)
        for tag in git(root, "tag", "--list", f"{prefix}*").split()
        if SEMVER.match(tag[len(prefix) :]) and version_key(tag[len(prefix) :]) < version_key(version)
    ]
    return max(older)[1] if older else None


LOOKUP_TIMEOUT = 20  # seconds per GitHub commit lookup
FIELD, CO_AUTHORS = "\x1f", "\x1e"  # separators no author name can contain; records are NUL-separated
NOREPLY = re.compile(r"^(?:\d+\+)?([A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)@users\.noreply\.github\.com$", re.I)
TRAILER = re.compile(r"^\s*(.*?)\s*<([^<>]+)>\s*$")
ORIGIN = re.compile(r"github\.com[:/]+([^/:]+/[^/]+?)(?:\.git)?/*$")


def github_author(repo, sha):
    """(login, is_bot) for the account GitHub links to a commit's author, or None.

    None also when GitHub can't be asked or doesn't answer in time, for example for a commit it
    doesn't have yet. That costs the person their @mention, with a warning, rather than the release.
    """
    try:
        proc = subprocess.run(
            ["gh", "api", f"repos/{repo}/commits/{sha}", "--jq", '.author // empty | "\\(.login)\\t\\(.type)"'],
            capture_output=True,
            text=True,
            timeout=LOOKUP_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(
            f"warning: couldn't look up commit {sha[:10]} on GitHub ({exc}); listing its author by name",
            file=sys.stderr,
        )
        return None
    if proc.returncode:
        print(f"warning: GitHub lookup of commit {sha[:10]} failed; listing its author by name", file=sys.stderr)
        return None
    login, _, kind = proc.stdout.strip().partition("\t")
    return (login, kind == "Bot") if login else None


def default_repo(root):
    if os.environ.get("GITHUB_REPOSITORY"):
        return os.environ["GITHUB_REPOSITORY"]
    url = git(root, "remote", "get-url", "origin").strip()
    match = ORIGIN.search(url)
    if not match:
        raise SystemExit(f"error: can't tell the GitHub repository from origin {url!r}; pass --repo OWNER/NAME")
    return match.group(1)


def display_name(name):
    """A git name as inert text: one line, in code formatting, so it can't link to or mention anyone."""
    cleaned = " ".join(name.replace("`", "'").split())
    return f"`{cleaned}`" if cleaned else "`(no name)`"


def contributors(root, plugin, version, target, repo, lookup=github_author):
    """Everyone whose commits changed the plugin since its previous release, bots excluded.

    People are told apart by email. Commit authors and Co-authored-by trailers both count. Each
    person is shown as @login when GitHub links an account (or their email is a GitHub noreply
    address), and otherwise by their git name.
    """
    if git(root, "rev-parse", "--is-shallow-repository").strip() == "true":
        raise SystemExit(
            "error: this is a shallow clone, so release history is incomplete; run `git fetch --unshallow`"
        )
    directory = plugin_dirs(root)[plugin].relative_to(root).as_posix()
    previous = previous_tag(root, plugin, version)
    revisions = f"{previous}..{target}" if previous else target
    trailers = "%(trailers:key=Co-authored-by,valueonly,separator=%x1e)"
    log = git(root, "log", "-z", f"--format=%H%x1f%ae%x1f%an%x1f{trailers}", revisions, "--", directory)

    people = {}  # lower-cased email -> {"name", "login", "bot"}

    def add(email, name, login=None, bot=False):
        noreply = NOREPLY.match(email)
        login = login or (noreply.group(1) if noreply else None)
        person = people.setdefault(email.lower(), {"name": name, "login": None, "bot": False})
        person["login"] = person["login"] or login
        person["bot"] = person["bot"] or bot or (login or name).endswith("[bot]")

    for record in log.split("\0"):
        if not record.strip():
            continue
        sha, email, name, co_authors = record.lstrip("\n").split(FIELD, 3)
        found = lookup(repo, sha)
        add(email, name, *(found or ()))
        for trailer in co_authors.split(CO_AUTHORS):
            match = TRAILER.match(trailer)
            if match:
                add(match.group(2), match.group(1))

    shown = {}
    for email, person in people.items():
        if person["bot"]:
            continue
        if person["login"]:
            shown.setdefault(("login", person["login"].lower()), f"@{person['login']}")
        else:
            shown.setdefault(("email", email), display_name(person["name"]))
    return sorted(shown.values(), key=lambda s: s.strip("`@").lower())


def cmd_release_notes(root, plugin, version, target, repo, lookup):
    directory = plugin_dirs(root).get(plugin)
    section = changelog_section(directory / "CHANGELOG.md", version) if directory else None
    if section is None:
        print(f"error: no changelog section for {plugin} {version}", file=sys.stderr)
        return 1
    people = contributors(root, plugin, version, target, repo or default_repo(root), lookup)
    print(section)
    if people:
        print("\n## Contributors\n")
        print("\n".join(f"- {person}" for person in people))
    return 0


def main(argv=None, lookup=github_author):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check")
    check.add_argument("--base", help="git ref to compare against, e.g. origin/main")
    sub.add_parser("releases")
    notes = sub.add_parser("notes")
    notes.add_argument("plugin")
    notes.add_argument("version")
    release_notes = sub.add_parser("release-notes")
    release_notes.add_argument("plugin")
    release_notes.add_argument("version")
    release_notes.add_argument("--target", default="HEAD", help="the commit being released")
    release_notes.add_argument("--repo", help="owner/name on GitHub (default: $GITHUB_REPOSITORY or origin)")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.command == "check":
        return cmd_check(root, args.base)
    if args.command == "releases":
        return cmd_releases(root)
    if args.command == "release-notes":
        return cmd_release_notes(root, args.plugin, args.version, args.target, args.repo, lookup)
    return cmd_notes(root, args.plugin, args.version)


if __name__ == "__main__":
    sys.exit(main())
