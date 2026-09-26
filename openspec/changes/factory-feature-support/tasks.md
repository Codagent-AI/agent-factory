- [x] [Own every execution through a named backend and settle runs left unsupervised by a restart](tasks/01-execution-backends.md)
- [x] [Turn the fix kind into a shared pull-request work kind](tasks/02-shared-pull-request-kind.md)
- [x] [Carve `core/verify-change` out of Agent Runner's `core/implement-change`](tasks/03-runner-verify-change.md)
- [x] [Package the autonomous `factory-feature` and `factory-define` workflows](tasks/04-feature-workflows.md)
- [x] [Configure, admit, run, and report feature work on the host](tasks/05-feature-kind-happy-path.md)
- [x] [Stop, resume, continue, and recover feature claims](tasks/06-feature-stops-resumes-recovery.md)

## Operator gates (not implementor tasks)

- The Agent Runner pull request from task 3 must be merged to Runner `main`, must reach
  Runner `dev` (confirm after `scripts/deploy.sh` that `origin/dev` contains the merge;
  deploy skips the merge with a warning on conflict), and the installed host Runner must
  provide `core/verify-change` before a deploy that includes task 6's `[feature]` section.
  Doctor's `feature-host` group enforces the last condition.
- Task 6's final commit (the `[feature]` section in `config/codagent.toml`) is the
  migration plan's enabling step; ship it as a separate pull request when features should
  be enabled after the rest of the change is deployed.
- After task 6: agent acceptance AT-001 to AT-005 and human-only HT-001 from `test-plan.md`,
  run in a disposable scratch repository with the installed Runner, never against the live
  service's configuration or database.
