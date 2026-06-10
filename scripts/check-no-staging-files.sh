#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor S.r.l.
#
# Reject commits adding staging-only files. Part of the aurig-sentinel
# pre-commit safety net (Model A workflow).

set -e

# Files that should never be committed
BLOCKED_PATTERNS=(
    '^BRIEF\.md$'
    '^MIGRATION\.md$'
    '^MIGRATION_STATE\.md$'
    '^STATUS\.md$'
    '^AURIG_PLAN\.md$'
    '^docs/ISSUE_.*_PLAN\.md$'
    '^\.claude/'
    '^\.transition/'
    '^\.codex/'
    '^local_config\.yaml$'
    '\.local\.yaml$'
    '\.local\.yml$'
)

STAGED=$(git diff --cached --name-only --diff-filter=AM)

if [ -z "$STAGED" ]; then
    exit 0
fi

FOUND=0
for pattern in "${BLOCKED_PATTERNS[@]}"; do
    for file in $STAGED; do
        if echo "$file" | grep -E "$pattern" > /dev/null; then
            echo "ERROR: staging-only file matching pattern '$pattern': $file"
            FOUND=1
        fi
    done
done

if [ "$FOUND" -ne 0 ]; then
    echo ""
    echo "These files should not be committed. Either:"
    echo "  1. Add them to .gitignore"
    echo "  2. Remove from staging: git reset HEAD <file>"
    echo "  3. Delete the file if not needed"
    exit 1
fi

exit 0
