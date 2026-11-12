# Pull request catch-up

`michen00/pr-catchup` is a composite GitHub Action that merges the base branch into every open pull request that targets it, comes from a branch in the same repository, and does not conflict, so those pull requests stay current without anyone pressing Update branch. You run it from a workflow triggered by each push to the base. Each merge goes through the GitHub API, so GitHub creates and signs the merge commit, with a message you choose. When a merge is made with the workflow's default token, the action approves the runs that commit causes, which GitHub otherwise holds for approval, and a run it cannot clear fails the workflow.

## Why use it

### The problem

Many repositories require a pull request to be up to date with its base before it merges ("Require branches to be up to date before merging" in branch protection and in rulesets). Every merge to the base then leaves every other open pull request behind, and each one needs an update and a fresh round of checks before it can merge.

Automating that update with the workflow's `GITHUB_TOKEN` has a catch. Since 2026-06-11, the `pull_request` runs that a `github-actions[bot]` commit causes are created but held at `action_required` until a user with write access approves them. The [GitHub changelog of 2026-06-11](https://github.blog/changelog/2026-06-11-bot-created-pull-requests-can-run-workflows-if-approved/) announces this for pull requests the bot creates, and [GitHub's `GITHUB_TOKEN` documentation](https://docs.github.com/en/actions/concepts/security/github_token#when-github_token-triggers-workflow-runs) gives the same approval-required state to the runs that follow when a workflow using `GITHUB_TOKEN` updates a pull request. The pull request looks current while its required checks never report at the new head, so it cannot merge. No check fails, so nobody is told. The hold applies to the bot actor, not to forks, so no repository setting lifts it.

### What this action does differently

- **A signed merge commit with a message you choose.** The action calls [`POST /repos/{owner}/{repo}/merges`](https://docs.github.com/en/rest/branches/branches#merge-a-branch), so GitHub creates and signs the merge commit, and a rule that requires signed commits passes without a signing key on the runner. The [update-branch endpoint](https://docs.github.com/en/rest/pulls/pulls#update-a-pull-request-branch), and the Update branch button when it updates with a merge commit, write the subject `Merge branch '<base>' into <head>`, which a commit-message rule (a ruleset `commit_message_pattern` rule, or commitlint) rejects unless its pattern makes an exception for it. Here the message is a template, and the default renders as `chore: merge main into #42`, a Conventional Commit.
- **It avoids the approval hold, or approves its own held runs.** Pass a GitHub App installation token, and the merges are made as the App, so GitHub holds none of the runs they cause. Without one, the action approves the held runs at its own merge commits (see [Security](#security)) and fails, naming each run and the command that approves it, if any stays held after the approval pass.
- **Every outcome is classified.** An empty response means the branch is already current. A conflict is a 409 response on a pull request whose `mergeable` field GitHub settles as `CONFLICTING`; it is reported as a notice and left to the author. Anything else is a refusal: a permission error, a rate limit, a server error, or a 409 on a pull request GitHub reports as mergeable, which is how GitHub refuses a merge that writes a workflow file to a token without the workflows permission (an App without it, or `github-token`, which cannot have it). A refusal does not stop the other merges, and it fails the run at the end with the commands that recover it. A 409 alone is never read as a conflict.
- **The action never handles the App private key.** It takes an installation token minted in your job, not the key. The token expires after one hour, and `actions/create-github-app-token` revokes it when the job ends. The recommended setup keeps the key in an environment that only the default branch can deploy to, so a workflow pushed on another branch cannot read it.
- **No Docker or Node runtime.** It is a composite action whose steps run bash scripts from `scripts/`, which call the GitHub CLI preinstalled on GitHub-hosted runners. Nothing is pulled or installed when it starts, and the scripts it runs are in this repository to read before you pin it.

## Compared with other options

Verified on 2026-11-11 by reading each project's source code or documentation; later versions may differ.

| Option | How it updates a pull request | Commit message | Held runs and failures |
| --- | --- | --- | --- |
| This action | Composite action (bash and `gh`). Merges with `POST /repos/{owner}/{repo}/merges`, so GitHub creates and signs the merge commit. Updates every eligible pull request on each run. | A template with `{base}`, `{number}`, and `{branch}`; the default renders as `chore: merge main into #42`. | With an App token, none are held. With `github-token`, it approves the runs its own merge commits caused and fails if any stays held. A refused merge fails the run. |
| [chinthakagodawita/autoupdate](https://github.com/chinthakagodawita/autoupdate) | Docker action; latest release v1.7.0 on 2023-03-26. Merges with `POST /repos/{owner}/{repo}/merges`. | One fixed string from `MERGE_MSG`, the same for every pull request. | Its README passes `GITHUB_TOKEN`; nothing in its source approves held runs. |
| [adRise/update-pr-branch](https://github.com/adRise/update-pr-branch) | JavaScript action. Calls the update-branch endpoint (octokit `pulls.updateBranch`) for one pull request per run: by default the newest with auto-merge on, two approvals, and passing checks. | Set by GitHub: `Merge branch '<base>' into <head>`. | Takes a personal access token or an App token; its README says `GITHUB_TOKEN` cannot be used. |
| [wechuli/pull-request-updater](https://github.com/wechuli/pull-request-updater) | JavaScript action. Calls `PUT /repos/{owner}/{repo}/pulls/{number}/update-branch` and, by default, comments on each pull request it updates. | Set by GitHub: `Merge branch '<base>' into <head>`. | Defaults to `github.token`; nothing in its source approves held runs. |
| [chrismbryant/pr-auto-update](https://github.com/chrismbryant/pr-auto-update) | A workflow to copy, not an action. For pull requests with auto-merge on, runs `git merge` on the runner and pushes with `git push`, using an App token. | `Auto-update: merge <base> into <branch>`. Inferred from the workflow, which sets up no signing: the commit is unsigned. | A failed merge or push is printed, and the job still succeeds. |
| [Mergify update action](https://docs.mergify.com/workflow/actions/update/) | Part of a hosted GitHub App: "This is done by merging the base branch into the pull request branch." Its only parameter is `bot_account`. | No commit message option is documented. | Updates as Mergify itself, or as the user named in `bot_account`. |

GitHub's own Update branch button, and `gh pr update-branch`, update one pull request at a time as the person who asks: a merge update writes the fixed subject above, and a rebase update rewrites the branch.

[GitHub merge queue](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue) solves a different problem. Instead of updating branches, it tests each pull request merged with the latest base and the pull requests ahead of it, and every required workflow must also trigger on [`merge_group`](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#merge_group). When the goal is only that the base stays green, and your repository can use merge queue and run its required checks on `merge_group`, merge queue is the better choice: it makes "Require branches to be up to date before merging" unnecessary, and this action with it. Use this action when each pull request's own branch should carry the base, so that its checks and its reviewers see current code, or when merge queue is not an option.

## Usage

The action needs a runner with bash and the GitHub CLI (`gh`); GitHub-hosted runners have both. It needs no checkout.

Pin the action to the full commit SHA of a release, which the release notes give, with the version in a comment; before the first release, pin the SHA of a commit you have reviewed. The examples write this as `michen00/pr-catchup@<full-commit-sha> # vX.Y.Z`.

### Minimal workflow, without an App

Save as `.github/workflows/pr-catchup.yml`. If your default branch is not `main`, change the branch under `push`.

```yaml
name: Pull request catch-up

on:
  push:
    branches: [main]
  workflow_dispatch:

permissions: {}

concurrency:
  group: pr-catchup
  cancel-in-progress: false

jobs:
  catch-up:
    # A dispatch runs the workflow file from the ref it was dispatched from.
    if: >-
      github.event_name != 'workflow_dispatch'
      || github.ref == format('refs/heads/{0}', github.event.repository.default_branch)
    runs-on: ubuntu-latest
    timeout-minutes: 15
    permissions:
      actions: write # approve the runs its merge commits cause
      contents: write # create the merge commits
      pull-requests: read # list pull requests and read mergeable
    steps:
      - uses: michen00/pr-catchup@<full-commit-sha> # vX.Y.Z
```

### Recommended workflow, with a GitHub App token

Merges made as an App cause no held runs, so nothing waits for approval. To set up the App:

1. Create a GitHub App under your account or organization: Settings, then Developer settings, then GitHub Apps, then New GitHub App. Under Repository permissions, set Contents to Read and write and leave the rest at No access. Under Webhook, clear Active.
2. Install the App on the repositories that run the catch-up.
3. On the App's settings page, copy the Client ID and generate a private key.
4. In each repository, store both for Actions:

   ```sh
   gh variable set APP_CLIENT_ID --repo OWNER/REPO --body '<client id>'
   gh secret set APP_PRIVATE_KEY --repo OWNER/REPO < path/to/private-key.pem
   ```

   The catch-up runs on pushes to the base and on dispatch, never on a Dependabot pull request, so the key does not need to be in the Dependabot secret store. A run that Dependabot triggers reads only that store, and it runs the pull request's own copy of the workflow.

   A repository secret reaches a workflow pushed on any branch, so anyone with write access can read the key. That grants nothing new when the App is installed only on this repository with Contents write, which they can already write to. When the App is installed on more than one repository, or when some people with write access should not hold the key, keep both in an environment that only the default branch can deploy to, and add `environment: pr-catchup` to the job: an environment secret reaches only jobs that name the environment and run from a branch it admits. With `gh`, for a default branch named `main`:

   ```sh
   gh api --method PUT repos/OWNER/REPO/environments/pr-catchup \
     -F 'deployment_branch_policy[protected_branches]=false' \
     -F 'deployment_branch_policy[custom_branch_policies]=true'
   gh api --method POST repos/OWNER/REPO/environments/pr-catchup/deployment-branch-policies \
     -f name=main -f type=branch
   gh variable set APP_CLIENT_ID --env pr-catchup --repo OWNER/REPO --body '<client id>'
   gh secret set APP_PRIVATE_KEY --env pr-catchup --repo OWNER/REPO < path/to/private-key.pem
   ```

   On GitHub Free, environment secrets and deployment branch rules are available only in public repositories ([GitHub documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)).

Then use this workflow, saved as `.github/workflows/pr-catchup.yml`:

```yaml
name: Pull request catch-up

on:
  push:
    branches: [main]
  workflow_dispatch:

permissions: {}

concurrency:
  group: pr-catchup
  cancel-in-progress: false

jobs:
  catch-up:
    # A dispatch runs the workflow file from the ref it was dispatched from.
    if: >-
      github.event_name != 'workflow_dispatch'
      || github.ref == format('refs/heads/{0}', github.event.repository.default_branch)
    runs-on: ubuntu-latest
    timeout-minutes: 15
    permissions:
      actions: write # approve runs when a merge falls back to github-token
      contents: write # create the merge commits when it falls back
      pull-requests: read # list pull requests and read mergeable
    steps:
      # Without APP_CLIENT_ID, or when minting fails, the merges use github-token.
      - name: Mint an App token
        id: app-token
        if: vars.APP_CLIENT_ID != ''
        continue-on-error: true
        uses: actions/create-github-app-token@<full-commit-sha> # vX.Y.Z
        with:
          client-id: ${{ vars.APP_CLIENT_ID }}
          private-key: ${{ secrets.APP_PRIVATE_KEY }}
          permission-contents: write

      - uses: michen00/pr-catchup@<full-commit-sha> # vX.Y.Z
        with:
          token: ${{ steps.app-token.outputs.token }}
```

Pin `actions/create-github-app-token` the same way, to a release you have reviewed: v3.1.0 or later, which has the `client-id` input. This repository's own [catch-up workflow](.github/workflows/pr-catchup.yml) shows a current pin, which Dependabot keeps up to date.

### Why each setting is there

- **`push` to the base.** Each push to the base can leave open pull requests behind. If you set the `base` input to a branch other than the default branch, list that branch here instead.
- **`workflow_dispatch`.** Runs the catch-up by hand: after a push that started no workflow (see [Limitations](#limitations)), or after you fix the cause of a refusal.
- **`permissions`.** `actions: write` lets `github-token` approve held runs ([`POST /repos/{owner}/{repo}/actions/runs/{run_id}/approve`](https://docs.github.com/en/rest/actions/workflow-runs#approve-a-workflow-run-for-a-fork-pull-request)). `contents: write` lets it create merge commits. `pull-requests: read` lets it list pull requests and read `mergeable`. The App workflow keeps all three, because a merge refused to the App with HTTP 401, 403, or 404 is retried with `github-token`.
- **`concurrency` with `cancel-in-progress: false`.** A canceled run could stop after a merge and before it approves the runs that merge caused, leaving them held with no failure reported. With one group and no cancellation, a push during a run queues one more run; GitHub keeps only the newest pending run, which is enough because each run reads the current base.
- **`timeout-minutes: 15`.** Bounds a run that hangs. The approval step polls for up to about a minute for each pull request merged with `github-token`, so raise the limit if many pull requests are updated at once without an App.
- **The job `if:`.** A dispatched run uses the workflow file from the ref it was dispatched from. The condition admits a dispatch only from the default branch, so a manual run uses the same workflow file as a push. It is not a security boundary: someone with write access can edit a branch's copy of the workflow and drop it.
- **`if:` and `continue-on-error: true` on the token step.** The workflow works before the App is set up, and a failed mint falls back to `github-token` instead of failing the job.

## How a run works

The action runs three steps and passes state between them in `$RUNNER_TEMP/pr-catchup`.

1. **Merge the base into pull requests behind it** (`scripts/catch-up.sh`). It lists up to 1000 open pull requests whose base is `base` and keeps those that come from this repository, that GitHub does not already report as `CONFLICTING`, and that carry none of `exclude-labels`. For each, it calls the merges endpoint with `token` if set, and otherwise with `github-token`. It prints `updated #N to <sha>`, `no update needed for #N`, or a conflict notice, records any refusal, and sets the `updated` output.
2. **Approve the runs those merges caused** (`scripts/approve-caused-runs.sh`). For each merge made with `github-token`, it waits up to 10 polls, 3 seconds apart, for a `pull_request` run at the merge commit. It then sweeps up to 10 times, 3 seconds apart, approving each held run that qualifies, and stops once the count of held runs at that commit reads zero on two consecutive polls. When every merge was made as the App, there is nothing to approve.
3. **Fail on refused merges and on runs still held, unverified, or missing** (`scripts/report.sh`). It prints an error and a recovery command for each problem, described in [When a run fails](#when-a-run-fails), and exits 1 if there is any.

## Inputs

All inputs are optional.

| Input | Default | Description |
| --- | --- | --- |
| `token` | `''` | A GitHub App installation token with Contents write, minted in the calling job, for example with `actions/create-github-app-token`. When it is set, the merges are made as the App, and GitHub holds none of the runs they cause. A merge refused to the App with HTTP 401, 403, or 404 is retried with `github-token`; other refusals, including a 409, are not retried. |
| `github-token` | `${{ github.token }}` | Lists the pull requests, reads whether they conflict, makes the merges when `token` is empty or refused, and approves the runs those merges cause. It needs `actions: write`, `contents: write`, and `pull-requests: read`. |
| `base` | `${{ github.event.repository.default_branch }}` | The branch merged into the open pull requests that target it. Only pull requests whose base is this branch are listed. An empty value fails the run. |
| `commit-message` | `chore: merge {base} into #{number}` | The merge commit message. `{base}`, `{number}`, and `{branch}` are replaced by the base branch, the pull request number, and the pull request's head branch, in one pass; any other `{...}` text is kept as written. With the defaults on `main` it renders as `chore: merge main into #42`. An empty value fails the run. A skip instruction such as `[skip ci]` in the message stops GitHub from starting a `pull_request` run at the merge commit, so leave it out. |
| `exclude-labels` | `''` | Comma- or newline-separated label names. A pull request carrying any of them is skipped. Names are trimmed, blank entries are ignored, and names are compared case-insensitively (ASCII letters only). |

## Outputs

| Output | Description |
| --- | --- |
| `updated` | Space-separated numbers of the pull requests this run merged the base into, whether the App or `github-token` made the merge. It is empty when nothing was merged. |

## When a run fails

The last step reports each problem as an error annotation followed by the commands that recover it, with this repository, the branch, the commit, and the rendered message filled in. The examples below use `OWNER/REPO`, pull request #42, and a workflow file named `pr-catchup.yml`; the log names the workflow file that called the action.

### A merge was refused

```text
merging main into PR #42 was refused, so it stays behind main: <GitHub's message>
```

The merges endpoint refused the merge for a reason that is neither "already current" (an empty response) nor a conflict (a 409 on a pull request whose `mergeable` GitHub settles as `CONFLICTING`). Examples are a 403 or 404 for a token without access, a rate limit, a server error, a repository rule that rejects the commit, and a 409 on a pull request GitHub reports as mergeable, which is how GitHub refuses a merge that writes a workflow file to a token without the workflows permission (an App without it, or `github-token`, which cannot have it). In the last case, the message ends with `(GitHub reports it as MERGEABLE)`. A 409 on a pull request whose `mergeable` GitHub has not settled after five reads, 2 seconds apart, is a refusal too, and its message ends with `(GitHub reports it as UNKNOWN)`; it may be a conflict, which a later run skips once GitHub reports it.

The log prints:

```sh
gh api --method POST repos/OWNER/REPO/merges -f base=feature/login -f head=main \
  -f commit_message='chore: merge main into #42'
gh workflow run pr-catchup.yml --repo OWNER/REPO
```

The first command makes the same merge with your own credentials. Use it when the cause is something only the workflow's tokens lack; for a workflow-file refusal, your credentials need the workflow scope (`gh auth refresh --scopes workflow`). Runs caused by a merge you make are not held. The second command runs the catch-up again for every pull request once the cause clears.

### A run is still held

```text
run 123456789 for PR #42 is still awaiting approval, so its required checks cannot report
```

After the approval pass, a run at the merge commit still waits at `action_required`. The approval step's log says why:

- `could not approve run 123456789; github-token may not be permitted to approve runs here`: `github-token` was refused when it tried to approve the run.
- `out of scope, left for a human: run 123456789 (...)`, with the run's repository, event, actor, and state: the run is outside what the action approves.
- `approved run 123456789 for #42`, or no line about the run: the run was approved, or first appeared, too late in the pass to read as cleared. Check its state before approving it again.

Approve it from the pull request's Checks tab, or run:

```sh
gh api --method POST repos/OWNER/REPO/actions/runs/123456789/approve
```

If every catch-up fails this way, pass an App token as `token`: merges made as the App are not attributed to `github-actions[bot]`, so GitHub holds none of their runs.

### Whether a run cleared the hold is unknown

```text
whether PR #42 cleared the approval gate at <sha> is unknown: an Actions lookup failed during the approval pass
```

A lookup of the runs at the merge commit failed or returned something other than a count, or the count of held runs stayed above zero while the list of held runs came back empty. The action cannot tell held from clear, so it reports neither. List the runs at the commit and approve any whose conclusion is `action_required`:

```sh
gh api 'repos/OWNER/REPO/actions/runs?head_sha=<sha>'
```

### No `pull_request` run appeared

```text
no pull_request run ever appeared for PR #42 at <sha>, so its required checks cannot report
```

The action polled 10 times, 3 seconds apart, and found no `pull_request` run at the merge commit. Either GitHub was slow to queue the runs, or nothing was going to start one: no workflow runs on `pull_request` for this base, the workflows' path filters exclude the pull request's files, or the commit message carries a skip instruction such as `[skip ci]`. List what exists at the commit, approve anything listed as `action_required`, or push to the pull request's branch to start fresh runs:

```sh
gh api 'repos/OWNER/REPO/actions/runs?head_sha=<sha>'
```

### The run fails before any merge

The first step stops, and nothing is merged, when `base` is empty (`base is empty; its default is the event's repository default branch`), when `commit-message` is empty (`commit-message is empty`), or when listing the pull requests fails, for example because `github-token` lacks `pull-requests: read`; the log then shows the error from `gh`.

### Messages that do not fail the run

- `#42 conflicts with main, so it stays behind` is a notice: only the author can resolve a conflict. Pull requests that GitHub already reports as conflicting when they are listed are skipped without a message.
- `App merge refused for #42; retrying with github-token` is a warning: the App could not make the merge, and `github-token` made the attempt instead, so that merge's runs go through the approval step.

## Security

### What `github-token` approves

The approval step approves a run only when all of these hold:

- its head commit is a merge commit that this run created with `github-token`;
- its head repository is this repository;
- its event is `pull_request`;
- its actor is `github-actions[bot]`;
- it is still held at `action_required`.

An empty value never matches, so a failed lookup cannot widen the match. The commits come only from the merges the first step made: it empties its state files in `$RUNNER_TEMP/pr-catchup` before it writes them, and the action has no input that names a commit to approve.

The scope is safe because such a commit merges the base into a branch that is already in this repository, and only an account or App with write access can push a branch there. Approving its runs lets the same workflows run that would have run if a person had pushed that merge. Pull requests from forks are never merged into and never approved.

### Inputs never reach the shell as code

The inputs reach the scripts only through environment variables, never as `${{ }}` expressions inside a `run:` body, and branch names and labels from the API are handled only as quoted data, so a crafted branch name cannot inject shell commands. The recovery commands quote the branch and the base with `printf %q` and single-quote the message.

### The App key stays out of the action

The action takes an installation token, not the App's private key. The token, scoped by `permission-contents: write`, expires after one hour and is revoked when the job ends. The key can mint a token for every repository the App is installed on, and a repository or organization secret reaches a workflow pushed on any branch; neither the job `if:` nor anything else in the workflow file stops a branch's own copy of the workflow from reading it. When the key grants more than write access to this one repository, keep it in an environment, as step 4 of the [App setup](#recommended-workflow-with-a-github-app-token) describes.

### Pin a commit SHA

A tag can be moved to different code; a full commit SHA cannot. This action runs with a token that can write contents and approve runs, and its steps run the scripts from the same commit as `action.yml`, so pinning the SHA fixes all the code the action ships; `gh` and bash come from the runner image. See GitHub's guidance on [using third-party actions](https://docs.github.com/en/actions/reference/security/secure-use#using-third-party-actions). There is no floating major tag, and a published tag must not move, as [RELEASING.md](RELEASING.md) explains; Dependabot's `github-actions` ecosystem updates a SHA pin and its version comment together.

To report a vulnerability, follow [SECURITY.md](SECURITY.md).

## Limitations

- **Same-repository pull requests only.** Pull requests from forks are skipped.
- **Only pull requests that target `base`.** A pull request stacked on another branch is not updated. Draft pull requests are included; give them a label in `exclude-labels` to skip them.
- **Conflicts are left to the author.** A conflicting pull request stays behind, and the run does not fail for it, unless GitHub has not settled `mergeable` within about 10 seconds of the 409; the run then reports it as a refusal.
- **Pushes made with `GITHUB_TOKEN` start no catch-up.** GitHub starts no `push` workflow for a push that `GITHUB_TOKEN` makes to the base ([GitHub documentation](https://docs.github.com/en/actions/concepts/security/github_token#when-github_token-triggers-workflow-runs)), so a Dependabot auto-merge that merges with `GITHUB_TOKEN` leaves pull requests behind until the next push or a manual `gh workflow run pr-catchup.yml`. Making that merge with an App token closes the gap.
- **Workflow files.** When the pull request and the base both changed the same workflow file, the merge writes new content to it, and GitHub refuses that merge with HTTP 409 to a token without the workflows permission. The action reports it as a refusal, not a conflict, and does not retry it with `github-token`, which cannot be granted a workflows permission. Giving the App the Workflows permission and adding `permission-workflows: write` to the token step removes the refusal, at the cost of a token that can change workflows. Without an App, make that merge by hand, as [A merge was refused](#a-merge-was-refused) shows.
- **Dependabot stops rebasing a pull request once another commit is pushed to it.** After a catch-up merge, a later conflict leaves a Dependabot pull request behind until someone comments `@dependabot recreate`. Either skip Dependabot's pull requests with `exclude-labels: dependencies`, so that only Dependabot updates them (it rebases to resolve a conflict, not to catch up), or add a line `[dependabot skip]` to `commit-message`, which lets Dependabot force push over the catch-up merge ([GitHub documentation](https://docs.github.com/en/code-security/dependabot/working-with-dependabot/managing-pull-requests-for-dependency-updates)). This repository's own [catch-up workflow](.github/workflows/pr-catchup.yml) does the second.
- **Without an App, each updated pull request needs a `pull_request` run.** A merge made with `github-token` that starts no `pull_request` run fails the catch-up as [No `pull_request` run appeared](#no-pull_request-run-appeared). With an App, the action does not check the runs its merges start.
- **Up to 1000 pull requests per run.** `gh pr list` stops at `--limit 1000` without a warning.
- **Label matching folds ASCII letters only.**

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Released under the [MIT License](LICENSE).
