# shellcheck shell=bash
# Approval predicate and helpers for the pull_request runs that GitHub holds when
# github-actions[bot] pushes the commit they run on. approve-caused-runs.sh sources this
# file, which reads REPO and approves with the token gh finds in GH_TOKEN.
set -euo pipefail

approvable() {
  # A run qualifies only as the product of the commit this caller just pushed:
  # the same head SHA, a head in this repository so a fork's code can never be
  # admitted, the bot-attributed pull_request event, and still held at the gate.
  # Empty operands are rejected so a failed lookup cannot widen this into
  # "approve anything".
  local run_sha="$1" caused_sha="$2" run_repo="$3" this_repo="$4"
  local run_event="$5" run_actor="$6" run_gate="$7"
  [ -n "$run_sha" ] && [ -n "$caused_sha" ] && [ "$run_sha" = "$caused_sha" ] || return 1
  [ -n "$run_repo" ] && [ -n "$this_repo" ] && [ "$run_repo" = "$this_repo" ] || return 1
  [ "$run_event" = "pull_request" ] || return 1
  [ "$run_actor" = "github-actions[bot]" ] || return 1
  [ "$run_gate" = "action_required" ] || return 1
}

gated_runs() {
  # A held run reports status "completed" with conclusion "action_required",
  # never status "action_required", even though that is the filter which selects
  # it. Both fields are normalized into one gate value because reading .status
  # alone would reject every real run.
  #
  # The timestamps do not identify a held run. While a run is held, created_at
  # and run_started_at are identical, because it never executed. The gap between
  # them opens only after approval and measures how long the run waited, so a gap
  # shows a run was held and then released, and its absence shows nothing.
  gh api "repos/$REPO/actions/runs?head_sha=$1&status=action_required" \
    --jq '.workflow_runs[]
          | [ .id, .head_sha, .head_repository.full_name, .event, .actor.login,
              (if .status == "action_required" or .conclusion == "action_required"
               then "action_required" else "other" end) ]
          | @tsv'
}

gated_count() {
  gh api "repos/$REPO/actions/runs?head_sha=$1&status=action_required" --jq .total_count
}

head_runs_count() {
  # Only pull_request runs carry the required checks. Code-scanning runs appear at
  # the same commit under the dynamic event and are never held, so counting every
  # event would read a head with no required-check runs as healthy.
  gh api "repos/$REPO/actions/runs?head_sha=$1&event=pull_request" --jq .total_count
}

is_count() {
  # A case pattern rather than [[ =~ ]], which would set BASH_REMATCH in the caller.
  case "$1" in
    '' | *[!0-9]*) return 1 ;;
    *) return 0 ;;
  esac
}

approve_bot_gated_runs_for_sha() {
  # Approves the held pull_request runs at caused_sha. The optional second argument
  # is a pull request number for messages. Writes the ids of runs still held after
  # the sweep to APPROVE_BOT_GATED_OUT, one per line. Returns 0 when runs exist and
  # none stays held; 1 when some stay held; 2 when no pull_request run appeared at
  # the SHA within the bounded wait; 3 when a run lookup failed or returned a
  # non-number.
  local caused_sha="$1"
  local pr_label="${2:-}"
  local out_file="${APPROVE_BOT_GATED_OUT:?}"
  local seen=0
  # Declared apart from their assignments below: `local x="$(cmd)"` returns local's
  # own status, which would swallow a failing lookup.
  local remaining count

  : > "$out_file"

  for _ in $(seq 1 10); do
    # Callers run this under ||, which disables errexit inside it, so each lookup's
    # status is checked here.
    count="$(head_runs_count "$caused_sha")" || return 3
    is_count "$count" || return 3
    if [ "$count" != "0" ]; then
      seen=1
      break
    fi
    sleep 3
  done

  if [ "$seen" = "0" ]; then
    return 2
  fi

  # Sweep on every poll rather than once. More than one workflow can answer
  # the synchronize event a catch-up produces, so a second held run can
  # appear after the first sweep already ran; polling only the count would
  # then wait for a run nothing ever approved. A run already reported is not
  # repeated on a later pass.
  local settled=0
  local seen_file="$out_file.seen"
  : > "$seen_file"
  remaining=1
  for _ in $(seq 1 10); do
    gated_runs "$caused_sha" |
      while IFS=$'\t' read -r id run_sha run_repo run_event run_actor run_gate; do
        # A run just approved can still read action_required for a poll or two
        # after the approve call succeeds, so an id already recorded is skipped
        # rather than approved or printed twice.
        if grep -qxF "$id" "$seen_file"; then
          continue
        fi
        if approvable "$run_sha" "$caused_sha" "$run_repo" "$REPO" "$run_event" "$run_actor" "$run_gate"; then
          if gh api --method POST "repos/$REPO/actions/runs/$id/approve" > /dev/null 2>&1; then
            echo "$id" >> "$seen_file"
            if [ -n "$pr_label" ]; then
              echo "approved run $id for #$pr_label"
            else
              echo "approved run $id for $caused_sha"
            fi
            continue
          fi
          note="::warning::could not approve run $id; github-token may not be permitted to approve runs here"
        else
          note="out of scope, left for a human: run $id ($run_repo $run_event $run_actor $run_gate)"
        fi
        echo "$id" >> "$seen_file"
        echo "$note"
      done || return 3

    remaining="$(gated_count "$caused_sha")" || return 3
    is_count "$remaining" || return 3
    # Two consecutive zero counts, not one: the count can read zero between a
    # run clearing and a second workflow's run being created for the same
    # commit, and stopping on the first zero would call that gap clear.
    if [ "$remaining" = "0" ] && [ "$settled" = "1" ]; then break; fi
    if [ "$remaining" = "0" ]; then settled=1; else settled=0; fi
    sleep 3
  done

  if [ "$remaining" != "0" ]; then
    gated_runs "$caused_sha" | cut -f1 > "$out_file" || return 3
    return 1
  fi

  return 0
}
