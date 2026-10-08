---
name: codagent-github-project
description: Create, update, classify, prioritize, move, close, or inspect issues on the Codagent board (GitHub Project 1 across Codagent-AI repositories). Use whenever Paul asks to file, open, log, capture, or update a Codagent issue (Bug, Feature, Task, or Eval), change its Status, Owner, Priority, or dependencies, or review the backlog. To hand an issue to the Agent Factory, use the factory-assign skill instead.
version: 0.2.0
author: Paul Caplan, Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [codagent, github, issues, projects, backlog, agent-factory]
---

# Codagent GitHub Project

Manage Codagent product work through repository issues and the organization-level [Codagent Project](https://github.com/orgs/Codagent-AI/projects/1). The Project is the canonical product backlog; do not create or maintain backlog notes in the Obsidian vault.

## When to use

Use this skill whenever Paul asks to create or update a Codagent issue, even if he only says "file an issue", "log a bug", "add this to the board", or "capture this". Specifically, use it to:

- create, update, classify, close, or inspect a Codagent issue;
- add, remove, prioritize, or move work on the Codagent Project;
- decide what Codagent work belongs in Backlog or Ready;
- create or manage a candidate or executable Eval;
- add or remove an issue dependency;
- review the Codagent backlog across repositories.

When Paul asks to **assign an issue to the factory**, have the factory fix, build, or run it, or queue it for the factory, use the `factory-assign` skill (`agent-factory/.claude/skills/factory-assign`, linked into `~/.claude/skills`). It sets what admission needs, runs a tick, and confirms the claim. Do not set Owner=factory and Ready by hand for that purpose. If Paul asks to create an issue *and* give it to the factory, create it here, then run `factory-assign`.

Do not use it for generic GitHub work outside the `Codagent-AI` organization. Do not turn newsletter, speaking, career, or other non-product planning into GitHub issues unless Paul explicitly asks.

## Source of truth

- Board: `https://github.com/orgs/Codagent-AI/projects/1`
- Organization: `Codagent-AI`
- Project number: `1`
- Project node ID: `PVT_kwDOEARcIs4Bi5pP`
- Current statuses: Backlog, Ready, Running, Review, Done
- Native issue types: Task, Bug, Feature, Eval
- Factory targets (`[fix] targets` in `agent-factory/config/codagent.toml` on `origin/main`): agent-runner, agent-skills, agent-validator, agent-plugin, agent-evals, agent-factory, and-scene. Fix, feature, and task work all use these. Evals come only from `agent-evals`. Read the live config when it matters; the list changes.
- Priority: organization issue field `Priority` (`Urgent`, `High`, `Medium`, `Low`) on the Project. New issues default to Low. Factory and views rank highest Priority first, then newest created. GitHub cannot mark the field required or assign a native default.

Repository code, Project schema, and live GitHub state override cached IDs or dated documentation. Read `references/project-api.md` before the first mutation in a task and rerun its discovery queries whenever live state may have changed.

## Daily workflow

1. Search open and closed issues before creating anything. Reuse an existing issue when it represents the same work.
2. Choose the repository that owns the behavior or artifact. Do not guess when ownership materially changes the work; inspect the Codagent repository map or ask Paul.
3. Classify the issue with exactly one native type. The type selects the Factory work kind, so classify by what the work changes:
   - **Bug** (factory `fix`): existing behavior is broken or violates its contract. Fixes may change runtime behavior to restore the contract.
   - **Feature** (factory `feature`, an OpenSpec-driven definition and implementation): new behavior, or an improvement that changes runtime behavior, a public API or CLI, persisted data, or specs.
   - **Task** (factory `task`, `chore:` PRs): low-risk maintenance that preserves behavior: development tools and dependencies, CI, docs, behavior-preserving refactors and cleanups. Not a Task: anything changing runtime behavior, a public API or CLI, persisted data, OpenSpec specs, credentials, release/publish/version/sign/tag/deploy configuration, branch protection, work spanning repositories, oversized work, or work that leaves a product or design decision open. Factory task triage declines those with `needs-input`; file them as Feature (or Bug) instead.
   - **Eval** (factory `eval`): a proposed evaluation run of a candidate configuration or revision, filed in `agent-evals`. See Eval rules.
   If the type is genuinely ambiguous (for example Task vs Feature) and it decides what the factory would do, ask Paul.
4. Write the issue at the same level of detail Paul provided. Preserve his wording, scope, uncertainty, and identifiers; add only relevant factual context already known.
5. Create or update the repository issue. Native type is issue metadata, not a label; do not recreate redundant `enhancement` labels.
   Before `gh issue create --body-file F`, run `~/.agent-factory/releases/current/.venv/bin/python -m agent_factory.notify.marker stamp F` to record this Claude session. Before every `gh issue edit --body-file F`, save the current issue body in `OLD` and run `~/.agent-factory/releases/current/.venv/bin/python -m agent_factory.notify.marker carry OLD F`. If the marker command fails, note it in one line and continue without a marker. One session is tracked per issue; `factory-assign` replaces this marker when it hands the issue off.
6. Confirm Project membership. Routing handles configured repositories; add other issues manually.
7. Set status and ownership deliberately (see Factory handoff below):
   - Default for new issues: Backlog with Owner human or unset.
   - **Ready is a factory handoff in factory target repositories.** The factory polls Ready cards and takes ownership of any open Bug, Feature, or Task there whose author has write access, even when Owner is human. Do not move such an issue to Ready to mean "near-term human work"; keep it in Backlog and use Priority. Ready as a human-priority signal is safe only in repositories that are not factory targets (for example dot-dev, homebrew-tap).
   - Active human work: Running, then Review.
   - Completed work: close the issue and verify the tracked card reaches Done.
8. Set Priority on the issue (`Urgent`, `High`, `Medium`, `Low`). Default to Low unless Paul specified another value. Do not invent a priority label or rank by dragging cards.
9. Read back the exact issue and Project item after every mutation before reporting success.

## Issue-writing rule — critical

Issue creation is backlog capture, not automatic specification. When Paul asks to create an issue:

- Write what he said, with only light editing for clarity.
- Keep the issue at his level of detail. A one-sentence request can remain a one-sentence issue.
- Include relevant facts the agent already knows when they explain the problem or context, but state them descriptively and without prescribing a solution.
- Preserve open questions and uncertainty instead of silently resolving them.
- Do not invent or expand requirements, acceptance criteria, edge cases, implementation steps, architecture, APIs, classes, data models, migration plans, or test plans.
- Do not turn likely implementation ideas into commitments. If Paul mentioned an approach as a possibility, label it as such.
- Do not add generic issue-template boilerplate merely to make the issue look complete.
- Ask a minimal clarification only when missing information would make the issue materially misleading or prevent choosing the owning repository or native type. Otherwise, create the concise issue directly.

An executable Factory Eval may require its supported request template, but fill it only with information Paul supplied or facts verified from the relevant repositories. Never manufacture missing inputs to complete the template.

## Factory handoff

The board drives the live Agent Factory. Know what reaches it:

- **Bug, Feature, and Task** are not auto-routed. Creating one, whoever the author, lands it in Backlog and does not start factory work. It reaches the factory only when its card moves to Ready (or via `factory-assign`).
- **Eval** reaches the factory through `eval-request` routing (see Eval rules).
- `needs-input` on an issue blocks admission; the factory adds it when it needs Paul's answer. Do not remove it unless Paul asks.
- `blocked` and GitHub blocked-by relationships do **not** stop admission. Keep dependent work in Backlog until its prerequisite lands.
- To give work to the factory, use `factory-assign`. Never set Owner=factory or Ready on an issue only to express priority.

## Eval rules

An Eval is a proposed evaluation run, not a generic issue in `agent-evals`:

- Broken suite behavior is Bug.
- New suite capability is Feature.
- A proposed run is Eval.

For a candidate Eval:

- create a native Eval with a descriptive body;
- do not add `eval-request`;
- keep it in Backlog with Owner human or unset.

For an executable Eval, use the delivered `agent-evals` request template and supported fenced inputs. Factory admission requires all of the following to be verified live:

- open native Eval in the configured eval-source repository;
- `eval-request` label;
- Owner=factory;
- Status=Ready;
- authorized author;
- valid parsed request;
- admission window, pause state, capacity, readiness, and quota conditions allow execution.

Ready + Owner=factory is an operational admission signal. Never set it merely to express human priority. A `blocked` label or dependency is not currently a Factory admission veto.

## Dependencies and removal

- Model a real prerequisite with GitHub's native blocked-by relationship.
- Treat the `blocked` label as a separate visual flag; remove it only after all blockers are resolved.
- The dependency endpoint requires the blocker's numeric database ID, not its issue number or GraphQL node ID.
- To park an unwanted idea, remove its Project membership without closing or deleting the issue unless Paul asks otherwise.
- Respect intentionally removed cards. Do not bulk re-add them during inventory refreshes.
- Routing can re-add a removed issue after a later subscribed issue or PR event; report that limitation rather than claiming permanent exclusion.

## Authentication

Use Paul's normal `gh` login for repository issue operations, explicitly ignoring token overrides:

```sh
issue_gh() { env -u GH_TOKEN -u GITHUB_TOKEN gh "$@"; }
```

The normal login may lack organization Project scope. Use the Factory App installation token for Project GraphQL operations as documented in `references/project-api.md`.

Never print a token, enable shell tracing around token creation, save a token in an issue body, or place one in a note. Installation tokens expire; mint a fresh one when needed.

## Mutation discipline

- Discover IDs; never infer or interchange issue number, numeric database ID, GraphQL issue node ID, and Project item ID.
- Use typed GraphQL variables and body files rather than interpolating user content into GraphQL or shell source.
- A timeout after issue creation is ambiguous. Search/read before retrying; do not create a duplicate.
- Treat GraphQL partial errors and permission failures as failures, not successful empty results.
- Updating one issue field must preserve unrelated labels, assignees, Project fields, and body content unless Paul requested changes.
- After changing the repository or issue number, resolve the Project item ID again before mutating fields.

## Verification

Before reporting completion, verify all applicable facts from live GitHub:

- issue URL, repository, number, state, title, and native type;
- Project membership and Project item ID;
- Status and Owner;
- Priority and created time when ranking changed;
- dependency relationship and visual label when changed;
- absence of Project membership after removal;
- no duplicate issue was created;
- no requirements, acceptance criteria, or implementation details were added beyond what Paul supplied.

Return the issue URL and a concise summary of verified board state. If authentication or permission blocks verification, report the exact blocker and do not claim success.

## Pitfalls

- Project Status and issue open/closed state are separate.
- Assignee and the Project's Owner field are separate.
- Project views are not separate projects or issue types.
- Native Eval type alone does not trigger Factory routing.
- Creating a Bug does not start a factory fix; only Ready or `factory-assign` hands it over.
- Ready on a Bug, Feature, or Task in a factory target hands it to the factory regardless of Owner.
- Never change an existing issue's type between Task, Bug, and Feature without Paul's confirmation; the type selects the factory work kind.
- `eval-request` can route an authorized `agent-evals` issue to Owner=factory and Ready.
- Routing initializes fields; it does not continuously overwrite later human field choices.
- Closed-item routing is event-driven and may not repair cards whose closing event predated the deployed workflow.
