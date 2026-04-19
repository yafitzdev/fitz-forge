# benchmarks/guard_hook.py
"""Claude Code PreToolUse hook: restrict file reads to a plan-derived allowlist.

Reads the tool-call JSON from stdin. The hook locates its allowlist via
``<cwd>/.claude/fitzforge_allowlist.json`` where ``cwd`` comes from the
hook payload Claude Code writes on stdin. Logs allow/block decisions to
``<cwd>/.claude/guard_log.txt``. Env vars are NOT required (shell-form
quoting across cmd.exe/bash/PowerShell is fragile enough that we avoid
it). If the allowlist file is missing, the hook silently allows — this
lets sessions with no plan scope pass through untouched.

The hook blocks:

* ``Read`` on any ``file_path`` not in the allowlist
* ``Bash`` commands that appear to dump file content (cat/head/tail/less/more/
  type/sed -n) with a path not in the allowlist
* ``Grep`` with a ``path`` scope outside the allowlist

Glob is allowed through — it returns filenames only, not content.

Exit:

* 0 + ``{"hookSpecificOutput": ...}`` on stdout — canonical allow or deny
* 0 silent — implicit allow when no allowlist is configured
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


_BASH_READ_RE = re.compile(
    r"\b(?:cat|head|tail|less|more|type|bat)\s+([^\s|;&<>()]+)"
)
# sed -n printing and awk file arg also leak file content into context.
_SED_PRINT_RE = re.compile(r"\bsed\s+-n\b[^|;&<>()]*?\s([^\s|;&<>()]+)$")


def _load_allowlist(worktree_root: Path) -> set[Path] | None:
    allowlist_file = worktree_root / ".claude" / "fitzforge_allowlist.json"
    if not allowlist_file.exists():
        return None
    data = json.loads(allowlist_file.read_text(encoding="utf-8"))
    allowed = data.get("allowed", [])
    resolved: set[Path] = set()
    for rel in allowed:
        candidate = Path(rel)
        if not candidate.is_absolute():
            candidate = (worktree_root / candidate).resolve()
        else:
            candidate = candidate.resolve()
        resolved.add(candidate)
    return resolved


def _log(worktree_root: Path, msg: str) -> None:
    log_path = worktree_root / ".claude" / "guard_log.txt"
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def _is_allowed(path_str: str, allowlist: set[Path], cwd: Path) -> bool:
    if not path_str:
        # Unusable paths (e.g. empty file_path) — block conservatively
        return False
    try:
        p = Path(path_str)
        if not p.is_absolute():
            p = (cwd / p).resolve()
        else:
            p = p.resolve()
    except Exception:
        return False
    if p in allowlist:
        return True
    # Directory match: file under an allowed directory
    for allowed in allowlist:
        try:
            p.relative_to(allowed)
            return True
        except ValueError:
            continue
    return False


def _bash_reads_blocked(cmd: str, allowlist: set[Path], cwd: Path) -> tuple[bool, str]:
    """Return (blocked, reason) if the bash command dumps a non-allowlisted file."""
    hits: list[str] = []
    for m in _BASH_READ_RE.finditer(cmd):
        hits.append(m.group(1))
    for m in _SED_PRINT_RE.finditer(cmd):
        hits.append(m.group(1))
    bad = [h for h in hits if not _is_allowed(h, allowlist, cwd)]
    if bad:
        return True, f"file-read to non-allowlisted path(s): {', '.join(bad[:3])}"
    return False, ""


def _emit_decision(decision: str, reason: str) -> int:
    """Write canonical PreToolUse decision JSON to stdout and exit 0."""
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }
    sys.stdout.write(json.dumps(payload))
    return 0


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception as e:
        # Malformed input — don't block the agent; just pass through.
        sys.stderr.write(f"guard_hook: malformed stdin: {e}\n")
        return 0

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {}) or {}
    cwd = Path(payload.get("cwd", os.getcwd()))

    allowlist = _load_allowlist(cwd)
    if allowlist is None:
        # No guard configured — allow silently.
        return 0

    # Read
    if tool_name == "Read":
        fp = tool_input.get("file_path", "")
        if _is_allowed(fp, allowlist, cwd):
            _log(cwd, f"ALLOW Read {fp}")
            return 0
        _log(cwd, f"BLOCK Read {fp}")
        return _emit_decision(
            "deny",
            (
                f"{fp} is not in the plan's referenced files. "
                "Stay within the plan scope — work from files already in "
                "the plan artifacts. If you genuinely need a different "
                "file, state it in your final answer instead of reading it."
            ),
        )

    # Bash — check for file-content-dumping commands
    if tool_name == "Bash":
        cmd = tool_input.get("command", "") or ""
        blocked, reason = _bash_reads_blocked(cmd, allowlist, cwd)
        if blocked:
            _log(cwd, f"BLOCK Bash {cmd[:120]!r} :: {reason}")
            return _emit_decision(
                "deny",
                (
                    f"Bash command {reason}. "
                    "Stay within the plan scope — read only files listed in "
                    "the plan artifacts."
                ),
            )
        _log(cwd, f"ALLOW Bash {cmd[:80]!r}")
        return 0

    # Grep — block if path scope is outside allowlist
    if tool_name == "Grep":
        path = tool_input.get("path", "") or str(cwd)
        if not _is_allowed(path, allowlist, cwd):
            _log(cwd, f"BLOCK Grep path={path!r}")
            return _emit_decision(
                "deny",
                (
                    f"Grep scope {path} escapes the plan's referenced files. "
                    "Scope the search to a file or directory in the plan."
                ),
            )
        _log(cwd, f"ALLOW Grep path={path!r}")
        return 0

    # Any other matched tool — allow silently
    return 0


if __name__ == "__main__":
    sys.exit(main())
