.DEFAULT_GOAL := help

VERSION = $(shell cat VERSION)

app_root := $(CURDIR)
pkg_src = $(app_root)/src/twagent
tests_src = $(app_root)/tests

# Ordered deliberately: the gate runs BEFORE build, so a failure costs seconds
# and nothing reaches PyPI. A publish is not revocable -- a bad version can only
# be yanked, never replaced.
.PHONY: all
all: clean lint ty test build publish  ## Test, build and publish to PyPI

################################################################################
# Testing \
TESTING:  ## ############################################################

.PHONY: test
test: test-unit  ## run tests

.PHONY: test-unit
test-unit:  ## run all tests except "integration" marked
	RUN_ENV=local uv run python -m pytest -m "not (integration or experimentation)" --cov-config=pyproject.toml --cov-report=html --cov-report=term --cov=$(pkg_src) tests

################################################################################
# Code Quality \
QUALITY:  ## ############################################################

.PHONY: format
format:  ## perform ruff formatting
	@uv run pre-commit run ruff-format --all-files

.PHONY: lint
lint:  ## check style with ruff
	uv run ruff check --fix $(pkg_src) $(tests_src)

.PHONY: ty
ty:  ## check type hint annotations
	@uv run ty check $(pkg_src)

.PHONY: pre-commit-install
pre-commit-install:  ## install pre-commit hooks
	uv run pre-commit install

################################################################################
# Building, Deploying \
BUILDING:  ## ############################################################
.PHONY: install
install: uninstall  ## uv install
	uv tool install -e .
	twagent --install-completion bash

.PHONY: uninstall
uninstall:  ## uv uninstall
	-uv tool uninstall twagent


.PHONY: build
build: clean format  ## format and build
	uv build

.PHONY: publish
publish:  ## publish to PyPI
	uv run twine upload --verbose dist/*

.PHONY: bump-major
bump-major: check-github-token  ## bump-major, tag and push
	bump-my-version bump --commit --tag major
	git push && git push --tags
	@$(MAKE) create-release

.PHONY: bump-minor
bump-minor: check-github-token  ## bump-minor, tag and push
	bump-my-version bump --commit --tag minor
	git push && git push --tags
	@$(MAKE) create-release

.PHONY: bump-patch
bump-patch: check-github-token  ## bump-patch, tag and push
	bump-my-version bump --commit --tag patch
	git push && git push --tags
	@$(MAKE) create-release

.PHONY: create-release
create-release: check-github-token  ## create a release on GitHub via the gh cli
	@command -v gh >/dev/null || { echo "gh CLI not installed"; exit 1; }
	@echo "Creating GitHub release for v$(VERSION)"
	gh release create "v$(VERSION)" --generate-notes --latest

.PHONY: check-github-token
check-github-token:
	@if [ -z "$$GITHUB_TOKEN" ]; then \
		echo "GITHUB_TOKEN is not set. Please export your GitHub token before running this command."; \
		exit 1; \
	fi
	@echo "GITHUB_TOKEN is set"

################################################################################
# Clean \
CLEAN:  ## ############################################################

.PHONY: clean
clean: clean-build clean-pyc  ## remove all build, test, coverage and Python artifacts

# A virtualenv's bytecode is not ours to delete: removing it only forces a
# needless recompile, and where the venv is read-only (sandboxes, CI caches) the
# rm fails outright and takes `make clean` -- and so `make all` -- down with it.
# clean-build always pruned these; clean-pyc did not, which is the asymmetry that
# broke `make clean`.
venv_prune := \( -path ./env -o -path ./venv -o -path ./.env -o -path ./.venv \) -prune -o

.PHONY: clean-build
clean-build:
	rm -fr build/ dist/ .eggs/
	find . $(venv_prune) -name '*.egg-info' -exec rm -fr {} +
	find . $(venv_prune) -name '*.egg' -exec rm -f {} +

.PHONY: clean-pyc
clean-pyc:
	find . $(venv_prune) -name '*.pyc' -exec rm -f {} +
	find . $(venv_prune) -name '*.pyo' -exec rm -f {} +
	find . $(venv_prune) -name '*~' -exec rm -f {} +
	find . $(venv_prune) -name '__pycache__' -exec rm -fr {} +

################################################################################
# Misc \
MISC:  ## ############################################################

define PRINT_HELP_PYSCRIPT
import re, sys

for line in sys.stdin:
	match = re.match(r'^([a-zA-Z0-9_-]+):.*?## (.*)$$', line)
	if match:
		target, help = match.groups()
		print("\033[36m%-20s\033[0m %s" % (target, help))
endef
export PRINT_HELP_PYSCRIPT

.PHONY: help
help:
	@uv run python -c "$$PRINT_HELP_PYSCRIPT" < $(MAKEFILE_LIST)
