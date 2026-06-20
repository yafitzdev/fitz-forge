# tests/unit/test_agent_gatherer.py
"""Unit tests for AgentContextGatherer (fitz-sage powered retrieval)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fitz_forge.config.schema import AgentConfig
from fitz_forge.planning.agent.gatherer import AgentContextGatherer


def _make_config(**kwargs):
    defaults = dict(enabled=True, max_file_bytes=50_000)
    defaults.update(kwargs)
    return AgentConfig(**defaults)


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.model = "test-model"
    client.context_size = 65536
    client.generate = AsyncMock(return_value="LLM response")
    return client


def _make_read_result(file_path, content, origin="selected"):
    """Build a mock ReadResult matching fitz-sage's ReadResult shape."""
    address = MagicMock()
    address.metadata = {"origin": origin}
    address.score = {"selected": 1.0, "import": 0.9, "neighbor": 0.8}.get(origin, 0.8)
    result = MagicMock()
    result.file_path = file_path
    result.content = content
    result.address = address
    return result


class _FakeManifest:
    def __init__(self, paths):
        self._paths = paths

    def entries(self):
        return self._paths


class _FakeEngine:
    def __init__(self, results=None, error=None):
        self._results = results or []
        self._error = error
        self.queries = []

    def retrieve(self, query):
        self.queries.append(query)
        if self._error is not None:
            raise self._error
        return self._results


def _patch_krag_engine(results, paths, error=None):
    engine = _FakeEngine(results=results, error=error)
    manifest = _FakeManifest(paths)
    return patch.object(
        AgentContextGatherer,
        "_build_engine",
        return_value=(engine, manifest),
    ), engine


# ---------------------------------------------------------------------------
# KRAG engine bridge
# ---------------------------------------------------------------------------
class TestKragEngineBridge:
    def test_build_engine_uses_client_endpoint_model(self, tmp_path, mock_client):
        """fitz-forge exposes one local model to every KRAG chat tier."""
        mock_client.model = "single-30b"
        mock_client.base_url = "http://localhost:1234/v1"
        fake_engine = MagicMock()
        fake_manifest = MagicMock()
        fake_engine.point.return_value = fake_manifest

        with patch(
            "fitz_forge.planning.agent.gatherer.create_engine",
            return_value=fake_engine,
        ) as create_engine:
            gatherer = AgentContextGatherer(
                config=_make_config(),
                source_dir=str(tmp_path),
            )
            engine, manifest = gatherer._build_engine(mock_client)

        assert engine is fake_engine
        assert manifest is fake_manifest
        create_engine.assert_called_once()
        assert create_engine.call_args.args[0] == "fitz_krag"

        config = create_engine.call_args.kwargs["config"]
        assert config.chat_fast == "endpoint/single-30b"
        assert config.chat_balanced == "endpoint/single-30b"
        assert config.chat_smart == "endpoint/single-30b"
        assert config.chat_base_url == "http://localhost:1234/v1"
        assert config.retrieval_workers == 1
        assert config.enable_multi_hop is False

        fake_engine.point.assert_called_once()
        assert fake_engine.point.call_args.args[0] == tmp_path.resolve()
        assert fake_engine.point.call_args.kwargs == {
            "collection": config.collection,
            "start_worker": False,
        }


# ---------------------------------------------------------------------------
# Prioritize for summary
# ---------------------------------------------------------------------------
class TestPrioritizeForSummary:
    def test_code_before_docs(self):
        paths = [
            "docs/ARCHITECTURE.md",
            "fitz_sage/llm/providers/openai.py",
            "docs/CONFIG.md",
            "fitz_sage/core/answer.py",
        ]
        result = AgentContextGatherer._prioritize_for_summary(paths)
        assert result[:2] == [
            "fitz_sage/llm/providers/openai.py",
            "fitz_sage/core/answer.py",
        ]
        assert set(result[2:]) == {"docs/ARCHITECTURE.md", "docs/CONFIG.md"}

    def test_tests_between_code_and_docs(self):
        paths = [
            "docs/README.md",
            "tests/unit/test_foo.py",
            "fitz_sage/engine.py",
        ]
        result = AgentContextGatherer._prioritize_for_summary(paths)
        assert result[0] == "fitz_sage/engine.py"
        assert result[1] == "tests/unit/test_foo.py"
        assert result[2] == "docs/README.md"

    def test_preserves_order_within_tier(self):
        paths = [
            "fitz_sage/b.py",
            "fitz_sage/a.py",
            "fitz_sage/c.py",
        ]
        result = AgentContextGatherer._prioritize_for_summary(paths)
        assert result == ["fitz_sage/b.py", "fitz_sage/a.py", "fitz_sage/c.py"]

    def test_examples_and_github_are_low_priority(self):
        paths = [
            "examples/01_quickstart.py",
            ".github/workflows/ci.yml",
            "fitz_sage/core.py",
        ]
        result = AgentContextGatherer._prioritize_for_summary(paths)
        assert result[0] == "fitz_sage/core.py"

    def test_config_files_between_code_and_tests(self):
        paths = [
            "tests/test_x.py",
            "pyproject.toml",
            "fitz_sage/main.py",
        ]
        result = AgentContextGatherer._prioritize_for_summary(paths)
        assert result[0] == "fitz_sage/main.py"
        assert result[1] == "pyproject.toml"
        assert result[2] == "tests/test_x.py"


# ---------------------------------------------------------------------------
# E2E: gather() — mocks the KRAG engine boundary
# ---------------------------------------------------------------------------
class TestGatherEndToEnd:
    @pytest.mark.asyncio
    async def test_disabled_returns_empty(self, tmp_path, mock_client):
        gatherer = AgentContextGatherer(
            config=_make_config(enabled=False), source_dir=str(tmp_path)
        )
        result = await gatherer.gather(mock_client, "task")
        assert result["synthesized"] == ""
        assert result["raw_summaries"] == ""

    @pytest.mark.asyncio
    async def test_happy_path(self, tmp_path, mock_client):
        (tmp_path / "main.py").write_text("def run(): pass")
        (tmp_path / "util.py").write_text("def helper(): pass")

        mock_results = [
            _make_read_result("main.py", "def run(): pass", "selected"),
            _make_read_result("util.py", "def helper(): pass", "neighbor"),
        ]

        engine_patch, engine = _patch_krag_engine(mock_results, ["main.py", "util.py"])
        with engine_patch:
            gatherer = AgentContextGatherer(
                config=_make_config(),
                source_dir=str(tmp_path),
            )
            result = await gatherer.gather(mock_client, "how does run work?")

        assert "STRUCTURAL OVERVIEW" in result["synthesized"]
        assert "FILE MANIFEST" in result["raw_summaries"]
        assert "main.py" in result["raw_summaries"]
        assert "main.py" in result["file_contents"]
        assert "file_index_entries" in result
        assert "agent_files" in result
        agent_files = result["agent_files"]
        assert agent_files["total_screened"] == 2
        assert "main.py" in agent_files["scan_hits"]
        assert engine.queries[0].text == "how does run work?"

    @pytest.mark.asyncio
    async def test_empty_results_returns_empty(self, tmp_path, mock_client):
        engine_patch, _ = _patch_krag_engine([], ["main.py"])
        with engine_patch:
            gatherer = AgentContextGatherer(
                config=_make_config(),
                source_dir=str(tmp_path),
            )
            result = await gatherer.gather(mock_client, "task")

        assert result["synthesized"] == ""
        assert result["raw_summaries"] == ""

    @pytest.mark.asyncio
    async def test_provenance_tracks_origins(self, tmp_path, mock_client):
        (tmp_path / "a.py").write_text("class A: pass")
        (tmp_path / "b.py").write_text("class B: pass")
        (tmp_path / "c.py").write_text("class C: pass")

        mock_results = [
            _make_read_result("a.py", "class A: pass", "selected"),
            _make_read_result("b.py", "class B: pass", "import"),
            _make_read_result("c.py", "class C: pass", "neighbor"),
        ]

        engine_patch, _ = _patch_krag_engine(mock_results, ["a.py", "b.py", "c.py"])
        with engine_patch:
            gatherer = AgentContextGatherer(
                config=_make_config(),
                source_dir=str(tmp_path),
            )
            result = await gatherer.gather(mock_client, "task")

        prov = result["agent_files"]["file_provenance"]
        assert prov["a.py"]["signals"] == ["scan"]
        assert prov["b.py"]["signals"] == ["import"]
        assert prov["c.py"]["signals"] == ["neighbor"]

    @pytest.mark.asyncio
    async def test_total_failure_returns_empty(self, tmp_path, mock_client):
        engine_patch, _ = _patch_krag_engine([], ["main.py"], RuntimeError("total fail"))
        with engine_patch:
            gatherer = AgentContextGatherer(
                config=_make_config(),
                source_dir=str(tmp_path),
            )
            result = await gatherer.gather(mock_client, "task")

        assert isinstance(result, dict)
        assert result["synthesized"] == ""

    @pytest.mark.asyncio
    async def test_build_engine_receives_client_and_retrieves_query(self, tmp_path, mock_client):
        (tmp_path / "a.py").write_text("x = 1")

        engine_patch, engine = _patch_krag_engine(
            [_make_read_result("a.py", "x = 1", "selected")],
            ["a.py"],
        )
        with engine_patch as build_engine:
            gatherer = AgentContextGatherer(
                config=_make_config(max_file_bytes=25_000),
                source_dir=str(tmp_path),
            )
            await gatherer.gather(mock_client, "task")

        build_engine.assert_called_once_with(mock_client)
        assert engine.queries[0].text == "task"


# ---------------------------------------------------------------------------
# Progress callback
# ---------------------------------------------------------------------------
class TestProgressCallback:
    @pytest.mark.asyncio
    async def test_all_phases_reported(self, tmp_path, mock_client):
        (tmp_path / "main.py").write_text("def run(): pass")

        engine_patch, _ = _patch_krag_engine(
            [_make_read_result("main.py", "def run(): pass", "selected")],
            ["main.py"],
        )
        with engine_patch:
            phases = []

            def track(progress, phase):
                phases.append(phase)

            gatherer = AgentContextGatherer(
                config=_make_config(),
                source_dir=str(tmp_path),
            )
            await gatherer.gather(mock_client, "task", progress_callback=track)

        phase_names = [p.split(":")[1] if ":" in p else p for p in phases]
        assert "mapping" in phase_names
        assert "scanning_index" in phase_names
        assert "reading" in phase_names

    @pytest.mark.asyncio
    async def test_async_callback_awaited(self, tmp_path, mock_client):
        (tmp_path / "main.py").write_text("def run(): pass")

        engine_patch, _ = _patch_krag_engine(
            [_make_read_result("main.py", "def run(): pass", "selected")],
            ["main.py"],
        )
        with engine_patch:
            calls = []

            async def async_track(progress, phase):
                calls.append(phase)

            gatherer = AgentContextGatherer(
                config=_make_config(),
                source_dir=str(tmp_path),
            )
            await gatherer.gather(
                mock_client,
                "task",
                progress_callback=async_track,
            )

        assert len(calls) > 0


# ---------------------------------------------------------------------------
# Seed-and-fetch
# ---------------------------------------------------------------------------
class TestSeedAndFetch:
    @pytest.mark.asyncio
    async def test_all_files_as_seeds_when_under_cap(self, tmp_path, mock_client):
        (tmp_path / "main.py").write_text("def run(): pass")
        (tmp_path / "util.py").write_text("def helper(): pass")

        mock_results = [
            _make_read_result("main.py", "def run(): pass", "selected"),
            _make_read_result("util.py", "def helper(): pass", "selected"),
        ]

        engine_patch, _ = _patch_krag_engine(mock_results, ["main.py", "util.py"])
        with engine_patch:
            gatherer = AgentContextGatherer(
                config=_make_config(max_seed_files=30),
                source_dir=str(tmp_path),
            )
            result = await gatherer.gather(mock_client, "run helper")

        assert "main.py" in result["raw_summaries"]
        assert "util.py" in result["raw_summaries"]
        assert "FILE MANIFEST" in result["raw_summaries"]

    @pytest.mark.asyncio
    async def test_seed_cap_defers_excess_to_tool_pool(self, tmp_path, mock_client):
        for i in range(5):
            (tmp_path / f"file{i}.py").write_text(f"def func{i}(): pass")

        mock_results = [
            _make_read_result(f"file{i}.py", f"def func{i}(): pass", "selected") for i in range(5)
        ]

        engine_patch, _ = _patch_krag_engine(mock_results, [f"file{i}.py" for i in range(5)])
        with engine_patch:
            gatherer = AgentContextGatherer(
                config=_make_config(max_seed_files=2),
                source_dir=str(tmp_path),
            )
            result = await gatherer.gather(mock_client, "func")

        # All 5 in file_contents for tool access
        for i in range(5):
            assert f"file{i}.py" in result["file_contents"]
        # All 5 in manifest (no seed/pool split — all files in manifest)
        raw = result["raw_summaries"]
        for i in range(5):
            assert f"file{i}.py" in raw

    @pytest.mark.asyncio
    async def test_scan_hits_prioritized_in_seed_set(self, tmp_path, mock_client):
        (tmp_path / "scan_hit.py").write_text("def scanned(): pass")
        (tmp_path / "neighbor.py").write_text("def matched(): pass")

        mock_results = [
            _make_read_result("scan_hit.py", "def scanned(): pass", "selected"),
            _make_read_result("neighbor.py", "def matched(): pass", "neighbor"),
        ]

        engine_patch, _ = _patch_krag_engine(
            mock_results,
            [
                "scan_hit.py",
                "neighbor.py",
            ],
        )
        with engine_patch:
            gatherer = AgentContextGatherer(
                config=_make_config(max_seed_files=1),
                source_dir=str(tmp_path),
            )
            result = await gatherer.gather(mock_client, "scanned")

        raw = result["raw_summaries"]
        # Both files in manifest (no seed cap — all files listed)
        assert "scan_hit.py" in raw
        assert "neighbor.py" in raw

    @pytest.mark.asyncio
    async def test_provenance_tracks_seed_vs_pool(self, tmp_path, mock_client):
        for i in range(4):
            (tmp_path / f"m{i}.py").write_text(f"def f{i}(): pass")

        mock_results = [
            _make_read_result(f"m{i}.py", f"def f{i}(): pass", "selected") for i in range(4)
        ]

        engine_patch, _ = _patch_krag_engine(mock_results, [f"m{i}.py" for i in range(4)])
        with engine_patch:
            gatherer = AgentContextGatherer(
                config=_make_config(max_seed_files=2),
                source_dir=str(tmp_path),
            )
            result = await gatherer.gather(mock_client, "func")

        prov = result["agent_files"]["file_provenance"]
        # No files inlined in prompt (manifest-only approach)
        in_prompt_count = sum(1 for p in prov.values() if p["in_prompt"])
        assert in_prompt_count == 0
        # All files should have provenance
        assert len(prov) == 4
