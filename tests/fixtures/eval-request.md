---
name: Evaluation request
about: Request a configured and-scene evaluation
title: "Eval: "
labels: ["eval-request"]
type: Eval
---

<!-- The eval-request label is the explicit factory marker. Do not place credentials here. -->

Describe the change or comparison this evaluation should perform.

```eval
# Optional TOML overrides. Invalid values route for correction and are not executed.
agent_runner_ref = "main"
agent_skills_ref = "main"
lead = "codex:gpt-5.6-sol:high"
implementor = "codex:gpt-5.6-sol:high"
tester = "codex:gpt-5.6-sol:high"
skip_validator = false
repetitions = 3
```
