# fitz_forge/planning/validation/grounding/__init__.py
"""Grounding validation: does a plan reference real things in the codebase?

Organized into five submodules:

    index      — StructuralIndexLookup + IndexedClass/Method/Function,
                 augment_from_source_dir with two-pass inference, MRO walking
    inference  — return type inference, class field extraction, self._attr
                 tracking, docstring parsing, yield-type detection (tree-sitter)
    parser     — tree-sitter parser construction + recovery chain
    check      — Violation, check_artifact, _SKIP_NAMES, per-artifact walk
    llm        — GroundingReport, validate_grounding, repair_violations,
                 build_llm_grounding_prompt

Public re-exports below preserve the flat `grounding.X` import path.
"""

from .check import (
    _SKIP_NAMES,
    Violation,
    check_all_artifacts,
    check_artifact,
)
from .index import (
    IndexedClass,
    IndexedFunction,
    IndexedMethod,
    StructuralIndexLookup,
)
from .inference import (
    class_name_of_expr,
    extract_class_fields,
    extract_init_self_attrs,
    extract_type_name,
    infer_return_type,
    unparse_annotation,
)
from .llm import (
    GroundingReport,
    build_llm_grounding_prompt,
    repair_violations,
    validate_grounding,
)
from .parser import parse_python

__all__ = [
    # index
    "IndexedMethod",
    "IndexedClass",
    "IndexedFunction",
    "StructuralIndexLookup",
    # inference
    "class_name_of_expr",
    "extract_class_fields",
    "extract_init_self_attrs",
    "extract_type_name",
    "infer_return_type",
    "unparse_annotation",
    # parser
    "parse_python",
    # check
    "Violation",
    "check_artifact",
    "check_all_artifacts",
    "_SKIP_NAMES",
    # llm
    "GroundingReport",
    "build_llm_grounding_prompt",
    "repair_violations",
    "validate_grounding",
]
