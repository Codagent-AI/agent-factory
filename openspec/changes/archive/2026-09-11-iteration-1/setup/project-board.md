# Provisioned Codagent board

Project: https://github.com/orgs/Codagent-AI/projects/1

Created through the installed App during design, with private visibility (GitHub's default). Two explicitly marked dummy issues were subsequently created in agent-factory to verify Type swimlanes: #1 (Eval) and #2 (Task). They have no factory ownership. Paul confirmed the board looks correct and tested a manual drag; both temporary issues were subsequently deleted after confirmation.

| Setting | Provisioned value |
|---|---|
| Status | Backlog, Ready, Running, Review, Done |
| Owner | factory, human; may remain unset |
| Refs | Text |
| Verdict | pending-human-review, failed, quota-deferred, infra-error |
| Native Type | Existing GitHub issue-type field; no duplicate custom field |
| All work | Board view 2; no filter |
| Eval queue | Board view 3; `type:Eval owner:factory status:Ready` |
| Active factory work | Board view 4; `owner:factory status:Ready,Running,Review` |

All three view creation responses confirm Status columns, horizontal grouping by the native Type field, visible labels/Owner/Refs/Verdict/Repository/Type, and an empty sort configuration for manual ordering. The initial unused table view was removed. A subsequent GraphQL read independently confirms the three board layouts, filters, Status columns, and no field sorting. Its fields and groupByFields connections omit the native Type field even though the REST fields response includes it and view creation confirms its group_by ID; Paul subsequently confirmed the visual layout after the two dummy issues were added.

`project-fields.json` and `project-views.json` preserve the REST setup evidence. `project-board.json` is the subsequent GraphQL read. `project-identifiers.toml` records field and option IDs for the implementation agent; it does not prescribe the final configuration schema.

## Remaining

- Paul created the native organizational issue type **Eval** manually. A subsequent API read verified it exists and is enabled; `eval-issue-type.json` records its identity. The current CLI token lacks admin:org for type creation, so no broader credential permission was added.
- Board appearance and native Type swimlanes were confirmed by Paul. Both temporary dummy issues have been deleted using the administrator CLI identity; the App cannot delete issues.
- Templates, labels, routing workflows, general-work intake, and closure automation remain implementation work. Creating this empty board does not enable those behaviors.
