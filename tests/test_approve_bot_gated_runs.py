"""Contract tests for the library that approves runs held at the bot gate.

approve-caused-runs.sh sources scripts/approve-bot-gated-runs.sh to approve the
pull_request runs the action's own merge commits cause. The library is a
privileged step: it approves workflow runs with github-token. These tests hold
its predicate to exactly those runs, and its return status to whether any of
them is still held, never appeared, or could not be checked.
"""

import json
import shlex
import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

from tests.harness import BASH, REPO, SCRIPTS, approvals, gh_calls, script_env, sleeps

_LIBRARY = SCRIPTS / 'approve-bot-gated-runs.sh'
_SOURCE = _LIBRARY.read_text(encoding='utf-8')
_CAUSED = 'e' * 40
_BOT = 'github-actions[bot]'


def _extract_shell_function(name: str) -> str:
    """Return one top-level shell function from the library."""
    start = _SOURCE.index(f'{name}() {{')
    end = _SOURCE.index('\n}\n', start) + len('\n}')
    return _SOURCE[start:end]


def _approve_for_sha(
    tmp_path: Path, fake: dict[str, str | None], stub: Path | None = None
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Source the library and approve the runs at _CAUSED for pull request 12.

    The call is made under `||`, as approve-caused-runs.sh makes it, which turns
    off errexit inside the function.

    Returns:
        The finished process and the APPROVE_BOT_GATED_OUT file.
    """
    out_file = tmp_path / 'gated-12.txt'
    script = (
        f'source {shlex.quote(str(_LIBRARY))}\n'
        f'APPROVE_BOT_GATED_OUT={shlex.quote(str(out_file))}\n'
        f'approve_bot_gated_runs_for_sha {_CAUSED} 12 || exit $?\n'
    )
    env = (
        script_env(tmp_path, fake) if stub is None else script_env(tmp_path, fake, stub)
    )
    result = subprocess.run(
        [BASH, '-c', script], env=env, capture_output=True, text=True, check=False
    )
    return result, out_file


def _held(run_id: str, sha: str = _CAUSED) -> str:
    """One TSV line of the held-run listing, as gated_runs prints it."""
    return f'{run_id}\t{sha}\t{REPO}\tpull_request\t{_BOT}\taction_required\n'


def test_the_predicate_admits_only_bot_runs_at_the_caused_sha() -> None:
    """The predicate admits only bot pull_request runs held at the caused SHA."""
    guard = _extract_shell_function('approvable')
    caused = 'a' * 40
    unrelated = 'b' * 40

    def case(**overrides: str) -> tuple[str, ...]:
        """Return one argument list for the guard: the approvable baseline, spoiled.

        The defaults are the case that approves, so each call names only the
        field it breaks. The values come back in insertion order, because the
        shell function takes them positionally.

        Returns:
            The seven operands, in order.
        """
        fields = {
            'run_sha': caused,
            'caused_sha': caused,
            'run_repo': REPO,
            'this_repo': REPO,
            'run_event': 'pull_request',
            'run_actor': _BOT,
            'run_gate': 'action_required',
        }
        fields.update(overrides)
        return tuple(fields.values())

    cases = [
        case(),
        case(run_sha=unrelated),
        case(caused_sha=''),
        case(run_sha='', caused_sha=''),
        case(run_repo='outsider/repo'),
        case(this_repo=''),
        case(run_repo='', this_repo=''),
        case(run_event='pull_request_target'),
        case(run_actor='michen00'),
        case(run_gate='completed'),
    ]
    calls = '\n'.join(
        'check ' + ' '.join(shlex.quote(field) for field in one) for one in cases
    )
    script = (
        'set -uo pipefail\n'
        f'{guard}\n'
        'check() { if approvable "$@"; then echo approve; else echo skip; fi; }\n'
        f'{calls}\n'
    )

    result = subprocess.run(
        [BASH, '-c', script], check=True, capture_output=True, text=True
    )

    assert result.stdout.splitlines() == ['approve'] + ['skip'] * 9


def test_the_timing_signature_is_documented_beside_the_gate_reader() -> None:
    """The library says why equal timestamps do not identify a held run."""
    prose = ' '.join(
        ' '.join(
            line.lstrip().lstrip('#').strip()
            for line in _SOURCE.splitlines()
            if line.lstrip().startswith('#')
        ).split()
    )

    assert 'created_at' in prose
    assert 'run_started_at' in prose
    assert 'only after approval' in prose
    assert 'conclusion' in prose


def test_a_run_still_held_after_approval_returns_1_and_names_it(
    tmp_path: Path,
) -> None:
    """A run still held after the approval pass returns 1 and is written out.

    report.sh prints the operator's recovery path from that id; see
    test_approve_caused_runs.py.
    """
    result, out_file = _approve_for_sha(
        tmp_path,
        {'FAKE_PR_RUNS': '4', 'FAKE_GATED_COUNT': '1', 'FAKE_GATED_TSV': _held('111')},
    )

    assert result.returncode == 1
    assert out_file.read_text(encoding='utf-8') == '111\n'
    assert result.stdout.count('approved run 111 for #12') == 1
    # The sweep runs ten times, 3 seconds apart, before it gives up on the run.
    assert sleeps(tmp_path) == ['3'] * 10


def test_a_head_with_no_pull_request_run_returns_2(tmp_path: Path) -> None:
    """A head with no pull_request run returns 2, because its checks never report.

    The code-scanning runs that do appear at the same commit are not counted.
    The wait reads the count ten times, 3 seconds apart.
    """
    result, out_file = _approve_for_sha(
        tmp_path, {'FAKE_PR_RUNS': '0', 'FAKE_ALL_RUNS': '2'}
    )
    head_reads = [
        call
        for call in gh_calls(tmp_path)
        if any('event=pull_request' in arg for arg in call)
    ]

    assert result.returncode == 2
    assert out_file.read_text(encoding='utf-8') == ''
    assert approvals(tmp_path) == []
    assert len(head_reads) == 10
    assert sleeps(tmp_path) == ['3'] * 10


def test_a_final_listing_that_fails_partway_returns_3(tmp_path: Path) -> None:
    """A last held-run listing that fails after printing some ids returns 3, not 1.

    The ids it printed may be only part of what is held, so the head is unverified
    rather than held by exactly those runs.
    """
    result, out_file = _approve_for_sha(
        tmp_path,
        {
            'FAKE_PR_RUNS': '4',
            'FAKE_GATED_COUNT': '1',
            'FAKE_GATED_TSV': _held('111'),
            'FAKE_FAILURE': 'final-list',
        },
    )

    assert result.returncode == 3
    assert out_file.read_text(encoding='utf-8') == '111\n'


def test_runs_that_are_not_held_leave_the_script_green(tmp_path: Path) -> None:
    """Runs that exist and are not held need no approval, and the function returns 0."""
    result, out_file = _approve_for_sha(
        tmp_path, {'FAKE_PR_RUNS': '4', 'FAKE_GATED_COUNT': '0'}
    )

    assert result.returncode == 0
    assert out_file.read_text(encoding='utf-8') == ''


def test_a_held_run_reads_completed_with_conclusion_action_required(
    tmp_path: Path,
) -> None:
    """A real held run reports status completed and conclusion action_required.

    The listing filters on status=action_required, but the runs it returns carry
    that value only in conclusion, so reading status alone would approve nothing.
    """
    run = {
        'id': 111,
        'head_sha': _CAUSED,
        'head_repository': {'full_name': REPO},
        'event': 'pull_request',
        'actor': {'login': _BOT},
        'status': 'completed',
        'conclusion': 'action_required',
    }

    result, _ = _approve_for_sha(
        tmp_path, {'FAKE_PR_RUNS': '1', 'FAKE_GATED_RUNS': json.dumps([run])}
    )

    assert result.returncode == 0
    assert approvals(tmp_path) == ['111']


def _second_run_stub(tmp_path: Path, first_id: str, second_id: str) -> Path:
    """Write a stub gh where a second held run appears only after the first sweep.

    More than one workflow can answer the synchronize event a catch-up produces,
    so a run held by a second workflow can appear after the first one has been
    swept and approved. Approving a run removes it from every later listing;
    `second_id` appears from the second listing onward.

    Returns:
        The stub's path.
    """
    approved = tmp_path / 'approved-ids.txt'
    approved.write_text('', encoding='utf-8')
    listings = tmp_path / 'gated-calls'
    stub = tmp_path / 'second_run_gh.py'
    stub.write_text(
        dedent(f"""\
            import os
            import sys
            from pathlib import Path

            args = sys.argv[1:]
            url = next((arg for arg in args if arg.startswith("repos/")), "")
            jq = args[args.index("--jq") + 1] if "--jq" in args else ""
            approved = Path({str(approved)!r})
            listings = Path({str(listings)!r})

            if url.endswith("/approve"):
                with approved.open("a") as handle:
                    handle.write(url.rsplit("/", 2)[1] + "\\n")
                sys.stdout.write("{{}}\\n")
                raise SystemExit(0)
            if "/actions/runs?" in url and "event=pull_request" in url:
                sys.stdout.write(os.environ["FAKE_PR_RUNS"] + "\\n")
                raise SystemExit(0)
            if "/actions/runs?" in url and "status=action_required" in url:
                seen = int(listings.read_text()) if listings.exists() else 0
                listings.write_text(str(seen + 1))
                already = approved.read_text().split()
                held = ["{first_id}"] if seen == 0 else ["{second_id}"]
                held = [run for run in held if run not in already]
                if jq == ".total_count":
                    sys.stdout.write(f"{{len(held)}}\\n")
                else:
                    for run in held:
                        fields = [run, "{_CAUSED}", "{REPO}", "pull_request",
                                  "{_BOT}", "action_required"]
                        sys.stdout.write("\\t".join(fields) + "\\n")
                raise SystemExit(0)
            raise SystemExit(f"stub gh: unexpected call: {{args}}")
            """),
        encoding='utf-8',
    )
    return stub


def test_a_run_gated_after_the_first_sweep_is_still_approved(tmp_path: Path) -> None:
    """A run that a second workflow holds after the first pass is approved too.

    A count-only poll after a single approval pass would see this second run and
    wait for it to clear, but nothing would ever approve it.
    """
    stub = _second_run_stub(tmp_path, '111', '222')

    result, _ = _approve_for_sha(tmp_path, {'FAKE_PR_RUNS': '2'}, stub)

    assert 'approved run 111 for #12' in result.stdout
    assert 'approved run 222 for #12' in result.stdout
    assert result.returncode == 0


def _run_after_a_zero_count_stub(tmp_path: Path, first_id: str, second_id: str) -> Path:
    """Write a stub gh where a second held run is created once the count reads zero.

    The held count can read zero between one workflow's run clearing and a
    second workflow's run being created for the same commit. `first_id` is held
    from the start; `second_id` exists only after a count has read zero.

    Returns:
        The stub's path.
    """
    approved = tmp_path / 'approved-ids.txt'
    approved.write_text('', encoding='utf-8')
    zero_read = tmp_path / 'zero-read'
    stub = tmp_path / 'run_after_zero_gh.py'
    stub.write_text(
        dedent(f"""\
            import os
            import sys
            from pathlib import Path

            args = sys.argv[1:]
            url = next((arg for arg in args if arg.startswith("repos/")), "")
            jq = args[args.index("--jq") + 1] if "--jq" in args else ""
            approved = Path({str(approved)!r})
            zero_read = Path({str(zero_read)!r})

            if url.endswith("/approve"):
                with approved.open("a") as handle:
                    handle.write(url.rsplit("/", 2)[1] + "\\n")
                sys.stdout.write("{{}}\\n")
                raise SystemExit(0)
            if "/actions/runs?" in url and "event=pull_request" in url:
                sys.stdout.write(os.environ["FAKE_PR_RUNS"] + "\\n")
                raise SystemExit(0)
            if "/actions/runs?" in url and "status=action_required" in url:
                created = ["{first_id}"]
                if zero_read.exists():
                    created.append("{second_id}")
                already = approved.read_text().split()
                held = [run for run in created if run not in already]
                if jq == ".total_count":
                    if not held:
                        zero_read.touch()
                    sys.stdout.write(f"{{len(held)}}\\n")
                else:
                    for run in held:
                        fields = [run, "{_CAUSED}", "{REPO}", "pull_request",
                                  "{_BOT}", "action_required"]
                        sys.stdout.write("\\t".join(fields) + "\\n")
                raise SystemExit(0)
            raise SystemExit(f"stub gh: unexpected call: {{args}}")
            """),
        encoding='utf-8',
    )
    return stub


def test_one_zero_count_does_not_end_the_sweep(tmp_path: Path) -> None:
    """The sweep stops only on two zero counts in a row, so a later run is approved.

    Stopping on the first zero would call the head clear while a run created
    just after it stays held with nothing to approve it.
    """
    stub = _run_after_a_zero_count_stub(tmp_path, '111', '222')

    result, out_file = _approve_for_sha(tmp_path, {'FAKE_PR_RUNS': '2'}, stub)

    assert result.stdout.splitlines() == [
        'approved run 111 for #12',
        'approved run 222 for #12',
    ]
    assert result.returncode == 0
    assert out_file.read_text(encoding='utf-8') == ''


def _lagging_gate_stub(tmp_path: Path, run_id: str, lag_polls: int) -> Path:
    """Write a stub gh where an approved run keeps reading action_required a while.

    The Actions API can still report a just-approved run as held for a poll or
    two after the approve call succeeds. `lag_polls` is how many listings keep
    reporting `run_id` as held before it clears, whatever approved it.

    Returns:
        The stub's path.
    """
    approve_calls = tmp_path / 'approve-calls.txt'
    approve_calls.write_text('', encoding='utf-8')
    polls = tmp_path / 'poll-count.txt'
    stub = tmp_path / 'lagging_gate_gh.py'
    stub.write_text(
        dedent(f"""\
            import os
            import sys
            from pathlib import Path

            args = sys.argv[1:]
            url = next((arg for arg in args if arg.startswith("repos/")), "")
            jq = args[args.index("--jq") + 1] if "--jq" in args else ""
            approve_calls = Path({str(approve_calls)!r})
            polls = Path({str(polls)!r})

            if url.endswith("/approve"):
                with approve_calls.open("a") as handle:
                    handle.write("{run_id}\\n")
                sys.stdout.write("{{}}\\n")
                raise SystemExit(0)
            if "/actions/runs?" in url and "event=pull_request" in url:
                sys.stdout.write(os.environ["FAKE_PR_RUNS"] + "\\n")
                raise SystemExit(0)
            if "/actions/runs?" in url and "status=action_required" in url:
                count = int(polls.read_text()) if polls.exists() else 0
                if jq != ".total_count":
                    count += 1
                    polls.write_text(str(count))
                lagging = count <= {lag_polls}
                if jq == ".total_count":
                    sys.stdout.write(("1" if lagging else "0") + "\\n")
                elif lagging:
                    fields = ["{run_id}", "{_CAUSED}", "{REPO}", "pull_request",
                              "{_BOT}", "action_required"]
                    sys.stdout.write("\\t".join(fields) + "\\n")
                raise SystemExit(0)
            raise SystemExit(f"stub gh: unexpected call: {{args}}")
            """),
        encoding='utf-8',
    )
    return stub


def test_a_still_gated_run_after_approval_is_not_reapproved(tmp_path: Path) -> None:
    """A run that still reads as held just after approval is approved once only.

    The sweep polls on a fixed interval regardless of that lag, so an id is
    recorded the moment it is approved; otherwise the next poll would approve
    the same run again and print a second "approved run" line for it.
    """
    stub = _lagging_gate_stub(tmp_path, '111', lag_polls=2)

    result, _ = _approve_for_sha(tmp_path, {'FAKE_PR_RUNS': '2'}, stub)

    assert result.stdout.count('approved run 111') == 1
    assert (tmp_path / 'approve-calls.txt').read_text(encoding='utf-8').split() == [
        '111'
    ]
    assert result.returncode == 0


def test_the_sourced_helper_leaves_nothing_behind_in_the_caller(tmp_path: Path) -> None:
    """Calling the helpers adds no variable to the scope that sourced them.

    approve-caused-runs.sh sources this file, so an assignment made without
    `local` inside one of its functions is a variable in that script, silent
    until it collides with one of the caller's own. That includes BASH_REMATCH,
    which a [[ =~ ]] test sets.
    """
    probe = '\n'.join(
        [
            'set +u',
            'before=; after=',
            f'source {shlex.quote(str(_LIBRARY))}',
            'set +eu',
            'before="$(compgen -v | sort)"',
            (
                'approvable "$(printf a%.0s {1..40})" "$(printf a%.0s {1..40})" '
                f'"{REPO}" "{REPO}" pull_request "{_BOT}" action_required || true'
            ),
            'approve_bot_gated_runs_for_sha "$(printf c%.0s {1..40})" 31 || true',
            'after="$(compgen -v | sort)"',
            'comm -13 <(printf "%s\\n" "$before") <(printf "%s\\n" "$after")',
        ]
    )

    result = subprocess.run(
        [BASH, '-c', probe],
        env=script_env(
            tmp_path,
            {
                'APPROVE_BOT_GATED_OUT': str(tmp_path / 'gated.txt'),
                'FAKE_PR_RUNS': '2',
                'FAKE_GATED_COUNT': '0',
            },
        ),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.stdout.split() == []


def test_sourcing_the_library_approves_nothing(tmp_path: Path) -> None:
    """Sourcing the file defines functions and makes no call, whatever the environment.

    The library once approved CAUSED_SHA when run with APPROVE_BOT_GATED_MAIN=1.
    That entry point named an arbitrary commit to approve, so it is gone.
    """
    result = subprocess.run(
        [BASH, '-c', f'source {shlex.quote(str(_LIBRARY))}'],
        env=script_env(
            tmp_path,
            {
                'APPROVE_BOT_GATED_MAIN': '1',
                'CAUSED_SHA': _CAUSED,
                'PR_NUMBER': '12',
                'FAKE_PR_RUNS': '1',
                'FAKE_GATED_COUNT': '1',
                'FAKE_GATED_TSV': _held('111'),
            },
        ),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == ''
    assert gh_calls(tmp_path) == []


def test_the_out_file_has_no_default(tmp_path: Path) -> None:
    """Without APPROVE_BOT_GATED_OUT, the function fails before any call."""
    script = (
        f'source {shlex.quote(str(_LIBRARY))}\n'
        f'approve_bot_gated_runs_for_sha {_CAUSED} 12\n'
    )

    result = subprocess.run(
        [BASH, '-c', script],
        env=script_env(tmp_path, {'FAKE_PR_RUNS': '1'}),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert 'APPROVE_BOT_GATED_OUT' in result.stderr
    assert gh_calls(tmp_path) == []


@pytest.mark.parametrize(
    'failure', ['head', 'list', 'count', 'head-not-a-number', 'count-not-a-number']
)
def test_a_failed_or_non_numeric_lookup_returns_3(tmp_path: Path, failure: str) -> None:
    """A lookup that fails, or a count that is not a number, returns 3, never 0 or 2.

    A failed lookup prints nothing, which would otherwise read as zero runs
    seen, sending the head to the silent report, or as zero runs held, passing it.
    """
    result, _ = _approve_for_sha(
        tmp_path,
        {'FAKE_PR_RUNS': '4', 'FAKE_GATED_COUNT': '0', 'FAKE_FAILURE': failure},
    )

    assert result.returncode == 3
