import contextlib
import datetime as dt
import importlib.util
import io
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "plugins" / "deep-review" / "scripts" / "pending_reviews.py"


def load_module():
    spec = importlib.util.spec_from_file_location("pending_reviews", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    dont_write = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = dont_write
    return module


pr_mod = load_module()

ME = "me"
REPO = "Octo/App"
OTHER_REPO = "Zeta/Lib"
HEAD = "a1" * 20
OLD = "b2" * 20
NOW = dt.datetime(2026, 11, 11, 12, 0, tzinfo=dt.UTC)
MARKED = pr_mod.MARKER + ": the cache key ignores the tenant."
READY = "Reviewed at a1a1a1a. No blocking issues; ready for approval. 0 non-blocking comments inline."
COMMENT_ONLY = "No verdict: required check `ci` is failing. 1 non-blocking finding inline."
SKILL = REPO_ROOT / "plugins/deep-review/skills/review/SKILL.md"


def marked_body(text, kind):
    return f"{text}\n\n<!-- deep-review-verdict: {kind} -->"


def ago(minutes):
    return (NOW - dt.timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def make_pr(number, author="alice", head=HEAD, draft=False, updated=60):
    return {
        "number": number,
        "author": {"login": author},
        "headRefOid": head,
        "isDraft": draft,
        "title": f"PR {number}",
        "updatedAt": ago(updated),
        "url": f"https://github.com/x/y/pull/{number}",
    }


def review(at=90, commit=HEAD, body=READY, state="COMMENTED", author=ME):
    return {"user": {"login": author}, "submitted_at": ago(at), "commit_id": commit, "body": body, "state": state}


def rest_comment(at=90, body=MARKED, author=ME):
    return {"user": {"login": author}, "created_at": ago(at), "body": body}


def thread(*comments):
    nodes = [{"author": {"login": a}, "body": b, "createdAt": ago(m)} for a, b, m in comments]
    return {"opener": {"nodes": nodes[:1]}, "recent": {"nodes": nodes[-50:]}}


class FakeGitHub:
    def __init__(self):
        self.requested = []
        self.open_prs = {}
        self.reviews = {}
        self.comments = {}
        self.threads = {}
        self.thread_pages = {}  # key -> list of thread lists, one per page
        self.issue_comments = {}
        self.calls = []

    def add_pr(self, repo, number, **kwargs):
        pr = make_pr(number, **kwargs)
        self.open_prs.setdefault(repo, []).append(pr)
        return pr

    def deep_review(self, key, commit=HEAD, body=READY, at=90):
        self.reviews[key] = [review(at=at, commit=commit, body=body)]
        self.comments[key] = [rest_comment(at=at)]

    def review_fetches(self, key):
        repo, n = key.split("#")
        return sum(1 for c in self.calls if c[:3] == ("api", "--paginate", f"repos/{repo}/pulls/{n}/reviews"))

    def __call__(self, *args):
        self.calls.append(args)
        if args[:2] == ("api", "user"):
            return {"login": ME}
        if args[:2] == ("search", "prs"):
            hits = [k.split("#") for k in self.requested]
            return [{"repository": {"nameWithOwner": r}, "number": int(n)} for r, n in hits]
        if args[:2] == ("pr", "list"):
            return self.open_prs.get(args[args.index("--repo") + 1], [])
        if args[:2] == ("api", "graphql"):
            fields = dict(args[i + 1].split("=", 1) for i, a in enumerate(args) if a in ("-F", "-f"))
            key = f"{fields['owner']}/{fields['name']}#{fields['n']}"
            pages = self.thread_pages.get(key) or [self.threads.get(key, [])]
            page = int(fields.get("after", "0"))
            more = page + 1 < len(pages)
            info = {"hasNextPage": more, "endCursor": str(page + 1) if more else None}
            return {
                "data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": pages[page], "pageInfo": info}}}}
            }
        if args[:2] == ("api", "--paginate") and "/issues/" in args[2]:
            _, owner, name, _, n, _ = args[2].split("/")
            return self.issue_comments.get(f"{owner}/{name}#{n}", [])
        if args[:2] == ("api", "--paginate"):
            _, owner, name, _, n, kind = args[2].split("/")
            return {"reviews": self.reviews, "comments": self.comments}[kind].get(f"{owner}/{name}#{n}", [])
        raise AssertionError(f"unexpected gh call {args}")


class StateTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data_dir = pathlib.Path(tmp.name)
        self.state_path = self.data_dir / "state.json"
        for patcher in (
            mock.patch.object(pr_mod, "STATE", self.state_path),
            mock.patch.object(pr_mod, "now", return_value=NOW),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def write_state(self, prs=None, scan=None):
        self.state_path.write_text(json.dumps({"prs": prs or {}, "scan": scan or {}}))

    def read_state(self):
        return json.loads(self.state_path.read_text())

    def update(self, key, action, value=None):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            pr_mod.update_record(key, action, value)
        return json.loads(out.getvalue())


class ParseKeyTests(unittest.TestCase):
    def test_valid_key_is_normalised(self):
        self.assertEqual(pr_mod.parse_key("Octo/App#007"), "Octo/App#7")

    def test_malformed_keys_exit(self):
        for raw in ("Octo/App", "Octo/App#abc", "Octo/App#", "Octo/App#-1", "Octo/App/extra#1", "App#1"):
            with self.subTest(raw=raw), self.assertRaises(SystemExit):
                pr_mod.parse_key(raw)


class UpdateRecordTests(StateTestCase):
    def test_mark_increments_rounds_and_starts_round(self):
        self.update("Octo/App#1", "mark")
        printed = self.update("Octo/App#1", "mark")
        record = self.read_state()["prs"]["Octo/App#1"]
        self.assertEqual(record["rounds"], 2)
        self.assertEqual(pr_mod.ts(record["in_progress_since"]), NOW)
        self.assertEqual(printed, {"Octo/App#1": record})

    def test_done_clears_round_and_only_its_scan_cache(self):
        self.write_state(
            prs={"Octo/App#1": {"rounds": 3, "in_progress_since": ago(5)}},
            scan={"Octo/App#1": {"updatedAt": ago(5)}, "Octo/App#2": {"updatedAt": ago(5)}},
        )
        self.update("Octo/App#1", "done")
        state = self.read_state()
        self.assertIsNone(state["prs"]["Octo/App#1"]["in_progress_since"])
        self.assertEqual(state["prs"]["Octo/App#1"]["rounds"], 3)
        self.assertEqual(set(state["scan"]), {"Octo/App#2"})

    def test_track_notified_and_status_comment_accumulate(self):
        self.update("Octo/App#1", "track")
        self.update("Octo/App#1", "notified")
        self.update("Octo/App#1", "status_comment", 4242)
        record = self.read_state()["prs"]["Octo/App#1"]
        self.assertEqual(record, {"rounds": 0, "tracked": True, "notified": True, "status_comment_id": 4242})


class MainTests(StateTestCase):
    def run_main(self, *args):
        argv = ["pending_reviews.py", "--data-dir", str(self.data_dir / "nested"), *args]
        with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()) as out:
            pr_mod.main()
        return out.getvalue()

    def test_cli_actions_write_state_under_data_dir(self):
        self.run_main("--mark", "Octo/App#03")
        self.run_main("--status-comment", "Octo/App#3", "991")
        state = json.loads((self.data_dir / "nested" / "state.json").read_text())
        self.assertEqual(state["prs"]["Octo/App#3"]["rounds"], 1)
        self.assertEqual(state["prs"]["Octo/App#3"]["status_comment_id"], 991)
        self.assertFalse(self.state_path.exists())

    def test_cli_rejects_malformed_key_without_writing_state(self):
        with self.assertRaises(SystemExit):
            self.run_main("--track", "Octo/App#x")
        self.assertFalse((self.data_dir / "nested").exists())


class EvaluateTests(unittest.TestCase):
    key = "Octo/App#1"

    def setUp(self):
        self.fake = FakeGitHub()
        self.checks = mock.Mock(return_value=True)
        # By default the required checks pass and finished after any review in these tests.
        self.state = mock.Mock(return_value=("pass", NOW))
        for patcher in (
            mock.patch.object(pr_mod, "gh", self.fake),
            mock.patch.object(pr_mod, "required_checks_pass", self.checks),
            mock.patch.object(pr_mod, "checks_status", self.state),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def evaluate(self, head=HEAD):
        return pr_mod.evaluate(REPO, ME, make_pr(1, head=head))

    def test_no_submitted_review_by_me_is_not_deep(self):
        self.fake.reviews[self.key] = [
            review(author="bob"),
            {**review(), "submitted_at": None},
        ]
        self.assertEqual(self.evaluate(), (False, [], None))

    def test_review_without_marker_or_verdict_is_not_deep(self):
        self.fake.reviews[self.key] = [review(at=30, body="Looks fine to me")]
        self.fake.comments[self.key] = [rest_comment(body="nit: rename this")]
        self.assertEqual(self.evaluate(), (False, [], NOW - dt.timedelta(minutes=30)))

    def test_head_moved_since_latest_review(self):
        self.fake.reviews[self.key] = [review(at=120, commit="c3" * 20), review(at=60, commit=OLD)]
        deep, reasons, latest = self.evaluate()
        self.assertTrue(deep)
        self.assertEqual(reasons, [f"head moved {OLD[:10]} -> {HEAD[:10]}"])
        self.assertEqual(latest, NOW - dt.timedelta(minutes=60))

    def test_reply_by_someone_else_after_my_last_activity(self):
        self.fake.deep_review(self.key, at=90)
        self.fake.threads[self.key] = [thread((ME, MARKED, 90), ("alice", "Fixed", 30), ("bob", "+1", 20))]
        deep, reasons, _ = self.evaluate()
        self.assertTrue(deep)
        self.assertEqual(reasons, ["2 new replies in your threads"])

    def test_replies_that_do_not_count(self):
        cases = {
            "before my later reply in the thread": [
                thread((ME, MARKED, 90), ("alice", "Fixed", 30), (ME, "Thanks", 10))
            ],
            "my own reply": [thread((ME, MARKED, 90), (ME, "Still wrong", 20))],
            "thread opened by someone else": [thread(("alice", MARKED, 90), ("bob", "Agreed", 20))],
            "my thread without the marker": [thread((ME, "Why this?", 90), ("alice", "Because", 20))],
            "before my round read the threads": [thread((ME, MARKED, 90), ("alice", "Fixed", 60))],
        }
        for name, threads in cases.items():
            with self.subTest(name):
                body = READY + ("\n<!-- deep-review-read-at: " + ago(50) + " -->" if "read" in name else "")
                self.fake.deep_review(self.key, at=90, body=body)
                self.fake.threads[self.key] = threads
                self.assertEqual(self.evaluate(), (True, [], NOW - dt.timedelta(minutes=90)))

    def test_my_comment_elsewhere_on_the_pr_does_not_hide_a_reply(self):
        self.fake.deep_review(self.key, at=90)
        self.fake.comments[self.key].append(rest_comment(at=10, body="Unrelated note on another line"))
        self.fake.threads[self.key] = [thread((ME, MARKED, 90), ("alice", "Fixed", 30))]
        self.assertEqual(self.evaluate()[1], ["1 new reply in your threads"])

    def test_a_reply_posted_while_my_round_ran_still_counts(self):
        # The round read the threads at -50m and submitted at -30m; the answer came at -40m.
        body = READY + "\n<!-- deep-review-read-at: " + ago(50) + " -->"
        self.fake.deep_review(self.key, at=30, body=body)
        self.fake.threads[self.key] = [thread((ME, MARKED, 90), ("alice", "Intended", 40))]
        self.assertEqual(self.evaluate()[1], ["1 new reply in your threads"])

    def test_replies_on_later_pages_of_threads_count(self):
        filler = [thread(("bob", "a note", 80)) for _ in range(3)]
        self.fake.deep_review(self.key, at=90)
        self.fake.thread_pages[self.key] = [filler, [thread((ME, MARKED, 90), ("alice", "Fixed", 20))]]
        self.assertEqual(self.evaluate()[1], ["1 new reply in your threads"])
        pages = [c for c in self.fake.calls if c[:2] == ("api", "graphql")]
        self.assertEqual(len(pages), 2)
        self.assertIn("after=1", pages[1])

    def test_comment_only_verdict_follows_up_when_checks_pass(self):
        self.fake.deep_review(self.key, body=COMMENT_ONLY)
        self.assertEqual(self.evaluate()[1], ["last verdict was comment-only and required checks now pass"])
        self.checks.assert_called_once_with(REPO, 1)

    def test_comment_only_verdict_with_failing_checks_is_pending(self):
        self.checks.return_value = False
        self.fake.deep_review(self.key, body=COMMENT_ONLY)
        self.assertEqual(self.evaluate(), (True, ["__checks_pending__"], NOW - dt.timedelta(minutes=90)))

    def test_ready_for_approval_verdict_does_not_retrigger(self):
        self.fake.deep_review(self.key, body=READY)
        self.assertEqual(self.evaluate(), (True, [], NOW - dt.timedelta(minutes=90)))
        self.checks.assert_not_called()

    def test_comment_only_verdict_on_old_head_reports_head_moved_only(self):
        self.fake.deep_review(self.key, commit=OLD, body=COMMENT_ONLY)
        self.assertEqual(self.evaluate()[1], [f"head moved {OLD[:10]} -> {HEAD[:10]}"])
        self.checks.assert_not_called()

    def test_comment_only_review_without_findings_follows_up_when_checks_pass(self):
        # A clean PR reviewed comment-only because a required check failed has no inline findings;
        # the verdict marker alone makes it a deep review.
        body = marked_body("No verdict: required check `ci` is failing. 0 findings.", "comment")
        self.fake.reviews[self.key] = [review(body=body)]
        deep, reasons, _ = self.evaluate()
        self.assertTrue(deep)
        self.assertEqual(reasons, ["last verdict was comment-only and required checks now pass"])

    def test_checks_that_finished_before_the_comment_only_review_do_not_follow_up(self):
        self.fake.deep_review(self.key, body=COMMENT_ONLY, at=30)
        self.state.return_value = ("pass", NOW - dt.timedelta(minutes=45))
        self.assertEqual(self.evaluate(), (True, [], NOW - dt.timedelta(minutes=30)))
        self.state.assert_called_once_with(REPO, 1, HEAD)

    def test_checks_finishing_exactly_at_the_review_do_not_follow_up(self):
        self.fake.deep_review(self.key, body=COMMENT_ONLY, at=30)
        self.state.return_value = ("pass", NOW - dt.timedelta(minutes=30))
        self.assertEqual(self.evaluate()[1], [])

    def test_finish_time_is_compared_with_the_verdict_not_a_later_empty_review(self):
        # A REST thread reply creates an empty-body review after the verdict; it isn't the verdict.
        self.fake.deep_review(self.key, body=COMMENT_ONLY, at=60)
        self.fake.reviews[self.key].append(review(at=10, body=""))
        self.state.return_value = ("pass", NOW - dt.timedelta(minutes=30))
        self.assertEqual(self.evaluate()[1], ["last verdict was comment-only and required checks now pass"])

    def test_rollup_that_does_not_pass_is_pending(self):
        # required_checks_pass and the rollup read can disagree, e.g. a check re-ran between them.
        self.fake.deep_review(self.key, body=COMMENT_ONLY)
        self.state.return_value = ("pending", NOW)
        self.assertEqual(self.evaluate()[1], ["__checks_pending__"])

    def test_unreadable_rollup_is_pending_so_a_later_scan_retries(self):
        self.fake.deep_review(self.key, body=COMMENT_ONLY)
        for error in (subprocess.CalledProcessError(1, ["gh"]), json.JSONDecodeError("x", "", 0), TypeError("None")):
            with self.subTest(error=type(error).__name__):
                self.state.side_effect = error
                self.assertEqual(self.evaluate()[1], ["__checks_pending__"])

    def test_latest_marked_verdict_wins_over_a_later_unmarked_review(self):
        self.fake.reviews[self.key] = [
            review(at=60, body=marked_body("Reviewed at a1a1a1a. No blocking issues.", "ready")),
            review(at=10, body="Thanks, will merge after CI."),
        ]
        self.fake.comments[self.key] = []
        self.assertEqual(self.evaluate()[1], [])
        self.checks.assert_not_called()

    def test_the_closing_marker_wins_over_a_quoted_one(self):
        body = marked_body("Finding quotes `<!-- deep-review-verdict: ready -->` in a title.", "comment")
        self.assertEqual(pr_mod.verdict_kind(review(body=body)), "comment")

    def test_passing_checks_without_a_finish_time_do_not_follow_up(self):
        self.fake.deep_review(self.key, body=COMMENT_ONLY)
        self.state.return_value = ("pass", None)
        self.assertEqual(self.evaluate()[1], [])

    def test_marked_ready_verdict_does_not_retrigger_whatever_its_wording(self):
        self.fake.deep_review(self.key, body=marked_body("No blocking issues at a1a1a1a.", "ready"))
        self.assertEqual(self.evaluate(), (True, [], NOW - dt.timedelta(minutes=90)))
        self.checks.assert_not_called()

    def test_marked_comment_verdict_follows_up_even_with_a_reviewed_at_prefix(self):
        body = marked_body("Reviewed at a1a1a1a without a verdict: a lens could not run.", "comment")
        self.fake.deep_review(self.key, body=body)
        self.assertEqual(self.evaluate()[1], ["last verdict was comment-only and required checks now pass"])

    def test_verdict_marker_alone_makes_a_review_deep(self):
        for kind in ("approve", "ready", "changes", "comment"):
            with self.subTest(kind=kind):
                self.fake.reviews[self.key] = [review(at=30, body=marked_body("Some summary.", kind))]
                self.fake.comments[self.key] = []
                self.assertTrue(self.evaluate()[0])

    def awaiting_review(self, waits, at=30, read=None):
        body = marked_body("No verdict.", "comment") + f"\n<!-- deep-review-awaiting: {waits} -->"
        if read is not None:
            body += f"\n<!-- deep-review-read-at: {ago(read)} -->"
        self.fake.reviews[self.key] = [review(at=at, body=body)]
        self.fake.comments[self.key] = []

    def test_a_question_alone_never_polls_checks(self):
        self.awaiting_review("question")
        self.assertEqual(self.evaluate()[1], [])
        self.checks.assert_not_called()
        self.state.assert_not_called()

    def test_an_author_comment_outside_the_thread_answers_an_open_question(self):
        self.awaiting_review("question", read=35)
        self.fake.issue_comments[self.key] = [
            {"user": {"login": "alice"}, "created_at": ago(40), "body": "seen before the round read"},
            {"user": {"login": "mallory"}, "created_at": ago(5), "body": "not the author"},
        ]
        self.assertEqual(self.evaluate()[1], [])
        self.fake.issue_comments[self.key].append(
            {"user": {"login": "alice"}, "created_at": ago(5), "body": "Intended."}
        )
        self.assertEqual(self.evaluate()[1], ["the author commented while an intent question was open"])

    def test_checks_waits_allow_approval_once_none_fails_even_if_some_are_pending(self):
        due = ["last verdict was comment-only and the checks it waited on now allow approval"]
        cases = {
            "re-run passed, another still running": (("pending", NOW - dt.timedelta(minutes=5)), due),
            "still running, nothing finished since": (
                ("pending", NOW - dt.timedelta(minutes=45)),
                ["__checks_pending__"],
            ),
            "still failing": (("failing", NOW - dt.timedelta(minutes=5)), ["__checks_pending__"]),
            "unreadable": (("unknown", None), ["__checks_pending__"]),
            "no required checks reported yet": (("none", None), ["__checks_pending__"]),
            "all pass": (("pass", NOW - dt.timedelta(minutes=45)), due),
        }
        for name, (result, expected) in cases.items():
            with self.subTest(name):
                self.awaiting_review("checks")
                self.state.reset_mock()
                self.state.return_value = result
                self.assertEqual(self.evaluate()[1], expected)
                self.state.assert_called_once_with(REPO, 1, HEAD, required_only=True)
        self.checks.assert_not_called()  # the override-only legacy path isn't used

    def test_all_checks_waits_need_every_check_to_pass(self):
        due = ["last verdict was comment-only and the checks it waited on now allow approval"]
        cases = {
            "pending": (("pending", NOW - dt.timedelta(minutes=5)), ["__checks_pending__"]),
            "pass": (("pass", NOW - dt.timedelta(minutes=5)), due),
            "no checks at all": (("none", None), due),
        }
        for name, (result, expected) in cases.items():
            with self.subTest(name):
                self.awaiting_review("all-checks")
                self.state.reset_mock()
                self.state.return_value = result
                self.assertEqual(self.evaluate()[1], expected)
                self.state.assert_called_once_with(REPO, 1, HEAD, required_only=False)

    def test_checks_and_question_together_use_both_rules(self):
        self.awaiting_review("checks question", read=35)
        self.state.return_value = ("failing", None)
        self.fake.issue_comments[self.key] = [{"user": {"login": "alice"}, "created_at": ago(5), "body": "Intended."}]
        self.assertEqual(
            self.evaluate()[1], ["the author commented while an intent question was open", "__checks_pending__"]
        )

    def test_awaiting_line_parsing(self):
        body = "x\n<!-- deep-review-awaiting: question -->\n<!-- deep-review-awaiting: checks bogus -->"
        self.assertEqual(pr_mod.awaiting(review(body=body)), {"checks"})
        self.assertIsNone(pr_mod.awaiting(review(body="no line")))

    def test_skill_documents_every_awaiting_kind_and_the_read_at_line(self):
        text = SKILL.read_text()
        for kind in pr_mod.AWAITING_KINDS:
            self.assertIn(f"`{kind}`", text)
        self.assertIn("<!-- deep-review-awaiting:", text)
        self.assertIn("<!-- deep-review-read-at:", text)

    def test_skill_opens_intent_questions_with_the_marker_the_scanner_reads(self):
        text = SKILL.read_text()
        section = text[text.index("*Intent questions.*") :]
        self.assertIn(f'"{pr_mod.MARKER}:"', section[:3000])

    def test_skill_documents_every_verdict_marker_the_scanner_reads(self):
        text = SKILL.read_text()
        for kind in pr_mod.VERDICT_KINDS:
            self.assertIn(f"<!-- deep-review-verdict: {kind} -->", text)


class ScanTests(StateTestCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeGitHub()
        self.clones = {"octo/app": "/clones/app", "zeta/lib": "/clones/lib"}
        self.checks = mock.Mock(return_value=False)
        self.state = mock.Mock(return_value=("pass", NOW))

    def scan(self, review_requests=True):
        with (
            mock.patch.object(pr_mod, "gh", self.fake),
            mock.patch.object(pr_mod, "local_clones", return_value=self.clones),
            mock.patch.object(pr_mod, "required_checks_pass", self.checks),
            mock.patch.object(pr_mod, "checks_status", self.state),
        ):
            return pr_mod.scan(pathlib.Path("/clones"), review_requests)

    def track(self, key, **record):
        state = self.read_state() if self.state_path.exists() else {"prs": {}, "scan": {}}
        state["prs"][key] = {"rounds": 1, "tracked": True, **record}
        self.state_path.write_text(json.dumps(state))

    def keys(self, entries):
        return [e["key"] for e in entries]

    def test_requested_pr_with_clone_is_due_as_first_round(self):
        self.fake.requested = ["Octo/App#5"]
        self.fake.add_pr(REPO, 5)
        result = self.scan()
        self.assertEqual(result["me"], ME)
        self.assertEqual(len(result["due"]), 1)
        entry = result["due"][0]
        self.assertEqual(
            {k: entry[k] for k in ("key", "kind", "round", "path", "reasons", "head")},
            {
                "key": "Octo/App#5",
                "kind": "first",
                "round": 1,
                "path": "/clones/app",
                "reasons": ["review requested"],
                "head": HEAD,
            },
        )
        self.assertEqual((result["waiting"], result["no_clone"]), ([], []))

    def test_requested_pr_already_deep_reviewed_is_re_requested_follow_up(self):
        self.fake.requested = ["Octo/App#5"]
        self.fake.add_pr(REPO, 5)
        self.fake.deep_review("Octo/App#5")
        entry = self.scan()["due"][0]
        self.assertEqual((entry["kind"], entry["reasons"]), ("follow-up", ["review re-requested"]))

    def test_own_and_draft_prs_are_skipped(self):
        self.fake.requested = ["Octo/App#1", "Octo/App#2", "Octo/App#3"]
        self.fake.add_pr(REPO, 1, author=ME)
        self.fake.add_pr(REPO, 2, draft=True)
        self.fake.add_pr(REPO, 3)
        result = self.scan()
        self.assertEqual(self.keys(result["due"]), ["Octo/App#3"])
        self.assertEqual((result["waiting"], result["no_clone"]), ([], []))
        self.assertEqual(self.fake.review_fetches("Octo/App#1") + self.fake.review_fetches("Octo/App#2"), 0)

    def test_requested_pr_without_clone_is_listed_until_notified(self):
        self.clones = {}
        self.fake.requested = ["Octo/App#4"]
        self.fake.add_pr(REPO, 4)
        result = self.scan()
        self.assertEqual(self.keys(result["no_clone"]), ["Octo/App#4"])
        self.assertEqual(result["due"], [])
        self.update("Octo/App#4", "notified")
        self.assertEqual(self.scan()["no_clone"], [])

    def test_recent_activity_waits_for_quiet_window(self):
        self.fake.requested = ["Octo/App#1", "Octo/App#2"]
        self.fake.add_pr(REPO, 1, updated=2)
        self.fake.add_pr(REPO, 2, updated=4)
        result = self.scan()
        self.assertEqual(self.keys(result["due"]), ["Octo/App#2"])
        self.assertEqual(
            [(e["key"], e["why"]) for e in result["waiting"]], [("Octo/App#1", "activity in the last 3 minutes")]
        )

    def test_running_round_makes_every_candidate_wait(self):
        self.track("Octo/App#1", in_progress_since=(NOW - dt.timedelta(minutes=10)).isoformat())
        self.fake.requested = ["Octo/App#1", "Zeta/Lib#2"]
        self.fake.add_pr(REPO, 1)
        self.fake.add_pr(OTHER_REPO, 2)
        result = self.scan()
        self.assertEqual(result["due"], [])
        why = {e["key"]: e["why"] for e in result["waiting"]}
        self.assertEqual(why["Octo/App#1"], "a round on this PR is still running")
        self.assertIn("Octo/App#1", why["Zeta/Lib#2"])

    def test_stale_running_round_does_not_block(self):
        self.track("Octo/App#1", in_progress_since=(NOW - dt.timedelta(minutes=61)).isoformat())
        self.fake.requested = ["Zeta/Lib#2"]
        self.fake.add_pr(REPO, 1)
        self.fake.add_pr(OTHER_REPO, 2)
        result = self.scan()
        self.assertEqual(self.keys(result["due"]), ["Zeta/Lib#2"])
        self.assertEqual(result["waiting"], [])

    def test_review_posted_after_mark_ends_the_running_round(self):
        self.track("Octo/App#1", in_progress_since=(NOW - dt.timedelta(minutes=30)).isoformat())
        self.fake.add_pr(REPO, 1)
        self.fake.deep_review("Octo/App#1", at=10)
        self.fake.requested = ["Zeta/Lib#2"]
        self.fake.add_pr(OTHER_REPO, 2)
        result = self.scan()
        self.assertEqual(self.keys(result["due"]), ["Zeta/Lib#2"])
        self.assertIsNone(self.read_state()["prs"]["Octo/App#1"]["in_progress_since"])

    def test_follow_ups_sort_before_first_rounds(self):
        self.fake.requested = ["Octo/App#1"]
        self.fake.add_pr(REPO, 1)
        self.track("Zeta/Lib#9", rounds=3)
        self.fake.add_pr(OTHER_REPO, 9)
        self.fake.deep_review("Zeta/Lib#9", commit=OLD)
        due = self.scan()["due"]
        self.assertEqual([(e["key"], e["kind"]) for e in due], [("Zeta/Lib#9", "follow-up"), ("Octo/App#1", "first")])
        self.assertEqual(due[0]["round"], 4)

    def test_many_rounds_are_never_capped(self):
        self.track("Octo/App#1", rounds=40)
        self.fake.add_pr(REPO, 1)
        self.fake.deep_review("Octo/App#1", commit=OLD)
        result = self.scan()
        self.assertEqual([(e["key"], e["round"]) for e in result["due"]], [("Octo/App#1", 41)])
        self.assertEqual(result["capped"], [])

    def test_scan_cache_skips_unchanged_pr(self):
        self.track("Octo/App#1")
        pr = self.fake.add_pr(REPO, 1)
        self.fake.deep_review("Octo/App#1")
        self.scan()
        self.scan()
        self.assertEqual(self.fake.review_fetches("Octo/App#1"), 1)
        pr["updatedAt"] = ago(30)
        self.scan()
        self.assertEqual(self.fake.review_fetches("Octo/App#1"), 2)

    def test_pending_checks_are_rechecked_without_new_activity(self):
        self.track("Octo/App#1")
        self.fake.add_pr(REPO, 1)
        self.fake.deep_review("Octo/App#1", body=COMMENT_ONLY)
        self.assertEqual(self.scan()["due"], [])
        self.checks.return_value = True
        due = self.scan()["due"]
        self.assertEqual(self.keys(due), ["Octo/App#1"])
        self.assertEqual(due[0]["reasons"], ["last verdict was comment-only and required checks now pass"])

    def test_closed_prs_drop_out_of_scan_cache(self):
        self.track("Octo/App#1")
        state = self.read_state()
        state["scan"]["Octo/App#99"] = {"updatedAt": ago(500), "deep": True, "recheck": False}
        self.state_path.write_text(json.dumps(state))
        self.fake.add_pr(REPO, 1)
        self.scan()
        self.assertEqual(set(self.read_state()["scan"]), {"Octo/App#1"})

    def test_review_requests_disabled_still_follows_up_tracked_prs(self):
        self.fake.requested = ["Octo/App#2", "Other/Repo#3"]
        self.fake.add_pr(REPO, 2)
        self.fake.add_pr("Other/Repo", 3)
        self.track("Octo/App#1")
        self.fake.add_pr(REPO, 1)
        self.fake.deep_review("Octo/App#1", commit=OLD)
        result = self.scan(review_requests=False)
        self.assertEqual(self.keys(result["due"]), ["Octo/App#1"])
        self.assertFalse(any(c[:2] == ("search", "prs") for c in self.fake.calls))
        self.assertFalse(any("Other/Repo" in c for c in self.fake.calls))

    def test_comment_only_verdict_posted_with_passing_checks_does_not_loop(self):
        # Comment-only although checks already passed (e.g. the PR text tried to steer the review):
        # nothing changed since, so it must not come back on every scan.
        self.checks.return_value = True
        self.state.return_value = ("pass", NOW - dt.timedelta(minutes=20))
        self.track("Octo/App#1", in_progress_since=None)
        self.fake.add_pr(REPO, 1, updated=10)
        self.fake.deep_review("Octo/App#1", body=COMMENT_ONLY, at=10)
        self.assertEqual(self.scan()["due"], [])
        self.assertFalse(self.read_state()["scan"]["Octo/App#1"]["recheck"])
        self.assertEqual(self.scan()["due"], [])
        self.assertEqual(self.fake.review_fetches("Octo/App#1"), 1)


class LocalClonesTests(unittest.TestCase):
    def test_maps_github_origins_and_ignores_others(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            origins = {
                "https-clone": "https://github.com/Octo/App.git",
                "ssh-clone": "git@github.com:Zeta/Lib.git",
                "ssh-url-clone": "ssh://git@github.com/Mixed/CaseRepo",
                "gitlab-clone": "https://gitlab.com/Octo/Other.git",
                "no-origin": "",
            }
            for name in origins:
                (root / name / ".git").mkdir(parents=True)
            (root / "worktree").mkdir()
            (root / "worktree" / ".git").write_text("gitdir: /elsewhere")
            origins["worktree"] = "git@github.com:Octo/Worktree"
            (root / "plain-dir").mkdir()
            (root / "file.txt").write_text("x")

            def fake_run(cmd, **kwargs):
                return subprocess.CompletedProcess(cmd, 0, stdout=origins[pathlib.Path(cmd[2]).name] + "\n")

            with mock.patch.object(pr_mod.subprocess, "run", side_effect=fake_run) as run:
                clones = pr_mod.local_clones(root)

            self.assertEqual(
                clones,
                {
                    "octo/app": str(root / "https-clone"),
                    "zeta/lib": str(root / "ssh-clone"),
                    "mixed/caserepo": str(root / "ssh-url-clone"),
                    "octo/worktree": str(root / "worktree"),
                },
            )
            inspected = {pathlib.Path(c.args[0][2]).name for c in run.call_args_list}
            self.assertEqual(inspected, set(origins))

    def test_missing_root_has_no_clones(self):
        with mock.patch.object(pr_mod.subprocess, "run") as run:
            self.assertEqual(pr_mod.local_clones(pathlib.Path("/nonexistent/clones-root")), {})
        run.assert_not_called()


def check_run(name, conclusion="SUCCESS", started=40, completed=30, required=True, workflow="CI", event="pull_request"):
    return {
        "__typename": "CheckRun",
        "name": name,
        "isRequired": required,
        "conclusion": conclusion,
        "startedAt": ago(started) if started is not None else None,
        "completedAt": ago(completed) if completed is not None else None,
        "checkSuite": {"workflowRun": {"event": event, "workflow": {"name": workflow}}},
    }


def status(context, state="SUCCESS", created=30, required=True):
    return {
        "__typename": "StatusContext",
        "context": context,
        "isRequired": required,
        "state": state,
        "createdAt": ago(created),
    }


def rollup(*nodes, oid=HEAD, more=False, empty=False):
    commit = {
        "oid": oid,
        "statusCheckRollup": None if empty else {"contexts": {"pageInfo": {"hasNextPage": more}, "nodes": list(nodes)}},
    }
    return {"data": {"repository": {"pullRequest": {"commits": {"nodes": [{"commit": commit}]}}}}}


class ChecksStatusTests(unittest.TestCase):
    def status(self, response, required_only=True):
        calls = []

        def fake_gh(*args):
            calls.append(args)
            return response

        with mock.patch.object(pr_mod, "gh", fake_gh):
            result = pr_mod.checks_status(REPO, 12, HEAD, required_only=required_only)
        return result, calls

    def test_commit_status_finishes_at_its_created_time(self):
        result, calls = self.status(rollup(status("ci/jenkins", created=15)))
        self.assertEqual(result, ("pass", NOW - dt.timedelta(minutes=15)))
        args = calls[0]
        self.assertEqual(args[:2], ("api", "graphql"))
        self.assertIn("n=12", args)
        self.assertIn("isRequired(pullRequestNumber:$n)", args[-1])

    def test_latest_finish_across_check_runs_and_statuses(self):
        result, _ = self.status(rollup(check_run("build", completed=40), status("ci/legacy", created=5)))
        self.assertEqual(result, ("pass", NOW - dt.timedelta(minutes=5)))

    def test_only_required_checks_count_unless_asked_for_all(self):
        nodes = (check_run("build", completed=40), check_run("lint", "FAILURE", required=False))
        self.assertEqual(self.status(rollup(*nodes))[0], ("pass", NOW - dt.timedelta(minutes=40)))
        self.assertEqual(self.status(rollup(*nodes), required_only=False)[0][0], "failing")

    def test_only_the_newest_run_of_a_check_counts(self):
        older_failure = check_run("build", "FAILURE", started=60, completed=55)
        newer_success = check_run("build", "SUCCESS", started=20, completed=10)
        self.assertEqual(self.status(rollup(older_failure, newer_success))[0], ("pass", NOW - dt.timedelta(minutes=10)))
        self.assertEqual(self.status(rollup(newer_success, older_failure))[0][0], "pass")

    def test_runs_from_different_workflows_or_events_are_separate_checks(self):
        push = check_run("build", "FAILURE", started=20, event="push")
        pull = check_run("build", "SUCCESS", started=30)
        self.assertEqual(self.status(rollup(push, pull))[0][0], "failing")

    def test_skipped_and_neutral_checks_pass_as_github_treats_them(self):
        for conclusion in ("SKIPPED", "NEUTRAL"):
            with self.subTest(conclusion=conclusion):
                self.assertEqual(self.status(rollup(check_run("build", conclusion)))[0][0], "pass")

    def test_cancelled_timed_out_stale_and_similar_count_as_failing(self):
        for conclusion in ("FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE", "STALE"):
            with self.subTest(conclusion=conclusion):
                self.assertEqual(self.status(rollup(check_run("build", conclusion)))[0][0], "failing")
        for state in ("FAILURE", "ERROR"):
            with self.subTest(state=state):
                self.assertEqual(self.status(rollup(status("ci", state)))[0][0], "failing")

    def test_running_checks_are_pending_and_have_no_finish_time(self):
        cases = {"queued run": check_run("build", None, completed=None), "pending status": status("ci", "PENDING")}
        for name, node in cases.items():
            with self.subTest(name):
                self.assertEqual(self.status(rollup(node))[0], ("pending", None))

    def test_failing_beats_pending_and_pending_beats_pass(self):
        running = check_run("a", None, completed=None)
        self.assertEqual(self.status(rollup(check_run("b", "FAILURE"), running))[0][0], "failing")
        self.assertEqual(self.status(rollup(check_run("b"), running))[0][0], "pending")

    def test_unjudgeable_rollups(self):
        cases = {
            "no required checks": (rollup(check_run("lint", required=False)), "none"),
            "no rollup": (rollup(empty=True), "none"),
            "another head commit": (rollup(check_run("build"), oid=OLD), "unknown"),
            "more than one page": (rollup(check_run("build"), more=True), "unknown"),
            "no commits": ({"data": {"repository": {"pullRequest": {"commits": {"nodes": []}}}}}, "unknown"),
        }
        for name, (response, expected) in cases.items():
            with self.subTest(name):
                self.assertEqual(self.status(response)[0], (expected, None))


class RequiredChecksPassTests(unittest.TestCase):
    def run_checks(self, stdout, returncode=0):
        result = subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")
        with mock.patch.object(pr_mod.subprocess, "run", return_value=result) as run:
            return pr_mod.required_checks_pass(REPO, 12), run

    def test_empty_output_is_not_passing(self):
        for stdout in ("", "  \n"):
            with self.subTest(stdout=stdout):
                self.assertFalse(self.run_checks(stdout, returncode=1)[0])

    def test_all_required_checks_passing(self):
        passed, run = self.run_checks(json.dumps([{"bucket": "pass"}, {"bucket": "pass"}]))
        self.assertTrue(passed)
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[:3], ["gh", "pr", "checks"])
        self.assertIn("12", cmd)
        self.assertEqual(cmd[cmd.index("--repo") + 1], REPO)
        self.assertIn("--required", cmd)

    def test_any_non_passing_check_is_not_passing(self):
        for bucket in ("pending", "fail", "cancel"):
            with self.subTest(bucket=bucket):
                stdout = json.dumps([{"bucket": "pass"}, {"bucket": bucket}])
                self.assertFalse(self.run_checks(stdout, returncode=8)[0])
        self.assertFalse(self.run_checks("[]")[0])


if __name__ == "__main__":
    unittest.main()
