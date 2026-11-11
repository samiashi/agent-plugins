# Contributing

## Layout

```
.claude-plugin/marketplace.json   Claude Code catalog
.agents/plugins/marketplace.json  Codex catalog (lists the same plugins)
plugins/<name>/
  .claude-plugin/plugin.json      name and version (the source of truth)
  .codex-plugin/plugin.json       Codex manifest; same name and version
  CHANGELOG.md                    one section per release
  skills/, agents/, scripts/ …    the plugin itself
scripts/check_plugins.py          catalog, version and changelog checks; release helper
tests/                            unit tests for the scripts
```

Agents working in this repository should also read [AGENTS.md](AGENTS.md).

## Set up

You need `git`, [uv](https://docs.astral.sh/uv/) 0.10 or later, Node.js (the version in `.nvmrc`, with npm 11.10 or later) and `python3` 3.11 or later.

```bash
uv sync
```

```bash
npm ci
```

## Work on a plugin locally

Add your clone as a marketplace once. Claude Code then loads its plugins straight from your working tree, so edits apply at the next session start or after `/reload-plugins`:

```bash
claude plugin marketplace add "$PWD"
```

Codex installs a cached copy, so after an edit run `codex plugin add <plugin>@samiashi-plugins` and restart it.

If a plugin has `scripts/build_codex_skills.py`, its Codex skills are generated from `skills/`. Run `make codex-skills` after editing a skill and commit the result; CI fails on a stale build.

## Check your change

```bash
make check
```

CI runs the same checks on every pull request, plus actionlint. The unit tests run on every supported Python, 3.11 to 3.15. The checks are:

- **Ruff:** lint and format.
- **Plugin checks:**
  - the two catalogs agree;
  - each plugin's two manifests have the same name and version;
  - the version has a changelog section;
  - any Codex build is current;
  - every plugin whose shipped files changed has a higher version than on `main`. Shipped files are everything except `README.md` and `CHANGELOG.md`.
- **Release-age cooldown:**
  - every cooldown setting is 6 days;
  - `.nvmrc` is an exact version;
  - every npm or uv version that a pull request adds to a lockfile, and any new `.nvmrc`, was published at least 6 days ago.
- **Claude Code validation:** `claude plugin validate`, run on the marketplace and on each plugin.
- **Workflow security:** zizmor and actionlint.
- **Unit tests,** including every plugin's real Codex build on each tested Python.

## Release

1. Bump `version` in both `plugin.json` files, following [Semantic Versioning](https://semver.org/):
   - **patch:** fixes and wording;
   - **minor:** new behaviour that existing users can ignore;
   - **major:** a change users must act on, such as a renamed setting or command.
2. Move the plugin's `## [Unreleased]` notes under a new `## [x.y.z] - YYYY-MM-DD` heading.
3. Open a pull request. When it merges and CI passes on `main`, CI tags `<plugin>--v<x.y.z>` and publishes a GitHub release. Its notes are that changelog section plus a Contributors list: the GitHub users whose commits changed the plugin since its previous release, bots excluded. Preview them with `python3 scripts/check_plugins.py release-notes <plugin> <x.y.z>`.

Users only receive a change when its version changes, so never ship plugin changes without a bump. CI enforces this.

## Add a plugin

1. Create `plugins/<name>/` with both manifests (same `name` and `version`) and a `CHANGELOG.md`.
2. Add an entry to both catalogs. In `.claude-plugin/marketplace.json`, use `"source": "./plugins/<name>"` and no `version`. In `.agents/plugins/marketplace.json`, use a `local` source, a `policy` and a `category`.
3. Add the plugin to the table in `README.md`.

## Pull requests

- Branch from `main` and open a pull request. `main` only accepts changes through pull requests that pass CI, with a linear history.
- Don't commit secrets, tokens or personal data, including in test fixtures. GitHub push protection blocks known secret formats.
