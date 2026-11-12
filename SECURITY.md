# Security policy

## Supported versions

Only the latest release receives fixes. There is no floating major tag, so a fix reaches a workflow when that workflow's pin moves to the release that carries it.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting: [report a vulnerability](https://github.com/michen00/pr-catchup/security/advisories/new). Please do not open a public issue for a security report.

This is a personal project maintained on a best-effort basis. The maintainer aims to acknowledge a report within a week and ships fixes as patch releases.

## Guidance for workflows that use the action

The action runs with a token that can push merge commits to pull request branches and approve held workflow runs, so treat it as part of your supply chain:

- Pin it to a full commit SHA with the version in a comment, as [RELEASING.md](RELEASING.md) shows. Dependabot's `github-actions` ecosystem updates both.
- Grant the calling job only `actions: write`, `contents: write`, and `pull-requests: read`.
- Run it on `push` to the base branch and on `workflow_dispatch`, and hold a dispatch to the default branch with a job `if:`. A dispatched run loads the workflow file from the dispatched ref, so the condition stops a dispatch of an unchanged copy on another branch, such as a branch that only bumps the action pin. It does not stop someone with write access who edits that branch's copy, so it is not a security boundary.
- Store the App private key as an Actions secret, not a Dependabot secret: the catch-up never runs on a Dependabot pull request, and a run that Dependabot triggers executes the pull request's copy of the workflow.
- A repository or organization secret reaches a workflow on any branch, and the key can mint a token for every repository the App is installed on. When the App is installed on more than one repository, or when some people with write access should not hold the key, keep it in an environment that only the default branch can deploy to, as the [App setup](README.md#recommended-workflow-with-a-github-app-token) describes.
- An App token passed as `token` needs only Contents write.

The action approves held runs only at merge commits it created earlier in the same run, and it has no input that names a commit to approve.
