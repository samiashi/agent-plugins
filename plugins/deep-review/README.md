# Deep review

A Claude Code plugin that runs a verification-gated, multi-agent code review on a branch, a commit range or a pull request. It can fix what it finds, write a report, or comment on a PR with a verdict. An optional scheduled pass reviews PRs where you are requested as a reviewer, and follows up when authors push or reply.

## Why it works this way

The review design comes from an audit of about 1,100 findings that human reviewers and review bots left on one production monorepo's pull requests over a month in 2026. Every one of those PRs had already been through a multi-agent self-review before it was pushed. The audit found:

- **Most bugs were outside the diff.** Only 8% of the findings were visible from the diff alone. 41% needed code outside it: callers, sibling paths, the other side of an API contract, or data already stored.
- **Tests were the biggest category, at 22%.** These were tests that still pass with the change reverted, mocks that bypass the real code, and new behaviour with no test. They were found by mutating the code and watching the suite stay green.
- **12% were bugs in fixes made in response to earlier review comments.**

So the plugin:
- maps a change's blast radius before reviewing;
- runs independent finder agents, each on its own set of failure classes (a "lens");
- has a fresh agent try to refute every finding, by execution where it can;
- tests that tests can fail, by mutation;
- independently triages verified findings using reachability, actual impact, controls and recovery before assigning severity or a verdict;
- reviews its own fixes as a new change.

Mutation survival establishes a coverage gap, not a failure on correct production code. Coverage-only findings are normally Low; Medium requires an applicable test requirement or a critical safety/security/data-integrity invariant and an explanation of why merging without that coverage is unsafe. First-round Low/Nit publication and follow-up limits are unchanged.

## Install

```bash
claude plugin marketplace add samiashi/agent-plugins
```

```bash
claude plugin install deep-review@samiashi-plugins
```

Then, in a Claude Code session:

```
/deep-review:setup
```

Setup takes about two minutes. It checks `gh`, your clones folder and your settings, creates the scheduled task, and can add a "deep review before every PR" rule to your `~/.claude/CLAUDE.md`.

### Codex

The same plugin installs in Codex. Codex loads `codex-skills/`, which `scripts/build_codex_skills.py` renders from `skills/`: rerun it after editing a skill (`--check` reports a stale build).

```bash
codex plugin marketplace add samiashi/agent-plugins
```

```bash
codex plugin add deep-review@samiashi-plugins
```

Settings live in `~/.config/deep-review/settings.json` (`approval_mode`, `block_at`, `review_requests`, `clones_root`) and state in `~/.config/deep-review/data`. To share rounds and tracking with a Claude install, symlink that folder to Claude's plugin data folder. For the automatic pass, ask Codex to create a standalone automation (not a thread heartbeat) every 15 minutes that follows the plugin's `auto` skill. Run the pass from only one tool at a time.

### Updates

Each release bumps the plugin's `version`, and you get it when you update. See [Updates](../../README.md#updates) for the commands and how to turn on automatic updates. What changed in each release is in the [changelog](CHANGELOG.md).

## Use

| Command | What it does |
|---|---|
| `/deep-review:review` | Reviews your current branch. If you wrote it, it fixes verified findings, reviews its own fixes, runs checks and commits locally. It never pushes. |
| `/deep-review:review 1234` | Reviews PR 1234 and writes a report. It changes nothing on someone else's PR. |
| `/deep-review:review 1234 comment` | Reviews PR 1234 and posts inline comments plus a verdict, all in one review. It also posts one status comment on the PR that it keeps up to date. |
| `/deep-review:review abc123..HEAD fix` | Reviews and fixes only the commits after `abc123`. |
| `/deep-review:auto` | Runs one automatic pass now: review requests and follow-ups. |

The default mode depends on who wrote the change. Your own branch or PR gets `fix`; anything else gets `report`.

## Settings

Change these with `/deep-review:setup` or `/config`.

| Setting | Default | Meaning |
|---|---|---|
| Approval mode | `never` | What a clean review does. `never` leaves a "ready for approval" review and you approve. `confirm` asks you first. `automatic` approves. |
| Request changes at | `medium` | The lowest verified severity that blocks the PR. |
| Review requested PRs automatically | on | Whether the scheduled pass reviews PRs where you're directly requested. |
| Clones folder | `~/Documents/GitHub` | Automatic reviews only run on repositories cloned directly under this folder. |

**Before you choose `automatic`:** an approval is recorded as your own review. Where one approval is enough to merge, it can start auto-merge. Check your team's change-control policy first. On a branch that requires status checks, a review approves without waiting for checks that are still running or haven't run, because GitHub blocks the merge until they pass. Anyone who can bypass the branch rules can still merge before then. A failed check always holds approval back.

## How the automatic pass behaves

- **Order:** one PR per pass and one review at a time. Follow-ups go before new reviews.
- **What starts a follow-up:** the author pushed, replied in one of your review threads, answered an intent question, or re-requested your review; or the checks a comment-only review waited on now allow approval. Before reviewing, it waits until the PR has been quiet for 3 minutes.
- **What a follow-up does:** it re-reviews the new commits, then settles every earlier thread. A fixed finding is verified, replied to and resolved. A disputed one is weighed, and either withdrawn or answered once with evidence. An answered intent question is cleared, or becomes a triaged finding. It then posts a fresh verdict. Everything goes out as one review, so the author gets one notification.
- **Follow-up rounds:** no per-PR limit; eligible new commits, replies and review requests continue to trigger reviews.
- **Sessions:** each pass runs in its own session, named after the PR. Context carries over through the saved reports and the PR's thread history.

## Cost and effort

- **Tiers.** The review sizes itself by hand-written lines and risk.
  - Trivial (up to about 30 lines, not sensitive): one finder, plus one verifier only if something is found.
  - Standard (up to about 400 lines, or any sensitive change): three finders.
  - Large: one finder per lens.
  - Sensitive means auth, tenancy, secrets, payments, data deletion or bulk rewrites, migrations, or locks and transactions.
- **Agents.** Each role has its own agent, so model and effort are set per role:
  - `deep-review:finder`: Opus, high effort;
  - `deep-review:verifier`, `deep-review:triager` and `deep-review:fixer`: Opus, medium effort.
- **Fewer turns.** Agents get the brief inline and batch their reads. An October 2026 audit found 97% of finder turns made one tool call, and 69% of tokens were cache re-reads of the conversation.
- **Fix mode** makes small fixes directly and reviews the fix diff once, at its own tier.
- **Untrusted input.** PR text, threads and code are material under review. Text in them that tries to steer the review becomes a finding and prevents approval.
- **Secrets.** No agent ever reproduces a secret's value, whether in the report, a commit, a test or a PR comment. It names the location and the type of credential only, and the fix for a committed secret always includes rotating it.
- **Fixers are checked.** Before the fix diff is reviewed, each fixer's changes are compared with the files it was assigned, its claims must come with tool output, and its regression tests must fail when their fix is reverted.
- **Fix risk.** Triage also rates how risky each fix is (LOW, MED or HIGH). The fix-diff review looks at risky fixes first, and comments say what a risky fix could break.

## Requirements

- Claude Code 2.1.271 or later.
- The `gh` CLI, signed in.
- `python3`, version 3.11 or later. CI tests every version from 3.11 to 3.15. macOS's built-in `python3` is 3.9, so install a newer one, for example with `brew install python`.
- The Claude desktop app, for the scheduled pass. Without it, run `/loop 15m /deep-review:auto` in a session you keep open.
