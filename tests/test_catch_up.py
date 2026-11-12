"""Contract tests for scripts/catch-up.sh, the first step of the action.

catch-up.sh merges the base into every open, same-repository, conflict-free pull
request that targets it and carries none of the excluded labels. It records the
merges made with github-token, whose runs GitHub holds, for
approve-caused-runs.sh, and the refused merges for report.sh. Each test runs the
script against the fake gh in fake_gh.py.
"""

from pathlib import Path

import pytest

from tests.harness import (
    MERGE_SHA,
    catch_up,
    gh_calls,
    github_output,
    merge_calls,
    read_state,
    report,
    sleeps,
)


def _pr(
    number: int,
    *,
    branch: str = '',
    cross_repository: bool = False,
    mergeable: str = 'MERGEABLE',
    labels: tuple[str, ...] = (),
) -> dict[str, object]:
    """One open pull request as `gh pr list --json` describes it."""
    return {
        'number': number,
        'headRefName': branch or f'feat/pr-{number}',
        'isCrossRepository': cross_repository,
        'mergeable': mergeable,
        'labels': [{'name': name, 'color': 'ededed'} for name in labels],
    }


def _merged_branches(tmp_path: Path) -> list[str]:
    return [call['base'] for call in merge_calls(tmp_path)]


def _listing_call(tmp_path: Path) -> list[str]:
    return next(call for call in gh_calls(tmp_path) if call[:2] == ['pr', 'list'])


def _views(tmp_path: Path) -> list[list[str]]:
    return [call for call in gh_calls(tmp_path) if call[:2] == ['pr', 'view']]


def test_the_catchup_merge_commit_carries_the_default_subject(tmp_path: Path) -> None:
    """The merge commit has a typed subject that names the pull request.

    The update-branch endpoint cannot set a commit message, so its merge commit
    always reads `Merge branch 'main' into ...`. The merges endpoint takes a
    commit_message, and with the default inputs on main it renders the subject
    every hand-copied workflow used. The number stands in for the branch name,
    whose length would otherwise decide whether the subject fits a length limit.
    """
    result = catch_up(tmp_path, prs='31')
    [call] = merge_calls(tmp_path)

    assert result.returncode == 0
    # The base is merged into the pull request's branch, never the other way around.
    assert (call['base'], call['head']) == ('feat/pr-31', 'main')
    assert call['commit_message'] == 'chore: merge main into #31'
    assert result.stdout.splitlines() == [
        '::group::PR #31',
        f'updated #31 to {MERGE_SHA}',
        '::endgroup::',
    ]


def test_a_failed_pr_listing_turns_the_run_red(tmp_path: Path) -> None:
    """A pull request listing that fails turns the run red rather than updating nothing.

    A listing that died of a rate limit or an outage would otherwise look like a
    repository with no pull request behind the base: the step updates nothing
    and stays green, which is the state this action exists to make impossible.
    """
    result = catch_up(tmp_path, list_fails=True)

    assert result.returncode != 0
    assert read_state(tmp_path, 'default-token-merges.txt') == ''
    assert merge_calls(tmp_path) == []


def test_one_refused_pr_does_not_abandon_the_others(tmp_path: Path) -> None:
    """A refused pull request is recorded, and the loop goes on to update the next."""
    result = catch_up(tmp_path, prs='31 32', refused='31')

    refused = read_state(tmp_path, 'refused.txt')
    queued = read_state(tmp_path, 'default-token-merges.txt')

    assert result.returncode == 0
    assert refused.split()[0] == '31'
    assert queued.split() == ['32', MERGE_SHA]


def test_a_branch_already_carrying_the_base_is_not_treated_as_a_failure(
    tmp_path: Path,
) -> None:
    """A pull request the merge call answers with an empty 204 is current.

    Every catch-up leaves far more pull requests alone than it updates, so
    reading an empty body as anything but "nothing to do" would fail ordinary
    runs.
    """
    result = catch_up(tmp_path, prs='31', current='31')

    assert result.returncode == 0
    assert 'no update needed for #31' in result.stdout
    assert read_state(tmp_path, 'default-token-merges.txt') == ''
    assert read_state(tmp_path, 'refused.txt') == ''


def test_a_conflict_the_listing_could_not_hold_out_is_not_a_failure(
    tmp_path: Path,
) -> None:
    """A 409 on a conflicting pull request leaves it behind without failing the run.

    mergeable reads UNKNOWN until GitHub has computed it, and the listing holds
    out only CONFLICTING, so a conflicting pull request can still reach the merge
    call. Only its author can resolve the conflict.
    """
    result = catch_up(tmp_path, prs='31', conflicting='31')

    assert result.returncode == 0
    assert read_state(tmp_path, 'default-token-merges.txt') == ''
    assert read_state(tmp_path, 'refused.txt') == ''
    assert '::notice::#31 conflicts with main, so it stays behind' in result.stdout


def test_a_409_on_a_pr_github_reports_as_mergeable_fails_the_run(
    tmp_path: Path,
) -> None:
    """A 409 is a conflict only when GitHub also reports a conflict.

    A clean merge that would change a workflow file the token cannot write is
    refused with a 409 too. Reading that as a conflict would leave the pull
    request behind under a green run, with a notice that misnames the cause.
    """
    result = catch_up(tmp_path, prs='31', withheld='31')
    recorded = read_state(tmp_path, 'refused.txt')
    verdict = report(tmp_path, refused=recorded)

    assert result.returncode == 0
    assert 'conflicts with main' not in result.stdout
    assert recorded == (
        '31 feat/pr-31 gh: Merge conflict (HTTP 409) (GitHub reports it as MERGEABLE)\n'
    )
    assert verdict.returncode == 1


def test_a_refusal_that_is_neither_204_nor_409_is_reported_rather_than_read_as_current(
    tmp_path: Path,
) -> None:
    """A 403, a 404, or a rate limit leaves the branch behind, and the run says so.

    Only an empty body means the branch is current, and only a 409 on a
    conflicting pull request means the merge was never the action's to make.
    Folding any other refusal into either would report success for a pull
    request that looks current but whose runs were never created.
    """
    result = catch_up(tmp_path, prs='31', refused='31')
    recorded = read_state(tmp_path, 'refused.txt')
    verdict = report(tmp_path, refused=recorded)

    # The loop stays green so later pull requests are still updated; report.sh is
    # where every kind of stall turns the run red.
    assert result.returncode == 0
    # Without an App, the 403 is github-token's own, so there is nothing to retry.
    assert [call['token'] for call in merge_calls(tmp_path)] == ['default-token']
    assert 'App merge refused' not in result.stdout
    assert recorded == (
        '31 feat/pr-31 gh: Resource not accessible by integration (HTTP 403)\n'
    )
    assert verdict.returncode == 1
    # The status tells a fault from a current branch, and the branch lets an
    # operator repeat the merge without looking it up.
    assert '(HTTP 403)' in verdict.stdout
    assert '-f base=feat/pr-31 -f head=main' in verdict.stdout


def test_only_same_repository_conflict_free_prs_are_updated(tmp_path: Path) -> None:
    """A pull request from a fork, or one GitHub reports as conflicting, is skipped.

    The merge call writes to a branch of this repository named by headRefName,
    so a fork's pull request would name some other branch. One whose mergeable
    is still UNKNOWN is listed, and the merge call decides.
    """
    pull_requests = [
        _pr(31),
        _pr(32, cross_repository=True),
        _pr(33, mergeable='CONFLICTING'),
        _pr(34, mergeable='UNKNOWN'),
    ]

    result = catch_up(tmp_path, pull_requests=pull_requests)

    assert result.returncode == 0
    assert _merged_branches(tmp_path) == ['feat/pr-31', 'feat/pr-34']


def test_the_listing_reaches_every_open_pr(tmp_path: Path) -> None:
    """The listing is not capped near the open pull request count.

    `gh pr list` truncates at --limit without warning, so a low cap would leave
    the tail of the list behind indefinitely while the run reports success. The
    bound is what this asserts; the exact number is not load-bearing.
    """
    catch_up(tmp_path)
    listing = _listing_call(tmp_path)

    assert int(listing[listing.index('--limit') + 1]) >= 1000
    assert listing[listing.index('--state') + 1] == 'open'
    assert listing[listing.index('--repo') + 1] == 'owner/repo'


def test_a_refused_app_merge_retries_with_the_default_token(tmp_path: Path) -> None:
    """A merge denied to the App falls back to github-token and queues its runs."""
    result = catch_up(tmp_path, app_token='app-token', app_refused='403')

    assert result.returncode == 0
    assert [call['token'] for call in merge_calls(tmp_path)] == [
        'app-token',
        'default-token',
    ]
    assert '::warning::App merge refused for #31; retrying with github-token' in (
        result.stdout
    )
    assert read_state(tmp_path, 'default-token-merges.txt').split() == [
        '31',
        MERGE_SHA,
    ]
    assert read_state(tmp_path, 'refused.txt') == ''


def test_an_app_merge_needs_no_default_token_run_approval(tmp_path: Path) -> None:
    """A merge made as the App is left out of the approval queue.

    GitHub holds the runs that a github-actions[bot] merge commit causes, and
    none of the runs an App's merge commit causes.
    """
    result = catch_up(tmp_path, app_token='app-token')

    assert result.returncode == 0
    assert f'updated #31 to {MERGE_SHA}' in result.stdout
    assert [call['token'] for call in merge_calls(tmp_path)] == ['app-token']
    assert read_state(tmp_path, 'default-token-merges.txt') == ''


def test_the_updated_output_names_every_pull_request_merged(tmp_path: Path) -> None:
    """The updated output lists merged pull requests, not current or refused ones."""
    result = catch_up(tmp_path, prs='31 32 33 34', current='32', refused='33')

    assert result.returncode == 0
    assert github_output(tmp_path) == 'updated=31 34\n'


def test_the_updated_output_counts_merges_made_as_the_app(tmp_path: Path) -> None:
    """The updated output names a pull request whichever identity merged into it."""
    catch_up(tmp_path, prs='31 32', app_token='app-token')

    assert github_output(tmp_path) == 'updated=31 32\n'
    assert read_state(tmp_path, 'default-token-merges.txt') == ''


def test_the_updated_output_is_empty_when_nothing_was_merged(tmp_path: Path) -> None:
    """A run that merges nothing still sets updated, to an empty string."""
    catch_up(tmp_path, prs='31', current='31')

    assert github_output(tmp_path) == 'updated=\n'


def test_without_github_output_the_script_writes_no_output(tmp_path: Path) -> None:
    """Run outside Actions, the script skips the output instead of failing."""
    result = catch_up(tmp_path, env={'GITHUB_OUTPUT': None})

    assert result.returncode == 0
    assert github_output(tmp_path) == ''


def test_the_base_input_replaces_main_in_the_listing_the_merge_and_messages(
    tmp_path: Path,
) -> None:
    """The base input sets the listing, the merge, and the messages."""
    result = catch_up(tmp_path, prs='31 32', conflicting='32', base='develop')
    listing = _listing_call(tmp_path)

    assert result.returncode == 0
    assert listing[listing.index('--base') + 1] == 'develop'
    assert [
        (call['base'], call['head'], call['commit_message'])
        for call in merge_calls(tmp_path)
    ] == [
        ('feat/pr-31', 'develop', 'chore: merge develop into #31'),
        ('feat/pr-32', 'develop', 'chore: merge develop into #32'),
    ]
    assert '::notice::#32 conflicts with develop, so it stays behind' in result.stdout


def test_an_empty_base_fails_before_any_call(tmp_path: Path) -> None:
    """An empty base fails the step instead of listing every pull request."""
    result = catch_up(tmp_path, base='')

    assert result.returncode == 1
    assert result.stdout == (
        "::error::base is empty; its default is the event's repository default branch\n"
    )
    assert gh_calls(tmp_path) == []


def test_an_empty_commit_message_fails_before_any_call(tmp_path: Path) -> None:
    """An empty commit-message fails the step instead of merging with no message."""
    result = catch_up(tmp_path, commit_message='')

    assert result.returncode == 1
    assert result.stdout == '::error::commit-message is empty\n'
    assert gh_calls(tmp_path) == []


def test_the_commit_message_input_fills_in_base_number_and_branch(
    tmp_path: Path,
) -> None:
    """The commit-message input is a template over {base}, {number}, and {branch}."""
    catch_up(
        tmp_path,
        base='develop',
        commit_message=(
            'ci: sync {base} into {branch} (#{number})\n\nKeeps #{number} current.'
        ),
    )
    [call] = merge_calls(tmp_path)

    assert call['commit_message'] == (
        'ci: sync develop into feat/pr-31 (#31)\n\nKeeps #31 current.'
    )


def test_a_pull_request_carrying_an_excluded_label_is_left_alone(
    tmp_path: Path,
) -> None:
    """A pull request labeled with any of exclude-labels is skipped, whatever its case.

    A label can mark a pull request whose current revision is under review, or
    one that is not to move at all.
    """
    pull_requests = [_pr(31, labels=('Hold',)), _pr(32, labels=('bug',)), _pr(33)]

    result = catch_up(tmp_path, pull_requests=pull_requests, exclude_labels='hold')

    assert result.returncode == 0
    assert _merged_branches(tmp_path) == ['feat/pr-32', 'feat/pr-33']


def test_the_state_directory_defaults_to_runner_temp(tmp_path: Path) -> None:
    """Without PR_CATCHUP_DIR, state goes to $RUNNER_TEMP/pr-catchup, not to /tmp."""
    runner_temp = tmp_path / 'runner-temp'
    runner_temp.mkdir()

    result = catch_up(
        tmp_path, env={'PR_CATCHUP_DIR': None, 'RUNNER_TEMP': str(runner_temp)}
    )
    queued = runner_temp / 'pr-catchup' / 'default-token-merges.txt'

    assert result.returncode == 0
    assert queued.read_text(encoding='utf-8').split() == ['31', MERGE_SHA]


def test_a_409_waits_for_mergeable_to_settle_before_calling_it_a_conflict(
    tmp_path: Path,
) -> None:
    """GitHub's mergeable is read again while UNKNOWN, and the settled value decides."""
    result = catch_up(tmp_path, conflicting='31', unknown_polls=2)

    assert result.returncode == 0
    assert len(_views(tmp_path)) == 3
    assert sleeps(tmp_path) == ['2', '2']
    assert '::notice::#31 conflicts with main, so it stays behind' in result.stdout
    assert read_state(tmp_path, 'refused.txt') == ''


def test_a_mergeable_that_never_settles_is_recorded_as_a_refusal(
    tmp_path: Path,
) -> None:
    """Five UNKNOWN reads, 2 seconds apart, make the 409 a refusal, not a conflict."""
    result = catch_up(tmp_path, withheld='31', unknown_polls=99)

    assert result.returncode == 0
    assert len(_views(tmp_path)) == 5
    assert sleeps(tmp_path) == ['2'] * 5
    assert read_state(tmp_path, 'refused.txt') == (
        '31 feat/pr-31 gh: Merge conflict (HTTP 409) (GitHub reports it as UNKNOWN)\n'
    )


@pytest.mark.parametrize('status', ['401', '404'])
def test_an_app_merge_refused_with_401_or_404_is_retried_too(
    tmp_path: Path, status: str
) -> None:
    """An App token that is invalid or cannot see the branch falls back too."""
    result = catch_up(tmp_path, app_token='app-token', app_refused=status)

    assert result.returncode == 0
    assert [call['token'] for call in merge_calls(tmp_path)] == [
        'app-token',
        'default-token',
    ]
    assert read_state(tmp_path, 'default-token-merges.txt').split() == [
        '31',
        MERGE_SHA,
    ]


@pytest.mark.parametrize('status', ['409', '422', '500'])
def test_any_other_app_refusal_is_recorded_without_a_retry(
    tmp_path: Path, status: str
) -> None:
    """Only 401, 403, and 404 mean github-token might succeed where the App did not.

    An App without the Workflows permission gets a 409 on a merge that would
    change a workflow file; github-token cannot write workflow files either.
    """
    result = catch_up(tmp_path, app_token='app-token', app_refused=status)

    assert result.returncode == 0
    assert [call['token'] for call in merge_calls(tmp_path)] == ['app-token']
    assert f'(HTTP {status})' in read_state(tmp_path, 'refused.txt')
    assert read_state(tmp_path, 'default-token-merges.txt') == ''


def test_a_token_input_equal_to_github_token_still_queues_its_runs(
    tmp_path: Path,
) -> None:
    """Tokens are compared by value, so github-token passed as token still queues."""
    catch_up(tmp_path, app_token='default-token')

    assert read_state(tmp_path, 'default-token-merges.txt').split() == [
        '31',
        MERGE_SHA,
    ]


@pytest.mark.parametrize(
    ('exclude_labels', 'labels', 'skipped'),
    [
        ('hold', ('Hold',), True),
        ('HOLD', ('hold',), True),
        ('wip,hold', ('hold',), True),
        ('wip\nhold', ('hold',), True),
        (' wip ,\n\n hold \n', ('hold',), True),
        ('do not merge', ('Do Not Merge',), True),
        ('hold', ('bug', 'HOLD'), True),
        ('hold', ('on-hold',), False),
        ('hold', (), False),
        ('', ('hold',), False),
        (',\n ,', ('hold',), False),
    ],
    ids=[
        'case-insensitive',
        'case-insensitive-input',
        'comma-separated',
        'newline-separated',
        'trimmed-with-blank-entries',
        'inner-spaces-kept',
        'any-label-of-several',
        'whole-names-only',
        'no-labels',
        'empty-input',
        'separators-only',
    ],
)
def test_exclude_labels_match_whole_names_case_insensitively(
    tmp_path: Path, exclude_labels: str, labels: tuple[str, ...], *, skipped: bool
) -> None:
    """Each listed name is trimmed and matched whole against each label, any case."""
    pull_requests = [_pr(31, labels=labels), _pr(32)]

    result = catch_up(
        tmp_path, pull_requests=pull_requests, exclude_labels=exclude_labels
    )

    assert result.returncode == 0
    expected = ['feat/pr-32'] if skipped else ['feat/pr-31', 'feat/pr-32']
    assert _merged_branches(tmp_path) == expected
