# benchmarks/haiku_subagent_bench.py
"""
Run the decomposed pipeline but capture every LLM prompt,
so we can replay them through Claude Code subagents.

Outputs a JSON file with all prompts in order.
"""
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

sys.stderr.write("")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("haiku_bench")


class PromptCapture:
    """Mock LLM client that captures prompts instead of calling an API."""

    def __init__(self):
        self.prompts: list[dict] = []
        self.model = "prompt-capture"
        self.base_url = "capture"
        self._context_length = 65536
        self._call_metrics = []

    @property
    def context_size(self) -> int:
        return self._context_length

    async def generate(self, messages, **kwargs):
        self.prompts.append({
            "type": "generate",
            "messages": messages,
            "kwargs": {k: v for k, v in kwargs.items() if k != "messages"},
        })
        raise _CaptureComplete(len(self.prompts))

    async def generate_with_tools(self, messages, tools=None, **kwargs):
        self.prompts.append({
            "type": "generate_with_tools",
            "messages": messages,
            "tools": [t.__name__ if callable(t) else str(t) for t in (tools or [])],
            "kwargs": {k: v for k, v in kwargs.items()
                       if k not in ("messages", "tools")},
        })
        raise _CaptureComplete(len(self.prompts))

    async def health_check(self):
        return True

    def get_loaded_model(self):
        return "prompt-capture"

    def tool_result_message(self, tool_call_id, result):
        return {"role": "tool", "tool_call_id": tool_call_id, "content": result}


class _CaptureComplete(Exception):
    """Raised to short-circuit pipeline after capturing a prompt."""
    def __init__(self, count):
        self.count = count


def capture_prompts(source_dir: str, context_file: str, query: str) -> dict:
    """Run pipeline stages manually to capture all prompts."""
    from fitz_forge.config.loader import load_config
    from fitz_forge.planning.pipeline.orchestrator import PlanningPipeline
    from fitz_forge.planning.agent.gatherer import AgentContextGatherer

    config = load_config()
    context = json.loads(Path(context_file).read_text())

    # Build prior_outputs from pre-gathered context (same as benchmark)
    prior_outputs = {}
    prior_outputs["_agent_context"] = context
    prior_outputs["_gathered_context"] = context.get("synthesized", "")
    prior_outputs["_raw_summaries"] = context.get("raw_summaries", "")
    prior_outputs["_file_contents"] = context.get("file_contents", {})
    prior_outputs["_file_index_entries"] = context.get("file_index_entries", {})
    prior_outputs["_source_dir"] = source_dir

    # Run implementation check + call graph extraction
    # (these are deterministic pre-stages)
    capture = PromptCapture()

    async def run():
        pipeline = PlanningPipeline(
            config=config,
            client=capture,
            stages=[],
        )
        # Run pre-stages (impl check + call graph)
        await pipeline._run_pre_stages(
            job_id="capture",
            job_description=query,
            prior_outputs=prior_outputs,
            progress_callback=None,
        )
        return prior_outputs

    try:
        prior = asyncio.run(run())
    except Exception:
        prior = prior_outputs

    return {
        "query": query,
        "call_graph_text": prior.get("_call_graph_text", ""),
        "call_graph": _serialize_call_graph(prior.get("_call_graph")),
        "raw_summaries": prior.get("_raw_summaries", ""),
        "gathered_context": prior.get("_gathered_context", ""),
        "file_contents": prior.get("_file_contents", {}),
        "file_index_entries": prior.get("_file_index_entries", {}),
        "implementation_check": prior.get("_implementation_check", {}),
        "source_dir": source_dir,
    }


def _serialize_call_graph(cg) -> dict:
    """Serialize CallGraph to JSON-safe dict."""
    if not cg:
        return {}
    return {
        "nodes": [
            {
                "file_path": n.file_path,
                "symbols": n.symbols,
                "one_line_summary": n.one_line_summary,
                "depth": n.depth,
                "class_detail": n.class_detail,
            }
            for n in cg.nodes
        ],
        "edges": cg.edges,
        "entry_points": cg.entry_points,
        "max_depth": cg.max_depth,
        "formatted": cg.format_for_prompt(),
    }


if __name__ == "__main__":
    SOURCE_DIR = "C:/Users/yanfi/PycharmProjects/fitz-sage"
    CONTEXT_FILE = "benchmarks/ideal_context.json"
    QUERY = "Add query result streaming so answers are delivered token-by-token instead of waiting for the full response"

    logger.info("Capturing pipeline context...")
    t0 = time.monotonic()
    ctx = capture_prompts(SOURCE_DIR, CONTEXT_FILE, QUERY)
    t1 = time.monotonic()
    logger.info(f"Captured in {t1-t0:.1f}s")

    out = Path("benchmarks/results/haiku_context.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(ctx, indent=2, default=str))
    logger.info(f"Saved to {out} ({out.stat().st_size / 1024:.0f} KB)")

    # Print what we got
    print(f"Call graph: {len(ctx['call_graph'].get('formatted', ''))} chars")
    print(f"Raw summaries: {len(ctx['raw_summaries'])} chars")
    print(f"File contents: {len(ctx['file_contents'])} files")
    print(f"Implementation check: {ctx['implementation_check']}")
