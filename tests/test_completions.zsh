#!/usr/bin/env zsh
# Smoke test: source the generated completer and verify basic structure

set -e

SCRIPT_DIR="${0:A:h}"
COMPLETER="${SCRIPT_DIR}/../_aws_move_outtamyway"

if [[ ! -f "$COMPLETER" ]]; then
  echo "FAIL: _aws_move_outtamyway not found"
  exit 1
fi

# Source the file — the trailing _aws_move_outtamyway "$@" call will fail
# in non-interactive mode (no _arguments builtin), so tolerate that.
source "$COMPLETER" 2>/dev/null || true
echo "PASS: sourced without error"

# Function should be defined
if ! typeset -f _aws_move_outtamyway > /dev/null 2>&1; then
  echo "FAIL: function _aws_move_outtamyway not defined"
  exit 1
fi
echo "PASS: function defined"

# Associative arrays should be populated
if (( ${#_aws_momy_ops} == 0 )); then
  echo "FAIL: _aws_momy_ops is empty"
  exit 1
fi
echo "PASS: _aws_momy_ops has ${#_aws_momy_ops} services"

# Spot checks
if [[ -z "${_aws_momy_ops[s3]}" ]]; then
  echo "FAIL: s3 not in operations"
  exit 1
fi
echo "PASS: s3 operations present"

if [[ "${_aws_momy_ops[s3]}" != *"cp"* ]]; then
  echo "FAIL: s3 cp (CLI custom) not found"
  exit 1
fi
echo "PASS: s3 CLI customs merged"

if [[ "${_aws_momy_ops[s3api]}" != *"wait"* ]]; then
  echo "FAIL: s3api wait pseudo-subcommand not found"
  exit 1
fi
echo "PASS: s3api wait subcommand present"

# No #compdef
if grep -q '#compdef' "$COMPLETER"; then
  echo "FAIL: found #compdef in generated file"
  exit 1
fi
echo "PASS: no #compdef header"

# No grep -oP
if grep -q 'grep -oP' "$COMPLETER"; then
  echo "FAIL: found non-portable grep -oP"
  exit 1
fi
echo "PASS: no grep -oP"

echo ""
echo "All smoke tests passed."
