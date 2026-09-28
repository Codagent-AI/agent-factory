- [x] Implement the change described by these files:
  - [proposal.md](proposal.md)
  - [specs/factory-operations/spec.md](specs/factory-operations/spec.md)
  - [specs/factory-claim-lifecycle/spec.md](specs/factory-claim-lifecycle/spec.md)
  - [specs/factory-eval-reporting/spec.md](specs/factory-eval-reporting/spec.md)
  - [specs/factory-eval-execution/spec.md](specs/factory-eval-execution/spec.md)
  - [specs/factory-fly-execution/spec.md](specs/factory-fly-execution/spec.md)
  - [design.md](design.md)
  - [test-plan.md](test-plan.md)
  - [decisions.md](decisions.md)

  **Scope.** The whole change is one task.
  - **Terminal time.** Write `terminal_at` in `cleanup_json`, and only on a real transition
    into a terminal lifecycle (design D1, D8). Backfill it once from `updated_at`. Reopen
    cleanup and retention in `reserve_run`.
  - **Terminal sweep.** Add the sweep in `src/agent_factory/terminal.py`, run after the
    per-card loop in `runtime.cycle`, covering superseded and off-board claims.
  - **Release.**
    - Release terminal claims through a new `WorkKindHandler.release`.
    - For eval claims, release through `WorktreeCleanup.release`.
    - For pull-request claims, release through `PullRequestCleanup._release`, which now
      removes the whole `<root>/clones/<claim>/` directory.
    - Apply the quiescence, undelivered-reporting, and merged-only sync gates on both the
      sweep and the Done path (D10, D11).
  - **Retention.** Prune only from the sweep, along the three paths.
  - **Expiry.** Post the unreviewed eval expiry as the durable `review-expired` event, and
    update the human-review handoff text.
  - **Registry deletion.** Delete registry images by digest with current proof of ownership
    (D2, D9). Use `list_tags` and `delete_manifest` on `FlyMachinesClient` and
    `fly/registry_cleanup.py`, isolated from Machine disposal (D3).
  - **Status.** Add the `status` lines.
  - **Configuration and docs.** Add `[limits] unreviewed_retention_days` (default 30).
    Update `docs/operations.md` and `docs/installation.md`.

  **Tests.** Implement every `INT-*` and `E2E-*` obligation in `test-plan.md`. Add unit
  tests for the spec scenarios. `uv run pytest`, `uv run ruff format --check .`,
  `uv run ruff check .`, `uv run pyright`, and `uv build` must pass.

  **Safety constraints during implementation.**
  - Never run `tick`, `resident`, or `scripts/deploy.sh` with the live configuration.
  - Never modify `~/.agent-factory` state.
  - Never post to GitHub issues.
  - Never touch `claim-`, `base`, or `deployment-` registry tags.
  - Live registry checks follow the test plan's acceptance envelope, which allows only
    self-created `factory-probe-` tags, each deleted afterwards.
  - Do not bump the SQLite schema version.

  **Spec adjustments.** Small specification adjustments discovered during implementation
  are permitted. Record each one in the affected delta spec under `specs/`, and log it in
  `decisions.md`. Anything that changes what may be deleted, or when, returns to
  definition.
