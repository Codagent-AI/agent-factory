# Task: Add `--session-dir` to `agent-runner run` and rebuild the installed binary

## Goal

Give `agent-runner run` a `--session-dir <path>` flag so a caller can place the run's session directory deterministically, and land it on the Runner's default branch and in the operator's installed binary. Agent Factory's host execution mode depends on this flag to keep a host fix attempt's Runner session under the attempt's artifact directory instead of `~/.agent-runner/projects/`, and factory readiness refuses to launch host attempts until the installed binary lists the flag.

## Background

This task is in the **agent-runner** repository at `/Users/paul/codagent/agent-runner` (GitHub `Codagent-AI/agent-runner`), not in agent-factory. Work on a branch off `main` and open a PR; the factory change that consumes the flag is planned in `agent-factory` under `openspec/changes/dockerless-option/` and is not part of this task.

Current state of the Runner:

- The library already supports a caller-supplied session directory: `internal/runner/runner.go` declares `Options.SessionDir` ("Override session directory; computed automatically if empty"). When set, the Runner creates the directory with `MkdirAll`, writes no project `meta.json` under `~/.agent-runner/projects/`, and never removes a caller-provided directory. Do not change these semantics.
- The `run` command has no flag for it. `cmd/agent-runner/main.go` parses `run` flags in `parseRunCommandArgs` (which already handles `--until` and `--until=<value>`), carries them on `runCommandOptions` (`from`, `until`, `profileOverride`, `headless`, `agentOverride`, `liveOpts`), and builds the run through a `prepare` closure calling `prepareFreshRun(&freshRunRequest{...})` inside `handleRunWithRunOptions`. The session directory is decided while preparing the fresh run; the `RunHandle` carries it as `h.SessionDir`, and both the headless path (`AGENT_RUNNER_NO_TUI=1`, `runner.ExecuteFromHandle`) and the TUI path read it from the handle. The onboarding path (`handleOnboardingFromRun`) shares `runCommandOptions`.
- `printRunUsage` prints the `run` usage text and lists `--until`. `run --help` output is what the factory's readiness check inspects for the string `--session-dir`.
- Tests for `run` argument parsing live in `cmd/agent-runner/start_run_test.go` (see `TestParseRunCommandArgsSupportsUntilFlag`); use the same style.
- `make build` produces `bin/agent-runner`; the operator's install is the symlink `/Users/paul/.local/bin/agent-runner -> bin/agent-runner`, and `agent-runner -version` currently prints `dev`.

Constraints:

- Accept both `--session-dir <path>` and `--session-dir=<path>`; a missing value is an error in the same style as `--until requires a step ID`.
- Plumb the value from `runCommandOptions` through `freshRunRequest`/`prepareFreshRun` to `runner.Options.SessionDir` so both the headless and the TUI paths honour it; the onboarding path that shares `runCommandOptions` must keep compiling and behaving.
- Relative paths are resolved against the current working directory before use; the flag does not create a project entry under `~/.agent-runner/projects/`.
- `--session-dir` combined with `--resume` is not required; if the combination is meaningless in the current code, reject it with a clear message rather than silently ignoring the flag.
- Keep the change minimal: no new configuration keys, no change to session layout or discovery.

## Spec

The factory's `factory-fix-execution` specification (in agent-factory, `openspec/changes/dockerless-option/specs/factory-fix-execution/spec.md`) states the requirement this flag serves:

> In `host` mode the factory SHALL run the packaged fix workflow through the operator's installed Agent Runner, from the attempt's target clone, with the operator's own HOME so the selected CLIs use the operator's existing logins and installed codagent plugin. The launch SHALL write nothing to the operator's user-level configuration: [...] the Runner SHALL be given a session directory under the attempt's artifact directory; [...]

and the `factory-operations` specification:

> A host-mode fix attempt's Runner session directory SHALL be placed under the attempt's artifact directory by the factory at launch, so it lies under the root and is covered by the same evidence and retention rules; the run record SHALL name it.

The factory will invoke the Runner as:

```
agent-runner run factory-fix \
  --session-dir <evidence>/agent-runner-session \
  --param issue_file=<evidence>/input/issue.json \
  --param branch_name=<branch> \
  --param contract_version=factory-fix/1 \
  --param artifact_dir=<evidence>
```

with `AGENT_RUNNER_NO_TUI=1` set and the target clone as the working directory.

## Done When

- `parseRunCommandArgs` accepts `--session-dir <path>` and `--session-dir=<path>`, rejects a missing value with a clear error, and leaves positional and `--param` arguments untouched; unit tests in `cmd/agent-runner/start_run_test.go` cover the accepted forms, the missing-value error, and that the value reaches `runner.Options.SessionDir`.
- Running a workflow headlessly (`AGENT_RUNNER_NO_TUI=1 agent-runner run <workflow> --session-dir <tmp>`) writes `state.json`, `audit.log`, and step output under `<tmp>`, creates nothing new under `~/.agent-runner/projects/`, and leaves `<tmp>` in place after both success and failure; covered by a test using a temporary HOME.
- `agent-runner run --help` lists `--session-dir` with a one-line description.
- `go vet ./...` and `make test` pass; the change is committed on a branch, pushed, and a PR against `main` is open in `Codagent-AI/agent-runner`.
- The PR is merged into `main`, `main` is pulled in `/Users/paul/codagent/agent-runner`, `make build` has been run, and `agent-runner run --help` invoked through the operator's PATH (`/Users/paul/.local/bin/agent-runner`) lists `--session-dir`. These are hard completion criteria, not best effort: the design requires the flag to be installed before the factory's host mode is implemented, and the factory's host launch test needs the installed binary. If the merge or rebuild cannot be completed, this task is blocked and must be reported as incomplete with the PR link and the blocking reason; do not report it done.
