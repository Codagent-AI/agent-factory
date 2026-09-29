## Why

The service watcher (#61) now dispatches PR reviews and failure triage itself. The interactive `factory-watch` skill and its `watch.sh` loop cost a session per poll and duplicate the service's reviews. Paul wants an on-demand status report instead of a watcher.

## What Changes

- Remove the `factory-watch` skill and `watch.sh`.
- Add a `factory-triage` skill holding the failure-handling procedure, including the Headless triage section the service's `factory-watch` workflow follows.
- Add a `factory-status` skill that reports the factory's state once, on request, and never watches.
- Point the watch workflow's triage prompt at `factory-triage`.

## Impact

- `.claude/skills/`, `src/agent_factory/watch/workflow/factory-watch-v1.0.yaml`, `docs/operations.md`, `AGENTS.md`.
- The workflow contract `factory-watch/1` is unchanged.
