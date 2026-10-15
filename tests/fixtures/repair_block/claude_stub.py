#!/usr/bin/env python3
"""Local Claude stream-json stand-in. Unknown prompts fail instead of using a model."""

import json
import os
import re
import sys
from pathlib import Path

prompt = sys.argv[-1]
if "--version" in sys.argv:
    print("stub claude 1.0")
    sys.exit(0)
if "--" not in sys.argv:
    raise SystemExit("expected a headless Claude prompt")
assert "-p" in sys.argv and "stream-json" in sys.argv
session_flag = "--resume" if "--resume" in sys.argv else "--session-id"
session = sys.argv[sys.argv.index(session_flag) + 1]
transcript = Path.home() / ".claude/projects" / re.sub(r"[/._]", "-", os.getcwd())
transcript.mkdir(parents=True, exist_ok=True)
(transcript / (session + ".jsonl")).write_text("{}\n")
response = "Done"
if "STUB_BLOCK" in prompt:
    response = "A human must resolve the test blocker.\nREPAIR_BLOCKED"
else:
    change = Path("openspec/changes/runtime-plan")
    change.mkdir(parents=True, exist_ok=True)
    producers = {
        "Invoke codagent:propose": ("proposal", "proposal.md"),
        "Invoke codagent:spec ": ("specs", "specs/runtime/spec.md"),
        "Invoke codagent:design": ("design", "design.md"),
        "Invoke codagent:test-plan": ("test-plan", "test-plan.md"),
        "with exactly one implementation task": ("write-tasks", "tasks.md"),
    }
    for marker, (step, filename) in producers.items():
        if marker in prompt:
            file = change / filename
            file.parent.mkdir(parents=True, exist_ok=True)
            content = step + "\n"
            if step == "write-tasks":
                content += (change / "design.md").read_text() + (
                    change / "test-plan.md"
                ).read_text()
                content += "- [ ] Implement the runtime task\n"
            file.write_text(content)
            with (Path(os.environ["STUB_ARTIFACTS"]) / "producers.jsonl").open("a") as log:
                log.write(json.dumps(step) + "\n")
            break
    else:
        if any(
            marker in prompt
            for marker in (
                "Invoke codagent:proposal-review",
                "Invoke codagent:review-approach",
                "Read proposal-review-findings.json",
                "Read approach-review-findings.json",
                "Compare openspec/changes/",
            )
        ):
            (change / "decisions.md").write_text("Review applied\n")
        else:
            raise SystemExit("unrecognized stub prompt: " + prompt)
print(
    json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": response}]}})
)
print(
    json.dumps(
        {
            "type": "result",
            "result": response,
            "is_error": False,
            "total_cost_usd": 0,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    )
)
