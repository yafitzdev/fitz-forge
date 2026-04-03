# fitz_forge/planning/pipeline/stages/synthesis.py
"""
Synthesis stage: narrate pre-solved decisions into the final plan.

Receives all committed decision records + constraints. The model is narrating
pre-solved problems, not discovering anything new. Uses existing per-field
extraction, self-critique, and coherence checking.

Output: same PlanOutput format (ContextOutput + ArchitectureOutput + DesignOutput
+ RoadmapOutput + RiskOutput).
"""

import difflib
import json
import logging
import os
import re
import time
from typing import Any

from fitz_forge.planning.pipeline.stages.base import (
    SYSTEM_PROMPT,
    PipelineStage,
    StageResult,
    extract_json,
)
from fitz_forge.planning.prompts import load_prompt
from fitz_forge.planning.schemas import (
    ArchitectureOutput,
    ContextOutput,
    DesignOutput,
    RiskOutput,
    RoadmapOutput,
)

logger = logging.getLogger(__name__)

# Field groups for per-field extraction (same schemas as classic pipeline).

_CONTEXT_FIELD_GROUPS = [
    {
        "label": "description",
        "fields": ["project_description", "key_requirements", "constraints", "existing_context"],
        "schema": json.dumps({
            "project_description": "1-3 sentence specific description of what is being built",
            "key_requirements": ["concrete testable requirement 1", "requirement 2"],
            "constraints": ["real binding constraint 1", "constraint 2"],
            "existing_context": "existing codebase or tech context, or empty string if none",
        }, indent=2),
    },
    {
        "label": "stakeholders",
        "fields": ["stakeholders", "scope_boundaries"],
        "schema": json.dumps({
            "stakeholders": ["stakeholder with specific concern"],
            "scope_boundaries": {
                "in_scope": ["specific feature or capability"],
                "out_of_scope": ["explicitly excluded feature"],
            },
        }, indent=2),
    },
    {
        "label": "files",
        "fields": ["existing_files", "needed_artifacts"],
        "schema": json.dumps({
            "existing_files": ["path/to/relevant/file.py -- what it does"],
            "needed_artifacts": ["new_file.py -- what it produces (empty list [] if already implemented)"],
        }, indent=2),
    },
    {
        "label": "assumptions",
        "fields": ["assumptions"],
        "schema": json.dumps({
            "assumptions": [
                {"assumption": "what you assumed", "impact": "what changes if wrong", "confidence": "low|medium|high"}
            ],
        }, indent=2),
    },
]

_ARCH_FIELD_GROUPS = [
    {
        "label": "approaches",
        "fields": ["approaches", "recommended", "reasoning", "scope_statement"],
        "schema": json.dumps({
            "approaches": [
                {
                    "name": "Approach A",
                    "description": "What it looks like in production",
                    "pros": ["advantage"],
                    "cons": ["disadvantage"],
                    "complexity": "low|medium|high",
                    "best_for": ["scenario"],
                },
            ],
            "recommended": "must match one approach name exactly",
            "reasoning": "why this approach is right AND why the other is wrong",
            "scope_statement": "1-2 sentences characterizing the effort",
        }, indent=2),
    },
    {
        "label": "tradeoffs",
        "fields": ["key_tradeoffs", "technology_considerations"],
        "schema": json.dumps({
            "key_tradeoffs": {"tradeoff_name": "description"},
            "technology_considerations": ["technology with reason"],
        }, indent=2),
    },
]

_DESIGN_FIELD_GROUPS = [
    {
        "label": "adrs",
        "fields": ["adrs"],
        "schema": json.dumps({
            "adrs": [
                {
                    "title": "ADR: Decision Title",
                    "context": "What problem this solves",
                    "decision": "What was decided",
                    "rationale": "Why this is right",
                    "consequences": ["consequence"],
                    "alternatives_considered": ["Alternative -- rejected because reason"],
                }
            ],
        }, indent=2),
    },
    {
        "label": "components",
        "fields": ["components", "data_model"],
        "schema": json.dumps({
            "components": [
                {
                    "name": "ComponentName",
                    "purpose": "What it does",
                    "responsibilities": ["responsibility"],
                    "interfaces": ["methodName(param: Type) -> ReturnType"],
                    "dependencies": ["OtherComponent"],
                }
            ],
            "data_model": {"EntityName": ["field: type"]},
        }, indent=2),
    },
    {
        "label": "integrations",
        "fields": ["integration_points"],
        "schema": json.dumps({
            "integration_points": ["ExternalSystem -- what and how"],
        }, indent=2),
    },
    {
        "label": "artifacts",
        "fields": ["artifacts"],
        "schema": json.dumps({
            "artifacts": [
                {
                    "filename": "path/to/file",
                    "content": "complete file content",
                    "purpose": "why this artifact exists",
                }
            ],
        }, indent=2),
    },
]

_ROADMAP_FIELD_GROUPS = [
    {
        "label": "phases",
        "fields": ["phases"],
        "schema": json.dumps({
            "phases": [
                {
                    "number": 1,
                    "name": "Phase Name",
                    "objective": "What this phase achieves",
                    "deliverables": ["specific deliverable"],
                    "dependencies": [],
                    "estimated_complexity": "low|medium|high",
                    "key_risks": ["risk"],
                    "verification_command": "pytest tests/test_something.py -v",
                    "estimated_effort": "~2 hours",
                }
            ],
        }, indent=2),
    },
    {
        "label": "scheduling",
        "fields": ["critical_path", "parallel_opportunities", "total_phases"],
        "schema": json.dumps({
            "critical_path": [1, 2, 4],
            "parallel_opportunities": [[3, 5]],
            "total_phases": 5,
        }, indent=2),
    },
]

_RISK_FIELD_GROUPS = [
    {
        "label": "risks",
        "fields": ["risks", "overall_risk_level", "recommended_contingencies"],
        "schema": json.dumps({
            "risks": [
                {
                    "category": "technical|external|resource|schedule|quality|security",
                    "description": "What could go wrong",
                    "impact": "low|medium|high|critical",
                    "likelihood": "low|medium|high",
                    "mitigation": "Specific mitigation action",
                    "contingency": "What to do if it happens",
                    "affected_phases": [1, 3],
                    "verification": "assert something",
                }
            ],
            "overall_risk_level": "low|medium|high",
            "recommended_contingencies": ["contingency action"],
        }, indent=2),
    },
]


def _truncate_at_line(text: str, max_chars: int) -> str:
    """Truncate text at a line boundary, never mid-line."""
    if len(text) <= max_chars:
        return text
    # Find last newline before max_chars
    cut = text.rfind("\n", 0, max_chars)
    if cut == -1:
        cut = max_chars
    return text[:cut]


def _resolve_imported_type_apis(
    source: str,
    prior_outputs: dict[str, Any],
) -> str:
    """Resolve public APIs for types imported or used as local variables.

    Parses the target file for:
      - `from X import ClassName` → look up ClassName's public methods
      - `var: ClassName = ...` → look up ClassName's public methods
      - `def func(...) -> ClassName` → look up ClassName's public methods

    Returns a compact cheat sheet appended to the interface section.
    This prevents the model from fabricating methods on imported types
    (e.g. inventing service.query_stream() when FitzService only has query()).

    Generic — works for any codebase, not hardcoded to specific classes.
    """
    import ast as _ast

    if not source:
        return ""

    try:
        tree = _ast.parse(source)
    except SyntaxError:
        return ""

    # Collect type names from imports, annotations, and return types
    imported_types: set[str] = set()
    # Track imported functions to resolve their return types
    imported_funcs: dict[str, str] = {}  # func_name -> module_path

    for node in _ast.walk(tree):
        # from X import ClassName or function
        if isinstance(node, _ast.ImportFrom) and node.module:
            for alias in node.names:
                name = alias.name
                if name[0].isupper() and not name.startswith("_"):
                    imported_types.add(name)
                elif name[0].islower() and not name.startswith("_"):
                    # Imported function — resolve its return type later
                    imported_funcs[name] = node.module
        # var: ClassName = ... (annotations)
        if isinstance(node, _ast.AnnAssign) and isinstance(node.annotation, _ast.Name):
            name = node.annotation.id
            if name[0].isupper():
                imported_types.add(name)
        # def func(...) -> ClassName (return type hints)
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            if node.returns and isinstance(node.returns, _ast.Name):
                name = node.returns.id
                if name[0].isupper():
                    imported_types.add(name)
            # Parameter type hints
            for arg in node.args.args:
                if arg.annotation and isinstance(arg.annotation, _ast.Name):
                    name = arg.annotation.id
                    if name[0].isupper():
                        imported_types.add(name)

    # Filter out builtins and stdlib types
    _SKIP = {"Any", "Optional", "Iterator", "AsyncIterator", "Callable",
             "Dict", "List", "Tuple", "Set", "Type", "Union", "None",
             "Depends", "Request", "Response", "HTTPException",
             "BaseModel", "Field", "Query"}

    # Resolve return types of imported functions (e.g. get_service -> FitzService)
    source_dir = prior_outputs.get("_source_dir", "")
    if imported_funcs and source_dir:
        from pathlib import Path as _P
        for func_name, module_path in imported_funcs.items():
            rel_path = module_path.replace(".", "/") + ".py"
            func_file = _P(source_dir) / rel_path
            if not func_file.is_file():
                continue
            try:
                func_src = func_file.read_text(encoding="utf-8", errors="replace")
                func_tree = _ast.parse(func_src)
            except (OSError, SyntaxError):
                continue
            for fnode in _ast.walk(func_tree):
                if (isinstance(fnode, (_ast.FunctionDef, _ast.AsyncFunctionDef))
                        and fnode.name == func_name
                        and fnode.returns
                        and isinstance(fnode.returns, _ast.Name)):
                    ret_type = fnode.returns.id
                    if ret_type[0].isupper() and ret_type not in _SKIP:
                        imported_types.add(ret_type)
                    break

    imported_types -= _SKIP

    if not imported_types:
        return ""

    # Look up public methods for each type
    agent_ctx = prior_outputs.get("_agent_context", {})
    full_index = agent_ctx.get("full_structural_index", "")
    if not full_index:
        full_index = prior_outputs.get("_gathered_context", "")

    source_dir = prior_outputs.get("_source_dir", "")
    lines = []

    if full_index:
        from fitz_forge.planning.validation.grounding import StructuralIndexLookup
        lookup = StructuralIndexLookup(full_index)

        for type_name in sorted(imported_types):
            cls = lookup.find_class(type_name)
            if cls and cls.methods:
                meths = [
                    m for m in cls.methods
                    if not m.startswith("__")
                ]
                if meths:
                    sig_parts = []
                    for mname in meths[:10]:
                        minfo = cls.methods[mname]
                        sig = mname
                        if minfo.return_type:
                            sig += f" -> {minfo.return_type}"
                        sig_parts.append(sig)
                    lines.append(
                        f"{type_name} methods: {', '.join(sig_parts)}"
                    )

    # Fallback: parse source files on disk for types not found in index
    if source_dir:
        from pathlib import Path as _Path
        found_in_index = {l.split(" methods:")[0] for l in lines}
        missing = imported_types - found_in_index

        for type_name in missing:
            # Search disk for class definition
            for py in _Path(source_dir).rglob("*.py"):
                if ".venv" in str(py) or "__pycache__" in str(py):
                    continue
                stem = py.stem.lower()
                tn_lower = type_name.lower()
                if len(stem) < 3 or (tn_lower not in stem and stem not in tn_lower
                                      and "service" not in stem and "model" not in stem):
                    continue
                try:
                    src = py.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if f"class {type_name}" not in src:
                    continue
                try:
                    file_tree = _ast.parse(src)
                except SyntaxError:
                    continue
                for cnode in _ast.walk(file_tree):
                    if isinstance(cnode, _ast.ClassDef) and cnode.name == type_name:
                        meths = []
                        for child in _ast.iter_child_nodes(cnode):
                            if isinstance(child, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                                if child.name.startswith("_"):
                                    continue
                                params = [
                                    a.arg for a in child.args.args
                                    if a.arg != "self"
                                ]
                                sig = f"{child.name}({', '.join(params)})"
                                if child.returns:
                                    try:
                                        sig += f" -> {_ast.unparse(child.returns)}"
                                    except Exception:
                                        pass
                                meths.append(sig)
                        if meths:
                            lines.append(
                                f"{type_name} methods: {', '.join(meths[:10])}"
                            )
                        break
                if type_name in {l.split(" methods:")[0] for l in lines}:
                    break

    if lines:
        header = "## IMPORTED TYPE APIs (use ONLY these methods)"
        return header + "\n" + "\n".join(lines)
    return ""


def _extract_param_type_fields(
    reference_body: str,
    source_dir: str,
) -> str:
    """Extract field names from parameter types in a reference method.

    Parses type annotations like `query: Query` from the method signature,
    finds the class definition on disk, and returns field names. This tells
    the model that Query has (text, constraints, metadata) so it doesn't
    fabricate fields like conversation_context or history.
    """
    import ast as _ast
    import re as _re
    from pathlib import Path as _Path

    if not source_dir:
        return ""

    # Extract type names from the method signature (first line)
    # Match patterns like `param: TypeName` or `param: TypeName | None`
    type_names = set(_re.findall(
        r':\s*([A-Z][A-Za-z]+)\b', reference_body.split("\n")[0],
    ))
    # Also catch return types
    ret_match = _re.search(r'->\s*([A-Z][A-Za-z]+)', reference_body.split("\n")[0])
    if ret_match:
        type_names.add(ret_match.group(1))

    if not type_names:
        return ""

    lines = []
    for type_name in sorted(type_names):
        # Skip builtins
        if type_name in ("None", "Any", "Optional", "Iterator", "Callable",
                         "Dict", "List", "Tuple", "Set", "Type", "Union"):
            continue
        # Find source file containing this class
        fields = _extract_class_fields(type_name, {}, source_dir)
        if fields:
            lines.append(f"{type_name} fields: {', '.join(fields)}")

    return "\n".join(lines)


def _extract_reference_method(
    disk_source: str,
    purpose: str,
    relevant_decisions: str,
) -> str:
    """Extract the body of an existing method that the new artifact variants.

    When creating answer_stream() (streaming variant of answer()), the model
    needs to see answer()'s actual implementation to know how to chain
    _query_rewriter, _retrieval_router, _reader, etc. Without this, it
    fabricates internal API calls (F9).

    Heuristic: look for method names referenced in purpose/decisions that
    exist in the source, pick the longest public method as the reference.
    """
    import ast as _ast

    combined = (purpose + " " + relevant_decisions).lower()

    # Detect variant patterns: "streaming version of X", "parallel to X",
    # "same as X but", "mirrors X", "variant of X"
    import re as _re
    variant_patterns = [
        r'(?:stream|async|parallel)\s+(?:version|variant|equivalent)\s+of\s+(\w+)',
        r'(?:mirrors?|same\s+as|like|follows?)\s+(?:`?(\w+)`?\(?\)?)',
        r'(?:stream|async)\w*\s+(?:method|version)\s+.*?(?:of|for)\s+(?:`?(\w+)`?\(?\)?)',
        r'(\w+)\(\)\s+(?:but|except|with)\s+stream',
    ]
    target_methods: list[str] = []
    for pat in variant_patterns:
        for m in _re.finditer(pat, combined):
            name = m.group(1) or (m.group(2) if m.lastindex >= 2 else None)
            if name and not name.startswith("_"):
                target_methods.append(name)

    # Also check for common patterns: "answer_stream" implies "answer" is the ref
    method_refs = _re.findall(r'(\w+)_stream\b', combined)
    target_methods.extend(method_refs)
    method_refs = _re.findall(r'stream_(\w+)\b', combined)
    target_methods.extend(method_refs)

    # Deduplicate, filter out noise
    target_methods = list(dict.fromkeys(
        m for m in target_methods
        if m not in ("self", "the", "a", "an", "this", "add", "new", "method")
    ))

    if not target_methods:
        return ""

    try:
        tree = _ast.parse(disk_source)
    except SyntaxError:
        return ""

    # Find matching method bodies
    lines = disk_source.split("\n")
    best_body = ""
    best_name = ""

    for node in _ast.walk(tree):
        if not isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("_"):
            continue
        if node.name not in target_methods:
            continue

        # Extract the full method body from source lines
        start = node.lineno - 1  # 0-indexed
        end = node.end_lineno if hasattr(node, "end_lineno") else start + 1
        body = "\n".join(lines[start:end])

        if len(body) > len(best_body):
            best_body = body
            best_name = node.name

    if best_body:
        # Cap at 16K chars to avoid blowing prompt budget
        if len(best_body) > 16000:
            best_body = best_body[:16000] + "\n    # ... (truncated)"
        logger.info(
            f"F9: extracted reference method '{best_name}' "
            f"({len(best_body)} chars)"
        )

    return best_body


def _extract_method_signatures(content: str, filename: str) -> list[str]:
    """Extract method/function signatures from artifact code for F3 injection.

    Returns lines like:
        engine.py: async def answer_stream(self, query: Query, ...) -> AsyncIterator[str]
    """
    import ast as _ast
    import textwrap as _tw

    sigs = []
    # Try parsing the artifact content
    for attempt in [content, _tw.dedent(content), f"class _:\n    " + content.replace("\n", "\n    ")]:
        try:
            tree = _ast.parse(attempt)
            break
        except SyntaxError:
            continue
    else:
        return sigs

    short = filename.split("/")[-1]
    for node in _ast.walk(tree):
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            if node.name.startswith("_") and node.name != "__init__":
                continue
            prefix = "async def" if isinstance(node, _ast.AsyncFunctionDef) else "def"
            # Reconstruct signature from AST
            args = []
            for arg in node.args.args:
                ann = ""
                if arg.annotation:
                    ann = f": {_ast.unparse(arg.annotation)}"
                args.append(f"{arg.arg}{ann}")
            ret = ""
            if node.returns:
                ret = f" -> {_ast.unparse(node.returns)}"
            sig = f"{prefix} {node.name}({', '.join(args)}){ret}"
            sigs.append(f"{short}: {sig}")

    return sigs


def _compress_reasoning_for_artifact(reasoning: str) -> str:
    """Compress synthesis reasoning for per-artifact prompts.

    The full reasoning covers 5 sections (context, architecture, design,
    roadmap, risk). Artifacts only need architecture + design context.
    Extracts relevant sections and drops roadmap/risk/preamble.

    Typical compression: 12K -> 5K chars.
    """
    if len(reasoning) <= 4000:
        return reasoning  # Short enough, keep everything

    lines = reasoning.split("\n")
    sections: dict[str, list[str]] = {}
    current_section = "_preamble"
    sections[current_section] = []

    # Split into sections by markdown headers
    for line in lines:
        stripped = line.strip().lower()
        if stripped.startswith("### ") or stripped.startswith("## "):
            header = stripped.lstrip("#").strip()
            if any(kw in header for kw in ("context", "requirement", "scope")):
                current_section = "context"
            elif any(kw in header for kw in ("architect", "approach", "pattern")):
                current_section = "architecture"
            elif any(kw in header for kw in ("design", "component", "interface",
                                              "artifact", "adr", "integration",
                                              "data model")):
                current_section = "design"
            elif any(kw in header for kw in ("roadmap", "phase", "milestone",
                                              "implementation plan")):
                current_section = "roadmap"
            elif any(kw in header for kw in ("risk", "mitigation")):
                current_section = "risk"
            else:
                current_section = "other"
            sections.setdefault(current_section, [])
        sections.setdefault(current_section, []).append(line)

    # Keep: architecture + design (essential for artifacts)
    # Summarize: context (first 5 lines only)
    # Drop: roadmap, risk, preamble
    parts = []

    ctx_lines = sections.get("context", [])
    if ctx_lines:
        parts.append("## Context (summary)")
        parts.extend(ctx_lines[:5])

    for key in ("architecture", "design", "other"):
        section_lines = sections.get(key, [])
        if section_lines:
            parts.extend(section_lines)

    result = "\n".join(parts)
    if len(result) < 200:
        # Section detection failed — fall back to first half of reasoning
        return _truncate_at_line(reasoning, len(reasoning) // 2)

    return result


def _extract_class_fields(
    class_name: str,
    file_contents: dict[str, str],
    source_dir: str,
) -> list[str]:
    """Extract Pydantic/dataclass field names from a class definition.

    Returns list of field names like ['question', 'source', 'top_k'].
    Works by finding the class in source and extracting annotated assignments.
    """
    import ast as _ast
    from pathlib import Path as _Path

    # Find source containing this class
    class_marker = f"class {class_name}"
    src = None
    for path, content in (file_contents or {}).items():
        if class_marker in content:
            src = content
            break

    if not src and source_dir:
        cn_lower = class_name.lower()
        for py in _Path(source_dir).rglob("*.py"):
            if ".venv" in str(py) or "__pycache__" in str(py):
                continue
            stem = py.stem.lower()
            if len(stem) >= 4 and (cn_lower in stem or stem in cn_lower
                                    or "schema" in stem or "model" in stem):
                try:
                    content = py.read_text(encoding="utf-8", errors="replace")
                    if class_marker in content:
                        src = content
                        break
                except OSError:
                    continue

    if not src:
        return []

    try:
        tree = _ast.parse(src)
    except SyntaxError:
        return []

    for node in _ast.walk(tree):
        if isinstance(node, _ast.ClassDef) and node.name == class_name:
            fields = []
            for child in _ast.iter_child_nodes(node):
                if isinstance(child, _ast.AnnAssign) and isinstance(child.target, _ast.Name):
                    name = child.target.id
                    if not name.startswith("_"):
                        fields.append(name)
            return fields

    return []


def _build_type_attr_map(
    source: str,
) -> dict[str, str]:
    """Build reverse map: type_name -> attr_name from init assignments.

    Parses __init__/_init_components to find self._xxx = ClassName(...)
    and returns {ClassName: _xxx}. Used by type-aware repair to resolve
    semantic renames like _governance_decider -> _governor (via GovernanceDecider).
    """
    import ast as _ast
    import re as _re

    if not source:
        return {}

    try:
        tree = _ast.parse(source)
    except SyntaxError:
        return {}

    result: dict[str, str] = {}  # type_name -> attr_name
    for cls_node in _ast.iter_child_nodes(tree):
        if not isinstance(cls_node, _ast.ClassDef):
            continue
        for method in _ast.iter_child_nodes(cls_node):
            if not isinstance(method, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                continue
            if method.name not in ("__init__", "_init_components", "setup", "_setup"):
                continue
            for node in _ast.walk(method):
                if not isinstance(node, _ast.Assign):
                    continue
                for target in node.targets:
                    if (isinstance(target, _ast.Attribute)
                            and isinstance(target.value, _ast.Name)
                            and target.value.id == "self"
                            and target.attr.startswith("_")
                            and not target.attr.startswith("__")):
                        rhs = ""
                        if isinstance(node.value, _ast.Call):
                            if isinstance(node.value.func, _ast.Name):
                                rhs = node.value.func.id
                            elif isinstance(node.value.func, _ast.Attribute):
                                rhs = node.value.func.attr
                        if rhs and rhs[0].isupper():  # Only CamelCase type names
                            result[rhs] = target.attr

    return result


def _extract_init_attr_names(source: str) -> set[str]:
    """Extract ALL self._xxx attribute names from init methods.

    Unlike _build_type_attr_map which only captures CamelCase-typed attrs,
    this captures every self._xxx assignment regardless of RHS pattern.
    Used to prevent false repairs on real attrs like _chat_factory.
    """
    import ast as _ast

    if not source:
        return set()

    try:
        tree = _ast.parse(source)
    except SyntaxError:
        return set()

    attrs: set[str] = set()
    for cls_node in _ast.iter_child_nodes(tree):
        if not isinstance(cls_node, _ast.ClassDef):
            continue
        for method in _ast.iter_child_nodes(cls_node):
            if not isinstance(method, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                continue
            if method.name not in ("__init__", "_init_components", "setup", "_setup"):
                continue
            for node in _ast.walk(method):
                if not isinstance(node, _ast.Assign):
                    continue
                for target in node.targets:
                    if (isinstance(target, _ast.Attribute)
                            and isinstance(target.value, _ast.Name)
                            and target.value.id == "self"
                            and target.attr.startswith("_")
                            and not target.attr.startswith("__")):
                        attrs.add(target.attr)

    return attrs


def _build_attr_methods(
    type_attr_map: dict[str, str],
    prior_outputs: dict[str, Any],
) -> dict[str, str]:
    """Map each init attribute to its first public method.

    Uses the structural index or source AST to find the first non-dunder
    method on each component class. Used to fix attr-as-function calls:
    self._assembler() -> self._assembler.assemble()

    Returns {attr_name: first_method_name}.
    """
    import ast as _ast

    if not type_attr_map:
        return {}

    # Try structural index first
    agent_ctx = prior_outputs.get("_agent_context", {})
    full_index = agent_ctx.get("full_structural_index", "")
    if not full_index:
        full_index = prior_outputs.get("_gathered_context", "")

    from fitz_forge.planning.validation.grounding import StructuralIndexLookup
    lookup = StructuralIndexLookup(full_index) if full_index else None

    result: dict[str, str] = {}
    remaining = dict(type_attr_map)  # type_name -> attr_name

    # Strategy 1: structural index
    if lookup:
        for type_name, attr_name in list(remaining.items()):
            cls = lookup.find_class(type_name)
            if cls and cls.methods:
                for mname in cls.methods:
                    if not mname.startswith("_"):
                        result[attr_name] = mname
                        remaining.pop(type_name, None)
                        break

    # Strategy 2: parse source files for remaining types
    if remaining:
        file_contents = agent_ctx.get("file_contents", {})
        source_dir = prior_outputs.get("_source_dir", "")
        all_sources = list((file_contents or {}).values())

        if source_dir:
            from pathlib import Path as _Path
            for _py in _Path(source_dir).rglob("*.py"):
                if not remaining:
                    break
                _stem = _py.stem.lower()
                if not any(t.lower() in _stem or _stem in t.lower()
                           for t in remaining):
                    continue
                if ".venv" in str(_py) or "__pycache__" in str(_py):
                    continue
                try:
                    all_sources.append(
                        _py.read_text(encoding="utf-8", errors="replace"),
                    )
                except OSError:
                    continue

        for _src in all_sources:
            if not remaining:
                break
            try:
                _tree = _ast.parse(_src)
            except SyntaxError:
                continue
            for _node in _ast.walk(_tree):
                if isinstance(_node, _ast.ClassDef) and _node.name in remaining:
                    for _child in _ast.iter_child_nodes(_node):
                        if (isinstance(_child, (_ast.FunctionDef, _ast.AsyncFunctionDef))
                                and not _child.name.startswith("_")):
                            attr_name = remaining.pop(_node.name)
                            result[attr_name] = _child.name
                            break

    return result


def _type_aware_resolve(
    fabricated: str,
    type_attr_map: dict[str, str],
) -> str | None:
    """Try to resolve a fabricated attr name via type name matching.

    Splits the fabricated name into word parts and checks if any part
    matches a CamelCase component of a known type name.

    Example: _governance_decider -> [governance, decider]
             GovernanceDecider -> [Governance, Decider]
             'governance' matches -> real attr is _governor
    """
    import re as _re

    fab_parts = set(fabricated.lstrip("_").lower().split("_"))
    if not fab_parts:
        return None

    best_match: str | None = None
    best_overlap = 0

    for type_name, attr_name in type_attr_map.items():
        # Split CamelCase into lowercase parts
        type_parts = [p.lower() for p in _re.findall(r"[A-Z][a-z]+", type_name)]
        if not type_parts:
            continue
        overlap = len(fab_parts & set(type_parts))
        if overlap > best_overlap:
            best_overlap = overlap
            best_match = attr_name

    return best_match if best_overlap > 0 else None


def _repair_fabricated_refs(
    content: str,
    prior_outputs: dict[str, Any],
    type_attr_map: dict[str, str] | None = None,
    init_attrs: set[str] | None = None,
    attr_methods: dict[str, str] | None = None,
    filename: str = "",
) -> tuple[str, int]:
    """Deterministic post-generation repair of fabricated method references.

    Three repair strategies, applied in order:
    1. Type-aware resolution: match fabricated name against known type names
       (catches semantic renames like _governance_decider -> _governor)
    2. Test method leak filter: remove self.test_*() calls in non-test files
    3. Fuzzy string matching: difflib similarity >= 0.65

    Returns (repaired_content, number_of_fixes).
    """
    import ast as _ast
    import re as _re

    agent_ctx = prior_outputs.get("_agent_context", {})
    full_index = agent_ctx.get("full_structural_index", "")
    if not full_index:
        full_index = prior_outputs.get("_gathered_context", "")
    if not full_index:
        return content, 0

    from fitz_forge.planning.validation.grounding import StructuralIndexLookup
    lookup = StructuralIndexLookup(full_index)

    # Try parsing — handle code fragments via dedent + class wrapper
    import textwrap as _tw
    tree = None
    for attempt in [content, _tw.dedent(content), f"class _:\n    " + content.replace("\n", "\n    ")]:
        try:
            tree = _ast.parse(attempt)
            break
        except SyntaxError:
            continue

    if tree is None:
        return content, 0

    # Pre-collect artifact-defined methods
    artifact_methods = set()
    for n in _ast.walk(tree):
        if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            artifact_methods.add(n.name)

    is_test_file = "test" in filename.lower()
    fixes: dict[str, str] = {}  # fabricated_name -> corrected_name
    removals: list[str] = []  # refs to strip entirely (test leaks)

    for node in _ast.walk(tree):
        ref_name = None
        is_call = False

        # self.method() calls
        if (isinstance(node, _ast.Call)
                and isinstance(node.func, _ast.Attribute)
                and isinstance(node.func.value, _ast.Name)
                and node.func.value.id == "self"):
            ref_name = node.func.attr
            is_call = True

        # self._attr references (not calls)
        elif (isinstance(node, _ast.Attribute)
              and isinstance(node.value, _ast.Name)
              and node.value.id == "self"
              and not isinstance(node.ctx, _ast.Store)):
            ref_name = node.attr

        if not ref_name or ref_name.startswith("__"):
            continue
        if ref_name in fixes or ref_name in removals:
            continue  # Already handled

        # Test method leak: self.test_xxx() in non-test file
        if ref_name.startswith("test_") and is_call and not is_test_file:
            removals.append(ref_name)
            continue

        # Skip if it actually exists
        if lookup.method_exists_anywhere(ref_name):
            continue
        if ref_name in artifact_methods:
            continue
        # Skip if it's a known init attribute (not a method, but real)
        # BUT if it's being called as a function, fix the calling pattern
        if init_attrs and ref_name in init_attrs and is_call and attr_methods:
            first_method = attr_methods.get(ref_name)
            if first_method:
                fixes[f"{ref_name}("] = f"{ref_name}.{first_method}("
                logger.info(
                    f"  repair: self.{ref_name}() -> "
                    f"self.{ref_name}.{first_method}()"
                )
            continue
        if type_attr_map and ref_name in type_attr_map.values():
            continue
        if init_attrs and ref_name in init_attrs:
            continue

        # Strategy 1: Type-aware resolution
        if type_attr_map and ref_name.startswith("_"):
            resolved = _type_aware_resolve(ref_name, type_attr_map)
            if resolved and resolved != ref_name:
                # If resolving to an init attr AND it's a call, route through method
                if is_call and attr_methods and resolved in (init_attrs or set()):
                    first_method = attr_methods.get(resolved)
                    if first_method:
                        fixes[f"{ref_name}("] = f"{resolved}.{first_method}("
                        continue
                fixes[ref_name] = resolved
                continue

        # Strategy 2: Fuzzy string matching
        suggestions = lookup.suggest_method(ref_name)
        if suggestions:
            best = suggestions[0]
            ratio = difflib.SequenceMatcher(
                None, ref_name, best,
            ).ratio()
            if ratio >= 0.82:
                fixes[ref_name] = best

    # Apply fixes via string replacement
    repaired = content
    for fabricated, corrected in fixes.items():
        repaired = repaired.replace(
            f"self.{fabricated}", f"self.{corrected}",
        )
        logger.info(
            f"  repair: self.{fabricated} -> self.{corrected}"
        )

    # Remove test method leak lines entirely
    for test_ref in removals:
        # Remove lines containing self.test_xxx(
        lines = repaired.split("\n")
        repaired = "\n".join(
            line for line in lines
            if f"self.{test_ref}" not in line
        )
        logger.info(
            f"  repair: removed test leak self.{test_ref}()"
        )

    # NOTE: Hardcoded _INVALID_FIELD_PATTERNS removed. They were
    # codebase-specific (fitz-sage) and could produce wrong corrections
    # (e.g. request.question → request.message when QueryRequest DOES
    # have .question). F2 is now solved by prompt reorder + schema
    # field injection + F9 param type extraction.
    field_fixes = 0

    # F5 fix: repair wrong import paths using structural index
    import_fixes = 0
    if lookup:
        import_lines = repaired.split("\n")
        new_lines = []
        for line in import_lines:
            m = _re.match(r'^(\s*from\s+)([\w.]+)(\s+import\s+)(.+)$', line)
            if not m:
                new_lines.append(line)
                continue
            prefix, module_path, imp_kw, names_str = m.groups()
            # Skip stdlib / third-party
            top_pkg = module_path.split(".")[0]
            if top_pkg in ("typing", "dataclasses", "collections", "abc",
                           "enum", "pathlib", "os", "sys", "json", "re",
                           "asyncio", "logging", "pydantic", "fastapi",
                           "httpx", "pytest"):
                new_lines.append(line)
                continue
            # Check each imported name against structural index
            names = [n.strip().split(" as ")[0].strip() for n in names_str.split(",")]
            correct_module = None
            for name in names:
                classes = lookup.classes.get(name)
                if classes and len(classes) == 1:
                    cls_file = classes[0].file
                    cls_module = cls_file.replace("/", ".").replace("\\", ".").removesuffix(".py")
                    if cls_module != module_path:
                        correct_module = cls_module
                        break
                elif classes and len(classes) > 1:
                    logger.info(
                        f"  import: skipping ambiguous '{name}' "
                        f"({len(classes)} locations)"
                    )
            if correct_module:
                new_line = f"{prefix}{correct_module}{imp_kw}{names_str}"
                new_lines.append(new_line)
                import_fixes += 1
                logger.info(f"  repair: import {module_path} -> {correct_module}")
            else:
                new_lines.append(line)
        if import_fixes:
            repaired = "\n".join(new_lines)

    total_fixes = len(fixes) + len(removals) + field_fixes + import_fixes
    return repaired, total_fixes


def _build_attribute_template(
    referenced_files: set[str],
    prior_outputs: dict[str, Any],
    sections: dict[str, list[str]],
) -> str:
    """Extract instance attributes from source code of referenced files.

    Parses __init__ and setup methods to find self._xxx = ClassName(...)
    assignments. Produces a compact template telling the model what
    attributes actually exist on each class.

    This prevents the model from fabricating method names like
    self._build_context() when the real attribute is self._assembler.
    """
    import ast as _ast

    agent_ctx = prior_outputs.get("_agent_context", {})
    file_contents = agent_ctx.get("file_contents", {})
    if not file_contents:
        return ""

    lines = [
        "\n## INSTANCE ATTRIBUTES — real self._ attributes on key classes\n"
        "When writing new methods on these classes, use ONLY the attributes "
        "listed below. Do NOT invent helper methods or attributes.\n"
    ]
    found_any = False

    for ref_path in sorted(referenced_files):
        # Find source content
        content = file_contents.get(ref_path, "")
        if not content:
            for key in file_contents:
                if key.endswith(ref_path) or ref_path.endswith(key):
                    content = file_contents[key]
                    break
        if not content:
            continue

        try:
            tree = _ast.parse(content)
        except SyntaxError:
            continue

        for cls_node in _ast.iter_child_nodes(tree):
            if not isinstance(cls_node, _ast.ClassDef):
                continue

            # Extract self._xxx = ... assignments from __init__ and setup methods
            attrs: dict[str, str] = {}  # attr_name -> type_hint
            for method in _ast.iter_child_nodes(cls_node):
                if not isinstance(method, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                    continue
                if method.name not in ("__init__", "_init_components", "setup", "_setup"):
                    continue
                for node in _ast.walk(method):
                    if not isinstance(node, _ast.Assign):
                        continue
                    for target in node.targets:
                        if (isinstance(target, _ast.Attribute)
                                and isinstance(target.value, _ast.Name)
                                and target.value.id == "self"
                                and target.attr.startswith("_")
                                and not target.attr.startswith("__")):
                            # Resolve type from RHS
                            rhs = ""
                            if isinstance(node.value, _ast.Call):
                                if isinstance(node.value.func, _ast.Name):
                                    rhs = node.value.func.id
                                elif isinstance(node.value.func, _ast.Attribute):
                                    rhs = node.value.func.attr
                            if rhs:
                                attrs[target.attr] = rhs

            if not attrs:
                continue

            # Also get method names from the structural index for this class
            class_methods = []
            for idx_path, idx_lines in sections.items():
                if ref_path.endswith(idx_path) or idx_path.endswith(ref_path):
                    for line in idx_lines:
                        if line.startswith("classes:") and cls_node.name in line:
                            # Extract method list from brackets
                            import re
                            bracket_match = re.search(
                                rf'{cls_node.name}[^[]*\[([^\]]+)\]',
                                line,
                            )
                            if bracket_match:
                                for m in bracket_match.group(1).split(","):
                                    m = m.strip().split("->")[0].split("(")[0].strip()
                                    if m and not m.startswith("@"):
                                        class_methods.append(m)
                    break

            # Look up public methods for each component type.
            # Search both the structural index AND source code (for classes
            # not in the 30-file selected index).
            import re as _re
            component_methods: dict[str, list[str]] = {}  # type_name -> [method sigs]

            # Strategy 1: structural index
            for _idx_lines in sections.values():
                for _line in _idx_lines:
                    if not _line.startswith("classes:"):
                        continue
                    for type_name in set(attrs.values()):
                        if type_name not in _line:
                            continue
                        pattern = rf'{type_name}(?:\([^)]*\))?\s*(?:\[[^\]]*\])?\s*\[([^\]]+)\]'
                        _match = _re.search(pattern, _line)
                        if _match:
                            methods = []
                            for m in _match.group(1).split(","):
                                m = m.strip()
                                if m and not m.startswith("@") and not m.startswith("__"):
                                    methods.append(m)
                            if methods:
                                component_methods[type_name] = methods

            # Strategy 2: parse source files for classes not found in index
            # Search file_contents first, then fall back to disk via source_dir
            missing_types = set(attrs.values()) - set(component_methods.keys())
            source_dir = prior_outputs.get("_source_dir", "")
            all_sources = list((agent_ctx.get("file_contents") or {}).values())

            # Also read from disk for files not in the agent's pool
            if missing_types and source_dir:
                from pathlib import Path as _Path
                for _py in _Path(source_dir).rglob("*.py"):
                    if not missing_types:
                        break
                    # Quick check: does the filename hint at a missing type?
                    _stem = _py.stem.lower()
                    if not any(t.lower() in _stem or _stem in t.lower() for t in missing_types):
                        continue
                    try:
                        all_sources.append(_py.read_text(encoding="utf-8", errors="replace"))
                    except OSError:
                        continue

            for _src in all_sources:
                if not missing_types:
                    break
                try:
                    _tree = _ast.parse(_src)
                except SyntaxError:
                    continue
                for _node in _ast.walk(_tree):
                    if isinstance(_node, _ast.ClassDef) and _node.name in missing_types:
                        meths = []
                        for _child in _ast.iter_child_nodes(_node):
                            if isinstance(_child, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                                if _child.name.startswith("__"):
                                    continue
                                params = [a.arg for a in _child.args.args if a.arg != "self"]
                                sig = f"{_child.name}({', '.join(params)})"
                                if _child.returns:
                                    try:
                                        sig += f" -> {_ast.unparse(_child.returns)}"
                                    except Exception:
                                        pass
                                meths.append(sig)
                        if meths:
                            component_methods[_node.name] = meths
                            missing_types.discard(_node.name)

            lines.append(f"### {cls_node.name} ({ref_path})")
            lines.append("  Attributes:")
            for attr_name, type_name in sorted(attrs.items()):
                # Append component's public methods as inline comment
                comp_meths = component_methods.get(type_name, [])
                if comp_meths:
                    sig_str = ", ".join(comp_meths[:5])
                    lines.append(f"    self.{attr_name} = {type_name}  # has: {sig_str}")
                else:
                    lines.append(f"    self.{attr_name} = {type_name}(...)")
            if class_methods:
                lines.append(f"  Methods: {', '.join(class_methods)}")
            lines.append("")
            found_any = True

    if not found_any:
        return ""

    return "\n".join(lines)


class SynthesisStage(PipelineStage):
    """Synthesize resolved decisions into the final PlanOutput.

    The model receives ALL committed decision records and narrates them into
    a coherent plan. Then per-field extraction pulls structured data.

    This stage does NOT do original reasoning -- it organizes pre-solved answers.
    """

    @property
    def name(self) -> str:
        return "synthesis"

    @property
    def progress_range(self) -> tuple[float, float]:
        return (0.75, 0.95)

    def build_prompt(
        self, job_description: str, prior_outputs: dict[str, Any],
    ) -> list[dict]:
        resolution_output = prior_outputs.get("decision_resolution", {})
        resolutions = resolution_output.get("resolutions", [])

        decision_text = self._format_resolutions(resolutions)
        call_graph_text = prior_outputs.get("_call_graph_text", "")
        gathered_context = self._get_gathered_context(prior_outputs)

        layer_warning = self._build_layer_warning(prior_outputs)
        if layer_warning:
            gathered_context = gathered_context + "\n\n" + layer_warning

        prompt_template = load_prompt("synthesis")
        prompt = prompt_template.format(
            task_description=job_description,
            resolved_decisions=decision_text,
            call_graph=call_graph_text,
            gathered_context=gathered_context,
        )
        return self._make_messages(prompt)

    @staticmethod
    def _build_layer_warning(prior_outputs: dict[str, Any]) -> str:
        """Return a warning if interior call chain layers have no resolutions.

        Defensive fallback for when the coverage gate at decomposition wasn't
        sufficient. Appended to gathered_context so the model sees a concrete
        list of uncovered files before writing needed_artifacts.
        """
        call_graph = prior_outputs.get("_call_graph")
        if not call_graph or not call_graph.nodes:
            return ""
        max_depth = call_graph.max_depth
        if max_depth < 2:
            return ""

        interior = [n for n in call_graph.nodes if 0 < n.depth < max_depth]
        if len(interior) < 2:
            return ""

        resolution_output = prior_outputs.get("decision_resolution", {})
        resolutions = resolution_output.get("resolutions", [])
        covered_text = " ".join(
            r.get("decision", "") + " " + " ".join(r.get("evidence", []))
            for r in resolutions
        ).lower()

        def is_mentioned(path: str) -> bool:
            base = os.path.basename(path).replace(".py", "").lower()
            return path.lower() in covered_text or base in covered_text

        uncovered = [n for n in interior if not is_mentioned(n.file_path)]
        if not uncovered or len(uncovered) * 2 < len(interior):
            return ""

        file_list = ", ".join(n.file_path for n in uncovered[:4])
        return (
            "## LAYER COVERAGE WARNING\n"
            f"No decision explicitly covers: {file_list}\n"
            "If these files require changes, include them in needed_artifacts. "
            "Do not skip intermediate layers — trace the full call chain."
        )

    def parse_output(self, raw_output: str) -> dict[str, Any]:
        return extract_json(raw_output)

    @staticmethod
    def _build_artifact_source_context(
        prior_outputs: dict[str, Any],
    ) -> str:
        """Build a focused cheat sheet of real symbols for artifact writing.

        Extracts class names, method names, and function signatures from the
        structural index for files referenced in decision resolutions. Compact
        (~30-50 lines) so the model knows what exists without being overwhelmed.
        """
        # Collect file paths from decision resolutions
        resolution_output = prior_outputs.get("decision_resolution", {})
        resolutions = resolution_output.get("resolutions", [])
        referenced_files: set[str] = set()
        for r in resolutions:
            for ev in r.get("evidence", []):
                if ":" in ev:
                    path = ev.split(":")[0].strip()
                    if path.endswith(".py"):
                        referenced_files.add(path)

        if not referenced_files:
            return ""

        # Get full structural index (covers all codebase files)
        full_index = prior_outputs.get(
            "_agent_context", {},
        ).get("full_structural_index", "")
        if not full_index:
            full_index = prior_outputs.get("_gathered_context", "")
        if not full_index:
            return ""

        # Parse index into per-file sections
        sections: dict[str, list[str]] = {}
        current_file = ""
        for line in full_index.split("\n"):
            if line.startswith("## "):
                current_file = line[3:].strip()
            elif current_file and line.strip():
                sections.setdefault(current_file, []).append(line)

        # Also include service/dependency files that define how the API
        # layer connects to the engine — models consistently miss this layer
        _SERVICE_KEYWORDS = ("service", "dependencies", "factory")
        for idx_path in sections:
            base = idx_path.rsplit("/", 1)[-1].replace(".py", "").lower()
            if any(kw in base for kw in _SERVICE_KEYWORDS):
                referenced_files.add(idx_path)

        # Build compact cheat sheet — only files referenced in decisions
        parts = [
            "## ARTIFACT REFERENCE — real symbols from the codebase\n"
            "When writing artifact code, use ONLY these class names, method "
            "names, field names, and function signatures. If a method is not "
            "listed here, it does NOT exist — do not invent it.\n"
        ]
        matched = 0
        for ref_path in sorted(referenced_files):
            # Match against index sections (may need partial match)
            for idx_path, idx_lines in sections.items():
                if ref_path == idx_path or ref_path.endswith(idx_path) or idx_path.endswith(ref_path):
                    parts.append(f"\n### {idx_path}")
                    for line in idx_lines:
                        # Include classes and functions lines, skip imports/exports
                        if line.startswith(("classes:", "functions:", "doc:")):
                            parts.append(f"  {line}")
                    matched += 1
                    break

        if matched == 0:
            return ""

        # Build instance attribute template from source code
        # This tells the model what self._ attributes ACTUALLY exist
        attr_template = _build_attribute_template(
            referenced_files, prior_outputs, sections,
        )
        if attr_template:
            parts.append(attr_template)

        result = "\n".join(parts)
        logger.info(
            f"Stage 'synthesis': artifact cheat sheet: "
            f"{len(referenced_files)} files referenced, "
            f"{matched} matched ({len(result)} chars)"
        )
        return result

    @staticmethod
    def _extract_class_names(
        reasoning: str,
        prior_outputs: dict[str, Any],
    ) -> list[str]:
        """Extract CamelCase class names from resolutions and reasoning.

        Returns sorted list of likely project class names, filtering
        out stdlib/framework names. Used by pre-fill and tool history
        injection.
        """
        import re

        resolution_output = prior_outputs.get("decision_resolution", {})
        resolutions = resolution_output.get("resolutions", [])

        # Extract CamelCase class names (2+ words, e.g. FitzKragEngine)
        camel = re.compile(r'\b([A-Z][a-z]+(?:[A-Z][a-z0-9]+)+)\b')
        names: set[str] = set()

        for r in resolutions:
            for ev in r.get("evidence", []):
                names.update(camel.findall(ev))
            names.update(camel.findall(r.get("decision", "")))
            names.update(camel.findall(r.get("reasoning", "")))

        # Also scan reasoning (synthesis output)
        names.update(camel.findall(reasoning[:8000]))

        # Filter out stdlib / framework / generic names
        _SKIP = {
            "True", "False", "None",
            "Optional", "Dict", "List", "Tuple", "Type", "Any", "Union",
            "Callable", "Iterator", "AsyncIterator", "Generator",
            "AsyncGenerator", "Sequence", "Mapping", "Iterable",
            "Exception", "ValueError", "TypeError", "KeyError",
            "AttributeError", "NotImplementedError", "RuntimeError",
            "ImportError", "FileNotFoundError", "IOError", "OSError",
            "FastAPI", "BaseModel", "APIRouter", "StreamingResponse",
            "JSONResponse", "HTTPException", "Depends", "Response",
            "ReturnType", "TypeVar", "FieldInfo", "ConfigDict",
        }
        names -= _SKIP

        return sorted(names)

    @staticmethod
    def _build_tool_history(
        class_names: list[str],
        tools_map: dict[str, Any],
    ) -> tuple[list[dict], dict[str, str]]:
        """Pre-call lookup_class and format results as tool history.

        Returns (messages_to_inject, seen_calls_dict). The messages
        look like the model already called lookup_class for each class,
        keeping the model in verification mode rather than passive
        reading mode.
        """
        lookup_class = tools_map.get("lookup_class")
        if not lookup_class or not class_names:
            return [], {}

        tool_calls_list = []
        tool_results = []
        seen: dict[str, str] = {}
        found = 0

        for i, name in enumerate(class_names):
            result = lookup_class(class_name=name)
            call_key = (
                f'lookup_class:'
                f'{json.dumps({"class_name": name}, sort_keys=True)}'
            )
            seen[call_key] = result

            if "NOT FOUND" in result:
                continue

            tc_id = f"pre_{i}"
            tool_calls_list.append({
                "id": tc_id,
                "type": "function",
                "function": {
                    "name": "lookup_class",
                    "arguments": json.dumps({"class_name": name}),
                },
            })
            tool_results.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": result,
            })
            found += 1

        if not tool_calls_list:
            return [], seen

        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": tool_calls_list,
            },
            *tool_results,
        ]
        logger.info(
            f"Stage 'synthesis': injected {found} class lookups "
            f"as tool history"
        )
        return messages, seen

    async def _build_artifacts_per_file(
        self,
        client: Any,
        reasoning: str,
        prior_outputs: dict[str, Any],
        context_merged: dict[str, Any],
    ) -> list[dict]:
        """Build artifacts one per file with focused generate() calls.

        The main synthesis reasoning already decided WHAT artifacts are needed
        and WHY. This method handles the HOW — for each needed artifact, it
        makes a separate LLM call with the target file's real source code,
        so the model sees actual self._xxx attributes, real method signatures,
        and real field names.

        Falls back to template extraction if no source is available.
        """
        needed = context_merged.get("needed_artifacts", [])
        if not needed:
            # No needed_artifacts extracted — fall back to template
            logger.info(
                "Stage 'synthesis': no needed_artifacts, "
                "falling back to template extraction"
            )
            return await self._artifacts_template_fallback(
                client, reasoning, prior_outputs,
            )

        # Parse needed_artifacts into (filename, purpose) pairs
        artifact_specs: list[tuple[str, str]] = []
        for entry in needed[:8]:  # cap at 8 artifacts
            # Format: "path/to/file.py -- purpose description"
            if " -- " in entry:
                fname, purpose = entry.split(" -- ", 1)
                artifact_specs.append((fname.strip(), purpose.strip()))
            else:
                artifact_specs.append((entry.strip(), ""))

        if not artifact_specs:
            return await self._artifacts_template_fallback(
                client, reasoning, prior_outputs,
            )

        # Get source code pool and relevant decisions
        agent_ctx = prior_outputs.get("_agent_context", {})
        file_contents = agent_ctx.get("file_contents", {})
        source_dir = prior_outputs.get("_source_dir", "")
        resolution_output = prior_outputs.get("decision_resolution", {})
        resolutions = resolution_output.get("resolutions", [])

        # Build compact decision summary for injection
        decision_summary = self._format_resolutions(resolutions)

        artifacts: list[dict] = []
        prior_signatures: list[str] = []  # F3: accumulate method sigs
        t0 = time.monotonic()

        for filename, purpose in artifact_specs:
            # Find the real source code for this file
            source = self._find_file_source(
                filename, file_contents, source_dir,
            )

            # Filter decisions relevant to this file
            relevant_decisions = self._filter_decisions_for_file(
                filename, resolutions,
            )

            # F3 fix: inject prior artifact signatures for cross-consistency
            sig_context = ""
            if prior_signatures:
                sig_context = (
                    "\n## SIGNATURES FROM OTHER ARTIFACTS (match these exactly)\n"
                    + "\n".join(prior_signatures)
                )

            artifact = await self._generate_single_artifact(
                client, filename, purpose, source,
                relevant_decisions, reasoning, prior_outputs,
                prior_artifact_sigs=sig_context,
            )
            if artifact:
                artifacts.append(artifact)
                # Extract method signatures for subsequent artifacts
                sigs = _extract_method_signatures(
                    artifact.get("content", ""), filename,
                )
                prior_signatures.extend(sigs)

        elapsed = time.monotonic() - t0
        logger.info(
            f"Stage 'synthesis': per-file artifacts complete — "
            f"{len(artifacts)}/{len(artifact_specs)} artifacts "
            f"in {elapsed:.1f}s"
        )

        if not artifacts:
            logger.warning(
                "Stage 'synthesis': per-file artifacts produced 0, "
                "falling back to template"
            )
            return await self._artifacts_template_fallback(
                client, reasoning, prior_outputs,
            )

        return artifacts

    @staticmethod
    def _find_file_source(
        filename: str,
        file_contents: dict[str, str],
        source_dir: str,
    ) -> str:
        """Find source code for a file from the agent pool or disk."""
        # Direct match in file_contents
        for path, content in (file_contents or {}).items():
            if path == filename or path.endswith(filename):
                return content
            # Try matching just the basename
            if filename.split("/")[-1] == path.split("/")[-1]:
                return content

        # Disk fallback
        if source_dir:
            from pathlib import Path
            candidates = [
                Path(source_dir) / filename,
            ]
            # Also try with/without package prefix
            basename = filename.split("/", 1)[-1] if "/" in filename else filename
            for py in Path(source_dir).rglob(basename):
                parts_str = str(py)
                if ".venv" not in parts_str and "__pycache__" not in parts_str:
                    candidates.append(py)

            for candidate in candidates:
                if candidate.exists():
                    try:
                        return candidate.read_text(
                            encoding="utf-8", errors="replace",
                        )
                    except OSError:
                        continue

        return ""

    @staticmethod
    def _filter_decisions_for_file(
        filename: str,
        resolutions: list[dict],
    ) -> str:
        """Filter and format decisions relevant to a specific file."""
        basename = filename.split("/")[-1].replace(".py", "")
        relevant = []
        for r in resolutions:
            # Check if this file is mentioned in evidence or decision text
            evidence_text = " ".join(r.get("evidence", []))
            decision_text = r.get("decision", "")
            reasoning_text = r.get("reasoning", "")
            combined = f"{evidence_text} {decision_text} {reasoning_text}"
            if filename in combined or basename in combined.lower():
                relevant.append(r)

        if not relevant:
            # If no specific match, include all decisions (better than none)
            relevant = resolutions

        lines = []
        for r in relevant:
            did = r.get("decision_id", "?")
            decision = r.get("decision", "")
            constraints = r.get("constraints_for_downstream", [])
            lines.append(f"[{did}] {decision}")
            for c in constraints:
                lines.append(f"  constraint: {c}")
        return "\n".join(lines)

    @staticmethod
    def _resolve_schema_fields(
        relevant_decisions: str,
        reasoning: str,
        prior_outputs: dict[str, Any],
    ) -> str:
        """Extract Pydantic model fields referenced in decisions/reasoning.

        Deterministically looks up class fields from the structural index
        for any CamelCase class names found in the text. Returns a compact
        cheat sheet like:
            QueryRequest fields: question, source, top_k, conversation_context
            ChatRequest fields: message, history, collection
        """
        import re as _re

        agent_ctx = prior_outputs.get("_agent_context", {})
        full_index = agent_ctx.get("full_structural_index", "")
        if not full_index:
            full_index = prior_outputs.get("_gathered_context", "")
        if not full_index:
            return ""

        from fitz_forge.planning.validation.grounding import StructuralIndexLookup
        lookup = StructuralIndexLookup(full_index)

        # Find CamelCase names that look like schemas/models
        text = relevant_decisions + " " + reasoning[:3000]
        candidates = set(_re.findall(r"\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b", text))
        # Also catch single-word capitalized types (Query, Answer, etc.)
        candidates.update(_re.findall(r"\b([A-Z][a-z]{2,})\b", text))
        # Also include common request/response patterns
        candidates.update(
            name for name in lookup.classes
            if any(kw in name.lower() for kw in ("request", "response", "query", "chat", "answer"))
        )

        lines = []
        for name in sorted(candidates):
            cls = lookup.find_class(name)
            if not cls:
                continue
            # Get fields from AST if source available
            file_contents = agent_ctx.get("file_contents", {})
            source_dir = prior_outputs.get("_source_dir", "")
            fields = _extract_class_fields(name, file_contents, source_dir)
            if fields:
                lines.append(f"{name} fields: {', '.join(fields)}")

        return "\n".join(lines)

    @staticmethod
    def _resolve_class_interfaces(
        source: str,
        prior_outputs: dict[str, Any],
    ) -> str:
        """Extract available methods on instance attributes for a target file.

        Parses __init__/_init_components to find self._xxx = ClassName(...)
        assignments, then looks up each ClassName's public methods via the
        structural index and source AST. Returns a compact cheat sheet like:

            self._router → RetrievalRouter: route(query, top_k), get_strategy()
            self._chat → ChatClient: generate(prompt, **kwargs)

        This prevents the model from fabricating method names on referenced
        objects (e.g. inventing self._router.retrieve_chunks() when the real
        method is self._router.route()).
        """
        import ast as _ast

        if not source:
            return ""

        try:
            tree = _ast.parse(source)
        except SyntaxError:
            return ""

        # Extract self._xxx = ClassName(...) from init methods
        attrs: dict[str, str] = {}  # attr_name -> type_name
        for cls_node in _ast.iter_child_nodes(tree):
            if not isinstance(cls_node, _ast.ClassDef):
                continue
            for method in _ast.iter_child_nodes(cls_node):
                if not isinstance(method, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                    continue
                if method.name not in ("__init__", "_init_components", "setup", "_setup"):
                    continue
                for node in _ast.walk(method):
                    if not isinstance(node, _ast.Assign):
                        continue
                    for target in node.targets:
                        if (isinstance(target, _ast.Attribute)
                                and isinstance(target.value, _ast.Name)
                                and target.value.id == "self"
                                and target.attr.startswith("_")
                                and not target.attr.startswith("__")):
                            rhs = ""
                            if isinstance(node.value, _ast.Call):
                                if isinstance(node.value.func, _ast.Name):
                                    rhs = node.value.func.id
                                elif isinstance(node.value.func, _ast.Attribute):
                                    rhs = node.value.func.attr
                            if rhs:
                                attrs[target.attr] = rhs

        if not attrs:
            return ""

        # Look up public methods for each component type
        agent_ctx = prior_outputs.get("_agent_context", {})
        full_index = agent_ctx.get("full_structural_index", "")
        if not full_index:
            full_index = prior_outputs.get("_gathered_context", "")

        # Use StructuralIndexLookup if we have an index
        component_methods: dict[str, list[str]] = {}  # type_name -> [method sigs]
        if full_index:
            from fitz_forge.planning.validation.grounding import StructuralIndexLookup
            lookup = StructuralIndexLookup(full_index)
            for type_name in set(attrs.values()):
                cls = lookup.find_class(type_name)
                if cls and cls.methods:
                    meths = []
                    for mname, minfo in cls.methods.items():
                        if mname.startswith("__"):
                            continue
                        sig = mname
                        if minfo.return_type:
                            sig += f" -> {minfo.return_type}"
                        meths.append(sig)
                    if meths:
                        component_methods[type_name] = meths

        # Fallback: parse source files for types not found in index
        missing_types = set(attrs.values()) - set(component_methods.keys())
        if missing_types:
            file_contents = agent_ctx.get("file_contents", {})
            source_dir = prior_outputs.get("_source_dir", "")
            all_sources = list((file_contents or {}).values())

            if source_dir:
                from pathlib import Path as _Path
                for _py in _Path(source_dir).rglob("*.py"):
                    if not missing_types:
                        break
                    _stem = _py.stem.lower()
                    if not any(t.lower() in _stem or _stem in t.lower()
                               for t in missing_types):
                        continue
                    if ".venv" in str(_py) or "__pycache__" in str(_py):
                        continue
                    try:
                        all_sources.append(
                            _py.read_text(encoding="utf-8", errors="replace"),
                        )
                    except OSError:
                        continue

            for _src in all_sources:
                if not missing_types:
                    break
                try:
                    _tree = _ast.parse(_src)
                except SyntaxError:
                    continue
                for _node in _ast.walk(_tree):
                    if isinstance(_node, _ast.ClassDef) and _node.name in missing_types:
                        meths = []
                        for _child in _ast.iter_child_nodes(_node):
                            if isinstance(_child, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                                if _child.name.startswith("__"):
                                    continue
                                params = [a.arg for a in _child.args.args
                                          if a.arg != "self"]
                                sig = f"{_child.name}({', '.join(params)})"
                                if _child.returns:
                                    try:
                                        sig += f" -> {_ast.unparse(_child.returns)}"
                                    except Exception:
                                        pass
                                meths.append(sig)
                        if meths:
                            component_methods[_node.name] = meths
                            missing_types.discard(_node.name)

        # Build compact cheat sheet
        _MAX_INTERFACE_LINES = 50

        lines = []
        for attr_name, type_name in sorted(attrs.items()):
            meths = component_methods.get(type_name, [])
            if meths:
                sig_str = ", ".join(meths[:8])
                lines.append(f"self.{attr_name} → {type_name}: {sig_str}")
            elif (type_name[0].islower()
                  and ("factory" in type_name.lower()
                       or type_name.startswith("get_"))):
                # Callable/factory — not a class, call it directly
                lines.append(
                    f'self.{attr_name} → CALLABLE (use as self.{attr_name}("arg"), '
                    f'NOT self.{attr_name}.get_xxx())'
                )
            else:
                lines.append(f"self.{attr_name} → {type_name}")

        if not lines:
            return ""

        if len(lines) <= _MAX_INTERFACE_LINES:
            return "\n".join(lines)

        # Too many interfaces — truncate to cap.
        # Prioritize entries that have resolved methods (more useful)
        # over bare "self._xxx → TypeName" entries.
        with_methods = [l for l in lines if ": " in l]
        without_methods = [l for l in lines if ": " not in l]
        capped = (with_methods + without_methods)[:_MAX_INTERFACE_LINES]
        logger.info(
            f"Stage 'synthesis': interface cap {len(lines)}"
            f" → {len(capped)} (limit {_MAX_INTERFACE_LINES})"
        )
        return "\n".join(capped)

    async def _generate_single_artifact(
        self,
        client: Any,
        filename: str,
        purpose: str,
        source: str,
        relevant_decisions: str,
        reasoning: str,
        prior_outputs: dict[str, Any] | None = None,
        prior_artifact_sigs: str = "",
    ) -> dict | None:
        """Generate a single artifact with the target file's real source."""
        # Resolve class interfaces from DISK source (uncompressed).
        # The `source` param may come from file_contents which is
        # pre-compressed and strips init bodies — making init attr
        # extraction fail. Read uncompressed from disk instead.
        class_interfaces = ""
        type_attr_map: dict[str, str] = {}
        disk_source = ""
        if prior_outputs:
            source_dir = prior_outputs.get("_source_dir", "")
            disk_source = ""
            if source_dir:
                from pathlib import Path as _Path
                disk_path = _Path(source_dir) / filename
                if disk_path.is_file():
                    try:
                        disk_source = disk_path.read_text(
                            encoding="utf-8", errors="replace",
                        )
                    except OSError:
                        pass
            # Use disk source for interface extraction, fall back to source param
            interface_source = disk_source or source
            class_interfaces = self._resolve_class_interfaces(
                interface_source, prior_outputs,
            )
            type_attr_map = _build_type_attr_map(interface_source)
            init_attrs = _extract_init_attr_names(interface_source)
            # Map attr_name -> first public method (for attr-as-function repair)
            attr_methods = _build_attr_methods(type_attr_map, prior_outputs)
            # F10 fix: resolve APIs for types imported/used as local
            # variables (not just self._xxx attrs). When the artifact
            # calls service.xxx(), the model needs to know what methods
            # the service object actually has.
            imported_type_apis = _resolve_imported_type_apis(
                interface_source, prior_outputs,
            )
            logger.info(
                f"Stage 'synthesis': {filename} imported_type_apis="
                f"{len(imported_type_apis)} chars, "
                f"interface_source={len(interface_source)} chars"
            )
            if imported_type_apis:
                class_interfaces = (
                    class_interfaces + "\n" + imported_type_apis
                    if class_interfaces else imported_type_apis
                )

            logger.info(
                f"Stage 'synthesis': {filename} source={len(source)} chars, "
                f"disk={len(disk_source)} chars, "
                f"interfaces={len(class_interfaces)} chars, "
                f"type_map={len(type_attr_map)} entries"
            )

        # Compress source if it's very large
        if len(source) > 8000:
            from fitz_forge.planning.agent.compressor import compress_file
            source = compress_file(source, filename)

        # F9 fix: inject reference method body when creating a variant.
        # The model needs to see HOW existing methods chain internal
        # components to create a streaming/async/parallel variant.
        reference_body = ""
        if disk_source and purpose:
            reference_body = _extract_reference_method(
                disk_source, purpose, relevant_decisions,
            )
            if reference_body:
                logger.info(
                    f"Stage 'synthesis': {filename} injecting "
                    f"reference method ({len(reference_body)} chars)"
                )

        # F9 supplement: extract parameter type fields from reference method
        # so the model knows Query has (text, constraints, metadata) not
        # (conversation_context, history, mode, provider).
        param_type_fields = ""
        if reference_body and prior_outputs:
            param_type_fields = _extract_param_type_fields(
                reference_body, prior_outputs.get("_source_dir", ""),
            )

        # Resolve schema fields deterministically
        schema_fields = ""
        if prior_outputs:
            schema_fields = self._resolve_schema_fields(
                relevant_decisions, reasoning, prior_outputs,
            )

        # Compress reasoning: keep architecture + design, drop roadmap/risk
        reasoning_compressed = _compress_reasoning_for_artifact(reasoning)

        # Build prompt sections — priority order for truncation:
        # 1. decisions (must keep), 2. source (must keep),
        # 3. interfaces (must keep), 4. schema fields (must keep),
        # 5. reasoning (truncate first if over budget)
        _TOKEN_BUDGET_CHARS = 32000 * 4  # ~32K tokens in chars

        source_section = ""
        if source:
            source_section = (
                f"\n\n## CURRENT SOURCE CODE of {filename}\n"
                f"Use ONLY the attributes, methods, and field names you "
                f"see below. Do NOT invent methods that aren't here.\n\n"
                f"```python\n{source}\n```"
            )
        else:
            source_section = (
                f"\n\n(Source code for {filename} not available. "
                f"Use method names from the decisions above.)"
            )

        reference_section = ""
        if reference_body:
            reference_section = (
                f"\n\n## REFERENCE IMPLEMENTATION (follow this pattern exactly)\n"
                f"Your new method must chain components the same way as this "
                f"existing method. Use the same call signatures, parameter "
                f"names, and data flow:\n\n"
                f"```python\n{reference_body}\n```"
            )

        schema_section = ""
        all_schema = "\n".join(filter(None, [schema_fields, param_type_fields]))
        if all_schema:
            schema_section = (
                f"\n\n## DATA MODEL FIELDS (use these exact field names)\n"
                f"{all_schema}"
            )

        interface_section = ""
        if class_interfaces:
            interface_section = (
                f"\n\n## AVAILABLE METHODS ON INSTANCE ATTRIBUTES\n"
                f"When calling methods on self._xxx, use ONLY these:\n"
                f"{class_interfaces}"
            )

        schema = json.dumps({
            "filename": filename,
            "content": "ONLY the new methods/classes to add — not the entire file",
            "purpose": "why this artifact exists",
        }, indent=2)

        rules = (
            f"Rules:\n"
            f"- Write ONLY the new or modified code (not the entire file)\n"
            f"- Use exact attribute names from the source code above\n"
            f"- When calling self._xxx.method(), use ONLY methods listed "
            f"in AVAILABLE METHODS above\n"
            f"- When calling imported objects (e.g. service.xxx()), use ONLY "
            f"methods listed in IMPORTED TYPE APIs above. If a method is not "
            f"listed, it does NOT exist — do NOT assume it will be added later\n"
            f"- When adding a parallel method (e.g. generate_stream), "
            f"match the original method's parameters\n"
            f"- Do NOT fabricate method names — if unsure, omit the call\n"
        )

        # Build grounding block — goes FIRST after purpose to avoid
        # lost-in-the-middle effect (same fix as F7 for attr fabrication)
        grounding = ""
        grounding_parts = [
            s for s in [interface_section, schema_section, prior_artifact_sigs]
            if s.strip()
        ]
        if grounding_parts:
            grounding = "\n".join(grounding_parts)

        # Measure fixed sections (everything except reasoning)
        fixed = (
            f"Write a code artifact for: {filename}\n"
            f"Purpose: {purpose}\n\n"
            f"{rules}\n"
            f"{grounding}\n\n"
            f"## RELEVANT DECISIONS\n{relevant_decisions}\n\n"
            f"{source_section}"
            f"{reference_section}\n\n"
            f"Return ONLY valid JSON matching this schema:\n{schema}\n"
        )
        fixed_chars = len(fixed)

        # Reasoning gets whatever budget remains
        reasoning_budget = _TOKEN_BUDGET_CHARS - fixed_chars - 200  # 200 for header
        if reasoning_budget < 500:
            reasoning_budget = 500  # minimum to be useful

        reasoning_final = _truncate_at_line(reasoning_compressed, reasoning_budget)
        if len(reasoning_final) < len(reasoning):
            logger.info(
                f"Stage 'synthesis': {filename} reasoning "
                f"{len(reasoning)} -> {len(reasoning_compressed)} compressed "
                f"-> {len(reasoning_final)} final"
            )

        prompt = (
            f"Write a code artifact for: {filename}\n"
            f"Purpose: {purpose}\n\n"
            f"{rules}\n"
            f"{grounding}\n\n"
            f"## RELEVANT DECISIONS\n{relevant_decisions}\n\n"
            f"{source_section}"
            f"{reference_section}\n\n"
            f"## PLAN CONTEXT (background — lower priority than above)\n"
            f"{reasoning_final}\n\n"
            f"Return ONLY valid JSON matching this schema:\n{schema}\n"
        )

        messages = self._make_messages(prompt)

        logger.info(
            f"Stage 'synthesis': {filename} PROMPT SIZE: "
            f"{len(prompt)} chars (~{len(prompt)//4} tokens)"
        )
        # Temporary: dump prompt structure for debugging
        if os.environ.get("DUMP_ARTIFACT_PROMPT"):
            import sys as _sys
            _sys.stderr.write(f"\n{'='*80}\nARTIFACT PROMPT FOR {filename}\n{'='*80}\n")
            _sys.stderr.write(prompt[:3000])
            _sys.stderr.write(f"\n... ({len(prompt)} chars total)\n")
            _sys.stderr.write(prompt[-1000:])
            _sys.stderr.write(f"\n{'='*80}\n")

        try:
            t0 = time.monotonic()
            raw = await client.generate(
                messages=messages, max_tokens=4096,
            )
            elapsed = time.monotonic() - t0

            data = extract_json(raw)
            content = data.get("content", "")
            if not content:
                logger.warning(
                    f"Stage 'synthesis': artifact for {filename} "
                    f"had empty content ({elapsed:.1f}s)"
                )
                return None

            # Post-generation repair: auto-correct fabricated method refs
            if prior_outputs:
                content, n_fixes = _repair_fabricated_refs(
                    content, prior_outputs,
                    type_attr_map=type_attr_map,
                    init_attrs=init_attrs,
                    attr_methods=attr_methods,
                    filename=filename,
                )
                if n_fixes:
                    logger.info(
                        f"Stage 'synthesis': repaired {n_fixes} fabricated "
                        f"refs in {filename}"
                    )

            logger.info(
                f"Stage 'synthesis': artifact for {filename} "
                f"({len(content)} chars, {elapsed:.1f}s)"
            )
            return {
                "filename": data.get("filename", filename),
                "content": content,
                "purpose": data.get("purpose", purpose),
            }

        except Exception as e:
            logger.warning(
                f"Stage 'synthesis': artifact for {filename} failed: {e}"
            )
            return None

    async def _artifacts_template_fallback(
        self,
        client: Any,
        reasoning: str,
        prior_outputs: dict[str, Any],
    ) -> list[dict]:
        """Fallback: extract all artifacts from reasoning in one call."""
        extract_context = self._get_gathered_context(prior_outputs)
        artifact_source_context = self._build_artifact_source_context(
            prior_outputs,
        )
        extra = artifact_source_context if artifact_source_context else extract_context
        partial = await self._extract_field_group(
            client, reasoning, ["artifacts"],
            _DESIGN_FIELD_GROUPS[-1]["schema"],
            "artifacts",
            extra_context=extra,
        )
        return partial.get("artifacts", [])

    async def _build_artifacts_with_tools(
        self,
        client: Any,
        reasoning: str,
        prior_outputs: dict[str, Any],
        extract_context: str,
    ) -> tuple[list[dict] | None, str]:
        """Build artifacts using tool-assisted generation.

        The model gets codebase lookup tools (lookup_method, lookup_class,
        read_method_source) so it can verify real interfaces before writing
        code.

        Returns (artifacts, tool_context):
        - artifacts: list of artifact dicts if model produced JSON, else None
        - tool_context: formatted string of all tool results gathered,
          usable as enriched context for template fallback
        """
        if not hasattr(client, "generate_with_tools"):
            return None, ""

        # Build tools from the codebase context
        try:
            from fitz_forge.planning.pipeline.tools.codebase_tools import (
                make_codebase_tools,
            )
        except ImportError:
            return None, ""

        agent_ctx = prior_outputs.get("_agent_context", {})
        full_index = agent_ctx.get("full_structural_index", "")
        if not full_index:
            full_index = prior_outputs.get("_gathered_context", "")
        file_contents = agent_ctx.get("file_contents", {})
        source_dir = prior_outputs.get("_source_dir", "")

        if not full_index:
            return None, ""

        tools = make_codebase_tools(full_index, file_contents, source_dir)
        # Remove check_exists — model over-uses it (15+ calls per run),
        # checking stdlib types and re-checking things, causing degeneration.
        # lookup_class returns richer info anyway.
        tools = [fn for fn in tools if fn.__name__ != "check_exists"]
        tools_map = {fn.__name__: fn for fn in tools}

        schema = json.dumps({
            "artifacts": [{
                "filename": "path/to/file.py",
                "content": "ONLY the new methods/classes to add",
                "purpose": "why this artifact exists",
            }]
        }, indent=2)

        # Note: pre-calling lookup_class for resolution classes was tested
        # (run 29) and HURT scores (39.0 vs 43.4) because it seeds the
        # dedup cache, causing the model's organic calls to be flagged
        # as duplicates → earlier stale exit → less research time.
        # The model's organic research is more valuable than pre-filled info.

        prompt = (
            "You are writing implementation artifacts for a software plan.\n\n"
            "IMPORTANT: Before writing ANY code, use the lookup tools to "
            "verify real signatures:\n"
            "- lookup_method(class, method): get the REAL signature of "
            "an existing method\n"
            "- lookup_class(class): see real attributes and methods on "
            "a class\n"
            "- read_method_source(class, method): read the actual source "
            "code of a method\n\n"
            "Rules:\n"
            "- When adding a parallel method (e.g. generate_stream), call "
            "lookup_method\n"
            "  on the original (generate) FIRST to get the exact parameter "
            "list.\n"
            "- The parallel method MUST accept the same parameters.\n"
            "- Only use self._ attributes that lookup_class confirms "
            "exist.\n"
            "- Do NOT fabricate method names. If you are unsure whether a "
            "method exists, call lookup_class or lookup_method to check.\n\n"
            f"Return ONLY valid JSON matching this schema:\n{schema}\n\n"
            "--- PLAN ANALYSIS (what to build) ---\n"
            f"{reasoning[:8000]}\n\n"
            f"--- CODEBASE CONTEXT ---\n{extract_context[:4000]}"
        )

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        max_rounds = 10
        t0 = time.monotonic()
        seen_calls: dict[str, str] = {}
        total_tool_calls = 0
        consecutive_stale = 0  # rounds with zero new info

        def _format_tool_context(calls: dict[str, str]) -> str:
            """Format collected tool results as context for template."""
            if not calls:
                return ""
            parts = [
                "## VERIFIED CODEBASE INFO (from tool lookups)\n"
                "The following was verified by looking up real classes "
                "and methods. Use these exact signatures and attributes.\n"
            ]
            for key, result in calls.items():
                if "NOT FOUND" in result or "Error:" in result:
                    continue
                parts.append(result)
            ctx = "\n\n".join(parts)
            return ctx if len(parts) > 1 else ""

        try:
            for round_num in range(max_rounds):
                response = await client.generate_with_tools(
                    messages, tools,
                )

                if not response.tool_calls:
                    # Model voluntarily produced final answer
                    raw = response.content or ""
                    elapsed = time.monotonic() - t0
                    tool_ctx = _format_tool_context(seen_calls)
                    logger.info(
                        f"Stage 'synthesis': tool-assisted artifacts "
                        f"(voluntary, {round_num} rounds, "
                        f"{total_tool_calls} calls, "
                        f"{elapsed:.1f}s, {len(raw)} chars)"
                    )
                    try:
                        data = extract_json(raw)
                        artifacts = data.get("artifacts", [])
                        if artifacts:
                            return artifacts, tool_ctx
                    except ValueError:
                        logger.warning(
                            f"Stage 'synthesis': tool-assisted artifacts "
                            f"failed to parse JSON"
                        )
                    return None, tool_ctx

                # Execute tool calls
                messages.append(response.assistant_dict)
                new_info_this_round = 0
                for tc in response.tool_calls:
                    # Normalize cache keys: strip module paths so
                    # lookup_class("fitz_sage.x.Foo") deduplicates with
                    # lookup_class("Foo") — they return the same data.
                    norm_args = {
                        k: (v.rsplit(".", 1)[-1] if isinstance(v, str) else v)
                        for k, v in tc.arguments.items()
                    }
                    call_key = (
                        f"{tc.name}:"
                        f"{json.dumps(norm_args, sort_keys=True)}"
                    )

                    if call_key in seen_calls:
                        result = seen_calls[call_key]
                        logger.info(
                            f"Stage 'synthesis': DUPLICATE tool call "
                            f"{tc.name} — returning cached"
                        )
                    else:
                        fn = tools_map.get(tc.name)
                        if fn:
                            try:
                                result = fn(**tc.arguments)
                            except Exception as e:
                                result = f"Error: {e}"
                            seen_calls[call_key] = result
                            total_tool_calls += 1
                            new_info_this_round += 1
                            logger.info(
                                f"Stage 'synthesis': tool call {tc.name}"
                                f"({', '.join(f'{k}={v!r}' for k, v in tc.arguments.items())}) "
                                f"-> {len(result)} chars"
                            )
                        else:
                            result = f"Unknown tool: {tc.name}"

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })

                # Early exit: 2 consecutive all-duplicate rounds means
                # the model has exhausted useful research. Return the
                # collected tool results for template enrichment.
                if new_info_this_round == 0:
                    consecutive_stale += 1
                else:
                    consecutive_stale = 0

                if consecutive_stale >= 2:
                    elapsed = time.monotonic() - t0
                    tool_ctx = _format_tool_context(seen_calls)
                    logger.info(
                        f"Stage 'synthesis': tool early exit — "
                        f"{consecutive_stale} stale rounds after "
                        f"round {round_num + 1}, "
                        f"{total_tool_calls} unique calls, "
                        f"{elapsed:.1f}s, {len(tool_ctx)} chars "
                        f"of verified context for template"
                    )
                    return None, tool_ctx

            # Exhausted rounds — return collected tool results
            elapsed = time.monotonic() - t0
            tool_ctx = _format_tool_context(seen_calls)
            logger.warning(
                f"Stage 'synthesis': tool-assisted artifacts exhausted "
                f"{max_rounds} rounds without producing JSON "
                f"({total_tool_calls} calls, {elapsed:.1f}s, "
                f"{len(tool_ctx)} chars of verified context)"
            )
            return None, tool_ctx

        except Exception as e:
            logger.warning(
                f"Stage 'synthesis': tool-assisted artifacts failed: {e}"
            )
            return None, ""

    @staticmethod
    def _format_slim_design(design_merged: dict[str, Any]) -> str:
        """Format Design output for Roadmap/Risk extraction context.

        Includes components, interfaces, integration points, ADR titles,
        and artifact filenames — but NOT artifact code bodies.
        """
        lines = ["## Design Summary\n"]

        adrs = design_merged.get("adrs", [])
        if adrs:
            lines.append("### Key Decisions (ADRs)")
            for adr in adrs:
                title = adr.get("title", adr.get("decision", ""))
                lines.append(f"- {title}")
            lines.append("")

        components = design_merged.get("components", [])
        if components:
            lines.append("### Components")
            for comp in components:
                name = comp.get("name", "")
                responsibility = comp.get("responsibility", "")
                lines.append(f"- **{name}**: {responsibility}")
                for iface in comp.get("interfaces", []):
                    if isinstance(iface, dict):
                        lines.append(f"  - {iface.get('name', '')}: {iface.get('description', '')}")
                    else:
                        lines.append(f"  - {iface}")
            lines.append("")

        integrations = design_merged.get("integration_points", [])
        if integrations:
            lines.append("### Integration Points")
            for ip in integrations:
                if isinstance(ip, dict):
                    lines.append(f"- {ip.get('point', ip.get('name', ''))}: {ip.get('description', '')}")
                else:
                    lines.append(f"- {ip}")
            lines.append("")

        artifacts = design_merged.get("artifacts", [])
        if artifacts:
            lines.append("### Artifacts (files to create/modify)")
            for art in artifacts:
                fname = art.get("filename", "")
                purpose = art.get("purpose", "")
                lines.append(f"- {fname}: {purpose}")
            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def _format_constraints_only(resolutions: list[dict]) -> str:
        """Format decision constraints for Roadmap/Risk extraction.

        Decision ID + constraints only. No decision text, no reasoning,
        no evidence. Used to provide ordering/dependency info without
        the full decision bulk.
        """
        lines = ["## Decision Constraints\n"]
        for r in resolutions:
            did = r.get("decision_id", "?")
            constraints = r.get("constraints_for_downstream", [])
            if constraints:
                lines.append(f"[{did}]")
                for c in constraints:
                    lines.append(f"  - {c}")
        return "\n".join(lines)

    @staticmethod
    def _score_reasoning(
        reasoning: str, resolutions: list[dict[str, Any]],
    ) -> tuple[float, dict[str, float]]:
        """Score synthesis reasoning for best-of-2 selection.

        Criteria: file references, section coverage, decision coverage,
        length, concreteness (identifier density).
        """
        breakdown: dict[str, float] = {}

        # File path references (0-25)
        file_refs = re.findall(r'\b[\w/]+\.py\b', reasoning)
        breakdown["files"] = min(len(set(file_refs)) * 2.5, 25.0)

        # Section coverage (0-20)
        section_patterns = {
            "arch": r'(?i)(architecture|approach|pattern)',
            "design": r'(?i)(component|interface|data.?model|class)',
            "roadmap": r'(?i)(phase|milestone|step\s+\d|deliverable)',
            "risk": r'(?i)(risk|mitigation|fallback|concern)',
        }
        found = sum(
            1 for p in section_patterns.values()
            if re.search(p, reasoning)
        )
        breakdown["sections"] = found * 5.0

        # Decision coverage (0-25)
        decision_ids = {r.get("decision_id", "") for r in resolutions}
        decision_ids.discard("")
        if decision_ids:
            mentioned = sum(1 for did in decision_ids if did in reasoning)
            breakdown["decisions"] = (mentioned / len(decision_ids)) * 25.0
        else:
            breakdown["decisions"] = 12.5

        # Length (0-15)
        chars = len(reasoning)
        if chars >= 10_000:
            breakdown["length"] = 15.0
        elif chars >= 5_000:
            breakdown["length"] = 10.0
        elif chars >= 2_000:
            breakdown["length"] = 5.0
        else:
            breakdown["length"] = 0.0

        # Concreteness: identifier density (0-15)
        camel = set(re.findall(r'\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b', reasoning))
        snake = set(re.findall(r'\b[a-z]+(?:_[a-z]+){1,}\b', reasoning))
        breakdown["concrete"] = min(len(camel | snake) * 0.3, 15.0)

        return sum(breakdown.values()), breakdown

    def _format_resolutions(self, resolutions: list[dict]) -> str:
        """Format resolved decisions for the synthesis prompt.

        Compact format: drops the reasoning field (LLM's internal
        chain-of-thought) and truncates evidence to file:signature only.
        The synthesis model needs WHAT was decided and WHAT the constraints
        are, not WHY the decision was made.

        Saves ~54% tokens vs the full format.
        """
        lines = []
        for r in resolutions:
            lines.append(f"### Decision {r.get('decision_id', '?')}")
            lines.append(f"**Decided:** {r.get('decision', '')}")
            evidence = r.get("evidence", [])
            if evidence:
                lines.append("**Evidence:**")
                for e in evidence:
                    # Keep file:signature, drop explanation after ' -- '
                    sig = e.split(" -- ")[0] if " -- " in e else e
                    lines.append(f"  - {sig}")
            constraints = r.get("constraints_for_downstream", [])
            if constraints:
                lines.append("**Constraints:**")
                for c in constraints:
                    lines.append(f"  - {c}")
            lines.append("")
        return "\n".join(lines)

    async def execute(
        self,
        client: Any,
        job_description: str,
        prior_outputs: dict[str, Any],
    ) -> StageResult:
        try:
            # 1. Best-of-2 synthesis reasoning
            messages = self.build_prompt(job_description, prior_outputs)
            await self._report_substep("synthesizing")

            resolution_output = prior_outputs.get("decision_resolution", {})
            resolutions = resolution_output.get("resolutions", [])

            candidates: list[tuple[float, str, dict[str, float]]] = []
            last_error: Exception | None = None
            for i in range(2):
                try:
                    t0 = time.monotonic()
                    r = await client.generate(
                        messages=messages, temperature=0.7,
                    )
                    t1 = time.monotonic()
                    logger.info(
                        f"Stage '{self.name}': reasoning candidate {i+1} "
                        f"took {t1 - t0:.1f}s ({len(r)} chars)"
                    )
                    score, breakdown = self._score_reasoning(r, resolutions)
                    candidates.append((score, r, breakdown))
                    logger.info(
                        f"Stage '{self.name}': candidate {i+1} "
                        f"score={score:.1f} {breakdown}"
                    )
                except Exception as e:
                    last_error = e
                    logger.warning(
                        f"Stage '{self.name}': reasoning candidate "
                        f"{i+1} failed: {e}"
                    )

            if not candidates:
                raise last_error or RuntimeError(
                    "Both synthesis reasoning candidates failed"
                )

            candidates.sort(key=lambda c: c[0], reverse=True)
            reasoning = candidates[0][1]

            if len(candidates) > 1:
                margin = candidates[0][0] - candidates[1][0]
                logger.info(
                    f"Stage '{self.name}': selected reasoning "
                    f"(score={candidates[0][0]:.1f}, margin={margin:.1f})"
                )

            # 2. Self-critique the winner
            krag_context = self._get_gathered_context(prior_outputs)
            reasoning = await self._self_critique(
                client, reasoning, job_description, krag_context=krag_context,
            )

            # 3. Per-field extraction into all five schema sections
            extract_context = krag_context

            # Context fields
            context_merged: dict[str, Any] = {}
            for group in _CONTEXT_FIELD_GROUPS:
                extra = extract_context if group["label"] in {"files", "description"} else ""
                partial = await self._extract_field_group(
                    client, reasoning, group["fields"],
                    group["schema"], group["label"],
                    extra_context=extra,
                )
                context_merged.update(partial)

            # Architecture fields
            # F6: retry approaches if empty (critical for plan quality)
            _RETRY_FIELDS = {"approaches": "approaches", "components": "components", "phases": "phases"}
            arch_merged: dict[str, Any] = {}
            for group in _ARCH_FIELD_GROUPS:
                partial = await self._extract_field_group(
                    client, reasoning, group["fields"],
                    group["schema"], group["label"],
                    extra_context=extract_context,
                    retry_if_empty=_RETRY_FIELDS.get(group["label"]),
                )
                arch_merged.update(partial)

            # Design fields — artifacts use tool-assisted building when available
            design_merged: dict[str, Any] = {}
            for group in _DESIGN_FIELD_GROUPS:
                if group["label"] == "artifacts":
                    continue  # handled via tool-assisted loop below
                extra = extract_context if group["label"] in {"adrs", "components", "integrations"} else ""
                partial = await self._extract_field_group(
                    client, reasoning, group["fields"],
                    group["schema"], group["label"],
                    extra_context=extra,
                    retry_if_empty=_RETRY_FIELDS.get(group["label"]),
                )
                design_merged.update(partial)

            # Artifact building: per-artifact focused generation.
            # Each artifact gets its own generate() call with the target
            # file's real source code, so the model sees actual method
            # names, attributes, and signatures — not just an index.
            design_merged["artifacts"] = await self._build_artifacts_per_file(
                client, reasoning, prior_outputs, context_merged,
            )

            # Roadmap + Risk: use Design output + constraints instead of
            # raw codebase context. Roadmap needs to know WHAT was designed
            # (components, interfaces, artifacts) and decision ordering
            # constraints — not the raw codebase files.
            roadmap_risk_context = (
                self._format_slim_design(design_merged)
                + "\n"
                + self._format_constraints_only(resolutions)
            )

            # Roadmap fields
            roadmap_merged: dict[str, Any] = {}
            for group in _ROADMAP_FIELD_GROUPS:
                partial = await self._extract_field_group(
                    client, reasoning, group["fields"],
                    group["schema"], group["label"],
                    extra_context=roadmap_risk_context,
                    retry_if_empty=_RETRY_FIELDS.get(group["label"]),
                )
                roadmap_merged.update(partial)

            # Risk fields
            risk_merged: dict[str, Any] = {}
            for group in _RISK_FIELD_GROUPS:
                partial = await self._extract_field_group(
                    client, reasoning, group["fields"],
                    group["schema"], group["label"],
                    extra_context=roadmap_risk_context,
                )
                risk_merged.update(partial)

            # 4. Validate through Pydantic
            context = ContextOutput(**context_merged).model_dump()

            # Handle recommended approach matching
            approach_names = [a["name"] for a in arch_merged.get("approaches", [])]
            recommended = arch_merged.get("recommended", "")
            if recommended not in approach_names and approach_names:
                matches = difflib.get_close_matches(
                    recommended, approach_names, n=1, cutoff=0.4,
                )
                if matches:
                    arch_merged["recommended"] = matches[0]
                else:
                    arch_merged["recommended"] = approach_names[0]

            arch_merged.setdefault("approaches", [])
            arch_merged.setdefault("recommended", "")
            arch_merged.setdefault("reasoning", "")
            arch_merged.setdefault("key_tradeoffs", {})
            arch_merged.setdefault("technology_considerations", [])
            arch_merged.setdefault("scope_statement", "")
            architecture = ArchitectureOutput(**arch_merged).model_dump()

            design_merged.setdefault("adrs", [])
            design_merged.setdefault("components", [])
            design_merged.setdefault("data_model", {})
            design_merged.setdefault("integration_points", [])
            design_merged.setdefault("artifacts", [])
            design = DesignOutput(**design_merged).model_dump()

            # Fix roadmap
            from fitz_forge.planning.pipeline.stages.roadmap_risk import (
                _remove_dependency_cycles,
            )
            if "phases" in roadmap_merged:
                for phase in roadmap_merged["phases"]:
                    if "num" in phase and "number" not in phase:
                        phase["number"] = phase.pop("num")
                roadmap_merged["phases"] = _remove_dependency_cycles(
                    roadmap_merged["phases"]
                )
            roadmap_merged.setdefault("phases", [])

            # F4 fix: filter phantom phase references
            valid_phase_nums = {
                p.get("number", i + 1)
                for i, p in enumerate(roadmap_merged["phases"])
            }
            roadmap_merged["critical_path"] = [
                n for n in roadmap_merged.get("critical_path", [])
                if n in valid_phase_nums
            ]
            roadmap_merged["parallel_opportunities"] = [
                grp for grp in (
                    [n for n in group if n in valid_phase_nums]
                    for group in roadmap_merged.get("parallel_opportunities", [])
                )
                if len(grp) >= 2
            ]
            roadmap_merged["total_phases"] = len(roadmap_merged["phases"])
            roadmap = RoadmapOutput(**roadmap_merged).model_dump()

            risk_merged.setdefault("risks", [])
            risk_merged.setdefault("overall_risk_level", "medium")
            risk_merged.setdefault("recommended_contingencies", [])
            # Filter phantom phase refs in risks (same as F4)
            for risk_item in risk_merged["risks"]:
                if "affected_phases" in risk_item:
                    risk_item["affected_phases"] = [
                        n for n in risk_item["affected_phases"]
                        if n in valid_phase_nums
                    ]
            risk = RiskOutput(**risk_merged).model_dump()

            # 5. Combine into the output format expected by the orchestrator
            output = {
                "context": context,
                "architecture": architecture,
                "design": design,
                "roadmap": roadmap,
                "risk": risk,
            }

            return StageResult(
                stage_name=self.name,
                success=True,
                output=output,
                raw_output=reasoning,
            )
        except Exception as e:
            logger.error(f"Stage '{self.name}' failed: {e}", exc_info=True)
            return StageResult(
                stage_name=self.name,
                success=False,
                output={},
                raw_output="",
                error=str(e),
            )
