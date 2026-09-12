# GitHub routing deployment

The Codagent example uses the existing Codagent Factory App, Project #1, native `Eval` issue type, and IDs in `config/codagent.toml`. The App key remains outside Git; local keys must be owner-readable only. Repository Actions use `FACTORY_APP_PRIVATE_KEY` plus `FACTORY_APP_ID` / `FACTORY_APP_INSTALLATION_ID` variables.

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

Any open issue whose native Type is `Bug` in a configured source repository routes to `Owner=factory` / Ready when its author has write, maintain, or admin access, unless `factory-hold` is already applied when the creation event is delivered — the label must be present at delivery time, since routing does not retroactively rescan existing issues. Add `.github/ISSUE_TEMPLATE/bug-tracking-only.md` (copied from this repository) to each configured source repository so a writer can file a tracking-only bug in one step; it pre-sets the Bug type and the `factory-hold` label. A bug filed without that template and without the label is picked up by the factory like any other authorized Bug-typed issue.

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

After publishing a change to the shared routing workflow (including the bug rule, `bug_type`, and `hold_label`), replace `FACTORY_REVISION` in each of the five caller workflows with the new published commit SHA and deploy it on each caller's default branch, exactly as for any other shared-workflow change (see below).

Publish `agent-factory` first. Then replace each caller workflow's `FACTORY_REVISION` with that published, immutable full commit SHA and deploy it on the caller's default branch. Only then issue and pull-request events invoke the trusted reusable workflow and its matching configuration. Update the installed local factory and caller pins together; neither automatically follows `main`.

The configured `agent-evals` harness SHA is a real immutable execution pin, not a local `HEAD` or branch. Routing does not establish suite readiness. Before changing it, verify the selected revision contains the score-failure contract, calibration-gate removal, and linked-worktree metadata mounts described in [suite integration](suite-integration.md); then deploy the same explicit revision before unpausing admission.

The reusable workflow mints a short-lived App installation token and passes it only through `GH_TOKEN` to `gh api`. It never checks out contributor pull-request code and reads the current source item from the base-repository event context. Routing checks the author's effective collaborator permission; only `write`, `maintain`, and `admin` receive factory ownership and Ready. Unknown or denied permission is Backlog without ownership. A stable issue/PR receipt records one-time initialization, so retries preserve later human changes. Closing a tracked issue or PR moves its card to Done.

The shared `[github].bot_login` identifies the installed App's comment author
(for example, `codagent-factory[bot]`). Set it for your own App so lost-response
reconciliation recognizes its existing comments. The controller will not admit
work without an explicit identity.

The parser's ordinary CI contract uses the vendored `tests/fixtures/eval-request.md`
and its recorded companion revision, without requiring a sibling checkout.
Before publishing a changed issue template, compare that fixture with the delivered
`agent-evals/.github/ISSUE_TEMPLATE/eval-request.md` and refresh both fixture and
source revision together. Live template behavior remains part of AT-001.
