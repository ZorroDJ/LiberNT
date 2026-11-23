#!/usr/bin/env bash
# PROJECT:     LiberNT fork automation
# LICENSE:     GPL-3.0-or-later
# PURPOSE:     Preserve the current fork README only when integrating upstream
# COPYRIGHT:   Copyright 2026 ZorroDJ <ugo@mydsl.it>
set -euo pipefail

source=${FORK_README_SOURCE:-}
if [ -z "$source" ]; then
    read -r -a commits <<< "$(git rev-list --parents -n 1 HEAD)"
    if [ "${#commits[@]}" -lt 3 ]; then
        echo "Not a merge; README edits are retained."
        exit 0
    fi
    git fetch "${UPSTREAM_URL:-https://github.com/LiberNT/LiberNT.git}" master
    upstream=$(git rev-parse FETCH_HEAD)
    for parent in "${commits[@]:2}"; do
        status=0
        git merge-base --is-ancestor "$parent" "$upstream" || status=$?
        if [ "$status" = 0 ]; then
            source=${commits[1]}
            break
        elif [ "$status" != 1 ]; then
            exit "$status"
        fi
    done
    if [ -z "$source" ]; then
        echo "Not an upstream merge; README edits are retained."
        exit 0
    fi
fi

source=$(git rev-parse --verify "${source}^{commit}")
if git cat-file -e "$source:README.md" 2>/dev/null; then
    git restore --source="$source" --staged --worktree -- README.md
else
    git rm -f --ignore-unmatch -- README.md
fi
echo "Preserved the fork README from $source."
