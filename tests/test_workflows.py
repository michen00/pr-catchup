"""Contract tests for the workflows that call the action.

A composite action cannot set its own triggers, permissions, concurrency, timeout,
or job condition, so the workflow that calls it sets them. These tests read this
repository's own catch-up workflow and the two example workflows in README.md,
which callers copy, and the run: bodies of every workflow in the repository.
"""

import re
from pathlib import Path
from typing import Any

import pytest

from tests.harness import ROOT, expressions_in_run_bodies, load_yaml

_WORKFLOW_DIR = ROOT / '.github' / 'workflows'
_README = (ROOT / 'README.md').read_text(encoding='utf-8')
_DISPATCH_GUARD = (
    "github.event_name != 'workflow_dispatch' || github.ref == "
    "format('refs/heads/{0}', github.event.repository.default_branch)"
)
_MINT_ACTION = 'actions/create-github-app-token@'


def _readme_workflows() -> dict[str, dict[str, Any]]:
    """Return the README's example workflows: the minimal one, then the App one."""
    blocks = re.findall(
        r'^```yaml\n(.*?)^```$', _README, flags=re.MULTILINE | re.DOTALL
    )
    minimal, app = [load_yaml(block) for block in blocks if '\njobs:\n' in block]
    return {'readme-minimal': minimal, 'readme-app': app}


_CALLERS: dict[str, dict[str, Any]] = {
    'repository': load_yaml(
        (_WORKFLOW_DIR / 'pr-catchup.yml').read_text(encoding='utf-8')
    ),
    **_readme_workflows(),
}
_APP_CALLERS = ['repository', 'readme-app']


def _job(name: str) -> dict[str, Any]:
    """Return the one job of a calling workflow."""
    [job] = _CALLERS[name]['jobs'].values()
    return job


def _app_token_step(name: str) -> dict[str, Any]:
    """Return the step that mints the App token."""
    [step] = [
        step
        for step in _job(name)['steps']
        if str(step.get('uses', '')).startswith(_MINT_ACTION)
    ]
    return step


@pytest.mark.parametrize('name', list(_CALLERS))
def test_a_dispatch_is_honored_only_from_the_default_branch(name: str) -> None:
    """A manual dispatch runs the job only from the default branch.

    A dispatched run loads the workflow file from the ref it was dispatched from,
    while push events to the base arrive only from the default branch, so a
    manual run uses the same workflow file as a push.
    """
    assert _job(name)['if'] == _DISPATCH_GUARD


@pytest.mark.parametrize('name', list(_CALLERS))
def test_permissions_cover_approval_and_stay_least_privilege(name: str) -> None:
    """The job gets exactly what merging and approving need; the workflow, nothing."""
    assert _CALLERS[name]['permissions'] == {}
    assert _job(name)['permissions'] == {
        'actions': 'write',
        'contents': 'write',
        'pull-requests': 'read',
    }


@pytest.mark.parametrize('name', list(_CALLERS))
def test_a_push_to_the_base_and_a_dispatch_start_it_and_no_timer(name: str) -> None:
    """A push to main and a manual dispatch start the workflow, and no schedule does."""
    triggers = _CALLERS[name]['on']

    assert sorted(triggers) == ['push', 'workflow_dispatch']
    assert triggers['push'] == {'branches': ['main']}


@pytest.mark.parametrize('name', list(_CALLERS))
def test_runs_queue_without_cancellation_under_a_time_limit(name: str) -> None:
    """One concurrency group, never canceled, and a 15-minute job timeout.

    A canceled run could stop after a merge and before approving the runs that
    merge caused, leaving them held with no failure reported.
    """
    assert _CALLERS[name]['concurrency'] == {
        'group': 'pr-catchup',
        'cancel-in-progress': 'false',
    }
    assert _job(name)['timeout-minutes'] == '15'


@pytest.mark.parametrize('name', _APP_CALLERS)
def test_the_app_token_asks_only_for_what_the_merge_needs(name: str) -> None:
    """The App token serves only the merges endpoint, which needs Contents write."""
    inputs = _app_token_step(name)['with']

    assert {key: value for key, value in inputs.items() if 'permission' in key} == {
        'permission-contents': 'write'
    }
    assert _app_token_step(name)['continue-on-error'] == 'true'


@pytest.mark.parametrize('name', _APP_CALLERS)
def test_the_app_key_comes_from_an_environment(name: str) -> None:
    """The job that reads the App key names the environment that holds it.

    A repository or organization secret reaches a workflow pushed on any branch;
    an environment secret reaches only jobs that name the environment and run
    from a branch it admits.
    """
    assert _job(name)['environment'] == 'pr-catchup'
    assert _app_token_step(name)['with']['private-key'] == (
        '${{ secrets.APP_PRIVATE_KEY }}'
    )


@pytest.mark.parametrize(
    'path', sorted(_WORKFLOW_DIR.glob('*.y*ml')), ids=lambda path: path.name
)
def test_no_workflow_run_body_contains_an_expression(path: Path) -> None:
    """No ${{ }} expression appears in any workflow's run: body.

    Actions substitutes an expression into the script text before the shell
    parses it, and actionlint flags only the contexts it knows to be untrusted,
    so a ref name or a dispatch input spliced in that way would pass it.
    """
    workflow = load_yaml(path.read_text(encoding='utf-8'))
    steps = [step for job in workflow['jobs'].values() for step in job.get('steps', [])]

    assert expressions_in_run_bodies(steps) == []


def test_the_readme_pins_no_action_to_a_commit() -> None:
    """The README's examples carry placeholders, not commit SHAs.

    Dependabot updates the pins in workflow and action files only, so a SHA
    written into the README would fall behind the one the workflows run.
    """
    assert re.findall(r'@[0-9a-f]{40}\b', _README) == []


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('---\na: b\n', {'a': 'b'}),
        ('a: [main]\n', {'a': ['main']}),
        ("a: [pre-commit, 'x y'] # c\n", {'a': ['pre-commit', 'x y']}),
        ('a: []\n', {'a': []}),
        ('a: {}\n', {'a': {}}),
    ],
    ids=[
        'document-start',
        'flow-sequence',
        'flow-sequence-quoted',
        'empty-flow-sequence',
        'empty-flow-mapping',
    ],
)
def test_the_reader_takes_the_flow_forms_the_workflows_use(
    text: str, expected: dict[str, Any]
) -> None:
    """The YAML reader takes the few flow forms the workflows use."""
    assert load_yaml(text) == expected


@pytest.mark.parametrize(
    'text', ['a: [[x]]\n', 'a: [x, ]\n', 'a: {b: c}\n', "a: ['x, y']\n"]
)
def test_the_reader_rejects_other_flow_forms(text: str) -> None:
    """Any other flow form raises rather than being read wrongly."""
    with pytest.raises(ValueError, match='unsupported'):
        load_yaml(text)
