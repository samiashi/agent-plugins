# Agent plugins

Plugins for [Claude Code](https://code.claude.com/docs/en/plugins) and [Codex](https://developers.openai.com/plugins/build/plugins), published as one marketplace named `samiashi-plugins`.

| Plugin | What it does |
|---|---|
| [deep-review](plugins/deep-review/README.md) | Verification-gated multi-agent code review. It looks past the diff, proves each finding before reporting it, and fixes, reports or comments on a PR with a verdict. An optional scheduled pass reviews PRs you're requested on and follows up when authors respond. |

## Install

### Claude Code

```bash
claude plugin marketplace add samiashi/agent-plugins
```

```bash
claude plugin install deep-review@samiashi-plugins
```

If your machine has no GitHub SSH key, set `CLAUDE_CODE_PLUGIN_PREFER_HTTPS=1` in your environment first. Installs from the shell fall back to HTTPS on their own, but installs from `/plugin` and later updates don't.

### Codex

```bash
codex plugin marketplace add samiashi/agent-plugins
```

```bash
codex plugin add deep-review@samiashi-plugins
```

Each plugin's README covers its own setup.

## Updates

Every release bumps the plugin's `version`, and your tools only pick up a new copy when that version changes.

- **Claude Code, automatic:** open `/plugin`, go to **Marketplaces**, select `samiashi-plugins` and choose **Enable auto-update**. Auto-update is off by default for marketplaces you add yourself.
- **Claude Code, by hand:**

  ```bash
  claude plugin marketplace update samiashi-plugins
  ```

  ```bash
  claude plugin update deep-review@samiashi-plugins
  ```

- **Codex:**

  ```bash
  codex plugin marketplace upgrade samiashi-plugins
  ```

  Then restart Codex or the ChatGPT app.

### Stay on one version

Every release is tagged `<plugin>--v<version>`, for example `deep-review--v1.2.0`. To stay on a release, add the marketplace at its tag instead of the default branch:

```bash
claude plugin marketplace add samiashi/agent-plugins#deep-review--v1.2.0
```

```bash
codex plugin marketplace add samiashi/agent-plugins --ref deep-review--v1.2.0
```

## Releases

- `main` is the release channel. Each plugin is versioned on its own with [Semantic Versioning](https://semver.org/), and its `CHANGELOG.md` records every release.
- When a version change lands on `main` and CI passes, CI tags it and publishes a [GitHub release](https://github.com/samiashi/agent-plugins/releases). The release notes are that version's changelog entry, followed by a list of everyone whose commits changed the plugin since its previous release (co-authors included, bots excluded).
- Release tags are protected: they can't be moved or deleted.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). To report a security problem, see [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE)
