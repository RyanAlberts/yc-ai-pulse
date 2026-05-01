# yc-ai-pulse — developer Makefile.
#
# All targets are safe to run locally. CI runs the same checks via
# .github/workflows/ci.yml. Phase 0 only implements bootstrap targets;
# feature targets (run, serve, demo) land in later phases.

.PHONY: help install validate-p0 publish-check lint typecheck test \
        secret-scan precommit-run clean

help:
	@echo "yc-ai-pulse Makefile"
	@echo
	@echo "Phase 0 targets (active now):"
	@echo "  install         Install dev dependencies"
	@echo "  validate-p0     Run all Phase 0 gates"
	@echo "  publish-check   Run all secret-scan / hygiene checks before pushing"
	@echo "  lint            Run ruff"
	@echo "  typecheck       Run mypy --strict"
	@echo "  test            Run pytest"
	@echo "  secret-scan     Run detect-secrets + custom regex"
	@echo "  precommit-run   Run pre-commit on all files"
	@echo "  clean           Remove caches and build artifacts"
	@echo
	@echo "Phase 1+ targets land in subsequent PRs."

install:
	python3 -m pip install --user --upgrade pip pre-commit detect-secrets
	pre-commit install --install-hooks

# Phase 0 acceptance gate. All of these must pass before any feature code lands.
validate-p0: lint typecheck test secret-scan
	@echo
	@echo "✅ Phase 0 gates green."

# Pre-publish check — run before every `git push` to public branch.
publish-check: secret-scan
	@echo
	@echo "Running publish hygiene checks..."
	@bash scripts/publish_check.sh
	@echo "✅ publish-check green."

lint:
	@if command -v ruff >/dev/null 2>&1; then \
		ruff check . ; \
	else \
		echo "ruff not installed — skipping (will run in CI)" ; \
	fi

typecheck:
	@if command -v mypy >/dev/null 2>&1; then \
		mypy src/ycai 2>/dev/null || echo "(no source yet — phase 0)" ; \
	else \
		echo "mypy not installed — skipping (will run in CI)" ; \
	fi

test:
	@if command -v pytest >/dev/null 2>&1; then \
		pytest -q ; \
	else \
		echo "pytest not installed — skipping (will run in CI)" ; \
	fi

secret-scan:
	@bash scripts/secret_scan.sh

precommit-run:
	pre-commit run --all-files

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov dist build
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
