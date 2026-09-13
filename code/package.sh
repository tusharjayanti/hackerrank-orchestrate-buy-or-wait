#!/usr/bin/env bash
# Build code.zip from a committed revision so the archive matches the run that produced output.csv.
# The archive contains the code/ tree and, at its root, evaluation/usage_report.md.
# Usage (from the repo root): bash code/package.sh [git-revision]   (default: HEAD)
set -euo pipefail

revision="${1:-HEAD}"
repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

report_blob="$revision:code/evaluation/usage_report.md"
git cat-file -e "$report_blob" 2>/dev/null || { echo "code/evaluation/usage_report.md is not committed at $revision" >&2; exit 1; }
test -n "$(git show "$report_blob")" || { echo "committed usage_report.md is empty" >&2; exit 1; }

git archive --format=zip --output=code.zip "$revision" code
git show "$report_blob" | python3 -c '
import sys, zipfile
with zipfile.ZipFile(sys.argv[1], "a") as archive:
    archive.writestr("evaluation/usage_report.md", sys.stdin.buffer.read())
' code.zip

if unzip -l code.zip | awk '{print $4}' | grep -qE '(^|/)(\.env|log\.txt)$'; then
  echo "refusing to ship a .env or log.txt file" >&2
  rm -f code.zip
  exit 1
fi
unzip -l code.zip | tail -1
echo "Wrote $repo_root/code.zip from $(git rev-parse --short "$revision")"
