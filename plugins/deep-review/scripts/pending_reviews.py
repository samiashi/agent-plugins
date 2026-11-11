#!/usr/bin/env python3
"""List open PRs, across the user's repositories, that need an automatic deep-review round.

Two kinds of round:
  first      the authenticated user is directly requested as a reviewer and has
             not deep-reviewed the PR yet
  follow-up  the PR has the user's deep review (a review comment opening with the
             skill's marker, or a review body carrying a verdict marker or starting
             with a legacy verdict line), and since the user's latest review:
               - the head commit moved, or
               - someone else replied in one of the user's deep-review threads, or
               - the user's review was re-requested, or
               - the latest verdict was comment-only for lack of a verdict (not
                 a "ready for approval" review), every required check now passes,
                 and at least one of them finished after that review. Checks that
                 had already passed when the review was posted don't count, so a
                 comment-only verdict for another reason is not reviewed again.

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

MARKER = "I checked and found this potential issue to look into"
VERDICT_BODY_PREFIXES = ("Blocking:", "Reviewed at ")  # legacy bodies, before verdict markers
# The review skill ends every review body with <!-- deep-review-verdict: <kind> -->.
VERDICT_KINDS = ("approve", "ready", "changes", "comment")
VERDICT_MARKER = re.compile(r"<!--\s*deep-review-verdict:\s*(" + "|".join(VERDICT_KINDS) + r")\s*-->")
NOT_FINISHED = "0001-01-01T00:00:00Z"  # gh's completedAt for a check that hasn't finished
QUIET = dt.timedelta(minutes=3)
STALE_ROUND = dt.timedelta(minutes=60)
STATE = None

THREADS_QUERY = """
query($owner:String!,$name:String!,$n:Int!){repository(owner:$owner,name:$name){pullRequest(number:$n){
  reviewThreads(first:100){nodes{comments(first:50){nodes{author{login} body createdAt}}}}}}}
"""


def gh(*args):
    out = subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout
    return json.loads(out) if out.strip() else None


def ts(value):
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def now():
    return dt.datetime.now(dt.timezone.utc)


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


def required_checks_finished_at(repo, number):
    """When the most recently finished required check finished, or None if none has."""
    proc = subprocess.run(
        ["gh", "pr", "checks", str(number), "--repo", repo, "--required", "--json", "completedAt"],
        capture_output=True,
        text=True,
    )
    if not proc.stdout.strip():
        return None
    times = [c.get("completedAt") for c in json.loads(proc.stdout)]
    finished = [ts(t) for t in times if t and t != NOT_FINISHED]
    return max(finished) if finished else None


def verdict_kind(review):
    """approve | ready | changes | comment, from the verdict marker or, for older reviews, the state and body."""
    body = review.get("body") or ""
    match = VERDICT_MARKER.search(body)
    if match:
        return match.group(1)
    if review.get("state") == "APPROVED":
        return "approve"
    if review.get("state") == "CHANGES_REQUESTED":
        return "changes"
    return "ready" if body.startswith("Reviewed at ") else "comment"


def evaluate(repo, me, pr):
    """Return (is_deep_reviewed, reasons, latest_review_time) for one PR."""
    owner, name = repo.split("/")
    n = pr["number"]
    reviews = [
        r
        for r in gh("api", "--paginate", f"repos/{repo}/pulls/{n}/reviews")
        if login(r) == me and r.get("submitted_at")
    ]
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

    my_last_seen = max(
        [ts(r["submitted_at"]) for r in reviews] + [ts(c["created_at"]) for c in comments if login(c) == me]
    )
    reasons = []
    if latest.get("commit_id") and latest["commit_id"] != pr["headRefOid"]:
        reasons.append(f"head moved {latest['commit_id'][:10]} -> {pr['headRefOid'][:10]}")

    threads = gh(
        "api", "graphql", "-F", f"owner={owner}", "-F", f"name={name}", "-F", f"n={n}", "-f", f"query={THREADS_QUERY}"
    )
    replies = 0
    for t in threads["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]:
        cs = t["comments"]["nodes"]
        if not cs or login(cs[0]) != me or not cs[0]["body"].startswith(MARKER):
            continue
        replies += sum(1 for c in cs[1:] if login(c) != me and ts(c["createdAt"]) > my_last_seen)
    if replies:
        reasons.append(f"{replies} new repl{'y' if replies == 1 else 'ies'} in your threads")

    bodied = [r for r in reviews if (r.get("body") or "").strip()]
    last_verdict = max(bodied, key=lambda r: ts(r["submitted_at"])) if bodied else None
    if (
        not reasons
        and last_verdict
        and last_verdict["state"] == "COMMENTED"
        and verdict_kind(last_verdict) == "comment"
        and last_verdict.get("commit_id") == pr["headRefOid"]
    ):
        if not required_checks_pass(repo, n):
            reasons.append("__checks_pending__")
        else:
            finished = required_checks_finished_at(repo, n)
            if finished and finished > ts(last_verdict["submitted_at"]):
                reasons.append("last verdict was comment-only and required checks now pass")
    return True, reasons, ts(latest["submitted_at"])


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
