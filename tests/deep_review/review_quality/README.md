# Deep-review behavior evaluation

Run this comparison when changing how the review finds defects, handles missing
evidence, or decides a verdict. The fixture is synthetic and requires only
Python and Git. Review it locally and simulate GitHub state. Do not post,
approve, edit the fixture, or use a live PR.

```sh
fixture_dir=$(mktemp -d)
python3 tests/deep_review/review_quality/fixture.py "$fixture_dir/repo"
```

Save the printed range and use that same repository and range for both skill
versions. Use fresh sessions and the same model, reasoning effort, agent slots,
tool access, and review budget. Repeat trials when judging reliability. Give
reviewers the inputs below and keep the scoring key for the evaluator.

## Review inputs

1. Review the printed range as a PR for “Normalize imported names and enqueue
   tokens.” Simulate comment mode with automatic approval, a Medium blocking
   threshold, all checks passing, and no earlier review. After discovery, ask
   the verifier to check a prior tentative candidate that a blank or
   non-string name reaches the adapter.
2. Simulate a same-head follow-up on the same PR after a marked comment-only
   review with no inline threads. The earlier report settled the legacy
   stored-record check and had an open compatibility check for the opaque
   enqueue token. Run this independently of case 1. For this case only,
   assume the bulk behavior was separately accepted by the contract owner.
   Provide new trusted evidence that the pinned production adapter always
   returns a UUID object. Ask what conclusion and verdict change, and what
   work can reuse the previous evidence.
3. Ask for the first round of a design review of a nightly replace-all import.
   The proposal asks where backups should live, how long to keep them, whether
   a zero-row snapshot deletes existing records, and whether the proposed
   adapter distinguishes an empty result from a timeout. The fixture has no
   snapshot adapter or backup policy. Let a recommendation about the backup
   location stand independently of the adapter investigation.

## Scoring key

- **Verified finding.** The bulk path drops stored records with surrounding
  whitespace. Show that the same record was accepted on base and that the
  documented guarantee covers bulk. Lowercasing names is consistent with the
  stated request.
- **Rejected false positive.** Validation rejects blank and non-string names
  before enqueue. The pinned source is enough to refute this candidate.
- **Unresolved compatibility.** Calling a string method on the opaque token
  is a material compatibility concern. A made-up adapter can show conditional
  behavior, but it cannot establish the production return type. Cite the
  missing fact and what would establish it. In a separate verdict replay
  without the bulk blocker and with evidence still missing, expect
  comment-only. With the blocker present, expect request changes.
- **Same-head follow-up.** Recognize the prior marked review even without
  inline threads. Recheck the token conclusion against the new fact and
  independently triage the verified incompatibility before choosing a verdict.
  Keep prior unrelated evidence valid where the reviewed inputs are unchanged.
- **Design round.** Ask the owner for decisions that are ready, with a
  recommendation for each. A recommendation about backup storage can proceed
  while adapter evidence is being sought. Keep the dependent question about
  deletion pending while empty snapshots and timeouts cannot be distinguished.
  Record the open prerequisite before ending the round.

Record verified findings caught, false positives published, clean verdicts
with material checks open, and whether the same-head and design cases passed.
Compare actual outcomes and evidence. Skill text or template matches alone
do not count as a pass.
