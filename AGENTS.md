# Agent guidance

This repository is a public composite GitHub Action. `action.yml` runs three bash scripts, and pytest tests pin their behavior.

## Layout

- `action.yml`: inputs, the `updated` output, and three steps. Inputs reach the scripts only through `env:`.
- `scripts/catch-up.sh`, `scripts/approve-caused-runs.sh`, and `scripts/report.sh`: the three steps, in order.
- `scripts/approve-bot-gated-runs.sh` and `scripts/render-message.sh`: libraries the step scripts source; they are not executable.
- `tests/`: pytest tests that run the scripts under bash against a stub `gh` and a recording `sleep`, first on `PATH`. `tests/test_bot_automerge.py` also runs the provenance step of `bot-automerge.yml` against throwaway git repositories.
- `.github/workflows/ci.yml`: pre-commit and pytest. `.github/workflows/pr-catchup.yml` runs this action on this repository with `uses: ./`.
- `.github/workflows/bot-automerge.yml`: approves eligible Dependabot pull requests and arms auto-merge on them. `.github/workflows/bot-automerge-disarm.yml` turns auto-merge off when a pull request is labeled `do not auto-merge`.

## Commands

Setup needs uv, git, bash, and jq.

```sh
uv sync --locked                             # install the locked dev dependencies
uv run pre-commit install                    # install the commit and commit-message hooks
uv run --locked pytest                       # run the tests
uv run --locked pytest -k <pattern>          # run matching tests
uv run --locked pre-commit run --all-files   # run every hook, as CI does
uv run --locked pre-commit run shellcheck --all-files   # run one hook
```

## Hard rules

- The repository is public. Never name an organization, a private repository, an issue-tracker key, or a person other than the owner, `michen00`, in a file, commit message, or pull request.
- No `${{ }}` expression in a `run:` body, in `action.yml` or in a workflow. Pass values through `env:`.
- The action approves held runs only at merge commits it created in the same run. Never add an input or environment variable that names a commit to approve.
- Pin every third-party action to a full commit SHA with its version in a comment. Pin each tool version in exactly one place: hooks in `.pre-commit-config.yaml`, Python packages in `uv.lock`, and actions in the workflows, where Dependabot's grouped update rewrites every occurrence. Dependabot updates all three and no Markdown file, so documentation examples use a `<full-commit-sha>` placeholder.
- Never create a floating major tag; [RELEASING.md](RELEASING.md) says why. Do not tag or release unless asked.
- Files use LF line endings, end with a newline, and have no trailing whitespace.
- Prose uses American spelling, "pull request" unhyphenated even as a modifier, and the serial comma. Use the present indicative only for what is true now and modal verbs for rules and plans. Write absolute dates, such as 2026-06-11, never "today" or "recently".
- A code comment carries one non-obvious fact and is descriptive, not prescriptive. Reasoning belongs in `README.md`, commit messages, and pull requests.

## Tests pin the script behavior

The tests assert the `gh` calls each script makes, the state files it writes, its exit status, and the exact text it prints. A change to any of these changes the matching test in the same commit. When a test fails after a script change, the behavior changed: decide whether that was intended before editing the test.

## Commits

Commit messages follow Conventional Commits, as `.gitlint` defines: a known type, an optional scope, a lowercase subject, and at most 50 characters in the subject line.
