- [x] [Clean up finished claims outside Done and delete their Fly images](tasks/01-terminal-claim-cleanup.md)

## Operator gates (not implementor tasks)

- After the task, run agent acceptance AT-001, AT-002 (conditional), and AT-003 from
  `test-plan.md`.
  - AT-001 is read-only against the live store.
  - AT-002 writes and deletes one test-owned manifest in the `agent-factory-sandbox` registry.
  - AT-003 runs on an isolated root through the fake-GitHub harness.
- Never tick against the live board or the live `~/.agent-factory` root.
- After merge, deploy with `scripts/deploy.sh`.
  - The first tick records terminal observations only.
  - Watch `status` on day 3 and day 14 while each backlog drains, and look for any Fly
    image-deletion failures.
