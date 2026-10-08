# Codagent GitHub Project API Reference

Manage [the Codagent board](https://github.com/orgs/Codagent-AI/projects/1) with `gh api`, without opening a browser. These recipes apply to **Task, Bug, Feature, and Eval** issues across repositories.

Verified 2026-09-10 (factory handoff and routing sections updated 2026-10-03) against the live Project, native issue types, dependency API, deployed routing workflows, and Agent Factory code. Examples below are instructions to run when desired; writing this guide did not modify GitHub.

The GitHub Project is the canonical backlog for Codagent work. Do not maintain separate backlog notes in this vault; keep newsletter and other non-product planning in their own notes.

## Daily workflow

1. Search open and closed issues before creating anything; reuse an issue when it represents the same work.
2. Create the issue in the repository that owns the work and classify it as **Task**, **Bug**, **Feature**, or **Eval**.
3. Confirm it appears on the Codagent Project. Routing handles configured repositories; add issues from other repositories manually.
4. Put unprioritized, undeveloped, or blocked work in **Backlog** with Owner set to human or left unset. In factory target repositories, **Ready hands a Bug, Feature, or Task to the factory** regardless of Owner, so rank human work with Priority and leave it in Backlog. Use the `factory-assign` skill to hand work to the factory.
5. Rank work with the organization `Priority` issue field (`Urgent`, `High`, `Medium`, `Low`), not by dragging cards. Newest created wins among equal Priority. New issues default to Low; do not leave Priority empty. Do not create priority labels.
6. Move active work through **Running** and **Review**. Close completed issues; routing moves tracked closed issues to **Done**.

In factory target repositories, **Ready** is an admission signal, not merely a human priority, for every type. Use the rules below before moving a card to Ready, assigning Owner=factory, or adding `eval-request`.

## What lives where

| Concept | Meaning | Where to change it |
|---|---|---|
| Issue | The actual work request, discussion, and history. It belongs to one repository. | Repository Issues API |
| Native issue type | One classification: Task, Bug, Feature, or Eval. Shared organization vocabulary. | Issue's `type`, not a label |
| Label | Zero or more tags, such as `blocked`, `eval-request`, or `needs-input`. Labels belong to repositories. | Issue labels API |
| Project item | The board's reference to an issue. One issue can appear in several projects. | Projects GraphQL API |
| Project field | Board-specific information: Status, Owner, Refs, Verdict, plus the org `Priority` issue field. | Project item field mutation, or the issue Priority control |
| Assignee | A GitHub user assigned to the issue. | Issue API; separate from the Project's Owner field |

`Feature` includes enhancements to existing behavior. The redundant `enhancement` labels were removed; do not recreate them. A `feature` label, if retained on an older issue, does not set its native type or its swimlane.

| Type | Use when | Example |
|---|---|---|
| **Bug** | Existing behavior is broken or violates its expected contract. | Resume loses a saved agent session. |
| **Feature** | You want new behavior or an improvement to existing behavior. | Allow multiple Validator reviewers in a configuration. |
| **Task** | Behavior-preserving maintenance: dev tools and dependencies, CI, docs, refactors, cleanups. Not runtime behavior, public API/CLI, persisted data, specs, credentials, or release/deploy configuration (those are Feature or Bug). | Update a dev dependency or migrate a test fixture. |
| **Eval** | You want to measure a particular candidate configuration or revision. | Evaluate the multiple-reviewer configuration after its implementation lands. |

An Eval is a request to run an evaluation, not a generic bug or feature in `agent-evals`. Put a broken suite in **Bug**, a new suite capability in **Feature**, and a proposed evaluation run in **Eval**. All four types use the same CRUD recipes below.

## Board layout and priority

The current columns are **Backlog → Ready → Running → Review → Done**. They are Project Status values, separate from an issue being open or closed.

The board uses type swimlanes: horizontal groups within a view. **All work**, **Eval queue**, and **Active factory work** are view tabs on the same Project, not separate projects or types. The Eval queue currently filters `type:Eval owner:factory status:Ready`; a backlog Eval is visible in All work but excluded from that tab.

Relative priority is the organization `Priority` issue field (`Urgent`, `High`, `Medium`, `Low`), then created date newest-first. Factory admits in that order; it does not follow drag/`POSITION` order. Enable the same two-key sort on the human views so the board matches pickup. Routing sets Low when Priority is empty; GitHub has no required or native-default setting for the field. Changing a type or Status changes the group/column where the item appears; changing Priority or created time changes rank.

The factory runs four work kinds: Bug → fix, Feature → feature, Task → task (in `[fix] targets`), Eval → eval (in `agent-evals`). Each tick, the factory takes ownership of any open Bug, Feature, or Task card in Ready in a target repository whose author has write access, even when Owner is human, and admits it. Ready means “prioritized to start soon” only in repositories that are not factory targets. Keep undeveloped, human-owned, or blocked work in Backlog.

## Authenticate once

Prerequisites: `gh`, `jq`, repository access, and permission to edit the organization Project. A repository token's issue permissions do not imply organization Project access. The App needs **repository Issues: read/write** and **organization Projects: read/write**, installed on the involved repositories. Repository-level Projects permission is not a substitute.

Here, personal `gh` credentials can manage issues and discover native types, but lack Project scope. The Factory App installation token can manage this Project. Its organization-wide `issueTypes` query was denied with `Resource not accessible by integration`; the personal credential succeeded. That is an observed permission boundary of this installation, not a claim that all Apps lack issue-type access.

These two helpers make the credential choice explicit:

```sh
# Issue operations use your existing gh login, ignoring token overrides.
issue_gh() { env -u GH_TOKEN -u GITHUB_TOKEN gh "$@"; }

# Set FACTORY_PROJECT_TOKEN from your normal App-token provider.
# Keep tokens out of command history, notes, and traced shell output.
project_gh() { GH_TOKEN="$FACTORY_PROJECT_TOKEN" gh "$@"; }
```

On Paul's machine, the installed Factory provider can mint a token without displaying it. Run from the Factory checkout with its installed dependencies:

```sh
export FACTORY_APP_KEY="$HOME/.agent-factory/credentials/codagent-factory.pem"
FACTORY_PROJECT_TOKEN=$(
  /Users/paul/codagent/agent-factory/.venv/bin/python - <<'PY'
import os
from pathlib import Path
from agent_factory.github import AppCredentials, InstallationTokenProvider
credentials = AppCredentials(
    '4880516', '160216883', Path(os.environ['FACTORY_APP_KEY'])
)
print(InstallationTokenProvider(credentials)())
PY
)
```

The command substitution captures the token; do not echo it or use `set -x`. Installation tokens expire, so mint another when needed. A portable alternative is a suitably authorized personal token/login with `project` scope; in that case `project_gh() { issue_gh "$@"; }`. See [GitHub's Project authentication guidance](https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/using-the-api-to-manage-projects).

Set the working context. Replace repository and issue number for each item:

```sh
BOARD_ORG='Codagent-AI'
BOARD_NUMBER=1
PROJECT_ID='PVT_kwDOEARcIs4Bi5pP'
WORK_REPO='Codagent-AI/agent-runner'
ISSUE_NUMBER=69
```

## Discover IDs instead of guessing

An issue has three different identifiers: repository-local `number`, numeric database `id`, and GraphQL `node_id`. Its Project membership has a fourth identifier, the **Project item ID**. They are not interchangeable.

```sh
issue_gh api graphql -f query='
query($org:String!) {
  organization(login:$org) { issueTypes(first:25) { nodes { id name } } }
}' -f org="$BOARD_ORG"

project_gh api graphql -f query='
query($org:String!, $number:Int!) {
  organization(login:$org) {
    projectV2(number:$number) {
      id title
      fields(first:100) { nodes {
        ... on ProjectV2Field { id name dataType }
        ... on ProjectV2SingleSelectField { id name options { id name } }
      } }
    }
  }
}' -f org="$BOARD_ORG" -F number="$BOARD_NUMBER"
```

This board currently has these useful IDs; rerun discovery after recreating fields or moving to another Project:

| Field | Field ID | Options |
|---|---|---|
| Status | `PVTSSF_lADOEARcIs4Bi5pPzhhwAPg` | Backlog `d5afe107`; Ready `2262e12d`; Running `c39bcdf2`; Review `20c36f71`; Done `47d53512` |
| Owner | `PVTSSF_lADOEARcIs4Bi5pPzhhwAaE` | factory `ee3b16d6`; human `b6f46d6b` |
| Refs | `PVTF_lADOEARcIs4Bi5pPzhhwAbA` | Text, normally managed by Factory |
| Verdict | `PVTSSF_lADOEARcIs4Bi5pPzhhwAbE` | pending-human-review, failed, quota-deferred, infra-error; discover IDs above |
| Priority | `PVTSSF_lADOEARcIs4Bi5pPzhjHbIg` | Org issue field: Urgent, High, Medium, Low. Read from the issue, not Project option IDs. |

## Create or update an issue of any type

Before creating, search open **and closed** issues for the idea. Reuse an existing issue where it represents the same work:

```sh
issue_gh issue list --repo "$WORK_REPO" --state all \
  --search 'multiple reviewers' --limit 100
```

Create with the native type in the same request. Choose exactly `Task`, `Bug`, `Feature`, or `Eval`. Write the body to a file so Markdown, quotes, and multiline text stay intact:

```sh
ISSUE_TYPE='Feature'
ISSUE_TITLE='Short description of the requested behavior'
ISSUE_BODY_FILE='/absolute/path/to/issue-body.md'

ISSUE_NUMBER=$(
  issue_gh api --method POST "repos/$WORK_REPO/issues" \
    -f title="$ISSUE_TITLE" -f type="$ISSUE_TYPE" \
    -F "body=@$ISSUE_BODY_FILE" --jq '.number'
)
```

Do not blindly rerun creation after a timeout: check whether the first request succeeded. `clientMutationId` is not an issue-creation deduplication mechanism. The API may silently ignore a requested type if the caller lacks push access, so verify the resulting issue. [Issue create/update API](https://docs.github.com/en/rest/issues/issues).

```sh
# Read and verify the native type and identifiers.
issue_gh api "repos/$WORK_REPO/issues/$ISSUE_NUMBER" \
  --jq '{number,id,node_id,html_url,type:.type.name,state,title}'

# Change classification without creating another issue.
issue_gh api --method PATCH "repos/$WORK_REPO/issues/$ISSUE_NUMBER" \
  -f type='Bug' --jq '{number,type:.type.name}'

# Update only the supplied title/body; leave unrelated properties intact.
issue_gh api --method PATCH "repos/$WORK_REPO/issues/$ISSUE_NUMBER" \
  -f title='Updated title' -F "body=@$ISSUE_BODY_FILE" --jq '.html_url'
```

REST accepts the type name; GraphQL `updateIssue(input:{id:..., issueTypeId:...})` is the equivalent when working with node IDs. Do not try to set native type with `updateProjectV2ItemFieldValue`.

## Add an existing issue to the board

This paginated helper reads **all** Project items and their fields in manual order. It avoids assuming everything fits on one page:

```sh
board_items() {
  project_gh api graphql --paginate -f query='
  query($project:ID!, $endCursor:String) {
    node(id:$project) { ... on ProjectV2 {
      items(first:100, after:$endCursor,
            orderBy:{field:POSITION,direction:ASC}) {
        nodes {
          id isArchived
          content { ... on Issue { id number url title issueType { name } } }
          fieldValues(first:100) { nodes {
            ... on ProjectV2ItemFieldSingleSelectValue {
              name field { ... on ProjectV2SingleSelectField { name } }
            }
          } }
        }
        pageInfo { hasNextPage endCursor }
      }
    } }
  }' -f project="$PROJECT_ID" --jq '.data.node.items.nodes[]'
}

ISSUE_NODE_ID=$(issue_gh api "repos/$WORK_REPO/issues/$ISSUE_NUMBER" --jq '.node_id')
PROJECT_ITEM_ID=$(board_items | jq -r --arg issue "$ISSUE_NODE_ID" \
  'select(.content.id == $issue) | .id')

if [ -z "$PROJECT_ITEM_ID" ]; then
  PROJECT_ITEM_ID=$(project_gh api graphql -f query='
  mutation($project:ID!, $issue:ID!) {
    addProjectV2ItemById(input:{projectId:$project,contentId:$issue}) {
      item { id }
    }
  }' -f project="$PROJECT_ID" -f issue="$ISSUE_NODE_ID" \
    --jq '.data.addProjectV2ItemById.item.id')
fi
```

The membership check makes repeated imports safe and preserves existing field choices. After adding, set Status explicitly if this is a new manually managed card. An archived match is still a Project member; decide to unarchive it instead of creating another issue.

## Update Status, Owner, or another field

Use the **Project item ID**, not the issue ID. This helper is for single-select fields:

```sh
set_board_option() {
  project_gh api graphql -f query='
  mutation($project:ID!, $item:ID!, $field:ID!, $option:String!) {
    updateProjectV2ItemFieldValue(input:{
      projectId:$project,itemId:$item,fieldId:$field,
      value:{singleSelectOptionId:$option}
    }) { projectV2Item { id } }
  }' -f project="$PROJECT_ID" -f item="$PROJECT_ITEM_ID" \
    -f field="$1" -f option="$2" --jq '.data.updateProjectV2ItemFieldValue.projectV2Item.id'
}

STATUS_FIELD_ID='PVTSSF_lADOEARcIs4Bi5pPzhhwAPg'
OWNER_FIELD_ID='PVTSSF_lADOEARcIs4Bi5pPzhhwAaE'

# Explicitly keep a manually managed item out of Factory admission.
set_board_option "$OWNER_FIELD_ID" 'b6f46d6b'  # human
set_board_option "$STATUS_FIELD_ID" 'd5afe107' # Backlog

# Prioritize that human-owned item for soon.
set_board_option "$STATUS_FIELD_ID" '2262e12d' # Ready
```

To clear a custom field rather than choose an option:

```sh
FIELD_TO_CLEAR="$OWNER_FIELD_ID"
project_gh api graphql -f query='
mutation($project:ID!, $item:ID!, $field:ID!) {
  clearProjectV2ItemFieldValue(input:{projectId:$project,itemId:$item,fieldId:$field}) {
    projectV2Item { id }
  }
}' -f project="$PROJECT_ID" -f item="$PROJECT_ITEM_ID" -f field="$FIELD_TO_CLEAR"
```

A text field uses the same update mutation with `value:{text:$text}`. Refs and Verdict belong to Factory reporting during active work; do not overwrite them merely to prioritize a card. See the [Projects mutation reference](https://docs.github.com/en/graphql/reference/projects).

## Change Priority

Set the organization `Priority` issue field on the issue (`Urgent`, `High`, `Medium`, `Low`). Factory and sorted views rank that field, then newest `createdAt`. Drag-and-drop/`updateProjectV2ItemPosition` no longer determines pickup.

```sh
PRIORITY_FIELD_ID='IFSS_kgDOAmcJrg'
# Urgent IFSSO_kgDOBDQ5Cg; High IFSSO_kgDOBDQ5Cw; Medium IFSSO_kgDOBDQ5DA; Low IFSSO_kgDOBDQ5DQ
PRIORITY_OPTION_ID='IFSSO_kgDOBDQ5Cw'  # High

issue_gh api graphql -f query='
mutation($issue:ID!, $field:ID!, $option:ID!) {
  setIssueFieldValue(input:{
    issueId:$issue
    issueFields:[{fieldId:$field, singleSelectOptionId:$option}]
  }) {
    issueFieldValues { ... on IssueFieldSingleSelectValue { name } }
  }
}' -f issue="$ISSUE_NODE_ID" -f field="$PRIORITY_FIELD_ID" -f option="$PRIORITY_OPTION_ID"
```

Read the issue field back after writing. Do not clear Status, Owner, Refs, or Verdict merely to change rank.

## Mark a real dependency and show “blocked”

Use GitHub's native blocked-by relationship for the dependency. A `blocked` label is a visible flag; it is separate metadata and must be removed separately when appropriate. Neither a label nor the relationship currently acts as Factory's admission gate: **Backlog and Owner human/unset are what keep a candidate out of admission**.

The existing example is [agent-evals #10](https://github.com/Codagent-AI/agent-evals/issues/10), an Eval candidate blocked by [agent-runner #69](https://github.com/Codagent-AI/agent-runner/issues/69). It should remain a backlog candidate until the capability exists and its runnable inputs are ready.

```sh
# Set these to the blocked issue and its blocker.
WORK_REPO='Codagent-AI/agent-evals'
ISSUE_NUMBER=10
BLOCKER_REPO='Codagent-AI/agent-runner'
BLOCKER_NUMBER=69
BLOCKER_DATABASE_ID=$(issue_gh api "repos/$BLOCKER_REPO/issues/$BLOCKER_NUMBER" --jq '.id')

# Read current relationships before adding to avoid duplicate writes.
issue_gh api --paginate "repos/$WORK_REPO/issues/$ISSUE_NUMBER/dependencies/blocked_by" \
  --jq '.[] | {id,html_url,state}'

# Run only if the blocker is not already present.
issue_gh api --method POST "repos/$WORK_REPO/issues/$ISSUE_NUMBER/dependencies/blocked_by" \
  -F issue_id="$BLOCKER_DATABASE_ID"

# Create the visual label only if absent; then add without replacing other labels.
issue_gh api "repos/$WORK_REPO/labels/blocked"
# If that read returns 404:
issue_gh label create blocked --repo "$WORK_REPO" \
  --color B60205 --description 'Waiting on a dependency'
issue_gh api --method POST "repos/$WORK_REPO/issues/$ISSUE_NUMBER/labels" \
  -f 'labels[]=blocked' --jq '.[].name'
```

The dependency endpoint needs the blocker's numeric database `id`, **not** issue number 69 and not its GraphQL node ID. Cross-repository relationships require access to the relevant issues. [Issue dependencies API](https://docs.github.com/en/rest/issues/issue-dependencies).

If the relationship was mistaken, remove it explicitly. If the prerequisite was completed, retaining the relationship records history; check the blocker's state and remove the visual flag only after all blockers are resolved. Removing the flag does not automatically mean the request is executable.

```sh
# Remove an incorrect/no-longer-wanted relationship:
issue_gh api --method DELETE \
  "repos/$WORK_REPO/issues/$ISSUE_NUMBER/dependencies/blocked_by/$BLOCKER_DATABASE_ID"

# Remove the visual flag only when the issue is no longer blocked:
issue_gh api --method DELETE "repos/$WORK_REPO/issues/$ISSUE_NUMBER/labels/blocked"
```

After changing WORK_REPO/ISSUE_NUMBER, rerun the item lookup before changing its Project fields; an old PROJECT_ITEM_ID still points to the previous card.

## Remove a card without deleting the issue

First resolve and verify PROJECT_ITEM_ID for the intended issue, then remove its Project membership:

```sh
project_gh api graphql -f query='
mutation($project:ID!, $item:ID!) {
  deleteProjectV2Item(input:{projectId:$project,itemId:$item}) { deletedItemId }
}' -f project="$PROJECT_ID" -f item="$PROJECT_ITEM_ID"

# The issue remains present, with its existing open/closed state.
issue_gh api "repos/$WORK_REPO/issues/$ISSUE_NUMBER" --jq '{html_url,state,title}'
# No matching Project item should now be returned.
board_items | jq --arg issue "$ISSUE_NODE_ID" 'select(.content.id == $issue)'
```

Do not use issue deletion, issue closure, or Done to mean “remove this parked idea from my board.” Removing membership also discards that card's Project field values; record them first if you may want them back.

**Current routing limitation:** removal is not a permanent exclusion rule. For configured source repositories, a later subscribed issue/PR event can run routing and re-add the item. Routing currently has no “keep off board” marker. Do not promise that a removed parked/non-coding issue will stay absent forever, and do not bulk re-add intentionally removed issues during inventory refreshes.

## What Factory routing does today

Routing callers are deployed in **agent-evals, agent-runner, agent-skills, agent-validator, agent-plugin** (`factory-routing.yml`) and **agent-factory** (`route-work.yml`); and-scene is a factory target without routing, so add its issues to the board manually. Callers pin the shared Factory workflow by commit SHA. They subscribe to issue/PR opened, reopened, edited, closed, labeled, and unlabeled events. Other repositories can still be managed manually on this Project; automatic routing requires a configured source and deployed caller.

| Situation | Current behavior |
|---|---|
| Ordinary source issue/PR event | Add missing Project membership and initialize Backlog. Native classification remains on the issue; router does not infer Bug/Feature/Task from prose. |
| Bug, Feature, or Task | Not routed to the factory, whatever the author's role: it enters Backlog like any ordinary issue, and creating it starts no factory work. Moving the card to Ready (or `factory-assign`) hands it over. |
| `agent-evals` issue with `eval-request` label, authored by someone with write/maintain/admin permission | Initialize Owner=factory and Status=Ready. Native Eval type alone is not this routing trigger. |
| Unknown/insufficient author permission | Backlog without new factory ownership. |
| Routing retry | A one-time receipt comment preserves later human field changes. This is initialization, not continuous forced reassignment. |
| Tracked issue/PR closes | Move the Project card to Done. |

For a **candidate Eval**, create native Eval with a descriptive body, no `eval-request` label, and Backlog + human/unset Owner. This avoids a race where a newly created labeled issue is routed to Ready before you move it back.

For an **executable Eval**, use the delivered `agent-evals` eval-request template and its supported fenced eval inputs. Factory admission requires an open native Eval in the configured eval-source repository (`agent-evals`), the `eval-request` label, Owner=factory, Status=Ready, an authorized author, and a valid parsed request; execution also depends on the admission window, pause state, capacity, readiness, and quota holds. Current code does not treat `blocked` as an admission veto.

Do not use a speculative eval block or add `eval-request` merely to make an idea visible in the Eval swimlane. Conversely, do not assume removing the label cancels an already accepted execution: routing and the execution controller have distinct responsibilities. Use the supported Factory lifecycle controls for active work.

## Verification and ongoing use

After each mutation, read back the issue and relevant Project item. On errors, stop and inspect the response; do not interpret permission failures or partial GraphQL errors as successful empty results. Prefer JSON variables/typed `-F` inputs and body files over interpolating issue content into shell code or GraphQL text.

This guide's discovery queries, schema fields, current Project values, caller workflow pins, and the #10 → #69 dependency were checked read-only. Mutation examples were checked against the API schema/documentation and existing implementation, but were deliberately not executed against live issues while authoring this note.

Implementation references: `agent-factory/config/codagent.toml`, `src/agent_factory/routing.py`, `src/agent_factory/controller.py`, `src/agent_factory/github.py`, and `docs/github-setup.md`. Repository code/configuration and current GitHub state take precedence over this dated operational guide.

