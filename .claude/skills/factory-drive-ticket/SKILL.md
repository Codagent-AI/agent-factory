---
name: factory-drive-ticket
description: Drive one or more issues through the live Agent Factory from handoff to a reviewed pull request. Runs factory-assign, waits with factory-watch, answers `needs-input` questions it can settle (asking Paul only for decisions that are his), restarts runs after a factory defect is fixed, and runs factory-pr-review once a PR is ready. Use when Paul asks to drive, push through, shepherd, or see an issue through the factory.
---

# Factory drive ticket

This skill is also linked into `~/.claude/skills`, so it can start from another project. Run every command from the Agent Factory checkout, `/Users/paul/codagent/agent-factory` (read its `AGENTS.md`), unless you are already in a checkout of this repository. Never switch that checkout's branch.

It chains the other factory skills and follows each one's rules: `factory-assign`, `factory-watch`, `factory-triage`, and `factory-pr-review`. It never merges a pull request; only Paul merges.

Take the issues as `OWNER/REPO#N` or issue URLs. Drive several issues together with one watch.

## 1. Assign

Run `factory-assign` for each issue. Skip an issue that is already claimed and still running. If the check names a requirement only Paul can resolve (wrong type, author without write permission, closed issue, invalid eval body), ask him with `AskUserQuestion` and drop the issue if he does not resolve it.

A busy slot or closed admission window is fine: the card waits in Ready and the watch covers it.

## 2. Watch

Start `factory-watch`'s `watch.py` for every issue still being driven, as a background Bash command, exactly as that skill describes. Tell Paul in one line which issues you are driving, then wait for the command to finish. Do not poll it, and do not start `/loop` or a Monitor.

## 3. Act on each stopped issue

Read the `STOPPED` line, the card, and the issue's latest factory comment. Then:

| Where it stopped | Next step |
| --- | --- |
| `needs-input` label (a fix, feature, or task decline, a definition question, or a merge conflict) | Step 4 |
| Run `completed` with `outcome=pull-request`, or a review round finished on its PR | Step 5 |
| Failed, `infra-error`, or `WATCH EVENT MISSING` | Step 6 |
| Not queued for another reason, or settled without a PR | Report it and stop driving the issue |

When every issue still being driven has gone through its step, return to step 2 for the issues that went back to the factory.

## 4. Answer `needs-input`

Read the questions, the drafted direction, and the branch link from the factory's comment, along with the issue body, all of its comments, and any PR. Sort each question into one of two groups.

**You answer it** when one of these settles it: the issue and its comments, the target repository's code, specifications, and docs, the Codagent notes in the Obsidian vault (`codagent-obsidian-knowledge`), or Paul's recorded preferences. Answer each question in plain words and give the evidence.

**You decide it** when no source settles it but reasonable judgment does. This covers engineering and design choices, and smaller scope or behavior calls that fit the issue's evident intent, are consistent with the codebase, and are cheap to change in review. Pick the option you would defend in review, state it as a decision with a one-line reason, and record it for the report.

**Paul answers it** when it is one of these:

- a product direction or scope decision the issue leaves open on purpose, or one that would be costly to reverse once built;
- anything the factory's own triage reserves for a human: credentials, release, publish, or deploy settings, branch protection, work in another repository, or spending and limits on his Mac;
- a change of issue type, for example a decline saying a Bug "belongs in a Feature". Never retype without his yes. On yes, use `factory-assign --apply <kind> --retype`;
- a question his stated preferences answer in conflicting ways;
- a question the factory asks again after you already answered it once;
- a question where no option is clearly reasonable, or where the options would lead to substantially different work.

Ask Paul's questions with `AskUserQuestion`, one at a time. Give each one the issue number and two or three lines of context, put the recommended option first with "(Recommended)" at the end of its label, and give each option its one-line consequence.

For a merge conflict, resolve it only when the resolution is mechanical. Work in a worktree of the claim branch, run the target's checks, and push to the claim branch. Ask Paul about any conflict between two intended behaviors.

Then post **one** issue comment with Paul's `gh` login (`env -u GH_TOKEN -u GITHUB_TOKEN gh issue comment`). Number the answers to match the questions. Its first line says Claude wrote it on Paul's behalf, and it marks each answer as Paul's, settled by a named source, or a judgment call. A writer comment newer than the decline re-admits the claim. Do not remove the `needs-input` label or move the card. Run one `tick`, as in `factory-assign` step 4, and return to step 2.

## 5. Review the pull request

Once its run is `completed`, run `factory-pr-review` on the issue's PR and follow that skill: one reviewer agent, then Paul's decisions one at a time.

- When the reviewer posted a review, a factory round starts. Return to step 2 for this issue, then review again after the round.
- Stop driving the issue once a review returns "Mergeable as is" with nothing posted. Also stop after the third review of the same PR, and ask Paul with `AskUserQuestion` whether to merge as is or keep iterating.

## 6. Handle a failure

Read the service watcher's triage for the run (`factory-watch`, "Report"). Each issue gets one restart at most, and only after the cause is fixed.

- **The triage gives a transient or environment cause, with an operator action you can take**: take it, move the card back to Ready with `factory-assign --apply <kind>`, and return to step 2.
- **The triage gives a factory defect**: follow `factory-triage`, which fixes it through a tested PR. When that PR needs Paul's merge, ask him with `AskUserQuestion`. After the merge, deploy with `factory-deploy`. Then move the card back to Ready and return to step 2. A feature that failed after its plan checkpoint continues from its branch.
- **Anything else**, such as a defect in the work itself or a second failure: report it and stop driving the issue.

## Report

When every issue has stopped being driven, give Paul one short report and lead with what needs him. Do not list cards that the board already shows plainly.

- **Ready for you to merge**: the full PR URL and the last review verdict, in one clause.
- **Stopped elsewhere**: where and why, and what he needs to do.
- **Decisions made for you**: every judgment call from step 4, each with the issue, the question in a few words, what was chosen, and why, plus a link to the comment. Paul can overturn any of them in PR review.
- **What this session did**: the other answers it posted (with a link to each comment), restarts, deploys, and issues filed.

When the report covers several PRs, group them under **Merged**, **Ready for you to merge**, and **Other status**, with full URLs.
