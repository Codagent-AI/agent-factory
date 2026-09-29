---
name: factory-pr-review
description: Review a pull request the Agent Factory opened or updated. Runs the factory-pr-reviewer agent (code review, red and orange description items, a comment-only review the factory acts on, follow-up Bug issues assigned to the factory), then puts Paul's decisions to him one at a time with a recommendation. Use on every factory-watch PR-READY event, or when asked to review a factory PR.
---

# Factory PR review

Run this for every `PR-READY` event from `factory-watch`, and whenever Paul asks for a factory PR to be reviewed. PR-READY fires for initial and recovery runs, and again after each review round. Paul reviews the PR after this has run, so it has to catch what he would.

## 1. Start the reviewer

Start one background agent per PR, with `subagent_type: factory-pr-reviewer`. If that type is not listed, which happens in a session that started before the file existed, use `general-purpose` and tell it to follow `.claude/agents/factory-pr-reviewer.md`. The prompt gives:

- the PR URL, the repository, and the issue;
- the run's `kind`, `reason`, and run id (from the event line);
- the scratchpad directory to use for worktrees and review bodies.

Several PRs can be reviewed in parallel. Do not start a second reviewer on a PR while one is still running on it. If the PR changes again, start the next reviewer after the first finishes.

Keep watching while it runs: restart `watch.sh` right away.

## 2. Handle its report

- **Verdict and posted review**: show them to Paul only when the verdict is not "Mergeable as is", or when something was posted. Keep it to one line.
- **Issues filed**: they are on the board already. Mention only the count and anything filed at High.
- **Ruled out**: keep this to yourself unless Paul asks.
- **Decisions for Paul**: go to step 3.

Do not list PRs or their states; the board shows that.

## 3. Put decisions to Paul, one at a time

For each decision, call `AskUserQuestion` with one question, and wait for the answer before asking the next. Use `AskUserQuestion` rather than plain text: it notifies Paul, and plain text does not. In each question:

- give the PR number and a two- or three-line context in the question text;
- make the recommended option the first one, with "(Recommended)" at the end of its label;
- give each option a one-line consequence.

Act on each answer before moving on:

- a change to the PR: post it as a comment-only review so the factory runs a round;
- a new issue: file it and assign it with `factory-assign`;
- a merge: only Paul merges, unless he told you to merge this PR in this conversation;
- "leave it": note it and move on.
