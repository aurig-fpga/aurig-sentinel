#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor S.r.l.
#
# Reject commit messages containing AI co-author trailers or
# AI-generated content markers. Part of the aurig-sentinel pre-commit
# safety net (Model A workflow: public repo is source of truth).

set -e
COMMIT_MSG_FILE=$1

if [ -z "$COMMIT_MSG_FILE" ] || [ ! -f "$COMMIT_MSG_FILE" ]; then
    exit 0
fi

# Patterns to block
PATTERNS=(
    '[Cc]o-[Aa]uthored-[Bb]y:.*([Cc]laude|[Cc]odex|[Cc]opilot|[Aa]nthropic|[Oo]penai|[Cc]hatgpt)'
    'Generated.with.*[Cc]laude.[Cc]ode'
    'noreply@anthropic\.com'
    '🤖.*Generated'
    '[Aa]ssisted-[Bb]y:.*([Cc]laude|[Cc]opilot|[Cc]odex|[Cc]hatgpt)'
)

for pattern in "${PATTERNS[@]}"; do
    if grep -iE "$pattern" "$COMMIT_MSG_FILE" > /dev/null; then
        echo ""
        echo "ERROR: AI co-author trailer or marker detected in commit message."
        echo "Pattern: $pattern"
        echo ""
        echo "Per aurig-sentinel policy: no AI attribution in commit messages."
        echo "Remove the offending line(s) from the commit message and try again."
        exit 1
    fi
done

exit 0
