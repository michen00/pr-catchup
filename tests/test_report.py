"""Contract tests for scripts/report.sh, the last step of the action.

report.sh fails the run on each way a caught-up pull request can stay behind or
untested without a failure of its own: a refused merge, an approval pass that
could not see the gate, a run still held, and a head where no run appeared. It
prints a recovery path for each, because each needs a different fix.
"""

import subprocess
from pathlib import Path

import pytest

from tests.harness import BASH, gh_calls, report, script_env

_REFUSAL = 'gh: Resource not accessible by integration (HTTP 403)'


def _hint(output: str) -> str:
    """Return a refusal hint's commands, from the merge call to the dispatch."""
    lines = output.splitlines()
    start = next(
        index
        for index, line in enumerate(lines)
        if line.startswith('    gh api --method POST')
    )
    end = next(
        index
        for index in range(start, len(lines))
        if lines[index].startswith('    gh workflow run ')
    )
    return '\n'.join(lines[start : end + 1]) + '\n'


def _replay(tmp_path: Path, commands: str) -> list[list[str]]:
    """Run commands under bash against a gh that only records its arguments."""
    subprocess.run(
        [BASH, '-c', commands],
        env=script_env(tmp_path, {'FAKE_RECORD_ONLY': 'yes'}),
        check=True,
    )
    return gh_calls(tmp_path)


def test_report_fails_when_a_caused_run_stays_gated(tmp_path: Path) -> None:
    """A caused run still held at the gate turns the run red; none passes."""
    blocked = report(tmp_path, gated='30138052205 27\n')
    cleared = report(tmp_path)

    assert blocked.returncode == 1
    assert (
        '::error::run 30138052205 for PR #27 is still awaiting approval, so its '
        'required checks cannot report'
    ) in blocked.stdout
    assert cleared.returncode == 0


def test_report_fails_when_an_approval_pass_stayed_blind(tmp_path: Path) -> None:
    """An approval pass that could not confirm the gate turns the run red.

    The explanation names both ways a head ends up here: a lookup that failed or
    answered with something other than a count, and a held count with no run listed.
    """
    sha = 'd' * 40
    blocked = report(tmp_path, blind=f'31 {sha}\n')

    assert blocked.returncode == 1
    assert (
        f'::error::whether PR #31 cleared the approval gate at {sha} is unknown: '
        'an Actions lookup failed during the approval pass'
    ) in blocked.stdout
    assert (
        '  a run lookup failed or returned something other than a count, or held runs\n'
        '  were counted but none was listed, so this is unverified rather than clear.\n'
        '  Check the head and approve anything held there:\n'
    ) in blocked.stdout
    assert (
        f"    gh api 'repos/owner/repo/actions/runs?head_sha={sha}'" in blocked.stdout
    )


def test_report_fails_when_no_run_appeared_at_a_head(tmp_path: Path) -> None:
    """A head where no pull_request run appeared turns the run red and names it."""
    sha = 'c' * 40
    blocked = report(tmp_path, silent=f'31 {sha}\n')

    assert blocked.returncode == 1
    assert (
        f'::error::no pull_request run ever appeared for PR #31 at {sha}, so its '
        'required checks cannot report'
    ) in blocked.stdout
    assert (
        f"    gh api 'repos/owner/repo/actions/runs?head_sha={sha}'" in blocked.stdout
    )


def test_failure_output_carries_the_operator_recovery_path(tmp_path: Path) -> None:
    """A held run's report names both ways out: approving now, and an App token.

    Whether github-token may approve runs depends on the repository, so the run
    itself says what to do now and what to change if it keeps happening.
    """
    blocked = report(tmp_path, gated='30138052205 27\n')

    assert (
        '    gh api --method POST repos/owner/repo/actions/runs/30138052205/approve'
        in blocked.stdout
    )
    assert 'App installation token as the token input' in blocked.stdout
    assert 'github-actions[bot]' in blocked.stdout


def test_a_clean_run_says_every_caused_run_is_past_the_gate(tmp_path: Path) -> None:
    """With nothing recorded, report.sh passes and says so."""
    result = report(tmp_path)

    assert result.returncode == 0
    assert result.stdout == 'every run this catch-up caused is past the approval gate\n'


def test_missing_state_files_count_as_clean(tmp_path: Path) -> None:
    """A state file no earlier step wrote reads as empty."""
    state = tmp_path / 'empty-state'
    state.mkdir()

    result = report(tmp_path, env={'PR_CATCHUP_DIR': str(state)})

    assert result.returncode == 0


def test_the_refusal_hint_names_the_configured_base_and_message(
    tmp_path: Path,
) -> None:
    """The hint repeats the merge as attempted, with the base and message inputs."""
    result = report(
        tmp_path,
        refused=f'31 feat/pr-31 {_REFUSAL}\n',
        base='develop',
        commit_message='ci: sync {base} into #{number}',
    )

    assert result.returncode == 1
    assert result.stdout.splitlines() == [
        (
            '::error::merging develop into PR #31 was refused, so it stays behind '
            f'develop: {_REFUSAL}'
        ),
        '  an empty body means the branch is already current and a 409 on a pull',
        '  request GitHub reports as conflicting means it conflicts; neither is',
        '  reported here, so this is a refusal such as a permission error, a rate',
        '  limit, an outage, or a repository rule. Confirm what the endpoint says now,',
        '  then re-run the catch-up once the cause clears:',
        (
            '    gh api --method POST repos/owner/repo/merges -f base=feat/pr-31 '
            '-f head=develop \\'
        ),
        "      -f commit_message='ci: sync develop into #31'",
        '    gh workflow run catch-up.yml --repo owner/repo',
    ]


def test_the_refusal_hint_replays_exactly_through_bash(tmp_path: Path) -> None:
    """Pasted into bash, the hint makes the same merge call, whatever the branch holds.

    A branch name can carry quotes, dollar signs, and shell operators, and a
    message can span lines, so the branch and base are quoted with printf %q and
    the message is single-quoted.
    """
    branch = "feat/it's-$HOME&(x);{base}"
    message = "chore: merge {base} into #{number}\n\nIt's {branch}, not $HOME."
    result = report(
        tmp_path, refused=f'31 {branch} {_REFUSAL}\n', commit_message=message
    )

    calls = _replay(tmp_path, _hint(result.stdout))

    assert calls == [
        [
            'api',
            '--method',
            'POST',
            'repos/owner/repo/merges',
            '-f',
            f'base={branch}',
            '-f',
            'head=main',
            '-f',
            f"commit_message=chore: merge main into #31\n\nIt's {branch}, not $HOME.",
        ],
        ['workflow', 'run', 'catch-up.yml', '--repo', 'owner/repo'],
    ]


@pytest.mark.parametrize(
    'branch',
    [
        "feat/it's",
        'feat/"quoted"',
        'feat/$HOME',
        'feat/a&b;c|d',
        'feat/(x)<y>',
        'feat/`id`',
        'feat/#12!',
        'feat/{number}',
    ],
)
def test_the_refusal_hint_quotes_any_branch_name(tmp_path: Path, branch: str) -> None:
    """Each branch name git allows survives the round trip through the hint."""
    result = report(tmp_path, refused=f'31 {branch} {_REFUSAL}\n')

    merge, _ = _replay(tmp_path, _hint(result.stdout))

    assert merge[merge.index('-f') + 1] == f'base={branch}'
    assert merge[-1] == 'commit_message=chore: merge main into #31'


def test_the_refusal_hint_quotes_the_base_too(tmp_path: Path) -> None:
    """A base with shell syntax in its name round-trips as the branch does."""
    base = "release/it's-$v"
    result = report(tmp_path, refused=f'31 feat/pr-31 {_REFUSAL}\n', base=base)

    merge, _ = _replay(tmp_path, _hint(result.stdout))

    assert f'head={base}' in merge
    assert merge[-1] == f'commit_message=chore: merge {base} into #31'


@pytest.mark.parametrize(
    ('workflow_ref', 'workflow_name', 'expected'),
    [
        (
            'owner/repo/.github/workflows/catch-up.yml@refs/heads/main',
            None,
            'catch-up.yml',
        ),
        (
            'owner/repo/.github/workflows/sync.yaml@refs/heads/release/2026',
            None,
            'sync.yaml',
        ),
        (
            'owner/repo/.github/workflows/catch-up.yml@refs/pull/7/merge',
            None,
            'catch-up.yml',
        ),
        (
            'owner/repo/.github/workflows/catch-up.yml@refs/heads/team@host',
            None,
            'catch-up.yml',
        ),
        (
            'owner/repo/.github/workflows/catch up.yml@refs/heads/main',
            None,
            'catch\\ up.yml',
        ),
        (None, 'PR catch-up', 'PR\\ catch-up'),
        (None, None, '<workflow>'),
    ],
    ids=[
        'branch',
        'branch-with-slashes',
        'pr-merge-ref',
        'branch-with-at-sign',
        'file-name-with-space',
        'falls-back-to-workflow-name',
        'falls-back-to-placeholder',
    ],
)
def test_the_dispatch_hint_names_the_calling_workflow_file(
    tmp_path: Path, workflow_ref: str | None, workflow_name: str | None, expected: str
) -> None:
    """The hint dispatches the caller's workflow, read from GITHUB_WORKFLOW_REF.

    GITHUB_WORKFLOW_REF reads owner/repo/.github/workflows/<file>@<ref>. Without
    it, the hint names the workflow by GITHUB_WORKFLOW, and without that, by a
    placeholder.
    """
    result = report(
        tmp_path,
        refused=f'31 feat/pr-31 {_REFUSAL}\n',
        workflow_ref=workflow_ref,
        env={'GITHUB_WORKFLOW': workflow_name},
    )

    assert f'    gh workflow run {expected} --repo owner/repo\n' in result.stdout
