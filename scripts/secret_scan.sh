#!/usr/bin/env bash
# Belt-and-suspenders secret scanner. Runs detect-secrets if installed,
# then a custom regex sweep for known credential prefixes.
#
# Exit non-zero on ANY hit. Used by Makefile (`make secret-scan`) and
# .github/workflows/ci.yml.

set -euo pipefail

cd "$(dirname "$0")/.."

REPO_ROOT="$(pwd)"
FAILED=0

echo "[secret-scan] custom regex sweep..."
# Patterns we never want in any tracked file.
PATTERNS=(
  'sk-ant-[A-Za-z0-9_\-]{20,}'
  'sk-proj-[A-Za-z0-9_\-]{20,}'
  'sk-[A-Za-z0-9]{40,}'
  'ghp_[A-Za-z0-9]{36}'
  'github_pat_[A-Za-z0-9_]{60,}'
  'AKIA[0-9A-Z]{16}'
  '-----BEGIN ([A-Z]+ )?PRIVATE KEY-----'
)

# Use git ls-files so we only scan tracked (or about-to-be-committed) files.
FILES=$(git ls-files 2>/dev/null || true)
if [[ -z "$FILES" ]]; then
  echo "[secret-scan] no tracked files yet — skipping regex sweep."
else
  for pattern in "${PATTERNS[@]}"; do
    # Exclude:
    # - .secrets.baseline (it intentionally contains test fingerprints)
    # - scripts/secret_scan.sh (this file lists the patterns by definition)
    # - tests/test_sanitizer.py (test fixtures must contain the patterns
    #   they're testing redaction of). Reviewed manually — these are fake values.
    HITS=$(echo "$FILES" \
      | grep -v -E '^(\.secrets\.baseline|scripts/secret_scan\.sh|tests/test_sanitizer\.py|tests/test_researcher\.py)$' \
      | xargs grep -E -l "$pattern" 2>/dev/null || true)
    if [[ -n "$HITS" ]]; then
      echo "❌ pattern matched: $pattern"
      echo "$HITS" | sed 's/^/   /'
      FAILED=1
    fi
  done
fi

# Note: the detect-secrets pre-commit hook (configured separately in
# .pre-commit-config.yaml) is the canonical baseline check. We deliberately
# do NOT re-run `detect-secrets scan` here because that would rewrite the
# baseline's `generated_at` timestamp on every invocation, causing
# pre-commit to flag "files were modified by this hook" on a clean tree.

if [[ "$FAILED" -ne 0 ]]; then
  echo
  echo "❌ secret-scan FAILED"
  exit 1
fi

echo "✅ secret-scan clean."
