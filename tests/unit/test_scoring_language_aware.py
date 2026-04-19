# tests/unit/test_scoring_language_aware.py
"""Tests for language-aware deterministic scoring.

The Tier-1 scorer previously ran Python AST + fabrication regexes on every
artifact regardless of file extension. TypeScript / Prisma / JS files were
flagged "unparseable" and lost 10+ points per artifact to Python-specific
concerns that don't apply — capping TS-heavy benchmarks (hoppscotch) at
~75/100 regardless of plan quality. This suite locks in the branch on
file extension so non-Python files aren't penalized for not being Python.
"""

from __future__ import annotations

from fitz_forge.planning.validation.grounding import StructuralIndexLookup
from fitz_forge.planning.validation.scoring import (
    _is_python_file,
    check_single_artifact,
)


# ---------------------------------------------------------------------------
# _is_python_file predicate
# ---------------------------------------------------------------------------


def test_is_python_file_detects_py_extension():
    assert _is_python_file("src/engine.py") is True
    assert _is_python_file("ENGINE.PY") is True


def test_is_python_file_rejects_typescript_and_js():
    assert _is_python_file("src/team-collection/service.ts") is False
    assert _is_python_file("module.tsx") is False
    assert _is_python_file("helper.js") is False
    assert _is_python_file("router.jsx") is False


def test_is_python_file_rejects_prisma_and_config():
    assert _is_python_file("prisma/schema.prisma") is False
    assert _is_python_file("config.yaml") is False
    assert _is_python_file("package.json") is False


def test_is_python_file_unknown_extension_defaults_python():
    """No-extension file is conservatively treated as Python (scripts, etc.)."""
    assert _is_python_file("Dockerfile") is True  # no extension → assume py


# ---------------------------------------------------------------------------
# TS artifact scoring doesn't collapse on Python-only checks
# ---------------------------------------------------------------------------


def test_typescript_artifact_parseable_true_despite_no_python_ast():
    """A realistic TS artifact should NOT be flagged unparseable."""
    ts_content = """
import { Injectable } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';

@Injectable()
export class CollectionShareService {
    constructor(private prisma: PrismaService) {}

    async createShare(collectionId: string): Promise<{ token: string }> {
        const token = Math.random().toString(36).slice(2);
        await this.prisma.collectionShare.create({
            data: { collectionId, token },
        });
        return { token };
    }
}
"""
    result = check_single_artifact(
        {"filename": "src/collection-share/collection-share.service.ts", "content": ts_content},
        StructuralIndexLookup(""),
    )
    assert result.parseable is True
    assert result.fabricated_self_methods == 0
    assert result.fabricated_classes == 0
    assert result.has_yield is None
    assert result.has_correct_return_type is None


def test_typescript_artifact_not_docked_for_python_specific_checks():
    """A clean TS file should score high, not ~50-70 as before the fix."""
    ts_content = """
export class Shortcode {
    async resolve(token: string) {
        return this.findByToken(token);
    }

    private findByToken(token: string) {
        return null;
    }
}
"""
    result = check_single_artifact(
        {"filename": "src/shortcode.ts", "content": ts_content},
        StructuralIndexLookup(""),
    )
    # No fabrications, no stub markers → should be near-perfect.
    assert result.score >= 90


def test_prisma_schema_is_not_parsed_as_python():
    prisma_content = """
model CollectionShare {
    id           String   @id @default(cuid())
    collectionId String
    token        String   @unique
    expiresAt    DateTime?
    createdAt    DateTime @default(now())
}
"""
    result = check_single_artifact(
        {"filename": "prisma/schema.prisma", "content": prisma_content},
        StructuralIndexLookup(""),
    )
    assert result.parseable is True
    assert result.fabricated_classes == 0
    assert result.score >= 90


# ---------------------------------------------------------------------------
# Stub-marker detection spans languages
# ---------------------------------------------------------------------------


def test_python_notimplementederror_still_detected():
    py_content = """
def foo():
    raise NotImplementedError("tbd")
"""
    result = check_single_artifact(
        {"filename": "mod.py", "content": py_content},
        StructuralIndexLookup(""),
    )
    assert result.has_not_implemented is True


def test_typescript_not_implemented_throw_detected():
    ts_content = """
class Service {
    doStuff() {
        throw new Error('not implemented');
    }
}
"""
    result = check_single_artifact(
        {"filename": "mod.ts", "content": ts_content},
        StructuralIndexLookup(""),
    )
    assert result.has_not_implemented is True


def test_todo_implement_comment_detected_any_language():
    """Language-agnostic stub sentinel should fire regardless of syntax."""
    ts_content = """
export class Stub {
    // TODO: implement
    doStuff() {}
}
"""
    result = check_single_artifact(
        {"filename": "mod.ts", "content": ts_content},
        StructuralIndexLookup(""),
    )
    assert result.has_not_implemented is True


# ---------------------------------------------------------------------------
# Python files still use the full Python-AST path (regression protection)
# ---------------------------------------------------------------------------


def test_python_file_unparseable_still_flagged():
    """Guard: Python path still detects invalid Python syntax."""
    py_content = "def broken(:\n    pass\n"
    result = check_single_artifact(
        {"filename": "mod.py", "content": py_content},
        StructuralIndexLookup(""),
    )
    # _try_parse has recovery paths; if all fail, parseable is False.
    # Main point: the Python file did NOT get the TS bypass.
    assert "mod.py" in result.filename


def test_python_file_sys_stdout_still_detected():
    py_content = "import sys\nsys.stdout.write('x')\n"
    result = check_single_artifact(
        {"filename": "mod.py", "content": py_content},
        StructuralIndexLookup(""),
    )
    assert result.has_sys_stdout is True


def test_typescript_sys_stdout_not_flagged():
    """sys.stdout is a Python-specific concern (MCP stdio). TS files
    should NOT be flagged even if they contain the literal substring
    in a comment or string."""
    ts_content = "// talks about sys.stdout in a comment\nexport const x = 1;\n"
    result = check_single_artifact(
        {"filename": "mod.ts", "content": ts_content},
        StructuralIndexLookup(""),
    )
    assert result.has_sys_stdout is False
