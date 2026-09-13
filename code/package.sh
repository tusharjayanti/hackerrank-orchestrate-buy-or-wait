#!/usr/bin/env bash
# Build code.zip from a committed revision so the archive matches the run that produced output.csv.
# Usage (from the repo root): bash code/package.sh [git-revision]   (default: HEAD)
set -euo pipefail

revision="${1:-HEAD}"
repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

test -s code/evaluation/usage_report.md || { echo "code/evaluation/usage_report.md is missing or empty" >&2; exit 1; }
git archive --format=zip --output=code.zip "$revision" code
if unzip -l code.zip | grep -qE '(^|/)\.env$'; then
  echo "refusing to ship a .env file" >&2
  rm -f code.zip
  exit 1
fi
unzip -l code.zip | tail -1
echo "Wrote $repo_root/code.zip from $(git rev-parse --short "$revision")"
