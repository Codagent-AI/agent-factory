# GitHub routing deployment

The Codagent example uses the existing Codagent Factory App, Project #1, native `Eval` issue type, and IDs in `config/codagent.toml`. The App key remains outside Git; local keys must be owner-readable only. Repository Actions use `FACTORY_APP_PRIVATE_KEY` plus `FACTORY_APP_ID` / `FACTORY_APP_INSTALLATION_ID` variables.

The App commits each finished eval repetition's curated results to `eval.results_repository` on `eval.results_branch` (Codagent: `Codagent-AI/agent-evals` `main`), so its installation needs **Contents: write** on that repository and the branch must accept the App's pushes. Without it, the factory posts a "results could not be saved" note on the eval issue and retries every tick.

Create labels idempotently in each configured source repository before enabling callers:

```sh
for repo in Codagent-AI/agent-evals Codagent-AI/agent-runner Codagent-AI/agent-skills \
            Codagent-AI/agent-validator Codagent-AI/agent-plugin; do
  gh label create eval-request --repo "$repo" --color 0E8A16 --force
  gh label create needs-input --repo "$repo" --color B60205 --force
  gh label create factory-hold --repo "$repo" --color BFD4F2 --force
done
```

`eval-request` is applied by the regular Markdown template. `needs-input` is reserved for later controller feedback. `factory-hold` is the bug rule's bypass marker: a Bug-typed issue carrying it routes to `Owner=human` / Backlog instead of factory admission. All three labels are deliberately separate from Project fields.

## Bug routing and the tracking-only template

Any open issue whose native Type is `Bug` in a configured source repository routes to `Owner=factory` / Ready when its author holds the maintain or admin repository role (a write-only author's bug enters Backlog), unless `factory-hold` is already applied when the creation event is delivered — the label must be present at delivery time, since routing does not retroactively rescan existing issues. Add `.github/ISSUE_TEMPLATE/bug-tracking-only.md` (copied from this repository) to each configured source repository so a writer can file a tracking-only bug in one step; it pre-sets the Bug type and the `factory-hold` label. A bug filed without that template and without the label is picked up by the factory like any other authorized Bug-typed issue.

```sh
for repo in Codagent-AI/agent-runner Codagent-AI/agent-skills \
            Codagent-AI/agent-validator Codagent-AI/agent-plugin Codagent-AI/agent-evals; do
  gh api "repos/$repo/contents/.github/ISSUE_TEMPLATE/bug-tracking-only.md" \
    --method PUT \
    --field message="Add Bug (tracking only) issue template" \
    --field content="$(base64 < .github/ISSUE_TEMPLATE/bug-tracking-only.md)"
done
```

## Fix credential and branch protection

When `[feature]` is enabled, a writer-authored issue with the native `Feature`
type in a configured fix target can be handed to Factory by moving its Project
card to Ready. The next poll verifies the author's write, maintain, or admin
repository permission and sets `Owner=factory`; the router does not assign
Feature issues to Factory on its own. The feature workflow opens a pull request
with the same credential and target branch rules as the fix kind. A retyped,
untouched Bug card returns to Backlog instead of retaining its automatic Ready
assignment.

The fix work kind opens pull requests with a separate fine-grained personal access token, distinct from both the App installation token and the suite candidate token. Create it (preferably on a non-admin machine user) with **Contents**, **Pull requests**, and **Issues** access on the five target repositories only — no Workflows, Administration, or Projects access — and store it locally per [installation](installation.md#fix-kind-prerequisites). `doctor` verifies its shape, identity, and reach; it warns rather than fails if the identity turns out to hold organization-admin rights, since that is a hardening recommendation, not a hard requirement.

Protect `main` on each target repository with a ruleset requiring a pull request before merge, with no bypass for the fix credential's owner — the credential must never be able to push directly:

```sh
for repo in Codagent-AI/agent-runner Codagent-AI/agent-skills \
            Codagent-AI/agent-validator Codagent-AI/agent-plugin Codagent-AI/agent-evals; do
  gh api "repos/$repo/rulesets" --method POST --input - <<'JSON'
{
  "name": "protect-main-require-pr",
  "target": "branch",
  "enforcement": "active",
  "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}},
  "rules": [{"type": "pull_request"}]
}
JSON
done
```

## Bumping the caller revision

Each caller subscribes to issue `typed` events as well as creation, edits, closure, and label changes, so a Bug type set after an issue is created still routes it:

```yaml
on:
  issues:
    types: [opened, reopened, edited, closed, labeled, unlabeled, typed]
  pull_request:
    types: [opened, reopened, edited, closed, labeled, unlabeled]
```

After publishing a change to the shared routing workflow (including the bug rule, `bug_type`, and `hold_label`), replace `FACTORY_REVISION` in each of the five caller workflows with the new published commit SHA and deploy it on each caller's default branch, exactly as for any other shared-workflow change (see below).

Publish `agent-factory` first. Then replace each caller workflow's `FACTORY_REVISION` with that published, immutable full commit SHA and deploy it on the caller's default branch. Only then issue and pull-request events invoke the trusted reusable workflow and its matching configuration. Update the installed local factory and caller pins together; neither automatically follows `main`.

The configured `agent-evals` harness is a branch name (`eval.harness_ref`, default `main`), not a commit SHA; Factory resolves it to a commit at each claim's admission, and that resolved commit — not the branch name — is the comparability key across nights. Routing does not establish suite readiness. Before changing the branch, verify the revision it currently resolves to contains the score-failure contract, calibration-gate removal, and linked-worktree metadata mounts described in [suite integration](suite-integration.md); then deploy with that branch pointed at a ready revision before unpausing admission.

The reusable workflow mints a short-lived App installation token and passes it only through `GH_TOKEN` to `gh api`. It never checks out contributor pull-request code and reads the current source item from the base-repository event context. Routing checks the author's effective collaborator permission; for eval requests only `write`, `maintain`, and `admin` receive factory ownership and Ready, and for bugs only the `maintain` and `admin` roles do. Unknown or denied permission is Backlog without ownership. A stable issue/PR receipt records one-time initialization, so retries preserve later human changes. A Bug routed with the `factory-hold` bypass label carries `"hold_bypassed": true` in that receipt; the flag is sticky across receipt rewrites, so removing the label alone does not admit it. Hand it to the factory by moving it to Ready. Project column changes do not trigger the repository routing workflow, so the factory's regular Project poll detects an open configured Bug in Ready, verifies the author's effective write permission, and sets `Owner=factory`. A GitHub assignee is not required. Closing a tracked issue or PR moves its card to Done.

The shared `[github].bot_login` identifies the installed App's comment author
(for example, `codagent-factory[bot]`). Set it for your own App so lost-response
reconciliation recognizes its existing comments. The controller will not admit
work without an explicit identity.

The parser's ordinary CI contract uses the vendored `tests/fixtures/eval-request.md`
and its recorded companion revision, without requiring a sibling checkout.
Before publishing a changed issue template, compare that fixture with the delivered
`agent-evals/.github/ISSUE_TEMPLATE/eval-request.md` and refresh both fixture and
source revision together. Live template behavior remains part of AT-001.
