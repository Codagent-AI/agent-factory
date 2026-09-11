# `and-scene` suite integration

Agent Factory evaluates only the `and-scene` suite in iteration 1. Its deployed
harness is the full immutable SHA in `config/codagent.toml`, currently
`488550420230d0fccf8135c8dfa6abc5937942c0`. That revision includes the
automated-outcome contract, calibration-gate removal, linked-worktree metadata
mounts, Cursor session persistence, and the `tester` role interface. Do not
replace this pin with a branch name or a local checkout head. The configured
role names are `lead`, `implementor`, and `tester`; their defaults are complete
`cli:model:effort` selections in the same configuration file.

The machine-local configuration identifies the three source repositories, a
factory storage root, and a separate suite environment file. The storage root
defaults operationally to `~/.agent-factory/`; public examples use portable
paths. The suite environment file contains only candidate-delivery credentials
(for example, a repository-scoped token). It must never contain the Factory App
key or App token.

For every accepted claim, Factory resolves and records the full Runner, Skills,
and harness commits and creates detached worktrees below:

```
<storage-root>/worktrees/<claim-id>/{runner,skills,evals}
<storage-root>/artifacts/<claim-id>-rep-<n>
```

These paths remain fixed through quota waits and recovery. A valid suite
checkpoint is resumed with `--resume`; Factory permits a fresh retry using the
same artifact directory only when it has durable proof that the attempt stopped
before checkpoint and candidate execution. Corrupt or unexplained missing state
is a readiness/recovery error, never a new evaluation.

The selected wrapper passes backing `.git` and common Git directories through
the Runner's repeated `--docker-run-arg` interface, mounted read-only at their
host-resolved paths. This lets Git inside the container verify linked worktree
provenance without modifying a source checkout or Git metadata.

When a repetition is `pending-human-review`, the Factory report includes an
absolute, shell-quoted command of this form:

```sh
/absolute/path/to/human-review.sh --run-dir '/absolute/path/to/artifacts/run id'
```

Run it on the Mac holding the files. The command is valid while the item remains
in Review; Factory never performs the human rating. Product failures and
incomplete results intentionally receive no review command.

Evidence, candidate branches, draft PRs, controller logs, and SQLite history
are retained until operator-managed cleanup. After a reviewed item is observed
in Done, Factory removes only its recorded Runner, Skills, and evals worktrees.
It records partial failures and retries later; it does not prune evidence or
touch source checkouts, other claims, candidate branches, or PRs. Before manual
storage cleanup, confirm no remaining item is Running, waiting, or in Review.

Candidate environment files follow the selected Runner launcher's literal
`NAME=value` format: quotes, backslashes, spaces after `=`, and inline `#` are
part of the value. Do not add shell quotes around tokens or expect variable
expansion. Factory readiness and suite execution interpret these files identically.
