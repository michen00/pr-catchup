#!/usr/bin/env bash
# Merges BASE into each open pull request from REPO that targets BASE, is behind it, does
# not conflict, and carries none of EXCLUDE_LABELS, recording the outcomes in the state
# directory for approve-caused-runs.sh and report.sh.
#
# Environment: GH_TOKEN, APP_TOKEN (optional), REPO, BASE, COMMIT_MESSAGE, EXCLUDE_LABELS
# (optional), GITHUB_OUTPUT (optional), and RUNNER_TEMP or PR_CATCHUP_DIR.
set -euo pipefail

# Each script resolves this itself, so each can run alone.
state_dir="${PR_CATCHUP_DIR:-${RUNNER_TEMP:?}/pr-catchup}"
mkdir -p "$state_dir"

# shellcheck source=render-message.sh
source "$(dirname "${BASH_SOURCE[0]}")/render-message.sh"

: "${REPO:?}"
if [ -z "${BASE:-}" ]; then
  echo "::error::base is empty; its default is the event's repository default branch"
  exit 1
fi
if [ -z "${COMMIT_MESSAGE:-}" ]; then
  echo "::error::commit-message is empty"
  exit 1
fi

default_token="${GH_TOKEN:-}"
listing="$state_dir/listing.txt"
response="$state_dir/merge-response.txt"
app_refused='\(HTTP (401|403|404)\)'

# mergeable reads UNKNOWN until GitHub has computed it.
settled_mergeable() {
  local state='' tries=0
  while [ "$tries" -lt 5 ]; do
    state="$(gh pr view "$1" --repo "$REPO" --json mergeable --jq .mergeable)" ||
      state=''
    [ "$state" = UNKNOWN ] || break
    tries=$((tries + 1))
    sleep 2
  done
  printf '%s\n' "$state"
}

# The call returns the merge commit it created, so the new head is read from the response
# instead of polled for. A branch that already contains BASE gets 204 with an empty body,
# which reads as an empty SHA. gh prints a failing status only on stderr, as a trailing
# "(HTTP nnn)", so stdout goes to a file and only stderr reaches the caller.
merge() {
  local token="$1" branch="$2" message="$3"
  {
    GH_TOKEN="$token" gh api --method POST "repos/$REPO/merges" \
      -f base="$branch" -f head="$BASE" -f commit_message="$message" \
      --jq '.sha // empty' > "$response"
  } 2>&1
}

: > "$state_dir/default-token-merges.txt"
: > "$state_dir/refused.txt"
updated=''

# gh pr list truncates at --limit without warning.
# shellcheck disable=SC2016
gh pr list --repo "$REPO" --state open --base "$BASE" --limit 1000 \
  --json number,headRefName,isCrossRepository,mergeable,labels \
  --jq '[$ENV.EXCLUDE_LABELS // "" | ascii_downcase | splits("[,\n]")
         | gsub("^\\s+|\\s+$"; "") | select(length > 0)] as $excluded
    | .[]
    | select(.isCrossRepository == false and .mergeable != "CONFLICTING")
    | select(any(.labels[].name | ascii_downcase; IN($excluded[])) | not)
    | "\(.number) \(.headRefName)"' > "$listing"

while read -r num branch; do
  echo "::group::PR #$num"
  message="$(render_message "$num" "$branch")"
  merge_token="${APP_TOKEN:-$default_token}"
  refusal="$(merge "$merge_token" "$branch" "$message")" && merge_rc=0 || merge_rc=$?
  if [ "$merge_rc" != 0 ] && [ -n "${APP_TOKEN:-}" ] && [[ $refusal =~ $app_refused ]]; then
    echo "::warning::App merge refused for #$num; retrying with github-token"
    merge_token="$default_token"
    refusal="$(merge "$merge_token" "$branch" "$message")" && merge_rc=0 || merge_rc=$?
  fi
  after="$(cat "$response")"
  if [ "$merge_rc" = 0 ] && [ -n "$after" ]; then
    echo "updated #$num to $after"
    updated+="${updated:+ }$num"
    # Compared by value, so a token input equal to github-token still queues its runs.
    if [ "$merge_token" = "$default_token" ]; then
      echo "$num $after" >> "$state_dir/default-token-merges.txt"
    fi
  elif [ "$merge_rc" = 0 ]; then
    echo "no update needed for #$num"
  else
    # A 409 also refuses a clean merge that would change a workflow file the token
    # cannot write, so only GitHub's own mergeable says it conflicts.
    mergeable=''
    if [[ $refusal == *'(HTTP 409)'* ]]; then
      mergeable="$(settled_mergeable "$num")"
    fi
    if [ "$mergeable" = CONFLICTING ]; then
      # The listing filters out only CONFLICTING, so a pull request whose mergeable
      # still read UNKNOWN can reach the merge call and conflict there.
      echo "::notice::#$num conflicts with $BASE, so it stays behind"
    else
      # Any other refusal (a 403, a 404, a rate limit, a 5xx) leaves the pull request
      # behind. It is recorded so the loop goes on, and report.sh fails the run.
      reason="${refusal//$'\n'/ }${mergeable:+ (GitHub reports it as $mergeable)}"
      echo "$num $branch $reason" >> "$state_dir/refused.txt"
    fi
  fi
  echo "::endgroup::"
done < "$listing"

if [ -n "${GITHUB_OUTPUT:-}" ]; then
  echo "updated=$updated" >> "$GITHUB_OUTPUT"
fi
