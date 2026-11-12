#!/usr/bin/env python3
"""List open PRs, across the user's repositories, that need an automatic deep-review round.

Two kinds of round:
  first      the authenticated user is directly requested as a reviewer and has
             not deep-reviewed the PR yet
  follow-up  the PR has the user's deep review (a review comment opening with the
             skill's marker, or a review body carrying a verdict marker or starting
             with a legacy verdict line), and since the user's latest review:
               - the head commit moved, or
               - someone else replied in one of the user's deep-review threads, counted
                 per thread from the later of the user's own last reply there and when
                 the latest round read the threads (its read-at line), or
               - the user's review was re-requested, or
               - the latest verdict was comment-only (not "ready for approval") at the
                 current head, and what its awaiting line waits on has changed:
                   checks      no required check is failing, and either all pass or
                               one finished after the review (others may be running)
                   all-checks  every check passes, or the PR has no checks
                   question    the PR's author commented anywhere after the read time
                   other       nothing here; a push, reply or re-request starts a round
                 Comment-only verdicts written before the awaiting line keep the old
                 rule: every required check passes and one finished after the review.
                 A check run finishes at its completedAt, a commit status at its
                 createdAt; skipped and neutral checks pass, and cancelled, timed-out
                 and stale ones fail.

Repositories scanned: every repository where the user is directly requested on an
open PR (when review requests are enabled), plus every repository with a tracked
PR in the state file. A comment-mode review tracks its PR with --track, so manual
reviews get follow-ups too. A PR can only be reviewed when its repository has a
local clone directly under --clones-root; requested PRs without one are listed as
`no_clone`.

A due PR waits while it has had activity in the last QUIET minutes (so a burst of
pushes gets one round), and every PR waits while any round is still running (one
review at a time). Eligible follow-ups have no per-PR round limit.

State lives in <data-dir>/state.json, keyed "owner/name#N".

Usage (every call takes --data-dir; the scan also takes --clones-root and --review-requests):
  pending_reviews.py --data-dir D --clones-root C --review-requests true|false
                                               print JSON {due, waiting, capped, no_clone}
  pending_reviews.py --data-dir D --track KEY   follow up on PR KEY automatically from now on
  pending_reviews.py --data-dir D --mark KEY    a round on PR KEY is starting
  pending_reviews.py --data-dir D --done KEY    the round on PR KEY finished
  pending_reviews.py --data-dir D --notified KEY   the user was told PR KEY has no local clone
  pending_reviews.py --data-dir D --status-comment KEY ID   remember PR KEY's status comment id
KEY is "owner/name#N".
"""

import argparse
import datetime as dt
import json
import pathlib
import re
import subprocess
import sys

# Deliberately kept although ruff targets 3.11: it gives older interpreters a clear error.
if sys.version_info < (3, 11):  # noqa: UP036
    sys.exit(
        f"deep-review needs Python 3.11 or later, but this is Python {sys.version.split()[0]}. "
        "macOS's built-in python3 is 3.9; install a newer one (for example `brew install python`) "
        "so that `python3` resolves to it."
    )

MARKER = "I checked and found this potential issue to look into"
VERDICT_BODY_PREFIXES = ("Blocking:", "Reviewed at ")  # legacy bodies, before verdict markers
# The review skill ends every review body with <!-- deep-review-verdict: <kind> -->.
VERDICT_KINDS = ("approve", "ready", "changes", "comment")
VERDICT_MARKER = re.compile(r"<!--\s*deep-review-verdict:\s*(" + "|".join(VERDICT_KINDS) + r")\s*-->")
# A comment-only body also says what it waits on, e.g. <!-- deep-review-awaiting: checks question -->:
#   checks      a required check failed or couldn't be read, on a base branch that requires checks
#   all-checks  checks on a base branch that requires none, so every check must pass
#   question    an open intent question
#   other       anything else
AWAITING_KINDS = ("checks", "all-checks", "question", "other")
AWAITING_MARKER = re.compile(r"<!--\s*deep-review-awaiting:\s*([a-z][a-z \-]*?)\s*-->")
# Every body records when its round last read the PR's threads: <!-- deep-review-read-at: <ISO time> -->.
READ_AT_MARKER = re.compile(r"<!--\s*deep-review-read-at:\s*([0-9][0-9T:.+\-]*Z?)\s*-->")
FAILING_CONCLUSIONS = {"FAILURE", "TIMED_OUT", "ACTION_REQUIRED", "CANCELLED", "STARTUP_FAILURE", "STALE"}
PASSING_CONCLUSIONS = {"SUCCESS", "SKIPPED", "NEUTRAL"}
QUIET = dt.timedelta(minutes=3)
STALE_ROUND = dt.timedelta(minutes=60)
STATE = None

# Each thread's opener says whose it is; its latest 50 comments hold any new replies.
THREADS_QUERY = """
query($owner:String!,$name:String!,$n:Int!,$after:String){repository(owner:$owner,name:$name){pullRequest(number:$n){
  reviewThreads(first:100,after:$after){pageInfo{hasNextPage endCursor} nodes{
    opener:comments(first:1){nodes{author{login} body createdAt}}
    recent:comments(last:50){nodes{author{login} body createdAt}}}}}}}
"""

# `gh pr checks --json completedAt` reports a zero time for commit statuses, so read the rollup directly.
CHECKS_QUERY = """
query($owner:String!,$name:String!,$n:Int!){repository(owner:$owner,name:$name){pullRequest(number:$n){
  commits(last:1){nodes{commit{oid statusCheckRollup{contexts(first:100){pageInfo{hasNextPage} nodes{
    __typename
    ... on CheckRun{name isRequired(pullRequestNumber:$n) status conclusion startedAt completedAt
                    checkSuite{workflowRun{event workflow{name}}}}
    ... on StatusContext{context isRequired(pullRequestNumber:$n) state createdAt}
  }}}}}}}}}
"""


def gh(*args):
    out = subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout
    return json.loads(out) if out.strip() else None


def ts(value):
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def now():
    return dt.datetime.now(dt.UTC)


def key_of(repo, n):
    return f"{repo}#{n}"


def parse_key(raw):
    repo, _, number = raw.partition("#")
    if repo.count("/") != 1 or not number.isdigit():
        raise SystemExit(f"key must look like owner/name#N, got {raw!r}")
    return key_of(repo, int(number))


def load_state():
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    state.setdefault("prs", {})
    state.setdefault("scan", {})
    return state


def save_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2))


def login(c):
    return (c.get("user") or c.get("author") or {}).get("login")


def local_clones(root):
    """Map lower-cased owner/name to the local clone path, from each clone's origin remote."""
    clones = {}
    for d in root.iterdir() if root.is_dir() else []:
        if not (d / ".git").exists():
            continue
        url = subprocess.run(
            ["git", "-C", str(d), "remote", "get-url", "origin"], capture_output=True, text=True
        ).stdout.strip()
        if "github.com" not in url:
            continue
        slug = url.split("github.com", 1)[1].lstrip(":/").removesuffix(".git").lower()
        clones.setdefault(slug, str(d))
    return clones


def required_checks_pass(repo, number):
    proc = subprocess.run(
        ["gh", "pr", "checks", str(number), "--repo", repo, "--required", "--json", "bucket"],
        capture_output=True,
        text=True,
    )
    if not proc.stdout.strip():
        return False
    buckets = [c["bucket"] for c in json.loads(proc.stdout)]
    return bool(buckets) and all(b == "pass" for b in buckets)


def checks_status(repo, number, head, required_only=True):
    """(status, when the last finished check finished) for the PR's head commit, from one rollup read.

    status is "failing" when any check failed, was cancelled, timed out, needs action, hit a startup
    failure or went stale; otherwise "pending" when any is still running or queued; otherwise "pass"
    (skipped and neutral checks count as passing, as GitHub treats them); "none" when there are no
    checks; and "unknown" when this read can't judge, such as for another head commit or more than 100
    checks. Only the newest run of each check counts, as in `gh pr checks`. A check run finishes at its
    completedAt, a commit status at its createdAt. With required_only, only required checks count.
    Raises when gh fails, so the caller can retry later.
    """
    owner, name = repo.split("/")
    data = gh(
        "api",
        "graphql",
        "-F",
        f"owner={owner}",
        "-F",
        f"name={name}",
        "-F",
        f"n={number}",
        "-f",
        f"query={CHECKS_QUERY}",
    )
    commits = data["data"]["repository"]["pullRequest"]["commits"]["nodes"]
    commit = commits[0]["commit"] if commits else None
    if not commit or commit["oid"] != head:
        return "unknown", None
    rollup = commit.get("statusCheckRollup")
    if not rollup:
        return "none", None
    if rollup["contexts"]["pageInfo"]["hasNextPage"]:
        return "unknown", None

    newest = {}
    for c in rollup["contexts"]["nodes"]:
        if required_only and not c.get("isRequired"):
            continue
        if c["__typename"] == "CheckRun":
            run = (c.get("checkSuite") or {}).get("workflowRun") or {}
            key = ("run", c.get("name"), ((run.get("workflow") or {}).get("name")), run.get("event"))
            started, finished, conclusion = c.get("startedAt"), c.get("completedAt"), c.get("conclusion")
            outcome = (
                "failing"
                if conclusion in FAILING_CONCLUSIONS
                else "pass"
                if conclusion in PASSING_CONCLUSIONS
                else "pending"
            )
        else:
            key = ("status", c.get("context"))
            started = finished = c.get("createdAt")
            state = c.get("state")
            outcome = "failing" if state in ("FAILURE", "ERROR") else "pass" if state == "SUCCESS" else "pending"
            if outcome == "pending":
                finished = None
        order = ts(started) if started else dt.datetime.min.replace(tzinfo=dt.UTC)
        if key not in newest or order >= newest[key][0]:
            newest[key] = (order, outcome, ts(finished) if finished and outcome != "pending" else None)
    if not newest:
        return "none", None
    outcomes = {outcome for _, outcome, _ in newest.values()}
    status = "failing" if "failing" in outcomes else "pending" if "pending" in outcomes else "pass"
    times = [finished for _, _, finished in newest.values() if finished]
    return status, (max(times) if times else None)


def awaiting(review):
    """What a comment-only review waits on, as a set, or None for reviews written before the awaiting line."""
    lines = AWAITING_MARKER.findall(review.get("body") or "")
    return set(lines[-1].split()) & set(AWAITING_KINDS) if lines else None


def read_at(review):
    """When the review's round last read the PR's threads, falling back to when the review was submitted."""
    stamps = READ_AT_MARKER.findall(review.get("body") or "")
    try:
        return ts(stamps[-1]) if stamps else ts(review["submitted_at"])
    except ValueError:
        return ts(review["submitted_at"])


def review_threads(owner, name, n):
    """Every review thread on the PR, page by page."""
    threads, after = [], None
    while True:
        args = ["api", "graphql", "-F", f"owner={owner}", "-F", f"name={name}", "-F", f"n={n}"]
        args += ["-f", f"after={after}"] if after else []
        data = gh(*args, "-f", f"query={THREADS_QUERY}")
        connection = data["data"]["repository"]["pullRequest"]["reviewThreads"]
        threads += connection.get("nodes") or []
        page = connection.get("pageInfo") or {}
        if not page.get("hasNextPage") or not page.get("endCursor"):
            return threads
        after = page["endCursor"]


def verdict_kind(review):
    """approve | ready | changes | comment, from the verdict marker or, for older reviews, the state and body."""
    body = review.get("body") or ""
    kinds = VERDICT_MARKER.findall(body)
    if kinds:
        return kinds[-1]  # the marker ends the body; an earlier one is quoted text
    if review.get("state") == "APPROVED":
        return "approve"
    if review.get("state") == "CHANGES_REQUESTED":
        return "changes"
    return "ready" if body.startswith("Reviewed at ") else "comment"


def evaluate(repo, me, pr):
    """Return (is_deep_reviewed, reasons, latest_review_time) for one PR."""
    owner, name = repo.split("/")
    n = pr["number"]
    all_reviews = gh("api", "--paginate", f"repos/{repo}/pulls/{n}/reviews") or []
    reviews = [r for r in all_reviews if login(r) == me and r.get("submitted_at")]
    if not reviews:
        return False, [], None
    comments = gh("api", "--paginate", f"repos/{repo}/pulls/{n}/comments")
    marked = [c for c in comments if login(c) == me and c["body"].startswith(MARKER)]
    verdicts = [
        r
        for r in reviews
        if VERDICT_MARKER.search(r.get("body") or "") or (r.get("body") or "").startswith(VERDICT_BODY_PREFIXES)
    ]
    latest = max(reviews, key=lambda r: ts(r["submitted_at"]))
    if not marked and not verdicts:
        return False, [], ts(latest["submitted_at"])

    # When my latest round last read the threads; replies after it (or after my own last reply in a
    # thread) are new. Per thread, so my comments elsewhere on the PR don't hide an answer.
    seen = read_at(latest)
    reasons = []
    if latest.get("commit_id") and latest["commit_id"] != pr["headRefOid"]:
        reasons.append(f"head moved {latest['commit_id'][:10]} -> {pr['headRefOid'][:10]}")

    replies = 0
    for t in review_threads(owner, name, n):
        opener = (t.get("opener") or {}).get("nodes") or []
        if not opener or login(opener[0]) != me or not opener[0]["body"].startswith(MARKER):
            continue
        recent = (t.get("recent") or {}).get("nodes") or []
        since = max([seen] + [ts(c["createdAt"]) for c in recent if login(c) == me])
        replies += sum(1 for c in recent if login(c) != me and ts(c["createdAt"]) > since)
    if replies:
        reasons.append(f"{replies} new repl{'y' if replies == 1 else 'ies'} in your threads")

    # The latest review that carries a verdict marker; for older reviews, the latest with a body.
    bodied = [r for r in reviews if VERDICT_MARKER.search(r.get("body") or "")] or [
        r for r in reviews if (r.get("body") or "").strip()
    ]
    last_verdict = max(bodied, key=lambda r: ts(r["submitted_at"])) if bodied else None
    if (
        not reasons
        and last_verdict
        and last_verdict["state"] == "COMMENTED"
        and verdict_kind(last_verdict) == "comment"
        and last_verdict.get("commit_id") == pr["headRefOid"]
    ):
        reasons += comment_only_follow_up(repo, n, pr, last_verdict, all_reviews)
    return True, reasons, ts(latest["submitted_at"])


def comment_only_follow_up(repo, n, pr, verdict, all_reviews):
    """Reasons to follow up a comment-only verdict at the current head, by what it waits on."""
    reviewed_at = ts(verdict["submitted_at"])
    waits = awaiting(verdict)
    if waits is None:  # written before the awaiting line: every required check must pass
        status, finished = "pending", None
        if required_checks_pass(repo, n):
            try:
                status, finished = checks_status(repo, n, pr["headRefOid"])
            except (subprocess.CalledProcessError, KeyError, TypeError, ValueError):
                pass  # unreadable this time: treat as pending so a later scan retries
        if status != "pass":
            return ["__checks_pending__"]
        return (
            ["last verdict was comment-only and required checks now pass"]
            if finished and finished > reviewed_at
            else []
        )

    reasons = []
    if "question" in waits:
        author = login(pr)
        since = read_at(verdict)
        if any(
            login(r) == author and r.get("submitted_at") and ts(r["submitted_at"]) > since for r in all_reviews
        ) or any(
            login(c) == author and ts(c["created_at"]) > since
            for c in gh("api", "--paginate", f"repos/{repo}/issues/{n}/comments") or []
        ):
            reasons.append("the author commented while an intent question was open")
    rule = "checks" if "checks" in waits else "all-checks" if "all-checks" in waits else None
    if rule:
        try:
            status, finished = checks_status(repo, n, pr["headRefOid"], required_only=rule == "checks")
        except (subprocess.CalledProcessError, KeyError, TypeError, ValueError):
            status, finished = "unknown", None
        # A review only waits on checks when one failed or couldn't be read, so any pass is news.
        if status == "pass" or (rule == "all-checks" and status == "none"):
            reasons.append("last verdict was comment-only and the checks it waited on now allow approval")
        elif rule == "checks" and status == "pending" and finished and finished > reviewed_at:
            reasons.append("last verdict was comment-only and the checks it waited on now allow approval")
        else:
            reasons.append("__checks_pending__")
    return reasons


def scan(clones_root, review_requests):
    me = gh("api", "user", "--jq", "{login: .login}")["login"]
    state = load_state()
    clones = local_clones(clones_root)

    requested_hits = []
    if review_requests:
        requested_hits = (
            gh(
                "search",
                "prs",
                "user-review-requested:@me",
                "--state",
                "open",
                "--limit",
                "100",
                "--json",
                "repository,number",
            )
            or []
        )
    requested = {key_of(h["repository"]["nameWithOwner"], h["number"]) for h in requested_hits}
    repos = {h["repository"]["nameWithOwner"] for h in requested_hits} | {k.split("#")[0] for k in state["prs"]}

    candidates, no_clone, open_keys = [], [], set()
    for repo in sorted(repos):
        prs = (
            gh(
                "pr",
                "list",
                "--repo",
                repo,
                "--state",
                "open",
                "--limit",
                "200",
                "--json",
                "number,author,headRefOid,isDraft,title,updatedAt,url",
            )
            or []
        )
        for pr in prs:
            key = key_of(repo, pr["number"])
            open_keys.add(key)
            if login(pr) == me or pr["isDraft"]:
                continue
            record = state["prs"].get(key) or {}
            cached = state["scan"].get(key)
            if (
                cached
                and cached["updatedAt"] == pr["updatedAt"]
                and not cached.get("recheck")
                and not record.get("in_progress_since")
                and key not in requested
            ):
                continue

            deep, reasons, latest_review_at = evaluate(repo, me, pr)
            checks_pending = "__checks_pending__" in reasons
            reasons = [r for r in reasons if r != "__checks_pending__"]
            if (
                record.get("in_progress_since")
                and latest_review_at
                and latest_review_at > ts(record["in_progress_since"])
            ):
                record["in_progress_since"] = None
            if key in requested:
                reasons.append("review re-requested" if deep else "review requested")
            state["scan"][key] = {
                "updatedAt": pr["updatedAt"],
                "deep": deep,
                "recheck": bool(reasons) or checks_pending,
            }
            if not reasons:
                continue
            path = clones.get(repo.lower())
            if not path:
                if not record.get("notified"):
                    no_clone.append(
                        {
                            "key": key,
                            "repo": repo,
                            "pr": pr["number"],
                            "title": pr["title"],
                            "author": login(pr),
                            "url": pr["url"],
                        }
                    )
                continue
            candidates.append((repo, pr, deep, reasons, path))
    state["scan"] = {k: v for k, v in state["scan"].items() if k in open_keys}

    running = sorted(
        k
        for k, r in state["prs"].items()
        if r.get("in_progress_since") and now() - ts(r["in_progress_since"]) < STALE_ROUND
    )
    # Keep the empty capped list for existing scanner consumers.
    due, waiting, capped = [], [], []
    for repo, pr, deep, reasons, path in candidates:
        key = key_of(repo, pr["number"])
        record = state["prs"].get(key) or {}
        entry = {
            "key": key,
            "repo": repo,
            "pr": pr["number"],
            "path": path,
            "kind": "follow-up" if deep else "first",
            "round": record.get("rounds", 0) + 1,
            "title": pr["title"],
            "author": login(pr),
            "head": pr["headRefOid"],
            "url": pr["url"],
            "reasons": reasons,
            "status_comment_id": record.get("status_comment_id"),
        }
        if key in running:
            waiting.append({**entry, "why": "a round on this PR is still running"})
        elif running:
            waiting.append({**entry, "why": f"another round is running ({running[0]}); one at a time"})
        elif now() - ts(pr["updatedAt"]) < QUIET:
            waiting.append({**entry, "why": "activity in the last 3 minutes"})
        else:
            due.append(entry)
    due.sort(key=lambda e: (e["kind"] != "follow-up", e["key"]))
    save_state(state)
    return {"me": me, "due": due, "waiting": waiting, "capped": capped, "no_clone": no_clone}


def update_record(key, action, value=None):
    state = load_state()
    record = state["prs"].setdefault(key, {"rounds": 0})
    if action == "mark":
        record["rounds"] = record.get("rounds", 0) + 1
        record["in_progress_since"] = now().isoformat()
    elif action == "done":
        record["in_progress_since"] = None
        state["scan"].pop(key, None)
    elif action == "notified":
        record["notified"] = True
    elif action == "status_comment":
        record["status_comment_id"] = value
    elif action == "track":
        record["tracked"] = True
    save_state(state)
    print(json.dumps({key: record}))


def main():
    global STATE
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--clones-root", default="~/Documents/GitHub")
    ap.add_argument("--review-requests", default="true")
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--track")
    group.add_argument("--mark")
    group.add_argument("--done")
    group.add_argument("--notified")
    group.add_argument("--status-comment", nargs=2, metavar=("KEY", "COMMENT_ID"))
    a = ap.parse_args()
    STATE = pathlib.Path(a.data_dir).expanduser() / "state.json"
    if a.track:
        update_record(parse_key(a.track), "track")
    elif a.mark:
        update_record(parse_key(a.mark), "mark")
    elif a.done:
        update_record(parse_key(a.done), "done")
    elif a.notified:
        update_record(parse_key(a.notified), "notified")
    elif a.status_comment:
        update_record(parse_key(a.status_comment[0]), "status_comment", int(a.status_comment[1]))
    else:
        root = pathlib.Path(a.clones_root).expanduser()
        print(json.dumps(scan(root, a.review_requests.lower() == "true"), indent=2))


if __name__ == "__main__":
    sys.exit(main())
