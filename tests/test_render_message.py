"""Contract tests for render_message in scripts/render-message.sh.

catch-up.sh renders the commit-message input once per pull request, and
report.sh renders it again so a recovery hint repeats the exact message that was
attempted.
"""

import subprocess
from pathlib import Path

import pytest

from tests.harness import BASH, SCRIPTS, read_action, script_env


def _render(
    tmp_path: Path, message: str | None, base: str | None, number: str, branch: str
) -> subprocess.CompletedProcess[str]:
    """Run render_message under bash with COMMIT_MESSAGE and BASE as given."""
    script = f'source "{SCRIPTS}/render-message.sh"\nrender_message "$1" "$2"\n'
    return subprocess.run(
        [BASH, '-c', script, 'render', number, branch],
        env=script_env(tmp_path, {'COMMIT_MESSAGE': message, 'BASE': base}),
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_default_commit_message_renders_the_subject_every_copy_used(
    tmp_path: Path,
) -> None:
    """With the default inputs on main, the message is `chore: merge main into #7`.

    Every hand-copied workflow this action replaces wrote that subject, and it
    passes a Conventional Commits check, so callers that switch see no change in
    their history.
    """
    default = read_action()['inputs']['commit-message']['default']

    rendered = _render(tmp_path, default, 'main', '7', 'feat/x')

    assert rendered.returncode == 0
    assert rendered.stdout == 'chore: merge main into #7'


def test_an_empty_commit_message_fails_rather_than_rendering_nothing(
    tmp_path: Path,
) -> None:
    """An empty or missing template exits nonzero instead of printing nothing."""
    empty = _render(tmp_path, '', 'main', '7', 'feat/x')
    missing = _render(tmp_path, None, 'main', '7', 'feat/x')

    assert empty.returncode != 0
    assert missing.returncode != 0
    assert empty.stdout == missing.stdout == ''


def test_base_is_required_only_by_a_template_that_uses_it(tmp_path: Path) -> None:
    """Only a template that uses {base} fails without BASE."""
    without_base = _render(tmp_path, 'chore: update #{number}', None, '7', 'feat/x')
    needs_base = _render(tmp_path, 'chore: merge {base}', None, '7', 'feat/x')

    assert without_base.returncode == 0
    assert without_base.stdout == 'chore: update #7'
    assert needs_base.returncode != 0


@pytest.mark.parametrize(
    ('message', 'base', 'branch', 'expected'),
    [
        ('{base} {number} {branch}', 'main', 'feat/x', 'main 7 feat/x'),
        ('{number}{number}', 'main', 'feat/x', '77'),
        ('{branch}', 'main', 'feat/{base}', 'feat/{base}'),
        ('{base}', '{number}', 'feat/x', '{number}'),
        ('{branch} {base}', 'a&b', 'c\\&d', 'c\\&d a&b'),
        ('{other} {base}', 'main', 'feat/x', '{other} main'),
        ('{{base}}', 'main', 'feat/x', '{main}'),
        ('{} {BASE}', 'main', 'feat/x', '{} {BASE}'),
        ('a { b } c', 'main', 'feat/x', 'a { b } c'),
        ('a } {base', 'main', 'feat/x', 'a } {base'),
        ('line one\n\nIt is #{number}.', 'main', 'feat/x', 'line one\n\nIt is #7.'),
        ("it's $HOME and `id`", 'main', 'feat/x', "it's $HOME and `id`"),
    ],
    ids=[
        'every-placeholder',
        'repeated-placeholder',
        'placeholder-in-branch-stays-literal',
        'placeholder-in-base-stays-literal',
        'ampersand-and-backslash-in-values',
        'unknown-placeholder-passes-through',
        'nested-braces',
        'empty-and-uppercase-braces',
        'braces-around-other-text',
        'unmatched-braces',
        'multi-line',
        'shell-syntax-stays-literal',
    ],
)
def test_render_message_substitutes_each_placeholder_in_one_pass(
    tmp_path: Path, message: str, base: str, branch: str, expected: str
) -> None:
    """Placeholders are replaced left to right; substituted text is never rescanned."""
    rendered = _render(tmp_path, message, base, '7', branch)

    assert rendered.returncode == 0
    assert rendered.stdout == expected
