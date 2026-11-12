"""Contract tests for action.yml.

action.yml is a composite action of three bash steps, each running one script
under scripts/ with every input passed through env. These tests read properties
of the definition that a later edit could remove without any script test going
red. A minimal reader in harness.py parses the file, so the tests need no YAML
library.
"""

import re
from typing import Any

import pytest

from tests.harness import SCRIPTS, expressions_in_run_bodies, load_yaml, read_action

_ACTION = read_action()
_STEPS: list[dict[str, Any]] = _ACTION['runs']['steps']
_SCRIPT_RUN = re.compile(r'bash "\$GITHUB_ACTION_PATH/scripts/([a-z-]+\.sh)"')


def _script_of(step: dict[str, Any]) -> str | None:
    """Return the script a step runs, or None if its run: body does something else."""
    match = _SCRIPT_RUN.fullmatch(str(step.get('run') or '').strip())
    return match.group(1) if match else None


def _step_running(script: str) -> dict[str, Any]:
    """Return the one step that runs scripts/<script>."""
    [step] = [step for step in _STEPS if _script_of(step) == script]
    return step


def test_no_run_body_contains_an_expression() -> None:
    """No ${{ }} expression appears in a run: body, so no input is spliced into bash.

    Actions substitutes an expression into the script text before bash parses
    it, so a branch name or label that reached a run: body that way could run as
    code. Each value reaches the scripts as an environment variable instead.
    """
    assert expressions_in_run_bodies(_STEPS) == []


def test_the_action_is_composite_bash_with_no_other_actions() -> None:
    """The action runs bash and the runner's gh: no Docker, Node, or nested action."""
    assert _ACTION['runs']['using'] == 'composite'
    assert [step['shell'] for step in _STEPS] == ['bash', 'bash', 'bash']
    assert [step for step in _STEPS if 'uses' in step] == []


def test_the_steps_run_the_scripts_in_order() -> None:
    """The steps merge, then approve, then report, each by running one script."""
    assert [_script_of(step) for step in _STEPS] == [
        'catch-up.sh',
        'approve-caused-runs.sh',
        'report.sh',
    ]


def test_every_script_the_action_runs_or_sources_ships_with_it() -> None:
    """Each script a step runs, and each file a script sources, is under scripts/.

    The scripts source each other by paths relative to their own directory, so a
    renamed or missing library fails every caller at run time.
    """
    ran = {script for step in _STEPS if (script := _script_of(step)) is not None}
    assert len(ran) == len(_STEPS), 'each step runs its own script'
    sourced = {
        match.group(1)
        for path in SCRIPTS.glob('*.sh')
        for match in re.finditer(
            r'^\s*source "\$\(dirname "\$\{BASH_SOURCE\[0\]\}"\)/([a-z-]+\.sh)"',
            path.read_text(encoding='utf-8'),
            flags=re.MULTILINE,
        )
    }

    assert sourced == {'approve-bot-gated-runs.sh', 'render-message.sh'}
    assert (
        sorted(name for name in ran | sourced if not (SCRIPTS / name).is_file()) == []
    )


def test_each_step_passes_the_environment_its_script_reads() -> None:
    """Each step maps exactly the inputs and context its script reads into env.

    The approval step carries github-token, never token: approving held runs
    needs actions write, and token is asked for Contents write only.
    """
    assert _step_running('catch-up.sh')['env'] == {
        'GH_TOKEN': '${{ inputs.github-token }}',
        'APP_TOKEN': '${{ inputs.token }}',
        'REPO': '${{ github.repository }}',
        'BASE': '${{ inputs.base }}',
        'COMMIT_MESSAGE': '${{ inputs.commit-message }}',
        'EXCLUDE_LABELS': '${{ inputs.exclude-labels }}',
    }
    assert _step_running('approve-caused-runs.sh')['env'] == {
        'GH_TOKEN': '${{ inputs.github-token }}',
        'REPO': '${{ github.repository }}',
    }
    assert _step_running('report.sh')['env'] == {
        'REPO': '${{ github.repository }}',
        'BASE': '${{ inputs.base }}',
        'COMMIT_MESSAGE': '${{ inputs.commit-message }}',
    }


def test_every_input_reaches_a_script_through_env() -> None:
    """No input is declared and then dropped."""
    passed = {value for step in _STEPS for value in step.get('env', {}).values()}

    assert (
        sorted(
            name
            for name in _ACTION['inputs']
            if f'${{{{ inputs.{name} }}}}' not in passed
        )
        == []
    )


def test_no_input_names_a_sha_to_approve() -> None:
    """The inputs are the five decided ones, and none names a commit.

    Approving held runs with github-token is privileged, and it is safe only for
    a commit whose content the action itself generated. So the action approves
    runs only at merge commits it created in the same run.
    """
    assert sorted(_ACTION['inputs']) == [
        'base',
        'commit-message',
        'exclude-labels',
        'github-token',
        'token',
    ]


def test_the_inputs_default_to_the_values_callers_rely_on() -> None:
    """Without inputs, the action merges the default branch with github-token."""
    inputs = _ACTION['inputs']

    assert inputs['base']['default'] == '${{ github.event.repository.default_branch }}'
    assert inputs['github-token']['default'] == '${{ github.token }}'
    assert inputs['token']['default'] == ''
    assert inputs['exclude-labels']['default'] == ''
    assert {name: spec['required'] for name, spec in inputs.items()} == dict.fromkeys(
        inputs, 'false'
    )


def test_the_token_inputs_name_the_permissions_they_need() -> None:
    """Each token input's description says what its caller has to grant.

    A composite action cannot declare permissions; the calling workflow's job
    grants them, and a missing one surfaces only as a refusal at run time.
    """
    github_token = ' '.join(_ACTION['inputs']['github-token']['description'].split())
    token = ' '.join(_ACTION['inputs']['token']['description'].split())

    assert 'actions write' in github_token
    assert 'contents write' in github_token
    assert 'pull-requests read' in github_token
    assert 'Contents write' in token


def test_the_updated_output_comes_from_the_catch_up_step() -> None:
    """The updated output reads the step that runs catch-up.sh."""
    assert _ACTION['outputs']['updated']['value'] == (
        '${{ steps.catch-up.outputs.updated }}'
    )
    assert _step_running('catch-up.sh')['id'] == 'catch-up'


@pytest.mark.parametrize(
    ('run', 'flagged'),
    [
        ('run: echo "${{ inputs.base }}"', True),
        ("run: 'echo ${{ inputs.base }}'", True),
        ('run: |\n    echo start\n    echo "${{ github.head_ref }}"', True),
        ('run: >-\n    echo\n    ${{ inputs.base }}', True),
        ('run: |-\n    echo start\n\n    echo "${{ inputs.base }}"', True),
        ('run: bash "$GITHUB_ACTION_PATH/scripts/catch-up.sh"', False),
        ('run: |\n    echo "$BASE"  # ${BASE} is env', False),
    ],
    ids=[
        'plain',
        'single-quoted',
        'literal-block',
        'folded-block',
        'literal-block-after-blank-line',
        'script-call',
        'env-variable',
    ],
)
def test_the_expression_check_reads_every_run_body_style(
    run: str, *, flagged: bool
) -> None:
    """The reader returns the whole run: body in each YAML style, for the check."""
    action = load_yaml(
        'runs:\n'
        '  using: composite\n'
        '  steps:\n'
        '    - shell: bash\n'
        '      env:\n'
        '        BASE: ${{ inputs.base }}\n'
        f'      {run.replace(chr(10), chr(10) + "      ")}\n'
    )

    assert bool(expressions_in_run_bodies(action['runs']['steps'])) is flagged
