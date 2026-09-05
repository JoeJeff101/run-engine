"""The data layer, the command surface, the report writer, and the live backend.

The live backend is exercised against an injected fake client. That is not a
compromise: the thing worth testing is that the backend builds the right
request, reads the response shape tolerantly, records usage, and refuses
loudly without a credential. None of that needs the network, and a suite that
reaches the network is a suite that fails for reasons unrelated to the code.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from run_engine.agents.backends.anthropic import (
    DEFAULT_MODELS, AnthropicBackend, MissingCredential, _text_of,
)
from run_engine.cli import build_parser, main
from run_engine.datalayer import (
    EST, MANUAL, NEEDS_KEY, REAL, WIRED, DataLayerError, SourceDecl, SourceRegistry,
    rows_from_findings,
)
from run_engine.engine import RunOptions, run
from run_engine.pack import Pack
from run_engine.report import dossier, html

REPO = Path(__file__).resolve().parents[1]
PACKS = REPO / "packs"


@pytest.fixture(scope="module")
def manufacturing() -> Pack:
    return Pack.load(PACKS / "manufacturing")


# ---------------------------------------------------------------------------
# The data layer
# ---------------------------------------------------------------------------


def registry(**overrides) -> SourceRegistry:
    return SourceRegistry(sources=(
        SourceDecl(name="EDGAR", evidence_class="REAL", adapter="edgar",
                   answers="real margins of public comparables"),
        SourceDecl(name="Keepa", evidence_class="EST", adapter="keepa",
                   answers="competitor street prices"),
        SourceDecl(name="Nexar", evidence_class="REAL", adapter="nexar",
                   key_env="NEXAR_TOKEN", answers="component prices at break quantity"),
        SourceDecl(name="Xometry", evidence_class="REAL", manual=True,
                   answers="tooling cost at quantity"),
    ))


def test_the_class_comes_from_the_source_not_the_caller():
    """There is no argument by which a caller can propose a class, because a
    caller that could propose one could propose REAL."""
    reg = registry()
    assert reg.classify("EDGAR") == REAL
    assert reg.classify("Keepa") == EST
    # An aggregator's estimate is EST forever, however confident the finding looks.
    assert reg.classify("keepa") == EST  # lookup is case-insensitive, class is not negotiable


def test_an_undeclared_source_cannot_be_classified_at_all():
    with pytest.raises(DataLayerError, match="unknown source"):
        registry().classify("SomeGuysBlog")


def test_a_source_declaring_a_third_class_is_refused():
    with pytest.raises(DataLayerError, match="REAL or EST"):
        SourceDecl(name="X", evidence_class="PROBABLY")


def test_a_missing_key_makes_a_source_skip_and_say_so():
    """It never fabricates, and never silently downgrades itself to EST."""
    reg = registry()
    env: dict[str, str] = {}
    assert reg.get("Nexar").status(env).startswith(NEEDS_KEY)
    assert "NEXAR_TOKEN" in reg.get("Nexar").status(env)
    assert not reg.get("Nexar").available(env)
    # The class is unchanged by the credential being absent.
    assert reg.classify("Nexar") == REAL

    assert reg.get("Nexar").status({"NEXAR_TOKEN": "x"}) == WIRED


def test_a_manual_source_is_a_human_queue_not_a_failure():
    assert registry().get("Xometry").status({}) == MANUAL


def test_the_wiring_table_tells_you_which_keys_to_go_and_get():
    table = registry().table({})
    assert "NEXAR_TOKEN" in table
    assert "human queue" in table.lower()
    assert "2 of 4 sources are callable" in table


def test_findings_become_rows_graded_by_the_table():
    rows = rows_from_findings(registry(), [
        ("EDGAR", "T13", "comparable gross margin", "38%"),
        ("Keepa", "T01", "competitor street price", "$89"),
    ], run_id="r1")
    assert [r.grade for r in rows] == [REAL, EST]
    assert rows[0].origin == "EDGAR"


def test_duplicate_source_declarations_are_refused():
    with pytest.raises(DataLayerError, match="duplicate"):
        SourceRegistry(sources=(
            SourceDecl(name="A", evidence_class="REAL"),
            SourceDecl(name="A", evidence_class="EST"),
        ))


def test_a_pack_with_no_sources_cannot_produce_a_real_row(tmp_path):
    path = tmp_path / "sources.yaml"
    path.write_text(yaml.safe_dump({"sources": []}), encoding="utf-8")
    with pytest.raises(DataLayerError, match="no sources declared"):
        SourceRegistry.load(path)


def test_both_shipped_packs_declare_a_class_for_every_source():
    for name in ("manufacturing", "therapeutic"):
        pack = Pack.load(PACKS / name)
        assert pack.sources.sources, f"{name} declares no sources"
        for source in pack.sources.sources:
            assert source.evidence_class in (REAL, EST)
            assert source.answers, f"{name}/{source.name} says nothing about what it answers"


# ---------------------------------------------------------------------------
# The report writer
# ---------------------------------------------------------------------------


def test_the_dossier_and_the_page_agree_on_the_three_figures(manufacturing, tmp_path):
    result = run(manufacturing, RunOptions(runs_dir=tmp_path))
    md = result.folder.read("99_dossier.md")
    page = result.folder.read("report.html")

    assert f"{result.estimate.mean:.1%}" in md
    assert f"{result.estimate.mean:.1%}" in page
    assert "REAL fraction" in md and "REAL fraction" in page
    assert "80% CrI" in md


def test_the_page_escapes_pack_content_rather_than_interpolating_it(manufacturing, tmp_path):
    """Pack YAML is content, and content that reaches a page unescaped is a
    defect whatever its provenance."""
    from dataclasses import replace
    hostile = replace(manufacturing.brief, gain="ship <script>alert(1)</script> units")
    original, manufacturing.brief = manufacturing.brief, hostile
    try:
        result = run(manufacturing, RunOptions(runs_dir=tmp_path))
        page = result.folder.read("report.html")
        assert "<script>alert(1)</script>" not in page
        assert "&lt;script&gt;" in page
    finally:
        manufacturing.brief = original


def test_the_page_is_self_contained(manufacturing, tmp_path):
    result = run(manufacturing, RunOptions(runs_dir=tmp_path))
    page = result.folder.read("report.html")
    assert "<style>" in page
    assert "http://" not in page.replace("http://www.w3.org", "")
    assert "<script" not in page.lower()


# ---------------------------------------------------------------------------
# The command surface
# ---------------------------------------------------------------------------


def test_offline_is_the_default_and_live_is_an_opt_in():
    """Nothing should start spending money because someone forgot a flag."""
    args = build_parser().parse_args(["run", "--pack", "packs/manufacturing"])
    assert args.offline is True
    args = build_parser().parse_args(["run", "--pack", "packs/manufacturing", "--live"])
    assert args.offline is False


def test_sources_command_prints_the_wiring_table(capsys):
    assert main(["sources", "--pack", str(PACKS / "manufacturing")]) == 0
    out = capsys.readouterr().out
    assert "Class" in out and "Status" in out
    assert "property of the source" in out


def test_packs_command_lists_both_instantiations(capsys):
    assert main(["packs", "--root", str(PACKS)]) == 0
    out = capsys.readouterr().out
    assert "manufacturing" in out and "therapeutic" in out


def test_run_command_executes_and_reports(tmp_path, capsys):
    code = main(["run", "--pack", str(PACKS / "therapeutic"),
                 "--runs-dir", str(tmp_path)])
    assert code == 0
    out = capsys.readouterr().out
    assert "P(success)" in out
    assert "phase 0: frozen" in out


def test_calibrate_command_reports_a_brier_score(tmp_path, capsys):
    main(["run", "--pack", str(PACKS / "manufacturing"), "--runs-dir", str(tmp_path)])
    assert main(["calibrate", "--pack", str(PACKS / "manufacturing"),
                 "--runs-dir", str(tmp_path)]) == 0
    assert "brier" in capsys.readouterr().out.lower()


def test_a_broken_pack_is_reported_not_tracebacked(tmp_path, capsys):
    code = main(["run", "--pack", str(tmp_path / "nope"), "--runs-dir", str(tmp_path)])
    assert code == 2
    assert "pack directory not found" in capsys.readouterr().err


def test_amend_refuses_a_ratification_without_a_vote(tmp_path, capsys):
    import shutil
    root = tmp_path / "pack"
    shutil.copytree(PACKS / "manufacturing", root)

    main(["amend", "--pack", str(root), "propose", "--by", "cost",
          "--proposal", "raise the G0 prior", "--target", "gates.G0.prior_alpha",
          "--value", "5"])
    capsys.readouterr()

    code = main(["amend", "--pack", str(root), "ratify", "--id", "AMD-001", "--by", "qa"])
    assert code == 2
    assert "A vote alone cannot change the spec" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# The live backend
# ---------------------------------------------------------------------------


class FakeMessages:
    def __init__(self, text="a considered answer"):
        self.text = text
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)

        class Block:
            def __init__(self, t): self.text = t

        class Usage:
            input_tokens, output_tokens = 120, 45

        class Response:
            content = [Block(self.text)]
            usage = Usage()

        return Response()


class FakeClient:
    def __init__(self, text="a considered answer"):
        self.messages = FakeMessages(text)


def test_a_live_run_without_a_credential_refuses_and_names_the_variable(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(MissingCredential, match="ANTHROPIC_API_KEY"):
        AnthropicBackend()


def test_the_backend_sends_the_request_the_seat_asked_for():
    client = FakeClient()
    backend = AnthropicBackend(client=client)
    out = backend.complete(system="you are the cost accountant", prompt="what is COGS?",
                           tier="heavy", temperature=0.3, max_tokens=512)

    assert out == "a considered answer"
    sent = client.messages.calls[0]
    assert sent["system"] == "you are the cost accountant"
    assert sent["temperature"] == 0.3
    assert sent["max_tokens"] == 512
    assert sent["model"] == DEFAULT_MODELS["heavy"]
    assert sent["messages"] == [{"role": "user", "content": "what is COGS?"}]


def test_the_backend_records_usage_so_cost_stays_visible():
    backend = AnthropicBackend(client=FakeClient())
    backend.complete(system="s", prompt="p", tier="light")
    assert backend.usage.calls == 1
    assert backend.usage.input_tokens == 120
    assert backend.usage.estimated_cost() > 0
    assert "calls" in backend.usage.report()


def test_the_call_cap_is_a_real_ceiling():
    from run_engine.agents.backend import CallCapExceeded
    backend = AnthropicBackend(client=FakeClient(), call_cap=2)
    backend.complete(system="s", prompt="p")
    backend.complete(system="s", prompt="p")
    with pytest.raises(CallCapExceeded):
        backend.complete(system="s", prompt="p")


def test_the_model_id_comes_from_the_environment_when_set(monkeypatch):
    monkeypatch.setenv("MODEL_HEAVY", "some-other-vendor-model")
    backend = AnthropicBackend(client=FakeClient())
    assert backend.model_for_tier("heavy") == "some-other-vendor-model"


def test_response_parsing_tolerates_mixed_block_types():
    class Thinking:
        pass

    class Text:
        text = "the answer"

    class Response:
        content = [Thinking(), Text(), {"text": "and more"}]

    assert _text_of(Response()) == "the answer\nand more"
