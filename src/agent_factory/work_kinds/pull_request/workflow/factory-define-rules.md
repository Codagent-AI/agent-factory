# Autonomous feature definition

Follow these rules in every lead step. There are no approval gates: write the artifact to the named path and continue. For each open question, decide using the issue and eligible comments and record the assumption in the relevant artifact. Stop only for a direction-level decision: the choice contradicts the issue; the issue admits materially different readings; it breaks a public interface or persisted data format; or it requires a decision or change outside the target repository. A proposal no-go is also a stop.

Append every decision to `<change>/decisions.md` with the step, decision, alternatives considered, and whether it is decision-bearing. For a stop, write `{{artifact_dir}}/define-stop.json` with `step`, `questions` (array of strings), and `direction_summary`, then end the step. The ask-questions headless rule means stop or decide, never wait for input.
