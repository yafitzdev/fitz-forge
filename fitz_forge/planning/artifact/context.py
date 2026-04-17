# fitz_forge/planning/artifact/context.py
"""Input assembly for artifact generation.

Gathers everything the LLM needs to produce correct code:
source, reference method, available interfaces, schema fields.
All deterministic — no LLM calls.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ArtifactContext:
    """Everything the LLM needs to generate one artifact."""

    filename: str
    purpose: str

    # Source code
    disk_source: str = ""  # full uncompressed source from disk
    compressed_source: str = ""  # for prompt (compressed if large)

    # Reference method (for surgical rewrite)
    reference_method: str = ""  # body of method being varianted

    # Grounding context
    available_methods: str = ""  # self._xxx.method() interface list
    target_self_methods: str = ""  # self.method() / self._method() on the target class
    schema_fields: str = ""  # Pydantic/dataclass field names
    param_type_fields: str = ""  # parameter type fields from reference

    # Plan context
    decisions: str = ""  # relevant decisions text
    reasoning: str = ""  # compressed reasoning
    prior_sigs: str = ""  # signatures from prior artifacts

    # For validation
    structural_index: str = ""
    source_dir: str = ""


def assemble_context(
    filename: str,
    purpose: str,
    source_dir: str,
    structural_index: str,
    decisions: str,
    reasoning: str,
    prior_outputs: dict[str, Any] | None = None,
    prior_sigs: list[str] | None = None,
) -> ArtifactContext:
    """Assemble all inputs for artifact generation. No LLM calls."""
    # Lazy imports to avoid circular deps
    from fitz_forge.planning.pipeline.stages.synthesis import (
        _compress_reasoning_for_artifact,
        _extract_param_type_fields,
        _extract_reference_method,
    )

    ctx = ArtifactContext(
        filename=filename,
        purpose=purpose,
        source_dir=source_dir,
        structural_index=structural_index,
        decisions=decisions,
    )

    # Read disk source
    if source_dir:
        disk_path = Path(source_dir) / filename
        if disk_path.is_file():
            try:
                ctx.disk_source = disk_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass

    # Compressed source for prompt
    if ctx.disk_source:
        if len(ctx.disk_source) > 8000:
            from fitz_forge.planning.agent.compressor import compress_file

            ctx.compressed_source = compress_file(ctx.disk_source, filename)
        else:
            ctx.compressed_source = ctx.disk_source
    elif prior_outputs:
        # Try file_contents pool
        file_contents = prior_outputs.get("_file_contents", {})
        for key, content in file_contents.items():
            if key == filename or key.endswith(filename) or filename.endswith(key):
                ctx.compressed_source = content
                break

    # Reference method
    if ctx.disk_source and purpose:
        ctx.reference_method = _extract_reference_method(ctx.disk_source, purpose, decisions)
        if ctx.reference_method:
            logger.info(
                "artifact[%s]: reference method %d chars",
                filename,
                len(ctx.reference_method),
            )

    # Available methods (class interfaces)
    if prior_outputs and (ctx.disk_source or ctx.compressed_source):
        from fitz_forge.planning.pipeline.stages.synthesis import SynthesisStage

        stage = SynthesisStage()
        interface_source = ctx.disk_source or ctx.compressed_source
        ctx.available_methods = stage._resolve_class_interfaces(interface_source, prior_outputs)

        # Imported type APIs (local variable methods)
        from fitz_forge.planning.pipeline.stages.synthesis import (
            _resolve_imported_type_apis,
        )

        imported = _resolve_imported_type_apis(interface_source, prior_outputs)
        if imported:
            ctx.available_methods = (
                ctx.available_methods + "\n" + imported if ctx.available_methods else imported
            )

    # Schema fields
    if prior_outputs:
        from fitz_forge.planning.pipeline.stages.synthesis import SynthesisStage

        stage = SynthesisStage()
        ctx.schema_fields = stage._resolve_schema_fields(decisions, reasoning, prior_outputs)

    # Param type fields from reference method
    if ctx.reference_method and source_dir:
        ctx.param_type_fields = _extract_param_type_fields(ctx.reference_method, source_dir)

    # Self methods on the target class — restricts what `self.xxx` and
    # `self._xxx` the model may call. Without this, surgical prompts show
    # only the reference method, and the model loops through fabricated
    # helper names (self._execute_pipeline, self._run_pipeline, ...).
    if ctx.disk_source:
        ctx.target_self_methods = _extract_target_self_methods(ctx.disk_source)

    # Compress reasoning
    ctx.reasoning = _compress_reasoning_for_artifact(reasoning)

    # Prior signatures
    if prior_sigs:
        ctx.prior_sigs = "\n## SIGNATURES FROM OTHER ARTIFACTS (match these exactly)\n" + "\n".join(
            prior_sigs
        )

    return ctx


def _extract_target_self_methods(source: str) -> str:
    """Return a compact signature list of the primary class's methods.

    Finds the class with the most methods in the file (heuristic for
    "the target class") and returns one line per method in the form:

        async method_name(arg1, arg2) -> ReturnType

    Used by artifact strategies to show the model the REAL set of
    self methods so retries stop inventing new helper names.
    """
    if not source:
        return ""

    from fitz_forge.planning.validation.grounding.inference import (
        _function_is_async,
        _function_name,
        _returns_annotation,
        iter_all_classes,
        iter_class_methods,
        unparse_annotation,
    )
    from fitz_forge.planning.validation.grounding.parser import parse_python

    tree = parse_python(source)
    if tree is None:
        return ""

    best_class = None
    best_count = 0
    for cls in iter_all_classes(tree.root_node):
        count = sum(1 for _ in iter_class_methods(cls))
        if count > best_count:
            best_count = count
            best_class = cls
    if best_class is None:
        return ""

    name_node = next((c for c in best_class.children if c.type == "identifier"), None)
    cname = name_node.text.decode("utf-8") if name_node else "?"
    lines: list[str] = [f"# class {cname}"]
    for m in iter_class_methods(best_class):
        mname = _function_name(m)
        if mname is None:
            continue
        async_prefix = "async " if _function_is_async(m) else ""
        params: list[str] = []
        params_node = next((c for c in m.children if c.type == "parameters"), None)
        if params_node is not None:
            for p in params_node.children:
                if p.type == "identifier":
                    n = p.text.decode("utf-8")
                    if n != "self":
                        params.append(n)
                elif p.type in ("typed_parameter", "default_parameter", "typed_default_parameter"):
                    ident = next((c for c in p.children if c.type == "identifier"), None)
                    if ident is None:
                        continue
                    n = ident.text.decode("utf-8")
                    if n != "self":
                        params.append(n)
                elif p.type == "list_splat_pattern":
                    ident = next((c for c in p.children if c.type == "identifier"), None)
                    if ident is not None:
                        params.append(f"*{ident.text.decode('utf-8')}")
                elif p.type == "dictionary_splat_pattern":
                    ident = next((c for c in p.children if c.type == "identifier"), None)
                    if ident is not None:
                        params.append(f"**{ident.text.decode('utf-8')}")
        ret = ""
        ret_node = _returns_annotation(m)
        if ret_node is not None:
            unparsed = unparse_annotation(ret_node)
            if unparsed:
                ret = f" -> {unparsed}"
        lines.append(f"{async_prefix}{mname}({', '.join(params)}){ret}")
    return "\n".join(lines)
