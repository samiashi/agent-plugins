#!/usr/bin/env python3
"""Render the Codex copies of the review and auto skills from the Claude ones.

skills/ stays the source of truth. Codex resolves none of Claude Code's
substitutions (${CLAUDE_PLUGIN_ROOT}, ${CLAUDE_PLUGIN_DATA}, ${user_config.*},
$ARGUMENTS) and has none of its session tools, so each is rewritten here.
Every replacement must match exactly once: an edit to skills/ that breaks one
fails the build instead of shipping a half-ported skill.

    python3 scripts/build_codex_skills.py           # write codex-skills/
    python3 scripts/build_codex_skills.py --check   # fail if codex-skills/ is stale
"""

import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCE = ROOT / "skills"
TARGET = ROOT / "codex-skills"

SETTINGS_FILE = "~/.config/deep-review/settings.json"
DATA_DIR = "~/.config/deep-review/data"
PLUGIN_ROOT = "<plugin root>"
SCRIPT = f"{PLUGIN_ROOT}/scripts/pending_reviews.py"

PREAMBLE = f"""
<codex>
This is the Codex build of the deep-review plugin.
- `{PLUGIN_ROOT}` is the folder two levels above this SKILL.md; it holds `scripts/` and `codex-skills/`.
- Settings live in `{SETTINGS_FILE}` with the keys `approval_mode` (never | confirm | automatic), `block_at` (medium | high), `review_requests` (true | false) and `clones_root`. Read it first. A missing file or key means the defaults: `never`, `medium`, `true`, `~/Documents/GitHub`.
- The plugin's data folder is `{DATA_DIR}`.
- Use subagents for the finder, verifier and independent triage stages, and wait for each to finish before going on.
</codex>
"""

COMMON = [
    (re.compile(r"^allowed-tools:.*\n", re.M), ""),
    (re.compile(r"^argument-hint:.*\n", re.M), ""),
    (re.compile(r"\$\{user_config\.(\w+)\}"), r"<settings.\1>"),
    (re.compile(r"python3 \$\{CLAUDE_PLUGIN_ROOT\}/scripts/pending_reviews\.py"), f"python3 {SCRIPT}"),
    (re.compile(r"--data-dir \$\{CLAUDE_PLUGIN_DATA\}"), f"--data-dir {DATA_DIR}"),
    (re.compile(r"\$\{CLAUDE_PLUGIN_DATA\}"), DATA_DIR),
]

# (old, new): each old string must occur exactly once in its skill.
SPECIFIC = {
    "review": [
        (
            "Target and mode: $ARGUMENTS",
            "Target and mode: the target and mode given where this skill was invoked, for example `https://github.com/owner/repo/pull/12 comment`.",
        ),
        (
            "The user changes them with `/deep-review:setup` or `/config`.",
            f"The user changes them in `{SETTINGS_FILE}`.",
        ),
        (
            "This only matters if the user set up the scheduled task with `/deep-review:setup`.",
            "This only matters if the user runs the deep-review auto pass as a Codex automation.",
        ),
        (
            "**Agent types.** Launch each stage with this plugin's agent for it, which sets the model and reasoning effort for that role:\n"
            "- finders: `deep-review:finder`;\n"
            "- verifiers: `deep-review:verifier`;\n"
            "- triagers: `deep-review:triager`;\n"
            "- fixers: `deep-review:fixer`.\n"
            "\n"
            "Don't override their model. If these agent types are unavailable, use general-purpose subagents.",
            "**Reasoning effort.** Give finder subagents high reasoning effort, and verifier, triage and fixer subagents medium.",
        ),
        (
            "or hand them all to one `deep-review:verifier`, not one agent per thread",
            "or hand them all to one verifier subagent, not one agent per thread",
        ),
        (
            "Use a `deep-review:fixer` only for a fix",
            "Use a fixer subagent only for a fix",
        ),
    ],
    "auto": [
        (
            "Runs from the scheduled task that /deep-review:setup creates, or when the user asks to run the pass now.",
            "Runs from the user's Codex automation, or when the user asks to run the pass now.",
        ),
        (
            "**To notify the user, do both of these:**\n"
            '- If `osascript` exists (macOS), run `osascript -e \'display notification "<message>" with title "Deep review"\'`. Escape any double quotes in the message.\n'
            "- Call the PushNotification tool, loading it with ToolSearch if it is deferred. It only reaches the user's phone while Remote Control is on. If it reports that nothing was sent, carry on.\n",
            '**To notify the user:** if `osascript` exists (macOS), run `osascript -e \'display notification "<message>" with title "Deep review"\'`. Escape any double quotes in the message. If it fails, carry on.\n',
        ),
        (
            '   a. **Rename this session.** Use the `mcp__ccd_session_mgmt__set_session_title` tool (session "self"), loading it with ToolSearch if it is deferred. Title it',
            "   a. **Name this run.** If your client lets you name the current thread, name it",
        ),
        (
            "If the tool is unavailable or renaming fails, continue.",
            "If it can't, continue.",
        ),
        (
            "Use the Skill tool to run `deep-review:review` with arguments `<url> comment`, and follow it to the end, including independent impact-based triage after verification and before posting.",
            f"Read the deep-review review skill at `{PLUGIN_ROOT}/codex-skills/review/SKILL.md` and follow it to the end, with target `<url>` and mode `comment`, including independent impact-based triage after verification and before posting.",
        ),
        (
            "Wait for subagents through their completion notifications. Do not use ScheduleWakeup; this pass should finish on its own.",
            "Wait for every subagent to finish before going on, and don't end the run while one is still working. This pass should finish on its own.",
        ),
        (
            " Put the review URL in the PushNotification message.",
            "",
        ),
    ],
}

LEFTOVERS = re.compile(
    r"\$\{|\$ARGUMENTS|ToolSearch|PushNotification|ScheduleWakeup|Skill tool|mcp__|deep-review:(?:review|setup|auto|finder|verifier|triager|fixer)|/config"
)


def render(name: str) -> str:
    text = (SOURCE / name / "SKILL.md").read_text()
    for old, new in SPECIFIC[name]:
        count = text.count(old)
        if count != 1:
            sys.exit(f"{name}: expected one match, found {count}: {old[:70]!r}")
        text = text.replace(old, new)
    for pattern, new in COMMON:
        text = pattern.sub(new, text)
    head, sep, body = text.partition("\n---\n")
    if not sep:
        sys.exit(f"{name}: no frontmatter")
    text = head + sep + PREAMBLE + body
    leftover = sorted(set(LEFTOVERS.findall(text)))
    if leftover:
        sys.exit(f"{name}: Claude-only references remain: {leftover}")
    return text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    check = ap.parse_args().check
    stale = []
    for name in SPECIFIC:
        rendered = render(name)
        path = TARGET / name / "SKILL.md"
        if check:
            if not path.exists() or path.read_text() != rendered:
                stale.append(str(path.relative_to(ROOT)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rendered)
    if stale:
        print("Stale, rerun scripts/build_codex_skills.py: " + ", ".join(stale))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
