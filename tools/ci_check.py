# tools/ci_check.py
"""
Local pre-push verification script.

Runs the same checks as CI, locally. Use before pushing to catch issues early.

Usage:
    python tools/ci_check.py          # Format + lint only (fast)
    python tools/ci_check.py --test   # Format + lint + tier1 tests
    python tools/ci_check.py --full   # Format + lint + all tests
"""

import subprocess
import sys
import time


def _run(label: str, cmd: list[str]) -> bool:
    """Run a command and report pass/fail."""
    print(f"\n{'=' * 60}")
    print(f"  {label}")
    print(f"{'=' * 60}")
    t0 = time.monotonic()
    result = subprocess.run(cmd)
    elapsed = time.monotonic() - t0
    status = "PASS" if result.returncode == 0 else "FAIL"
    print(f"  [{status}] {label} ({elapsed:.1f}s)")
    return result.returncode == 0


def main() -> None:
    args = sys.argv[1:]
    run_tests = "--test" in args
    run_full = "--full" in args

    results: list[tuple[str, bool]] = []

    # 1. Format check
    ok = _run(
        "ruff format --check",
        [sys.executable, "-m", "ruff", "format", "--check", "fitz_forge/", "tests/"],
    )
    results.append(("Format", ok))

    # 2. Lint check
    ok = _run(
        "ruff check",
        [sys.executable, "-m", "ruff", "check", "fitz_forge/"],
    )
    results.append(("Lint", ok))

    # 3. Tests (optional)
    if run_full:
        ok = _run(
            "pytest (full suite)",
            [sys.executable, "-m", "pytest", "--no-cov", "-q", "--tb=short"],
        )
        results.append(("Tests (full)", ok))
    elif run_tests:
        ok = _run(
            "pytest -m tier1",
            [sys.executable, "-m", "pytest", "-m", "tier1", "--no-cov", "-q", "--tb=short"],
        )
        results.append(("Tests (tier1)", ok))

    # Summary
    print(f"\n{'=' * 60}")
    print("  SUMMARY")
    print(f"{'=' * 60}")
    all_pass = True
    for label, ok in results:
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {label}")
        if not ok:
            all_pass = False

    if all_pass:
        print("\n  All checks passed.")
    else:
        print("\n  Some checks failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
