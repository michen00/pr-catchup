"""Contract tests for scripts/approve-caused-runs.sh, the second step of the action.

GitHub holds the pull_request runs that a github-actions[bot] merge commit
causes until someone with write access approves them. approve-caused-runs.sh
approves the held runs at each merge commit catch-up.sh made with github-token
in the same run, and records for report.sh each head whose runs stay held,
could not be checked, or never appeared. The approval library it sources has its
own tests in test_approve_bot_gated_runs.py.
"""

from pathlib import Path

import pytest

from tests.harness import approvals, approve_caused_runs, gh_calls, read_state, report

_BOT = 'github-actions[bot]'


def _held_run(run_id: str, sha: str, repo: str = 'owner/repo') -> str:
    """One TSV line of the held-run listing, as gated_runs prints it."""
    return f'{run_id}\t{sha}\t{repo}\tpull_request\t{_BOT}\taction_required\n'


def test_a_head_with_no_required_check_runs_fails_rather_than_warns(
    tmp_path: Path,
) -> None:
    """An updated head whose checks never appear fails the run rather than warning.

    A warning that exits green leaves the runs held with nobody to approve them.
    `all_runs` exceeds `pr_runs` here on purpose: code-scanning runs do appear at
    the merge commit, so a wait that counted every event would call this head
    healthy.
    """
    sha = 'c' * 40
    approve = approve_caused_runs(tmp_path, f'31 {sha}\n', pr_runs='0', all_runs='2')
    recorded = read_state(tmp_path, 'silent.txt')
    verdict = report(tmp_path, silent=recorded)

    assert approve.returncode == 0
    assert recorded == f'31 {sha}\n'
    assert verdict.returncode == 1
    assert f'PR #31 at {sha}' in verdict.stdout


def test_a_head_whose_runs_are_ungated_stays_green(tmp_path: Path) -> None:
    """Runs that exist and are not held need nothing, and the step records nothing.

    This is the shape a repository takes if the hold is ever lifted: runs appear
    and report on their own. A rule that read "no held run found" as a failure
    would turn every catch-up red here.
    """
    approve = approve_caused_runs(
        tmp_path, f'31 {"d" * 40}\n', pr_runs='4', all_runs='6'
    )

    assert approve.returncode == 0
    assert read_state(tmp_path, 'silent.txt') == ''
    assert read_state(tmp_path, 'gated.txt') == ''
    assert read_state(tmp_path, 'blind.txt') == ''


def test_a_blind_approval_pass_is_reported_apart_from_a_confirmed_gate(
    tmp_path: Path,
) -> None:
    """A held count with no run to show for it is recorded as unverified, not as held.

    That is what a failed listing that printed nothing looks like from here.
    Recording it as held would name a run nobody identified.
    """
    sha = 'd' * 40
    approve = approve_caused_runs(
        tmp_path, f'31 {sha}\n', pr_runs='4', gated_count='1', gated_tsv=''
    )

    assert approve.returncode == 0
    assert read_state(tmp_path, 'gated.txt') == ''
    assert read_state(tmp_path, 'blind.txt') == f'31 {sha}\n'


def test_a_partial_final_listing_is_reported_as_unverified(tmp_path: Path) -> None:
    """A last listing that failed after printing ids is blind, not held by those ids.

    Only a listing that succeeded names every held run, so the ids a failed one
    printed are not reported as the runs to approve.
    """
    sha = 'e' * 40
    approve = approve_caused_runs(
        tmp_path,
        f'31 {sha}\n',
        pr_runs='4',
        gated_count='1',
        gated_tsv=_held_run('111', sha),
        failure='final-list',
    )

    assert approve.returncode == 0
    assert read_state(tmp_path, 'gated-31.txt') == '111\n'
    assert read_state(tmp_path, 'gated.txt') == ''
    assert read_state(tmp_path, 'blind.txt') == f'31 {sha}\n'


def test_a_run_still_held_after_the_approval_pass_is_reported_by_id(
    tmp_path: Path,
) -> None:
    """A run that stays held is approved once, recorded by id, and reported."""
    sha = 'e' * 40
    approve = approve_caused_runs(
        tmp_path,
        f'31 {sha}\n',
        pr_runs='4',
        gated_count='1',
        gated_tsv=_held_run('111', sha),
    )
    recorded = read_state(tmp_path, 'gated.txt')
    verdict = report(tmp_path, gated=recorded)

    assert approve.returncode == 0
    assert approve.stdout.count('approved run 111 for #31') == 1
    assert approvals(tmp_path) == ['111']
    assert recorded == '111 31\n'
    assert verdict.returncode == 1
    assert (
        '    gh api --method POST repos/owner/repo/actions/runs/111/approve'
        in verdict.stdout
    )


def test_nothing_is_approved_when_no_merge_used_github_token(tmp_path: Path) -> None:
    """With an empty queue, as after merges made as the App, the step makes no call."""
    approve = approve_caused_runs(tmp_path, '')

    assert approve.returncode == 0
    assert approve.stdout == (
        'no merge was made with github-token, so no run needs approval\n'
    )
    assert gh_calls(tmp_path) == []
    assert read_state(tmp_path, 'gated.txt') == ''


def test_approval_reads_shas_only_from_this_runs_merges(tmp_path: Path) -> None:
    """No variable names a commit to approve; only catch-up.sh's queue does.

    Approving held runs with github-token is privileged, and it is safe only for
    a commit whose content the action generated itself. The library's old
    standalone mode read CAUSED_SHA, which would let a caller name any commit.
    """
    approve = approve_caused_runs(
        tmp_path,
        None,
        pr_runs='4',
        gated_count='1',
        gated_tsv=_held_run('111', 'f' * 40),
        env={
            'APPROVE_BOT_GATED_MAIN': '1',
            'CAUSED_SHA': 'f' * 40,
            'PR_NUMBER': '31',
        },
    )

    assert approve.returncode == 0
    assert gh_calls(tmp_path) == []


def test_a_held_run_at_another_commit_is_left_for_a_human(tmp_path: Path) -> None:
    """A held run the listing returns for a different commit is never approved."""
    caused, other = 'e' * 40, 'f' * 40
    approve = approve_caused_runs(
        tmp_path,
        f'31 {caused}\n',
        pr_runs='4',
        gated_count='1',
        gated_tsv=_held_run('111', other),
    )

    assert approvals(tmp_path) == []
    assert (
        f'out of scope, left for a human: run 111 (owner/repo pull_request {_BOT} '
        'action_required)'
    ) in approve.stdout
    assert read_state(tmp_path, 'gated.txt') == '111 31\n'


def test_a_run_github_token_cannot_approve_is_reported_as_held(tmp_path: Path) -> None:
    """A refused approval warns once, and the run is reported as still held."""
    sha = 'e' * 40
    approve = approve_caused_runs(
        tmp_path,
        f'31 {sha}\n',
        pr_runs='4',
        gated_count='1',
        gated_tsv=_held_run('111', sha),
        approve_fails=True,
    )

    assert approve.returncode == 0
    assert (
        approve.stdout.count(
            '::warning::could not approve run 111; github-token may not be permitted '
            'to approve runs here'
        )
        == 1
    )
    assert read_state(tmp_path, 'gated.txt') == '111 31\n'


def test_each_queued_merge_is_checked_on_its_own(tmp_path: Path) -> None:
    """Every merge in the queue gets its own pass, and its own record."""
    first, second = 'a' * 40, 'c' * 40
    approve = approve_caused_runs(tmp_path, f'31 {first}\n32 {second}\n', pr_runs='0')

    assert approve.returncode == 0
    assert read_state(tmp_path, 'silent.txt') == f'31 {first}\n32 {second}\n'


@pytest.mark.parametrize(
    'failure', ['head', 'list', 'count', 'head-not-a-number', 'count-not-a-number']
)
def test_failed_run_lookup_never_reports_clear(tmp_path: Path, failure: str) -> None:
    """A run lookup that fails, or answers with something other than a count, is blind.

    A failed lookup prints nothing, which would otherwise read as no run held or
    no run seen. Each is recorded as unverified, and report.sh fails the run.
    """
    sha = 'd' * 40
    approve = approve_caused_runs(
        tmp_path, f'31 {sha}\n', pr_runs='4', gated_count='0', failure=failure
    )
    recorded = read_state(tmp_path, 'blind.txt')

    assert approve.returncode == 0
    assert recorded == f'31 {sha}\n'
    assert read_state(tmp_path, 'silent.txt') == ''
    assert read_state(tmp_path, 'gated.txt') == ''
    assert report(tmp_path, blind=recorded).returncode == 1
