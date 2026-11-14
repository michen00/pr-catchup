"""Shared test helpers: run the scripts against the fake gh, and read the YAML files.

Each script runs the way action.yml runs it, as `bash scripts/<name>.sh` with its
inputs in the environment, and with the state directory, the fake's records, and
GITHUB_OUTPUT all under the test's tmp_path.
"""

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
FAKE_GH = Path(__file__).resolve().parent / 'fake_gh.py'
REPO = 'owner/repo'
MERGE_SHA = 'b' * 40
DEFAULT_MESSAGE = 'chore: merge {base} into #{number}'
WORKFLOW_REF = 'owner/repo/.github/workflows/catch-up.yml@refs/heads/main'
# PR_CATCHUP_TEST_BASH runs every script under another bash, such as /bin/bash 3.2.
BASH = os.environ.get('PR_CATCHUP_TEST_BASH', 'bash')

# Variables the scripts, the runner, or bash itself read, which each test sets or
# omits on purpose; a CI runner's own GITHUB_OUTPUT would otherwise receive output.
_ISOLATED_PREFIXES = ('GITHUB_', 'RUNNER_', 'FAKE_', 'PR_CATCHUP_', 'GH_')
_ISOLATED_NAMES = frozenset(
    {
        'APP_TOKEN',
        'APPROVE_BOT_GATED_MAIN',
        'APPROVE_BOT_GATED_OUT',
        'BASE',
        'BASHOPTS',
        'BASH_ENV',
        'CAUSED_SHA',
        'CDPATH',
        'COMMIT_MESSAGE',
        'ENV',
        'EXCLUDE_LABELS',
        'PR_NUMBER',
        'REPO',
        'SHELLOPTS',
    }
)

type EnvOverrides = dict[str, str | None]


def state_dir(tmp_path: Path) -> Path:
    """The directory the scripts pass state through (PR_CATCHUP_DIR)."""
    return tmp_path / 'pr-catchup'


def fake_state(tmp_path: Path) -> Path:
    """The directory the fake gh records its calls in (FAKE_STATE)."""
    return tmp_path / 'fake-gh'


def gh_on_path(tmp_path: Path, stub: Path = FAKE_GH) -> Path:
    """Write bin/gh, which runs `stub` with this interpreter, and bin/sleep.

    The scripts poll with the external `sleep`, so bin/sleep returns at once and
    records each interval it was asked for under FAKE_STATE.

    Returns:
        The bin directory, to put first on PATH.
    """
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir(exist_ok=True)
    wrapper = bin_dir / 'gh'
    command = f'exec {shlex.quote(sys.executable)} {shlex.quote(str(stub))} "$@"'
    wrapper.write_text(f'#!/bin/sh\n{command}\n', encoding='utf-8')
    wrapper.chmod(0o755)
    sleep = bin_dir / 'sleep'
    sleep.write_text(
        '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "${FAKE_STATE:?}/sleeps.txt"\n',
        encoding='utf-8',
    )
    sleep.chmod(0o755)
    return bin_dir


def script_env(
    tmp_path: Path, overrides: EnvOverrides | None = None, stub: Path = FAKE_GH
) -> dict[str, str]:
    """Return the environment a script step sees, with the fake gh first on PATH.

    An override of None removes that variable.

    Returns:
        The environment, without anything the runner or the caller's shell set
        for the scripts.
    """
    env = {
        name: value
        for name, value in os.environ.items()
        if name not in _ISOLATED_NAMES and not name.startswith(_ISOLATED_PREFIXES)
    }
    state_dir(tmp_path).mkdir(exist_ok=True)
    fake_state(tmp_path).mkdir(exist_ok=True)
    env.update(
        PATH=f'{gh_on_path(tmp_path, stub)}{os.pathsep}{os.environ["PATH"]}',
        FAKE_STATE=str(fake_state(tmp_path)),
        PR_CATCHUP_DIR=str(state_dir(tmp_path)),
        REPO=REPO,
        GH_TOKEN='default-token',
    )
    for name, value in (overrides or {}).items():
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value
    return env


def run_script(
    name: str,
    tmp_path: Path,
    overrides: EnvOverrides | None = None,
    stub: Path = FAKE_GH,
) -> subprocess.CompletedProcess[str]:
    """Run scripts/<name> as action.yml runs it."""
    return subprocess.run(
        [BASH, str(SCRIPTS / name)],
        env=script_env(tmp_path, overrides, stub),
        capture_output=True,
        text=True,
        check=False,
    )


def catch_up(  # noqa: PLR0913 -- one keyword per input and per way a merge call answers
    tmp_path: Path,
    *,
    prs: str = '31',
    current: str = '',
    conflicting: str = '',
    withheld: str = '',
    refused: str = '',
    list_fails: bool = False,
    unknown_polls: int = 0,
    pull_requests: list[dict[str, object]] | None = None,
    app_token: str = '',
    app_refused: str = '',
    base: str = 'main',
    commit_message: str = DEFAULT_MESSAGE,
    exclude_labels: str = '',
    env: EnvOverrides | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run catch-up.sh, naming how the merge call answers each pull request.

    A pull request named in none of `current`, `conflicting`, `withheld`, or
    `refused` is merged and moves to MERGE_SHA. A `withheld` pull request gets the
    same 409 as a `conflicting` one, but GitHub reports it as mergeable.
    `app_refused` is the HTTP status the App token's merges get, if any, and
    `unknown_polls` is how many mergeable reads answer UNKNOWN first.

    Returns:
        The finished catch-up.sh process.
    """
    output = tmp_path / 'github-output'
    output.touch()
    overrides: EnvOverrides = {
        'FAKE_PRS': prs,
        'FAKE_CURRENT': current,
        'FAKE_CONFLICTING': conflicting,
        'FAKE_WITHHELD': withheld,
        'FAKE_REFUSED': refused,
        'FAKE_LIST_FAILS': 'yes' if list_fails else 'no',
        'FAKE_UNKNOWN_POLLS': str(unknown_polls),
        'FAKE_APP_REFUSED': app_refused,
        'APP_TOKEN': app_token,
        'BASE': base,
        'COMMIT_MESSAGE': commit_message,
        'EXCLUDE_LABELS': exclude_labels,
        'GITHUB_OUTPUT': str(output),
    }
    if pull_requests is not None:
        overrides['FAKE_PULL_REQUESTS'] = json.dumps(pull_requests)
    return run_script('catch-up.sh', tmp_path, overrides | (env or {}))


def approve_caused_runs(  # noqa: PLR0913 -- one keyword per answer the fake gives
    tmp_path: Path,
    merges: str | None,
    *,
    pr_runs: str = '0',
    all_runs: str = '0',
    gated_count: str = '0',
    gated_tsv: str = '',
    failure: str = '',
    approve_fails: bool = False,
    env: EnvOverrides | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run approve-caused-runs.sh after catch-up.sh queued `merges` ('num sha' lines).

    None leaves the queue file missing, as when catch-up.sh never ran.

    Returns:
        The finished approve-caused-runs.sh process.
    """
    state_dir(tmp_path).mkdir(exist_ok=True)
    if merges is not None:
        write_state(tmp_path, 'default-token-merges.txt', merges)
    overrides: EnvOverrides = {
        'FAKE_PR_RUNS': pr_runs,
        'FAKE_ALL_RUNS': all_runs,
        'FAKE_GATED_COUNT': gated_count,
        'FAKE_GATED_TSV': gated_tsv,
        'FAKE_FAILURE': failure,
        'FAKE_APPROVE_FAILS': 'yes' if approve_fails else 'no',
    }
    return run_script('approve-caused-runs.sh', tmp_path, overrides | (env or {}))


def report(  # noqa: PLR0913 -- one keyword per state file and per input
    tmp_path: Path,
    *,
    refused: str = '',
    blind: str = '',
    gated: str = '',
    silent: str = '',
    base: str = 'main',
    commit_message: str = DEFAULT_MESSAGE,
    workflow_ref: str | None = WORKFLOW_REF,
    env: EnvOverrides | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run report.sh over the state files the two earlier steps leave behind."""
    state_dir(tmp_path).mkdir(exist_ok=True)
    write_state(tmp_path, 'refused.txt', refused)
    write_state(tmp_path, 'blind.txt', blind)
    write_state(tmp_path, 'gated.txt', gated)
    write_state(tmp_path, 'silent.txt', silent)
    overrides: EnvOverrides = {
        'BASE': base,
        'COMMIT_MESSAGE': commit_message,
        'GITHUB_WORKFLOW_REF': workflow_ref,
    }
    return run_script('report.sh', tmp_path, overrides | (env or {}))


def write_state(tmp_path: Path, name: str, text: str) -> None:
    """Write one state file, as an earlier step would."""
    (state_dir(tmp_path) / name).write_text(text, encoding='utf-8')


def read_state(tmp_path: Path, name: str) -> str:
    """Read one state file a script wrote."""
    return (state_dir(tmp_path) / name).read_text(encoding='utf-8')


def _records(tmp_path: Path, name: str) -> list[Any]:
    path = fake_state(tmp_path) / name
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def merge_calls(tmp_path: Path) -> list[dict[str, str]]:
    """Each merge call: its base, head, commit_message, and the token it carried."""
    return _records(tmp_path, 'merges.jsonl')


def gh_calls(tmp_path: Path) -> list[list[str]]:
    """The arguments of every gh call, in order."""
    return _records(tmp_path, 'calls.jsonl')


def approvals(tmp_path: Path) -> list[str]:
    """The run ids the scripts asked to approve, in order."""
    return _records(tmp_path, 'approvals.jsonl')


def sleeps(tmp_path: Path) -> list[str]:
    """The arguments of every `sleep` the scripts ran, in order."""
    path = fake_state(tmp_path) / 'sleeps.txt'
    if not path.exists():
        return []
    return path.read_text(encoding='utf-8').splitlines()


def github_output(tmp_path: Path) -> str:
    """What catch-up.sh appended to GITHUB_OUTPUT."""
    return (tmp_path / 'github-output').read_text(encoding='utf-8')


def expressions_in_run_bodies(steps: list[dict[str, Any]]) -> list[str]:
    """Return the run: body of each step that holds a ${{ expression."""
    return [step['run'] for step in steps if '${{' in str(step.get('run') or '')]


# A reader for the YAML subset action.yml and the workflows use: a leading document
# start, block mappings, block sequences, plain and quoted scalars, literal and folded
# block scalars, an empty flow mapping, and one-line flow sequences of scalars.
# Anything else raises ValueError rather than being read wrongly.

_KEY = re.compile(r'([A-Za-z0-9_][A-Za-z0-9_.-]*):(?:[ \t]+(.*))?$')
_BLOCK_HEADER = re.compile(r'([|>])([+-]?)$')


def load_yaml(text: str) -> dict[str, Any]:
    """Parse a document in the YAML subset action.yml and the workflows use.

    Returns:
        The document's top-level mapping. Values are dicts, lists, and strings; no
        scalar is typed.

    Raises:
        ValueError: The document is empty or uses YAML outside the subset.
        TypeError: The document is not a mapping.
    """
    if text.startswith('---\n'):
        # Blanking the marker rather than dropping its line keeps line numbers.
        text = text[len('---') :]
    reader = _YamlReader(text)
    indent = reader.next_indent()
    if indent is None:
        msg = 'empty document'
        raise ValueError(msg)
    document = reader.node(indent)
    if not isinstance(document, dict):
        msg = 'the document is not a mapping'
        raise TypeError(msg)
    if reader.next_indent() is not None:
        msg = f'unexpected content at line {reader.index + 1}'
        raise ValueError(msg)
    return document


def read_action() -> dict[str, Any]:
    """Parse action.yml."""
    return load_yaml((ROOT / 'action.yml').read_text(encoding='utf-8'))


def _flow_sequence(text: str) -> list[str]:
    """Read a one-line flow sequence of scalars, such as [main] or ['*']."""
    inner = text[1:-1].strip()
    if not inner:
        return []
    items = [item.strip() for item in inner.split(',')]
    if any(not item or any(char in item for char in '[]{}') for item in items):
        msg = f'unsupported flow sequence: {text}'
        raise ValueError(msg)
    return [_scalar(item) for item in items]


def _scalar(text: str) -> str:
    if text.startswith("'"):
        end = text.find("'", 1)
        while end != -1 and text[end + 1 : end + 2] == "'":
            end = text.find("'", end + 2)
        if end == -1 or _strip_comment(text[end + 1 :]):
            msg = f'unsupported single-quoted scalar: {text}'
            raise ValueError(msg)
        return text[1:end].replace("''", "'")
    if text.startswith('"'):
        decoded, end = json.JSONDecoder().raw_decode(text)
        if _strip_comment(text[end:]):
            msg = f'unsupported double-quoted scalar: {text}'
            raise ValueError(msg)
        return decoded
    if text[0] in '[]{}&*!%@`|>':
        msg = f'unsupported YAML: {text}'
        raise ValueError(msg)
    return _strip_comment(text)


def _strip_comment(text: str) -> str:
    text = text.strip()
    if text.startswith('#'):
        return ''
    cut = text.find(' #')
    return text if cut == -1 else text[:cut].rstrip()


def _fold(lines: list[str]) -> str:
    """Join a folded block scalar's lines as YAML does.

    Returns:
        The lines joined: a break between two lines of text is a space, and each blank
        line between them a break. Breaks next to a more-indented line, one that
        starts with whitespace, are kept.
    """
    text = ''
    previous = None
    blanks = 0
    for line in lines:
        if not line:
            blanks += 1
            continue
        if previous is None:
            text += '\n' * blanks
        elif previous[0] in ' \t' or line[0] in ' \t':
            text += '\n' * (blanks + 1)
        else:
            text += '\n' * blanks if blanks else ' '
        text += line
        previous = line
        blanks = 0
    return text


class _YamlReader:
    def __init__(self, text: str) -> None:
        self.lines = text.split('\n')
        self.index = 0

    def next_indent(self) -> int | None:
        """Skip blank and comment lines; return the next line's indentation."""
        while self.index < len(self.lines):
            line = self.lines[self.index]
            stripped = line.strip()
            if stripped and not stripped.startswith('#'):
                if '\t' in line[: len(line) - len(line.lstrip())]:
                    msg = f'tab indentation at line {self.index + 1}'
                    raise ValueError(msg)
                return len(line) - len(line.lstrip(' '))
            self.index += 1
        return None

    def node(self, indent: int) -> object:
        content = self.lines[self.index][indent:]
        if content == '-' or content.startswith('- '):
            return self.sequence(indent)
        return self.mapping(indent)

    def mapping(self, indent: int) -> dict[str, object]:
        result: dict[str, object] = {}
        while (found := self.next_indent()) == indent:
            match = _KEY.match(self.lines[self.index][indent:])
            if match is None:
                msg = f'expected a mapping key at line {self.index + 1}'
                raise ValueError(msg)
            key, rest = match.group(1), (match.group(2) or '').strip()
            if key in result:
                msg = f'duplicate key {key!r} at line {self.index + 1}'
                raise ValueError(msg)
            self.index += 1
            result[key] = self.value(rest, indent)
        if found is not None and found > indent:
            msg = f'unexpected indentation at line {self.index + 1}'
            raise ValueError(msg)
        return result

    def sequence(self, indent: int) -> list[object]:
        items: list[object] = []
        while self.next_indent() == indent:
            content = self.lines[self.index][indent:]
            if not (content == '-' or content.startswith('- ')):
                break
            stripped = content[1:].lstrip(' ')
            if not stripped:
                self.index += 1
                nested = self.next_indent()
                if nested is None or nested <= indent:
                    msg = f'empty sequence item at line {self.index}'
                    raise ValueError(msg)
                items.append(self.node(nested))
            elif _KEY.match(stripped):
                item_indent = indent + len(content) - len(stripped)
                self.lines[self.index] = ' ' * item_indent + stripped
                items.append(self.mapping(item_indent))
            else:
                self.index += 1
                items.append(_scalar(stripped))
        return items

    def value(self, rest: str, indent: int) -> object:
        bare = _strip_comment(rest)
        if not bare:
            nested = self.next_indent()
            if nested is not None and nested > indent:
                return self.node(nested)
            return None
        header = _BLOCK_HEADER.match(bare)
        if header:
            return self.block_scalar(header.group(1), header.group(2), indent)
        if bare == '{}':
            return {}
        if bare.startswith('[') and bare.endswith(']'):
            return _flow_sequence(bare)
        return _scalar(rest)

    def block_scalar(self, style: str, chomping: str, indent: int) -> str:
        body: list[str] = []
        blank_lines: list[int] = []
        body_indent = None
        while self.index < len(self.lines):
            line = self.lines[self.index]
            if not line.strip():
                body.append('')
                blank_lines.append(self.index)
                self.index += 1
                continue
            found = len(line) - len(line.lstrip(' '))
            if found <= indent:
                break
            if body_indent is None:
                body_indent = found
            elif found < body_indent:
                msg = f'block scalar dedent at line {self.index + 1}'
                raise ValueError(msg)
            body.append(line[body_indent:])
            self.index += 1
        for index in blank_lines:
            # Past the indentation, or holding a tab, a blank-looking line is content.
            line = self.lines[index]
            if body_indent is not None and ('\t' in line or len(line) > body_indent):
                msg = f'unsupported whitespace-only line at line {index + 1}'
                raise ValueError(msg)
        trailing = 0
        while body and not body[-1]:
            body.pop()
            trailing += 1
        text = '\n'.join(body) if style == '|' else _fold(body)
        if not body or chomping == '-':
            return text
        return text + '\n' * (1 + trailing if chomping == '+' else 1)
