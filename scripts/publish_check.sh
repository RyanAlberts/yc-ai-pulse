#!/usr/bin/env bash
# Pre-publish hygiene gate. Run before every `git push` to a public branch.
# Codifies the checklist in the project plan under "PII & secrets hygiene".

set -euo pipefail

cd "$(dirname "$0")/.."

FAILED=0

# 1. runs/ must not be tracked.
if git ls-files | grep -qE '^runs/'; then
  echo "❌ runs/ has tracked files — should be gitignored."
  FAILED=1
fi

# 2. .env (real, not example) must not be tracked.
if git ls-files | grep -qE '^\.env$'; then
  echo "❌ .env is tracked — should be gitignored."
  FAILED=1
fi

# 3. No real-looking key material.
SUSPICIOUS=$(git ls-files | grep -v -E '^(\.secrets\.baseline|scripts/secret_scan\.sh|scripts/publish_check\.sh)$' \
  | xargs grep -l -E -i 'sk-ant-[A-Za-z0-9_\-]{20,}|ghp_[A-Za-z0-9]{36}|AKIA[0-9A-Z]{16}' 2>/dev/null || true)
if [[ -n "$SUSPICIOUS" ]]; then
  echo "❌ suspicious credential strings found in:"
  echo "$SUSPICIOUS" | sed 's/^/   /'
  FAILED=1
fi

# 4. No founder emails or other PII surfaces in examples/output (gentle regex).
#    Once examples/ is populated this catches accidental leaks.
if [[ -d examples/output ]]; then
  PII_HITS=$(grep -rE '\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b' examples/output 2>/dev/null \
    | grep -v -E 'noreply@|@example\.com|@yc-ai-pulse|RyanAlberts|ryan\.a\.alberts@gmail\.com' || true)
  if [[ -n "$PII_HITS" ]]; then
    echo "❌ possible email addresses in examples/output:"
    echo "$PII_HITS" | sed 's/^/   /'
    FAILED=1
  fi
fi

if [[ "$FAILED" -ne 0 ]]; then
  echo
  echo "❌ publish-check FAILED"
  exit 1
fi

echo "✅ publish hygiene checks clean."
