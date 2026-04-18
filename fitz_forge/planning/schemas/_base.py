# fitz_forge/planning/schemas/_base.py
"""Shared base class for all LLM-output schemas.

Local models commonly emit ``"field": null`` for optional string fields
even when the schema declares ``default=""``. Pydantic's field default
only fills a **missing** key, not a **present but null** value — so
``str`` fields with a null value fail validation and crash the whole
stage. That class of failure wiped V2 Run 1 (streaming benchmark,
2026-04-18): ``risks[2].verification`` and ``risks[3].verification``
came back null.

``LLMOutputModel`` adds one ``mode="before"`` coercer that runs on every
subclass: if a string-typed field is present with a null value, swap it
for the declared default (or empty string). Safe because the only
semantic of "null" for an optional string is "missing", which is
exactly what the default represents.
"""

from __future__ import annotations

from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel, model_validator


def _annotation_is_string_like(annotation: Any) -> bool:
    """True if the annotation is ``str`` or ``str | None``/``Optional[str]``.

    We coerce null for explicit str fields AND for optional str fields
    whose declared default is not None — both get treated the same way
    since the model emitting null for a ``str | None = ""`` field is
    still behaving as "I have no value" rather than "I chose null."
    """
    if annotation is str:
        return True
    origin = get_origin(annotation)
    if origin is Union:
        args = get_args(annotation)
        return any(a is str for a in args)
    return False


class LLMOutputModel(BaseModel):
    """BaseModel that tolerates LLM-emitted ``null`` for string fields."""

    @model_validator(mode="before")
    @classmethod
    def _coerce_null_strings_in_llm_output(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        for name, info in cls.model_fields.items():
            if name not in data or data[name] is not None:
                continue
            if not _annotation_is_string_like(info.annotation):
                continue
            default = info.default
            data[name] = default if isinstance(default, str) else ""
        return data
