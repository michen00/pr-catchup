"""A stand-in for the gh CLI that answers the calls the action's scripts make.

harness.gh_on_path puts a `gh` wrapper first on PATH that runs this file. It
answers from FAKE_* environment variables, records every call under FAKE_STATE,
and exits nonzero on a call it was not taught, so an unexpected request fails
loudly rather than reading as an empty result.

Each outcome is presented the way gh presents it: a merge prints the new SHA; a
branch that already carries the base prints nothing, because the endpoint
answers 204 with an empty body; and a refusal prints only a trailing
"(HTTP nnn)" on stderr.

`gh pr list` keeps only the fields named by --json and evaluates the --jq
program with the system jq, which reads $ENV the way gh does, so the script's
own filter decides which pull requests are listed.

With FAKE_RECORD_ONLY=yes, every call is recorded and answered with nothing.
"""

import itertools
import json
import os
import subprocess
import sys
from pathlib import Path

MERGE_SHA = 'b' * 40
# approve_bot_gated_runs_for_sha lists the held runs once per sweep, ten sweeps at
# most, and once more after the last sweep while any run stays held.
SWEEPS = 10


def env(name: str) -> str:
    """Read one FAKE_* setting, or any other variable gh would inherit."""
    return os.environ.get(name, '')


def env_list(name: str) -> list[str]:
    """Read a space-separated FAKE_* setting."""
    return env(name).split()


def out(text: str) -> None:
    """Print one line on stdout."""
    sys.stdout.write(text + '\n')


def refuse(message: str, status: str) -> None:
    """Exit the way gh does on an HTTP error: the status only on stderr.

    Raises:
        SystemExit: Always, with status 1.
    """
    sys.stderr.write(f'gh: {message} (HTTP {status})\n')
    raise SystemExit(1)


def record(state: Path, name: str, entry: object) -> None:
    """Append one JSON line to a record file under FAKE_STATE."""
    with (state / name).open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(entry) + '\n')


def pull_requests() -> list[dict[str, object]]:
    """The open pull requests: FAKE_PULL_REQUESTS as JSON, or built from FAKE_PRS."""
    if env('FAKE_PULL_REQUESTS'):
        return json.loads(env('FAKE_PULL_REQUESTS'))
    return [
        {
            'number': int(number),
            'headRefName': f'feat/pr-{number}',
            'isCrossRepository': False,
            'mergeable': 'MERGEABLE',
            'labels': [],
        }
        for number in env_list('FAKE_PRS')
    ]


def option(args: list[str], name: str) -> str:
    """The value that follows a flag, or an empty string."""
    return args[args.index(name) + 1] if name in args else ''


def jq(program: str, document: object) -> subprocess.CompletedProcess[str]:
    """Evaluate a --jq program the way gh does, printing strings raw."""
    return subprocess.run(
        ['jq', '-r', program],
        input=json.dumps(document),
        capture_output=True,
        text=True,
        check=False,
    )


def pr_list(args: list[str]) -> None:
    """Answer `gh pr list` by running its own --jq over the fields it asked for.

    Raises:
        SystemExit: With jq's exit status.
    """
    if env('FAKE_LIST_FAILS') == 'yes':
        refuse('API rate limit exceeded', '403')
    wanted = option(args, '--json').split(',')
    listing = [{key: pr[key] for key in wanted} for pr in pull_requests()]
    filtered = jq(option(args, '--jq') or '.', listing)
    sys.stdout.write(filtered.stdout)
    sys.stderr.write(filtered.stderr)
    raise SystemExit(filtered.returncode)


def pr_view(args: list[str], state: Path) -> None:
    """Answer `gh pr view N --json mergeable --jq .mergeable`."""
    views = state / 'pr-view-count'
    count = int(views.read_text(encoding='utf-8')) if views.exists() else 0
    views.write_text(str(count + 1), encoding='utf-8')
    if count < int(env('FAKE_UNKNOWN_POLLS') or '0'):
        out('UNKNOWN')
    elif args[2] in env_list('FAKE_CONFLICTING'):
        out('CONFLICTING')
    else:
        out('MERGEABLE')


def merge(args: list[str], state: Path) -> None:
    """Answer POST repos/R/merges for the pull request whose head is `base`."""
    fields = {}
    for flag, value in itertools.pairwise(args):
        if flag == '-f':
            key, _, field = value.partition('=')
            fields[key] = field
    token = env('GH_TOKEN')
    record(state, 'merges.jsonl', {**fields, 'token': token})
    number = next(
        (
            str(pr['number'])
            for pr in pull_requests()
            if pr['headRefName'] == fields['base']
        ),
        '',
    )
    if env('APP_TOKEN') and token == env('APP_TOKEN') and env('FAKE_APP_REFUSED'):
        refuse('App denied', env('FAKE_APP_REFUSED'))
    if number in env_list('FAKE_CONFLICTING') + env_list('FAKE_WITHHELD'):
        refuse('Merge conflict', '409')
    if number in env_list('FAKE_REFUSED'):
        refuse('Resource not accessible by integration', '403')
    if number not in env_list('FAKE_CURRENT'):
        out(MERGE_SHA)


def approve(url: str, state: Path) -> None:
    """Answer POST repos/R/actions/runs/ID/approve."""
    record(state, 'approvals.jsonl', url.rsplit('/', 2)[1])
    if env('FAKE_APPROVE_FAILS') == 'yes':
        refuse('Resource not accessible by integration', '403')
    out('{}')


def gated_from_runs(program: str, state: Path) -> None:
    """Answer the held-run listing from FAKE_GATED_RUNS, without approved runs."""
    approvals = state / 'approvals.jsonl'
    approved = (
        [json.loads(line) for line in approvals.read_text().splitlines()]
        if approvals.exists()
        else []
    )
    held = [
        run
        for run in json.loads(env('FAKE_GATED_RUNS'))
        if str(run['id']) not in approved
    ]
    answer = jq(program, {'total_count': len(held), 'workflow_runs': held})
    sys.stdout.write(answer.stdout)


def runs(url: str, program: str, state: Path) -> None:
    """Answer the GET repos/R/actions/runs?... listings and counts.

    FAKE_FAILURE names one lookup that fails: head, list, or count;
    head-not-a-number or count-not-a-number for a count that reads null; or
    final-list for the listing after the last sweep, which fails after printing.
    """
    failure = env('FAKE_FAILURE')
    counting = program == '.total_count'
    if 'status=action_required' in url:
        listings = state / 'gated-listings'
        listed = int(listings.read_text(encoding='utf-8')) if listings.exists() else 0
        if not counting:
            listings.write_text(str(listed + 1), encoding='utf-8')
        if failure == ('count' if counting else 'list'):
            refuse('lookup failed', '503')
        if counting and failure == 'count-not-a-number':
            out('null')
        elif env('FAKE_GATED_RUNS'):
            gated_from_runs(program, state)
        elif counting:
            out(env('FAKE_GATED_COUNT') or '0')
        else:
            sys.stdout.write(env('FAKE_GATED_TSV'))
            sys.stdout.flush()
            if failure == 'final-list' and listed >= SWEEPS:
                refuse('lookup failed', '503')
    elif 'event=pull_request' in url:
        if failure == 'head':
            refuse('lookup failed', '503')
        out('null' if failure == 'head-not-a-number' else env('FAKE_PR_RUNS') or '0')
    else:
        out(env('FAKE_ALL_RUNS') or '0')


def main() -> None:
    """Record the call, then answer it.

    Raises:
        SystemExit: On a call this fake was not taught.
    """
    args = sys.argv[1:]
    state = Path(os.environ['FAKE_STATE'])
    record(state, 'calls.jsonl', args)
    url = next((arg for arg in args if arg.startswith('repos/')), '')

    if env('FAKE_RECORD_ONLY') == 'yes':
        return
    if args[:2] == ['pr', 'list']:
        pr_list(args)
    elif args[:2] == ['pr', 'view']:
        pr_view(args, state)
    elif args[:1] == ['api'] and url.endswith('/merges'):
        merge(args, state)
    elif args[:1] == ['api'] and url.endswith('/approve'):
        approve(url, state)
    elif args[:1] == ['api'] and '/actions/runs?' in url:
        runs(url, option(args, '--jq'), state)
    else:
        msg = f'fake gh: unexpected call: {args}'
        raise SystemExit(msg)


if __name__ == '__main__':
    main()
