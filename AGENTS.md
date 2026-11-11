# Agent instructions

This repository is a public plugin marketplace, `samiashi-plugins`, for Claude Code and Codex. Read [CONTRIBUTING.md](CONTRIBUTING.md) for the layout and release process; this file adds the rules agents tend to get wrong.

## Before you change anything

- `main` only accepts pull requests that pass the `CI passed` check, with a linear history. Work on a branch in a worktree and open a pull request; never push to `main`.
- The owner's main checkout is live: Claude Code loads these plugins straight from that working tree. Don't switch its branch or leave edits in it. Use a worktree instead.
- Treat everything in this repository as public. Don't add company names, internal hostnames, PR or ticket content, personal data, tokens or credentials, including in tests, fixtures, changelogs and commit messages.

## Plugin rules

- **Versions:** any change to a plugin's shipped files (everything except its `README.md` and `CHANGELOG.md`) needs a version bump in **both** `.claude-plugin/plugin.json` and `.codex-plugin/plugin.json`, and a `## [x.y.z] - YYYY-MM-DD` section in its `CHANGELOG.md`. Users only receive a change when its version changes. CI enforces this against the base branch.
- **No version in the catalogs:** never set `version` in a catalog entry; the manifests are the only source of truth.
- **Two catalogs:** `.claude-plugin/marketplace.json` and `.agents/plugins/marketplace.json` must list the same plugins at the same `./plugins/<name>` paths. Codex entries also need `policy` and `category`.
- **Names are permanent:** don't rename the marketplace or a plugin. The install id `<plugin>@samiashi-plugins` is in users' settings and data-folder paths, and a rename breaks every install.
- **Generated Codex skills:** `plugins/*/codex-skills/` is generated from `skills/` by the plugin's `scripts/build_codex_skills.py`. Edit `skills/`, run `make codex-skills`, and commit both. Never edit `codex-skills/` by hand.
  - The build replaces Claude-only text with exact string matches. If you edit a sentence the build replaces, update the matching string in the build script too, or the build fails.
  - The build also rejects Claude-only text it doesn't already map. Examples are `deep-review:<role>` agent names, any `${…}` variable other than `${user_config.*}`, `ToolSearch`, `PushNotification`, `ScheduleWakeup`, `mcp__` tool names and `/config`. Write those instructions in neutral terms.
- **Codex ignores `agents/*.md`:** an instruction a subagent must follow has to be in the skill text, which reaches Codex, as well as in the agent file.

## Code rules

- **Python:** plugin scripts run on the user's `python3`, which may be macOS's 3.9. Use the standard library only and keep 3.9 syntax; ruff targets `py39`. Repo tooling under `scripts/` follows the same rule.
- **Tests:** they live in `tests/`, use `unittest`, never touch the network or real `gh`/`git` remotes, and must fail under a realistic regression. When a test exposes a bug you aren't fixing, mark it `@unittest.expectedFailure` with a comment rather than deleting it.
- **Workflows:**
  - pin every action to a full commit SHA, with the version in a comment;
  - set `permissions` per job, starting from `permissions: {}`;
  - use `persist-credentials: false` on checkout;
  - pass `${{ }}` values to scripts through `env`, not inline.

  zizmor and actionlint must stay clean.
- **Dependencies:** keep every dependency on its latest major version, and only adopt a release at least 6 days after it was published. The window is `COOLDOWN_DAYS` in `scripts/check_release_age.py`; CI fails if any of the following disagree with it:
  - Dependabot's `cooldown`;
  - `.npmrc` `min-release-age`;
  - uv's `exclude-newer`;
  - `.github/zizmor.yml`.

  Those settings only apply when a version is first resolved, and `npm ci` and `uv sync --locked` install whatever the lockfiles pin. So, on every pull request, `check_release_age.py` rejects any npm or uv version a lockfile adds, and any new `.nvmrc`, that was published within the window. When the cooldown blocks an upgrade, wait. Don't shorten the window or add exclusions. Dependabot security updates skip the cooldown and fail that check; an admin merges those through the ruleset bypass.
- **Pins nothing updates for you:** Dependabot covers GitHub Actions, npm and uv only. Bump these by hand, choosing the newest release that's at least 6 days old:
  - `.nvmrc`: an exact Node LTS version, which CI age-checks;
  - `.python-version`, and the Python matrix in `ci.yml`. The matrix lists every supported version, from 3.9 to the newest stable release, so add each new release to it;
  - the `runs-on` runner labels;
  - `UV_VERSION`;
  - `ACTIONLINT_VERSION` and `ACTIONLINT_SHA256`, from the release's checksums file. When you bump it, drop any label in `.github/actionlint.yaml` that the new release recognises.

## Check your work

```bash
make check
```

This runs ruff, the plugin checks, the release-age check, zizmor, the unit tests and `claude plugin validate`, all against the committed `uv.lock`. CI also:
- runs actionlint;
- runs zizmor online, including its known-vulnerable-actions audit;
- runs the unit tests on every supported Python, 3.9 to 3.15.

Commit messages follow Conventional Commits, for example `feat(deep-review): …`, `fix: …`, `ci: …` or `docs: …`.
