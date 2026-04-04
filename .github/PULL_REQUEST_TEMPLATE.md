## Summary

<!-- 1-3 bullet points describing what this PR does -->

-

## Type of Change

- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation update
- [ ] Refactoring

## Motivation

<!-- Why is this change needed? Link to issues if applicable -->

Closes #

## Changes

<!-- What was changed and how -->

-

## Test Plan

<!-- How was this tested? -->

- [ ] Unit tests added/updated
- [ ] Existing tests pass (`pytest`)

## Checklist

- [ ] Code follows the project style guide
- [ ] `ruff check fitz_forge/` passes
- [ ] `ruff format --check fitz_forge/ tests/` passes
- [ ] Tests added for new functionality
- [ ] CHANGELOG.md updated under `[Unreleased]`
- [ ] First line of new files has path comment (`# fitz_forge/path/to/file.py`)
- [ ] No `print()` statements (use `logging` to stderr)

## Architecture Compliance

- [ ] No imports from `planning/` in `config/` or `models/`
- [ ] No imports from `tools/` in `planning/` or `models/`
- [ ] Pipeline stages extend `PipelineStage` base class
- [ ] New schemas use Pydantic with field defaults (partial plan > no plan)
- [ ] LLM calls go through `client.generate()` — never direct HTTP
