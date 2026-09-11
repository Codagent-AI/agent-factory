# GitHub App setup

## Installed and verified

The organization-owned **Codagent Factory** App is installed on **all current and future Codagent-AI repositories**, as Paul selected during setup. Installation scope does not change the configured routing repository set.

| Setting | Value |
|---|---|
| App slug | `codagent-factory` |
| App ID | `4880516` |
| Client ID | `Iv23liKlgOKEsLE5Bqpo` |
| Installation ID | `160216883` |
| Local private key | `/Users/paul/.agent-factory/credentials/codagent-factory.pem` |
| Local credential metadata | `/Users/paul/.agent-factory/credentials/github-app.json` |

The credentials directory has mode 0700 and its key and metadata files have mode 0600. The downloaded PEM also has mode 0600. No private keys or tokens are stored in this change directory. Registration and installation were completed manually; browser automation was stopped at Paul's request.

Installed permissions are organization Projects read/write; repository Issues, Pull requests, and repository Projects read/write; Contents and Metadata read-only. Paul chose Pull requests write for future use and retained the unnecessary repository Projects permission. Iteration 1 suite jobs continue to use their separately configured personal token to push branches and create candidate PRs. Webhooks and user OAuth are not needed by the design.

Using the private key, setup successfully authenticated as the App, minted an installation token, listed all 11 accessible organization repositories, read agent-evals issues, checked pacaplan's effective repository permission (admin), and queried organization Projects. **The initial Project query succeeded and returned no Projects.** The Codagent board was subsequently created at https://github.com/orgs/Codagent-AI/projects/1; see `project-board.md` for current setup and verification. Evidence is in `github-app-verification.json`; it contains identifiers and results, never credentials.

## Actions credentials

The planned routing caller repositories are agent-factory, agent-evals, agent-runner, agent-skills, agent-validator, and agent-plugin. Each receives:

- Secret `FACTORY_APP_PRIVATE_KEY`.
- Variables `FACTORY_APP_ID`, `FACTORY_APP_CLIENT_ID`, and `FACTORY_APP_INSTALLATION_ID`.

The completed configuration and read-back verification are recorded in `github-actions-credentials.json`. Repository-level secrets are used because the current CLI token lacks organization-secret management scopes. Future caller repositories will need the same credential configuration. Reusable workflows must receive the secret from their caller; App installation alone does not provide it.

## Remaining acceptance prerequisites

- Confirm the provisioned Project and finish the remaining setup described in `project-board.md`; templates and routing workflows remain implementation deliverables.
- Verify Project mutations and issue reporting when implementation provides the acceptance flows. Current evidence verifies authentication/read access and granted write permissions, not an end-to-end workflow run.
- Implement author permission checks in routing and admission: effective repository write, maintain, or admin is required for automatic execution. Outside contributors enter Backlog without factory assignment. The App installation itself does not enforce this policy.
- Complete local service and suite readiness, including the separately delivered automated-score failure contract, before live evaluation acceptance.
