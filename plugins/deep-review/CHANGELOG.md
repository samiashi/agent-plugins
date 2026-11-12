# Changelog

All notable changes to the deep-review plugin. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/). Each release is tagged `deep-review--v<version>`.

## [Unreleased]

## [1.2.3] - 2026-11-12

### Changed

- **Checks no longer delay approval on protected branches.** On a branch that requires status checks, a review approves while required checks are still running or haven't run yet, because GitHub blocks the merge until they pass. Stacked PRs, whose CI often doesn't run until their parent merges, used to get no approval at all.
  - **Failed checks:** any failed, cancelled, timed-out, stale or action-needed required check still holds approval back.
  - **Unprotected branches:** where the branch requires no checks, every check on the PR must pass first, as before.
  - **Unreadable checks:** if the checks or branch rules can't be read, approval is held back.
- **Intent questions are now asked on the PR.** When an open question about intent would withhold approval, the review posts it as one inline question and keeps its thread. This applies even under a policy that otherwise posts only blocking findings. Before, the author saw approval withheld with nothing to answer.
  - **Who can answer:** a reply from the PR's author or a maintainer settles it, in the thread or anywhere on the PR.
  - **"Intended":** clears the question.
  - **"Not intended":** the behavior becomes a finding, with a severity from independent triage.
  - **Unanswered or unclear:** the question goes to you.
- **The scanner knows what a comment-only review waits on.** It reads a new hidden `deep-review-awaiting` line.
  - **Questions:** a review waiting only on a question no longer polls checks.
  - **Checks:** a review waiting on checks is followed up once none is failing, not only once all have passed.

### Fixed

- **Replies are counted per thread**, from the time the round last read the threads (a new hidden `deep-review-read-at` line). An answer posted while a round was running is no longer lost, and nor is one followed by your own comment elsewhere on the PR.
- **Review threads are paginated**, so a reply in a PR's 101st thread, or after a thread's first 50 comments, now starts a follow-up.

## [1.2.2] - 2026-11-11

### Removed

- Support for Python 3.9 and 3.10, which have both reached end of life. The plugin's scripts need Python 3.11 or later, and exit with a clear message on an older one. macOS's built-in `python3` is 3.9, so install a newer one, for example with `brew install python`. `/deep-review:setup` checks this.

### Changed

- `/deep-review:auto` now notifies you when its scanner fails, for example on a Python older than 3.11, instead of stopping without telling you.

## [1.2.1] - 2026-11-11

### Fixed

- The automatic pass no longer reviews the same PR again on every scan when its last review was comment-only and the required checks had already passed. A comment-only verdict now triggers a follow-up only once a required check finishes after that review. That covers both GitHub check runs and commit statuses, with only the newest run of each check counting, as in `gh pr checks`.
- If the pass can't read a PR's checks, it retries that PR on the next scan, without holding up the others.
- A comment-only review with no inline findings, such as a clean PR with a failing required check, is now recognised as a deep review. Its follow-up runs once the checks pass.

### Changed

- Every review body now ends with a hidden verdict marker, `<!-- deep-review-verdict: approve | ready | changes | comment -->`. The automatic pass decides follow-ups from the latest review that carries one, and if a body quotes a marker, it reads the closing one. Reviews posted before this release are still read from their state and wording.
- Before posting a verdict that depends on the required checks, the review re-reads them.

## [1.2.0] - 2026-11-11

First public release.

### Added

- A secrets rule for every agent: no secret value appears in candidates, reports, commits, tests, PR comments, suggestion blocks or notifications. Reviews name the location and the type of credential only, and the fix for a committed secret includes rotating it.
- Fix mode checks each fixer's work before the fix diff is reviewed: its changes stay within its assigned files, its claims come with tool output, and its regression tests assert the finding's consequence. When the fix diff is reviewed, each fix is reverted to confirm its test fails.
- Triage rates each fix's risk (LOW, MED or HIGH). The report records it, the fix-diff review starts with risky fixes, and comments say what a risky fix could break. HIGH-risk fixes never get a one-click suggestion.

### Changed

- The marketplace is now `samiashi-plugins`, published from `samiashi/agent-plugins`. The install id is `deep-review@samiashi-plugins`.

## [1.1.0] - 2026-10-21

### Added

- Risk tiers that size the review: trivial, standard and large, with sensitive changes never treated as trivial.
- Plugin agents for each role (`finder`, `verifier`, `triager`, `fixer`), each with its own model and effort.
- An untrusted-input rule: text in the change that tries to steer the review becomes a finding and prevents approval.

### Changed

- The brief is inlined in every agent's prompt, and agents batch their reads.
- Fix mode reviews its own fix diff once, at that diff's tier, and runs a second round only after a High or Critical.

## [1.0.1] - 2026-10-05

### Added

- A Codex build of the skills, generated by `scripts/build_codex_skills.py`.

### Fixed

- The scanner no longer reports a PR as capped while its last round is still running.

## [1.0.0] - 2026-10-01

### Added

- `/deep-review:review` in fix, report and comment modes, with follow-up rounds and a single status comment per PR.
- `/deep-review:auto`, a scheduled pass over review requests and follow-ups.
- `/deep-review:setup`.

[Unreleased]: https://github.com/samiashi/agent-plugins/compare/deep-review--v1.2.3...HEAD
[1.2.3]: https://github.com/samiashi/agent-plugins/releases/tag/deep-review--v1.2.3
[1.2.2]: https://github.com/samiashi/agent-plugins/releases/tag/deep-review--v1.2.2
[1.2.1]: https://github.com/samiashi/agent-plugins/releases/tag/deep-review--v1.2.1
[1.2.0]: https://github.com/samiashi/agent-plugins/releases/tag/deep-review--v1.2.0
