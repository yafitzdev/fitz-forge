# tools/pre_release.py
"""
Pre-release validation script.

Runs all quality checks before tagging a release. Catches issues that
would fail CI or produce a broken PyPI package.

Usage:
    python -m tools.pre_release          # Check only
    python -m tools.pre_release --fix    # Auto-fix formatting + lint, then check
"""

import subprocess
import sys
import time


def _run(label: str, cmd: list[str], *, allow_fail: bool = False) -> bool:
    """Run a command and report pass/fail."""
    print(f"\n--- {label} ---")
    t0 = time.monotonic()
    result = subprocess.run(cmd, capture_output=not sys.stdout.isatty())
    elapsed = time.monotonic() - t0
    status = "PASS" if result.returncode == 0 else "FAIL"
    print(f"  [{status}] {label} ({elapsed:.1f}s)")
    if result.returncode != 0 and not allow_fail:
        if result.stdout:
            print(result.stdout.decode(errors="replace")[-500:])
        if result.stderr:
            print(result.stderr.decode(errors="replace")[-500:])
    return result.returncode == 0


def main() -> None:
    fix_mode = "--fix" in sys.argv

    results: list[tuple[str, bool]] = []
    py = sys.executable

    # Step 1: Auto-fix (if --fix)
    if fix_mode:
        print("\n=== AUTO-FIX MODE ===")
        _run("ruff format (fix)", [py, "-m", "ruff", "format", "fitz_forge/", "tests/"])
        _run("ruff check (fix)", [py, "-m", "ruff", "check", "fitz_forge/", "--fix"])

    # Step 2: Format verification
    ok = _run("Format check", [py, "-m", "ruff", "format", "--check", "fitz_forge/", "tests/"])
    results.append(("Format", ok))

    # Step 3: Lint verification
    ok = _run("Lint check", [py, "-m", "ruff", "check", "fitz_forge/"])
    results.append(("Lint", ok))

    # Step 4: Critical imports (simulates CI with minimal deps)
    ok = _run(
        "Critical imports",
        [
            py,
            "-c",
            "from fitz_forge.cli import app; "
            "from fitz_forge.server import mcp; "
            "from fitz_forge.planning.pipeline.orchestrator import PlanningPipeline; "
            "print('All critical imports OK')",
        ],
    )
    results.append(("Imports", ok))

    # Step 5: Full test suite
    ok = _run("Test suite", [py, "-m", "pytest", "--no-cov", "-q", "--tb=short"])
    results.append(("Tests", ok))

    # Step 6: Build check
    ok = _run("Build package", [py, "-m", "build", "--sdist", "--wheel"])
    results.append(("Build", ok))

    # Summary
    print(f"\n{'=' * 60}")
    print("  PRE-RELEASE SUMMARY")
    print(f"{'=' * 60}")
    all_pass = True
    for label, ok in results:
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {label}")
        if not ok:
            all_pass = False

    if all_pass:
        print("\n  Ready to release.")
    else:
        print("\n  Fix issues before releasing.")
        sys.exit(1)


if __name__ == "__main__":
    main()
