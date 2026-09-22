- [x] [Fly execution mode — configuration, Machines API client, intake, readiness, doctor, and status](tasks/01-fly-config-readiness-operations.md)
- [x] [Fly launcher, guest scripts, and the and-scene plan under `fly`](tasks/02-fly-launcher-and-guest.md)
- [x] [Supervise Fly attempts through Machine ownership](tasks/03-fly-supervision-and-ownership.md)
- [x] [Dispose, hold, reconcile, and report Fly attempts end to end](tasks/04-fly-disposal-reconciliation-reporting.md)

## Operator gates (not implementor tasks)

- Before backend code is relied on: the launcher's `stand-in` mode against the real Fly app
  (acceptance flows AT-001 to AT-003) proves the production path with cheap jobs; HT-001 is the
  full-eval gate (design risk "unproven eval workload in a Machine"); it spends the operator's
  model quota.
- The `agent-runner` companion change (Dockerfile `chrome-linux64` path, `.dockerignore`) and the
  amd64 base image build precede live use.
- After task 4: AT-001 to AT-004 (agent acceptance against the real Fly app) and HT-001 (one full
  eval with human review) from `test-plan.md`. AT-002's `stopped` observation gates the quota-hold
  design; its recorded fallback is `auto_destroy: false`.
