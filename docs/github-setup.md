# GitHub routing deployment

The Codagent example uses the existing Codagent Factory App, Project #1, native `Eval` issue type, and IDs in `config/codagent.toml`. The App key remains outside Git; local keys must be owner-readable only. Repository Actions use `FACTORY_APP_PRIVATE_KEY` plus `FACTORY_APP_ID` / `FACTORY_APP_INSTALLATION_ID` variables.

Create labels idempotently in each configured source repository before enabling callers:

```sh
gh label create eval-request --repo Codagent-AI/agent-evals --color 0E8A16 --force
gh label create needs-input --repo Codagent-AI/agent-evals --color B60205 --force
```

`eval-request` is applied by the regular Markdown template. `needs-input` is reserved for later controller feedback. The red `needs-input` label and request label are deliberately separate from Project fields.

Publish `agent-factory` first. Then replace each caller workflow's `FACTORY_REVISION` with that published, immutable full commit SHA and deploy it on the caller's default branch. Only then issue and pull-request events invoke the trusted reusable workflow and its matching configuration. Update the installed local factory and caller pins together; neither automatically follows `main`.

The configured `agent-evals` harness SHA is a real immutable execution pin, not a local `HEAD` or branch. Routing does not establish suite readiness. Before changing it, verify the selected revision contains the score-failure contract, calibration-gate removal, and linked-worktree metadata mounts described in [suite integration](suite-integration.md); then deploy the same explicit revision before unpausing admission.

The reusable workflow mints a short-lived App installation token and passes it only through `GH_TOKEN` to `gh api`. It never checks out contributor pull-request code and reads the current source item from the base-repository event context. Routing checks the author's effective collaborator permission; only `write`, `maintain`, and `admin` receive factory ownership and Ready. Unknown or denied permission is Backlog without ownership. A stable issue/PR receipt records one-time initialization, so retries preserve later human changes. Closing a tracked issue or PR moves its card to Done.
