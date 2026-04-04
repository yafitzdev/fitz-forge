# Contributing to fitz-forge

Thank you for your interest in contributing to fitz-forge! This document provides guidelines and information for contributors.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Architecture Guidelines](#architecture-guidelines)
- [How to Contribute](#how-to-contribute)
- [Pull Request Process](#pull-request-process)
- [Testing](#testing)
- [Style Guide](#style-guide)

---

## Code of Conduct

Be respectful, inclusive, and constructive. We're all here to build something useful together.

---

## Getting Started

1. Fork the repository
2. Clone your fork locally
3. Set up the development environment
4. Create a branch for your work
5. Make your changes
6. Submit a pull request

---

## Development Setup

```bash
# Clone your fork
git clone https://github.com/yafitzdev/fitz-forge.git
cd fitz-forge

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install in development mode with all extras
pip install -e ".[dev]"

# Verify setup
pytest

# Check linting
ruff check fitz_forge/
ruff format --check fitz_forge/ tests/
```

**Prerequisites:**
- Python 3.10+
- [Ollama](https://ollama.com), [LM Studio](https://lmstudio.ai), or [llama.cpp](https://github.com/ggerganov/llama.cpp) for integration testing

---

## Architecture Guidelines

fitz-forge follows a layered architecture. Please respect these boundaries when contributing.

### Project Structure

```
fitz_forge/
├── cli.py                     # Typer CLI (entry point)
├── server.py                  # FastMCP server + lifecycle
├── tools/                     # Service layer (shared by CLI + MCP)
├── models/                    # JobStore ABC, SQLiteJobStore, JobRecord
├── background/                # BackgroundWorker, signal handling
├── llm/                       # LLM clients (Ollama, LM Studio, llama.cpp)
├── planning/
│   ├── pipeline/stages/       # Planning stages + orchestrator + checkpoints
│   ├── agent/                 # Code retrieval bridge to fitz-sage
│   ├── prompts/               # Externalized .txt prompt templates
│   └── confidence/            # Per-section confidence scoring
├── api_review/                # Anthropic review client + cost calculator
├── config/                    # Pydantic schema + YAML loader
└── validation/                # Input sanitization
```

### Layer Dependencies

```
config/        <- NO imports from planning/, tools/, cli
models/        <- May import from config/
tools/         <- May import from models/, config/
planning/      <- May import from llm/, models/, config/
cli/server     <- May import from all (user-facing layer)
```

### Key Rules

1. **File path comment required** — First line of every Python file: `# fitz_forge/path/to/file.py`
2. **No stdout** — MCP uses stdio. All logging goes to stderr via `logging`. Never `print()`.
3. **No legacy code** — No backwards compat shims. Delete completely when removing.

---

## How to Contribute

### Reporting Bugs

Open an issue with:
- Clear description of the bug
- Steps to reproduce
- Expected vs actual behavior
- Python version, OS, and LLM provider
- Relevant config snippets (redact API keys)

### Suggesting Features

Open an issue with:
- Clear description of the feature
- Use case / motivation
- Proposed CLI or MCP interface (if applicable)

### Contributing Code

1. **Small PRs are better**: Focused changes are easier to review
2. **One concern per PR**: Don't mix refactoring with features
3. **Tests required**: All new code needs tests
4. **Documentation**: Update relevant docs and CHANGELOG

---

## Pull Request Process

1. **Create a branch**
   ```bash
   git checkout -b feature/my-feature
   # or
   git checkout -b fix/bug-description
   ```

2. **Make your changes**
   - Follow the style guide
   - Add/update tests
   - Update CHANGELOG.md under `[Unreleased]`

3. **Run checks locally**
   ```bash
   # Format code
   ruff format fitz_forge/ tests/

   # Lint
   ruff check fitz_forge/ --fix

   # Run tests
   pytest

   # Type check (optional but recommended)
   mypy fitz_forge/
   ```

4. **Commit with clear messages**
   ```bash
   git commit -m "feat: add hybrid retrieval to agent pipeline"
   git commit -m "fix: handle empty embedding response in gatherer"
   git commit -m "docs: update configuration guide"
   ```

5. **Push and create PR**
   ```bash
   git push origin feature/my-feature
   ```

---

## Testing

### Running Tests

```bash
# All tests
pytest

# With coverage
pytest --cov=fitz_forge

# Specific module
pytest tests/unit/test_pipeline_stages.py

# Stop on first failure
pytest -x

# Verbose output
pytest -v
```

### Writing Tests

- Place tests in `tests/unit/`
- Name files `test_<module>.py`
- Use descriptive test function names: `test_<what>_<condition>_<expected>`
- Test both success and failure cases
- Mock LLM clients — never call real LLM providers in unit tests
- Use the `MockLLMClient` fixture from `conftest.py`

```python
# Good test example
async def test_orchestrator_resumes_from_checkpoint(sqlite_store, mock_llm):
    """Pipeline should resume from last checkpoint after interruption."""
    pipeline = PlanningPipeline(llm=mock_llm, store=sqlite_store)
    result = await pipeline.run(job_id=1, resume=True)

    assert result.status == "COMPLETE"
    assert mock_llm.call_count < full_run_call_count
```

### Test Markers

```python
@pytest.mark.unit          # Fast, no external dependencies
@pytest.mark.integration   # May require LLM or database
```

---

## Style Guide

### Python Style

- **Formatter**: ruff format (100-char line length)
- **Linter**: ruff (E, F, W, I, UP, B, SIM rules)
- **Type hints**: Recommended for public APIs
- **Docstrings**: Google style for public classes/functions

### Naming Conventions

| Item | Convention | Example |
|------|------------|---------|
| Modules | `snake_case` | `architecture_design.py` |
| Classes | `PascalCase` | `PlanningPipeline` |
| Functions | `snake_case` | `create_plan()` |
| Constants | `UPPER_SNAKE` | `DEFAULT_TIMEOUT` |
| Private | `_prefixed` | `_build_prompt()` |

### Commit Message Format

```
type: short description

type(scope): short description
```

Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `perf`

---

## Questions?

- Open a [GitHub issue](https://github.com/yafitzdev/fitz-forge/issues) with the `question` label
- Check existing issues first

Thank you for contributing!
