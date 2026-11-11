---
name: setup
description: Set up or change the deep-review plugin for this user. It checks prerequisites, reviews the approval and blocking settings, creates or updates the scheduled task for automatic reviews, and optionally adds a rule to run a deep review before every pull request.
disable-model-invocation: true
---

# Deep review setup

Walk the user through these steps in order. Explain each choice in a sentence or two, and recommend the safe default.

Current settings:
- Approval mode: `${user_config.approval_mode}`
- Request changes at or above: `${user_config.block_at}`
- Review requested PRs automatically: `${user_config.review_requests}`
- Clones folder: `${user_config.clones_root}`

## 1. Check prerequisites

Run each check and report the results in a short list.

- **GitHub CLI:** `gh auth status`, then `gh api user --jq .login` to get the user's login. Reviews post from this account.
- **Python:** `python3 --version`.
- **Clones folder:** list the git repositories directly under `${user_config.clones_root}` that have a GitHub `origin`. Automatic reviews only run on these.

If `gh` is not signed in, stop and tell the user to run `gh auth login` first.

## 2. Settings

Ask about all three settings in one AskUserQuestion call, offering the current value as one option.

1. **Approval mode.** What happens when a review finds nothing blocking?
   - `never` (recommended): it leaves a "ready for approval" review, and the user approves on GitHub themselves.
   - `confirm`: it asks in the session first; unattended runs leave the approval to the user.
   - `automatic`: it approves.

   Explain the trade-off:
   - an approval is recorded as the user's own review;
   - on repositories where one approval is enough to merge, an automatic approval can start auto-merge;
   - teams with a change-control policy often treat approvals as evidence of human review.
2. **Request changes at.**
   - `medium` (recommended): blocks on Critical, High and Medium.
   - `high`: blocks only on Critical and High.
3. **Review requested PRs automatically.** Choose on or off. Follow-up rounds on PRs the user already deep-reviewed run either way.

If anything changed, save the new values:
1. Find the plugin's installed ID with `claude plugin list --json`. It is the entry whose name starts with `deep-review@`.
2. In `~/.claude/settings.json`, set `pluginConfigs["<that id>"].options` to the new values. Keep every other key in the file as it is.
3. Tell the user the new values take effect in new sessions. They can also change them later in `/config`.

If the clones folder is wrong, ask for the right path and save it the same way, as `clones_root`.

## 3. Schedule

Ask how often automatic reviews should run, with these options:
- every 15 minutes, Monday to Friday, 08:00–19:45 (recommended): cron `*/15 8-19 * * 1-5`;
- hourly, Monday to Friday, 08:00–19:00: cron `0 8-19 * * 1-5`;
- every 30 minutes, every day: cron `*/30 * * * *`;
- no schedule: the user runs `/deep-review:auto` by hand when they want a pass.

Create or update the scheduled task with the scheduled-tasks tools, loading them with ToolSearch if they are deferred:
- use `list_scheduled_tasks` to look for an existing `deep-review-auto` task;
- `taskId`: `deep-review-auto`;
- `title`: "Deep review requests and follow-ups";
- `prompt`: `/deep-review:auto`;
- `cronExpression`: from the user's choice;
- `notifyOnCompletion`: false.

Use `update_scheduled_task` if the task exists and `create_scheduled_task` otherwise. For "no schedule", disable an existing task with `enabled: false`.

If the scheduled-tasks tools are not available (for example, Claude Code in a terminal without the desktop app), say so. Offer instead to run `/loop 15m /deep-review:auto` in a session the user keeps open.

Tell the user three things:
- scheduled tasks run only while the Claude app is open;
- the first real review may stop for permission prompts, and approvals given there are saved for later runs;
- each first review is a full multi-agent run of about 15–20 minutes, longer on large PRs.

## 4. Before every pull request (optional)

Ask whether Claude should always run a deep review in fix mode before raising a pull request for them.

If yes, add or replace this block in `~/.claude/CLAUDE.md`. Create the file if it does not exist, and keep everything outside the markers unchanged:

```
<!-- deep-review:pre-pr -->
## Deep review before raising a pull request

Before you create a pull request for me, in any repository, run the `deep-review:review` skill in fix mode on the branch. Create the PR only after its fixes are committed and its checks pass. My asking for the PR is the go-ahead for this review. If the review leaves findings blocked on a decision, list them as open items in the PR description and still raise the PR. Skip the review only when I say so for that PR.
<!-- /deep-review:pre-pr -->
```

If no, and the block exists, offer to remove it.

## 5. Summary

Finish with:
- the settings in effect;
- the schedule;
- whether the pull-request rule is on;
- three example commands:
  - `/deep-review:review` reviews and fixes your current branch;
  - `/deep-review:review 1234` reports on a colleague's PR;
  - `/deep-review:review 1234 comment` reviews and comments, with a verdict.
