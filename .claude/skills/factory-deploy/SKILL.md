---
name: factory-deploy
description: Redeploy the live Agent Factory on Paul's Mac with the latest merged code, including the latest Agent Runner, by running scripts/deploy.sh. Use when asked to deploy, redeploy, or update the factory, to make the factory use newly merged Agent Factory, Agent Runner, or Agent Evals changes, or after a factory fix PR merges.
---

# Factory deploy

Run the deploy script from the root of any Agent Factory checkout:

```sh
scripts/deploy.sh               # factory origin/main + latest Agent Runner
scripts/deploy.sh --no-runner   # factory only
scripts/deploy.sh origin/<branch>   # agreed hotfix only; see factory-watch
```

Pass `--no-runner` or a ref only when Paul asks. `AGENTS.md` ("Deploying") describes each step.

## What it covers

- **Agent Factory**: a new immutable release is built at `~/.agent-factory/releases/<commit>`, and the service moves to it. Running jobs keep their release, so deploying while jobs run is safe. Only the newest two releases are kept, plus any a job still uses.
- **Agent Runner**: the checkout configured as `[repositories] agent_runner` is brought up to `origin/dev`. Then `origin/main` is fast-forwarded or cleanly merged into `dev` and pushed, because evals build from `dev`. Finally `make build` updates the host runner that fix runs use.
- **Agent Evals**: nothing to do. Each eval admission fetches `harness_ref`.

## Reading the result

- **Runner step skipped because a fix is running**: the factory deployed, but the host runner was not rebuilt. If the runner changed, rerun the script once the fix slot is free, or tell Paul.
- **`warning:` lines**: the runner step or the `dev` merge was skipped, for example because of uncommitted changes, a branch other than `dev`, or a merge conflict. The deploy continued without it. Report the warning and what Paul needs to resolve.
- **Stopped with "the factory stays paused"**: report the printed failures. If `doctor` failed, the service still points at the previous release. Fix the cause and rerun the script. Do not `resume` by hand while `doctor` fails.
- **Success**: the last line is `deployed <sha>`. Report the factory commit, the runner commit it built, and whether it pushed `dev`.

The script restores the pause state the factory had before the deploy. If Paul had paused it, it stays paused.
