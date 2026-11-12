#!/usr/bin/env bash
# Approves the held runs at each merge commit catch-up.sh made with github-token, and
# records for report.sh each head whose runs stay held, unverified, or absent.
#
# Environment: GH_TOKEN, REPO, and RUNNER_TEMP or PR_CATCHUP_DIR.
set -euo pipefail

state_dir="${PR_CATCHUP_DIR:-${RUNNER_TEMP:?}/pr-catchup}"
mkdir -p "$state_dir"
: "${REPO:?}"

# shellcheck source=approve-bot-gated-runs.sh
source "$(dirname "${BASH_SOURCE[0]}")/approve-bot-gated-runs.sh"

: > "$state_dir/gated.txt"
: > "$state_dir/silent.txt"
: > "$state_dir/blind.txt"
if [ ! -s "$state_dir/default-token-merges.txt" ]; then
  echo "no merge was made with github-token, so no run needs approval"
  exit 0
fi

# The SHAs come only from merge commits catch-up.sh created in this job.
while read -r num sha; do
  echo "::group::PR #$num at $sha"

  APPROVE_BOT_GATED_OUT="$state_dir/gated-$num.txt"
  rc=0
  approve_bot_gated_runs_for_sha "$sha" "$num" || rc=$?
  if [ "$rc" = 2 ]; then
    echo "$num $sha" >> "$state_dir/silent.txt"
  elif [ "$rc" = 1 ] && [ -s "$APPROVE_BOT_GATED_OUT" ]; then
    while read -r id; do
      echo "$id $num" >> "$state_dir/gated.txt"
    done < "$APPROVE_BOT_GATED_OUT"
  elif [ "$rc" != 0 ]; then
    # A failed Actions lookup, or a held count with an empty run listing: recording
    # it as gated would name a run nobody identified.
    echo "$num $sha" >> "$state_dir/blind.txt"
  fi

  echo "::endgroup::"
done < "$state_dir/default-token-merges.txt"
