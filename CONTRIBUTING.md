# Contributing

Issues and pull requests are welcome. Please follow the [code of conduct](https://github.com/michen00/.github/blob/main/CODE_OF_CONDUCT.md) in every interaction.

## Questions, bugs, and ideas

Search the [issues](https://github.com/michen00/pr-catchup/issues) first. A bug report should give the version you pin, the workflow that calls the action, the log of the step that failed with tokens redacted, what you expected, and what happened instead. Report a security problem privately, as [SECURITY.md](SECURITY.md) describes, never in an issue.

## Setup

You need [uv](https://docs.astral.sh/uv/), git, bash, and [jq](https://jqlang.org/). The tests run the scripts under bash against a stub `gh` that evaluates `--jq` programs with the system jq.

```sh
git clone https://github.com/michen00/pr-catchup.git
cd pr-catchup
uv sync --locked
uv run pre-commit install
```

`pre-commit install` sets up the hooks that check each commit and its message.

## Checks

```sh
uv run --locked pytest
uv run --locked pre-commit run --all-files
```

CI runs both on every pull request that targets `main`. The hooks include shfmt and shellcheck for the scripts, actionlint for the workflows, ruff and ty for the tests, and markdownlint, yamllint, typos, and codespell for everything else.

## Changing the scripts

The tests pin the scripts' behavior: the `gh` calls they make, the state files they write, their exit statuses, and the text of the messages they print. A change to any of these needs the matching test change in the same pull request.

Inputs reach the scripts only through `env:` in `action.yml`; no `${{ }}` expression appears in a `run:` body. The action approves held runs only at merge commits it created in the same run, and a change that lets a caller name a commit to approve will not be accepted.

## Commits and pull requests

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/): `type(scope): subject`, with the scope optional, a lowercase subject, and at most 50 characters. gitlint checks each message once the hooks are installed. A pull request title should follow the same rules.

Prose in documentation, comments, and messages uses American spelling, the serial comma, and absolute dates such as 2026-06-11.
