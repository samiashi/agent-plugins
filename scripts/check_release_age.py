#!/usr/bin/env python3
"""Enforce the release-age cooldown on what CI actually installs.

npm's `min-release-age`, uv's `exclude-newer` and Dependabot's `cooldown` only apply when
a version is first resolved. `npm ci` and `uv sync --locked` install whatever the lockfiles
pin, and setup-node installs whatever `.nvmrc` names. This script closes that gap.

  check              Every cooldown setting equals COOLDOWN_DAYS, and `.nvmrc` is an
                     exact Node version. Needs no network.
  check --base REF   Also: every npm or uv package version that the lockfiles add since
                     the merge base with REF, and a changed `.nvmrc`, were published at
                     least COOLDOWN_DAYS ago. npm and Node publish times come from their
                     registries; uv.lock records its own upload times.

Dependabot security updates skip the cooldown by design, so this check fails on them.
An admin can merge such a pull request through the ruleset bypass.
"""

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

COOLDOWN_DAYS = 6
ROOT = Path(__file__).resolve().parent.parent
NPM_REGISTRY = "https://registry.npmjs.org/"
NODE_INDEX = "https://nodejs.org/dist/index.json"

# (file, pattern, description). Each pattern's first group is a number of days.
SETTINGS = [
    (".github/dependabot.yml", r"^\s*default-days:\s*(\d+)\s*$", "Dependabot cooldown default-days"),
    (".github/zizmor.yml", r"^\s*days:\s*(\d+)\s*$", "zizmor dependabot-cooldown days"),
    (".npmrc", r"^\s*min-release-age\s*=\s*(\d+)\s*$", "min-release-age"),
    ("pyproject.toml", r'^\s*exclude-newer\s*=\s*"(\d+) days?"\s*$', "uv exclude-newer"),
]
EXACT_VERSION = re.compile(r"^\d+\.\d+\.\d+$")


def fetch_json(url):
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "check_release_age"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def parse_time(value):
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def git_show(root, ref, path):
    proc = subprocess.run(["git", "-C", str(root), "show", f"{ref}:{path}"], capture_output=True, text=True)
    return proc.stdout if proc.returncode == 0 else None


def check_settings(root):
    errors = []
    for rel, pattern, label in SETTINGS:
        path = root / rel
        values = re.findall(pattern, path.read_text(), re.M) if path.exists() else []
        if not values:
            errors.append(f"{rel}: no {label} setting found")
        for value in values:
            if int(value) != COOLDOWN_DAYS:
                errors.append(f"{rel}: {label} is {value}, but the cooldown is {COOLDOWN_DAYS} days")
    nvmrc = root / ".nvmrc"
    version = nvmrc.read_text().strip() if nvmrc.exists() else ""
    if not EXACT_VERSION.match(version):
        errors.append(f".nvmrc: {version!r} is not an exact Node version (MAJOR.MINOR.PATCH)")
    return errors


def npm_entries(text):
    """(name, version) for every registry package in a package-lock.json."""
    if not text:
        return set()
    entries = set()
    for key, meta in json.loads(text).get("packages", {}).items():
        if not key or "version" not in meta or meta.get("link"):
            continue
        if not str(meta.get("resolved", "")).startswith(NPM_REGISTRY):
            continue
        name = meta.get("name") or key.rsplit("node_modules/", 1)[-1]
        entries.add((name, meta["version"]))
    return entries


def uv_entries(text):
    """{(name, version): newest upload time} for every registry package in a uv.lock."""
    if not text:
        return {}
    entries = {}
    for block in re.split(r"^\[\[package\]\]\s*$", text, flags=re.M)[1:]:
        name = re.search(r'^name = "([^"]+)"', block, re.M)
        version = re.search(r'^version = "([^"]+)"', block, re.M)
        uploads = re.findall(r'upload-time = "([^"]+)"', block)
        if name and version and "registry" in block.split("\n[", 1)[0] and uploads:
            entries[(name.group(1), version.group(1))] = max(parse_time(u) for u in uploads)
    return entries


def check_new_versions(root, base_ref, now, fetch):
    errors = []
    base = subprocess.run(
        ["git", "-C", str(root), "merge-base", base_ref, "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    cutoff = now - dt.timedelta(days=COOLDOWN_DAYS)

    def too_new(what, published):
        if published > cutoff:
            age = (now - published).total_seconds() / 86400
            errors.append(f"{what} was published {age:.1f} days ago; the cooldown is {COOLDOWN_DAYS} days")

    head_lock = (root / "package-lock.json").read_text() if (root / "package-lock.json").exists() else ""
    for name, version in sorted(npm_entries(head_lock) - npm_entries(git_show(root, base, "package-lock.json"))):
        try:
            published = fetch(NPM_REGISTRY + urllib.parse.quote(name, safe="@")).get("time", {}).get(version)
        except (OSError, ValueError) as exc:
            published, reason = None, exc
        else:
            reason = "the registry has no publish time for it"
        if published is None:
            errors.append(f"npm {name}@{version}: can't confirm its publish date ({reason})")
        else:
            too_new(f"npm {name}@{version}", parse_time(published))

    head_uv = (root / "uv.lock").read_text() if (root / "uv.lock").exists() else ""
    base_uv = uv_entries(git_show(root, base, "uv.lock"))
    for (name, version), uploaded in sorted(uv_entries(head_uv).items()):
        if (name, version) not in base_uv:
            too_new(f"uv {name}=={version}", uploaded)

    nvmrc = (root / ".nvmrc").read_text().strip() if (root / ".nvmrc").exists() else ""
    base_nvmrc = (git_show(root, base, ".nvmrc") or "").strip()
    if EXACT_VERSION.match(nvmrc) and nvmrc != base_nvmrc:
        try:
            dates = {r["version"].lstrip("v"): r["date"] for r in fetch(NODE_INDEX)}
        except (OSError, ValueError) as exc:
            errors.append(f".nvmrc: can't read the Node release index ({exc})")
        else:
            if nvmrc not in dates:
                errors.append(f".nvmrc: Node {nvmrc} is not in the Node release index")
            else:
                too_new(f".nvmrc: Node {nvmrc}", dt.datetime.fromisoformat(dates[nvmrc]).replace(tzinfo=dt.UTC))
    return errors


def main(argv=None, fetch_json=fetch_json, now=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check")
    check.add_argument("--base", help="git ref to compare the lockfiles and .nvmrc against, e.g. origin/main")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    errors = check_settings(root)
    if args.base:
        errors += check_new_versions(root, args.base, now or dt.datetime.now(dt.UTC), fetch_json)
    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    if not errors:
        scope = f"new versions since {args.base} are" if args.base else "settings are"
        print(f"ok: cooldown {scope} at least {COOLDOWN_DAYS} days")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
