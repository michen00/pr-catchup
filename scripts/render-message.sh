# shellcheck shell=bash
# Renders COMMIT_MESSAGE for one pull request: {base} becomes BASE, {number} the first
# argument, and {branch} the second. catch-up.sh and report.sh source this file.

# One pass, so a placeholder inside a substituted value stays literal.
render_message() {
  local rest="${COMMIT_MESSAGE:?}" out='' name
  while [[ $rest == *'{'*'}'* ]]; do
    out+="${rest%%\{*}"
    rest="${rest#*\{}"
    name="${rest%%\}*}"
    case "$name" in
      base)
        out+="${BASE:?}"
        rest="${rest#*\}}"
        ;;
      number)
        out+="$1"
        rest="${rest#*\}}"
        ;;
      branch)
        out+="$2"
        rest="${rest#*\}}"
        ;;
      *) out+='{' ;;
    esac
  done
  printf '%s' "$out$rest"
}
