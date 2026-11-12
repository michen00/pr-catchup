#!/usr/bin/env bash
# Fails the run on each way a caught-up pull request can stay behind or untested without
# a failure of its own, reporting each separately because each needs a different fix.
#
# Environment: REPO, BASE, COMMIT_MESSAGE, GITHUB_WORKFLOW_REF (or GITHUB_WORKFLOW), and
# RUNNER_TEMP or PR_CATCHUP_DIR.
set -euo pipefail

state_dir="${PR_CATCHUP_DIR:-${RUNNER_TEMP:?}/pr-catchup}"
: "${REPO:?}"
base="${BASE:?}"

# shellcheck source=render-message.sh
source "$(dirname "${BASH_SOURCE[0]}")/render-message.sh"

# GITHUB_WORKFLOW_REF reads owner/repo/.github/workflows/<file>@<ref>, where <ref> is a
# full ref such as refs/heads/main and can itself contain @.
workflow="${GITHUB_WORKFLOW_REF:-}"
workflow="${workflow%%@refs/*}"
workflow="${workflow##*/}"
workflow="${workflow:-${GITHUB_WORKFLOW:-}}"
if [ -n "$workflow" ]; then
  workflow="$(printf '%q' "$workflow")"
else
  workflow='<workflow>'
fi

# Each ' is written as '\''. bash 3.2 keeps the backslashes of an inline replacement, so
# the replacement comes from a variable.
single_quote() {
  local escaped="'\\''"
  printf "'%s'" "${1//\'/$escaped}"
}

stalled=0

if [ -s "$state_dir/refused.txt" ]; then
  stalled=1
  while read -r num branch reason; do
    echo "::error::merging $base into PR #$num was refused, so it stays behind $base: $reason"
    echo "  an empty body means the branch is already current and a 409 on a pull"
    echo "  request GitHub reports as conflicting means it conflicts; neither is"
    echo "  reported here, so this is a refusal such as a permission error, a rate"
    echo "  limit, an outage, or a repository rule. Confirm what the endpoint says now,"
    echo "  then re-run the catch-up once the cause clears:"
    printf '    gh api --method POST repos/%s/merges -f base=%q -f head=%q \\\n' \
      "$REPO" "$branch" "$base"
    printf '      -f commit_message=%s\n' \
      "$(single_quote "$(render_message "$num" "$branch")")"
    printf '    gh workflow run %s --repo %s\n' "$workflow" "$REPO"
  done < "$state_dir/refused.txt"
fi

if [ -s "$state_dir/blind.txt" ]; then
  stalled=1
  while read -r num sha; do
    echo "::error::whether PR #$num cleared the approval gate at $sha is unknown:" \
      "an Actions lookup failed during the approval pass"
    echo "  a run lookup failed or returned something other than a count, or held runs"
    echo "  were counted but none was listed, so this is unverified rather than clear."
    echo "  Check the head and approve anything held there:"
    echo "    gh api 'repos/$REPO/actions/runs?head_sha=$sha'"
  done < "$state_dir/blind.txt"
fi

if [ -s "$state_dir/gated.txt" ]; then
  stalled=1
  while read -r id num; do
    echo "::error::run $id for PR #$num is still awaiting approval, so its" \
      "required checks cannot report"
    echo "  approve it now from the pull request's Checks tab, or run:"
    echo "    gh api --method POST repos/$REPO/actions/runs/$id/approve"
  done < "$state_dir/gated.txt"
  echo "Approving unblocks the pull request that is stuck now. If every catch-up fails"
  echo "this way, github-token is not permitted to approve runs here, and the durable fix"
  echo "is to pass a GitHub App installation token as the token input: its merges are then"
  echo "not attributed to github-actions[bot], so GitHub holds none of their runs. No"
  echo "repository setting lifts this hold."
fi

if [ -s "$state_dir/silent.txt" ]; then
  stalled=1
  while read -r num sha; do
    echo "::error::no pull_request run ever appeared for PR #$num at $sha, so" \
      "its required checks cannot report"
    echo "  see what does exist at that commit:"
    echo "    gh api 'repos/$REPO/actions/runs?head_sha=$sha'"
    echo "  approve anything listed as action_required, or push to the pull request's"
    echo "  branch to create fresh runs. This is queueing latency or suppression, not the"
    echo "  approval gate, so there may be no run to approve at all."
  done < "$state_dir/silent.txt"
fi

if [ "$stalled" = 0 ]; then
  echo "every run this catch-up caused is past the approval gate"
  exit 0
fi
exit 1
