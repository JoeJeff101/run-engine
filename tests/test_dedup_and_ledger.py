"""Deduplication, authority ranking, and the evidence stage/promote gate."""

from __future__ import annotations

import re

from run_engine.evidence.dedup import (
    SaturationTracker,
    TopicDeduper,
    cluster_key,
    merge_records,
    normalize_doi,
)
from run_engine.evidence.ledger import (
    PRIMARY_SOURCE_RE,
    Row,
    StagingLedger,
    grade_for,
    promote,
    source_string,
)
from run_engine.evidence.models import Record

# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------


def _rec(**kwargs) -> Record:
    kwargs.setdefault("title", "A paper about interfaces")
    kwargs.setdefault("source", "openalex")
    return Record(**kwargs)


def test_doi_normalization_strips_prefixes():
    assert normalize_doi("https://doi.org/10.1234/ABC") == "10.1234/abc"
    assert normalize_doi("doi:10.1234/abc") == "10.1234/abc"
    assert normalize_doi("") is None


def test_same_doi_collapses_across_sources():
    merged = merge_records([
        _rec(doi="10.1234/x", source="openalex"),
        _rec(doi="https://doi.org/10.1234/X", source="crossref"),
    ])
    assert len(merged) == 1


def test_identifier_outranks_title_for_identity():
    a = _rec(title="Same Title", doi="10.1234/a")
    b = _rec(title="Same Title", doi="10.1234/b")
    assert cluster_key(a) != cluster_key(b)
    assert len(merge_records([a, b])) == 2


def test_a_malformed_doi_is_not_treated_as_an_identifier():
    """A DOI prefix is `10.` plus 4-9 digits. Anything else is not a DOI.

    Accepting a malformed identifier would be worse than ignoring it: the whole
    ledger rests on identifiers being independently resolvable, and "10.1/a"
    resolves to nothing. Such a record falls through to title matching.
    """
    assert normalize_doi("10.1/a") is None
    assert cluster_key(_rec(title="T", doi="10.1/a")) == "title:t"


def test_title_dedup_is_the_last_resort():
    a = _rec(title="Identical Title!", doi=None)
    b = _rec(title="identical title", doi=None)
    assert cluster_key(a) == cluster_key(b)
    assert len(merge_records([a, b])) == 1


def test_authority_decides_which_copy_survives():
    oa = _rec(doi="10.1234/x", source="core", authority="oa")
    primary = _rec(doi="10.1234/x", source="patentsview", authority="primary_db")
    assert merge_records([oa, primary])[0].source == "patentsview"
    assert merge_records([primary, oa])[0].source == "patentsview"


def test_content_breaks_authority_ties():
    thin = _rec(doi="10.1234/x", source="a", authority="indexed")
    rich = _rec(doi="10.1234/x", source="b", authority="indexed", abstract="text", tldr="summary")
    assert merge_records([thin, rich])[0].source == "b"


def test_citations_break_remaining_ties():
    low = _rec(doi="10.1234/x", source="a", authority="indexed", citation_count=2)
    high = _rec(doi="10.1234/x", source="b", authority="indexed", citation_count=400)
    assert merge_records([low, high])[0].source == "b"


def test_surviving_record_absorbs_fields_from_the_one_it_displaces():
    """The strong copy often lacks the weak copy's open-access link."""
    strong = _rec(doi="10.1234/x", source="patentsview", authority="primary_db")
    weak = _rec(doi="10.1234/x", source="core", authority="oa",
                open_access_pdf="http://example.com/p.pdf", abstract="body")

    survivor = merge_records([strong, weak])[0]

    assert survivor.source == "patentsview"
    assert survivor.open_access_pdf == "http://example.com/p.pdf"
    assert survivor.abstract == "body"


def test_results_sort_by_authority():
    merged = merge_records([
        _rec(doi="10.1234/a", authority="weak"),
        _rec(doi="10.1234/b", authority="primary_db"),
        _rec(doi="10.1234/c", authority="oa"),
        _rec(doi="10.1234/d", authority="indexed"),
    ])
    assert [r.authority for r in merged] == ["primary_db", "indexed", "oa", "weak"]


# ---------------------------------------------------------------------------
# Saturation and topic dedup
# ---------------------------------------------------------------------------


def test_saturation_needs_consecutive_dry_rounds():
    tracker = SaturationTracker(dry_rounds=2)
    tracker.record(5)
    tracker.record(0)
    assert not tracker.saturated, "one dry round is not saturation"
    tracker.record(3)
    tracker.record(0)
    assert not tracker.saturated, "a productive round must reset the counter"
    tracker.record(0)
    assert tracker.saturated


def test_topic_dedup_degrades_without_an_embedding_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    deduper = TopicDeduper()
    assert not deduper.enabled

    fresh = deduper.filter_new(["topic one", "topic two", "Topic One"])
    assert fresh == ["topic one", "topic two"], "exact matching still applies"


def test_topic_dedup_checks_within_the_incoming_batch(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    deduper = TopicDeduper()
    assert deduper.filter_new(["same", "same", "same"]) == ["same"]


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------


def test_registry_identifier_grades_real():
    assert grade_for(_rec(source="pubchem", entity_id="PUBCHEM:2244")) == "REAL"
    assert grade_for(_rec(source="patentsview", entity_id="USPTO:10000000")) == "REAL"


def test_doi_only_grades_est_never_real():
    assert grade_for(_rec(doi="10.1234/x")) == "EST"


def test_no_identifier_grades_weak():
    assert grade_for(_rec()) == "WEAK"


def test_source_string_satisfies_the_promotion_regex():
    """Cross-module contract: what staging writes, promotion must accept."""
    for rec in (
        _rec(source="pubchem", entity_id="PUBCHEM:2244"),
        _rec(doi="10.1234/abc"),
        _rec(pmid="12345678"),
    ):
        assert PRIMARY_SOURCE_RE.search(source_string(rec)), rec


def test_source_string_for_an_unidentified_record_fails_the_regex():
    assert not PRIMARY_SOURCE_RE.search(source_string(_rec()))


# ---------------------------------------------------------------------------
# Staging
# ---------------------------------------------------------------------------


def _row(claim="a claim", value="a value", grade="EST", source="10.1234/abc") -> Row:
    return Row(topic="a topic", claim=claim, value=value, grade=grade, source=source, origin="test")


def test_dry_run_is_the_default_and_writes_nothing(tmp_path):
    path = tmp_path / "staged.md"
    ledger = StagingLedger(path, run_id="R1")

    staged = ledger.stage([_row()])

    assert staged, "dry run still reports what it would write"
    assert not path.exists()


def test_apply_writes_a_nine_column_table(tmp_path):
    path = tmp_path / "staged.md"
    StagingLedger(path, run_id="R1").stage([_row()], dry=False)

    body = path.read_text(encoding="utf-8")
    data_lines = [ln for ln in body.splitlines() if ln.startswith("|") and "PROP-" in ln]
    assert len(data_lines) == 1
    assert len(data_lines[0].strip("|").split("|")) >= 9
    assert "PROP-R1-01" in body


def test_staging_is_idempotent(tmp_path):
    path = tmp_path / "staged.md"
    ledger = StagingLedger(path, run_id="R1")

    ledger.stage([_row()], dry=False)
    second = ledger.stage([_row()], dry=False)

    assert second == [], "re-staging the same row is a no-op"


def test_staging_is_bounded_per_call(tmp_path):
    ledger = StagingLedger(tmp_path / "staged.md", run_id="R1")
    rows = [_row(value=f"value {i}") for i in range(20)]
    assert len(ledger.stage(rows, max_rows=4)) == 4


def test_pipes_in_content_cannot_break_the_table(tmp_path):
    path = tmp_path / "staged.md"
    StagingLedger(path, run_id="R1").stage(
        [_row(claim="a | b | c", value="x | y")], dry=False
    )
    data = [ln for ln in path.read_text(encoding="utf-8").splitlines() if "PROP-" in ln][0]
    # Split on unescaped pipes only -- the escaped ones are content, not structure.
    cells = re.split(r"(?<!\\)\|", data.strip("|"))
    assert len(cells) == 9
    assert r"a \| b \| c" in data


# ---------------------------------------------------------------------------
# Promotion
# ---------------------------------------------------------------------------


def test_promotion_previews_without_writing(tmp_path):
    staged, authoritative = tmp_path / "s.md", tmp_path / "a.md"
    StagingLedger(staged, run_id="R1").stage([_row()], dry=False)

    report = promote(staged, authoritative)

    assert report.promoted and not report.applied
    assert not authoritative.exists()


def test_promotion_writes_only_with_apply(tmp_path):
    staged, authoritative = tmp_path / "s.md", tmp_path / "a.md"
    StagingLedger(staged, run_id="R1").stage([_row()], dry=False)

    report = promote(staged, authoritative, apply=True)

    assert report.applied
    assert "EV-001" in authoritative.read_text(encoding="utf-8")


def test_weak_rows_are_never_promotable(tmp_path):
    staged, authoritative = tmp_path / "s.md", tmp_path / "a.md"
    StagingLedger(staged, run_id="R1").stage(
        [_row(grade="WEAK", source="(no primary identifier)")], dry=False
    )

    report = promote(staged, authoritative, apply=True)

    assert report.promoted == []
    assert report.rejected


def test_a_row_without_a_primary_identifier_is_rejected(tmp_path):
    """Grade alone is not enough; the citation itself must carry an identifier."""
    staged, authoritative = tmp_path / "s.md", tmp_path / "a.md"
    StagingLedger(staged, run_id="R1").stage(
        [_row(grade="REAL", source="see the paper")], dry=False
    )

    report = promote(staged, authoritative, apply=True)

    assert report.promoted == []
    assert "no primary-source identifier" in report.rejected[0][1]


def test_promotion_refuses_duplicates(tmp_path):
    staged, authoritative = tmp_path / "s.md", tmp_path / "a.md"
    StagingLedger(staged, run_id="R1").stage([_row()], dry=False)

    promote(staged, authoritative, apply=True)
    second = promote(staged, authoritative, apply=True)

    assert second.promoted == []
    assert "already present" in second.rejected[0][1]


def test_promotion_backs_up_before_touching_the_authoritative_file(tmp_path):
    staged, authoritative = tmp_path / "s.md", tmp_path / "a.md"
    StagingLedger(staged, run_id="R1").stage([_row()], dry=False)
    promote(staged, authoritative, apply=True)

    StagingLedger(staged, run_id="R2").stage([_row(value="another value")], dry=False)
    report = promote(staged, authoritative, apply=True)

    assert report.backup is not None
    assert list(tmp_path.glob("a.backup_*.md"))


def test_agents_cannot_reach_the_authoritative_file_through_staging(tmp_path):
    """The structural guarantee: staging has no path to the authoritative ledger."""
    staged, authoritative = tmp_path / "s.md", tmp_path / "a.md"
    ledger = StagingLedger(staged, run_id="R1")

    ledger.stage([_row() for _ in range(4)], dry=False)

    assert staged.exists()
    assert not authoritative.exists(), "only promote() may create the authoritative ledger"


# ---------------------------------------------------------------------------
# The invariant, stated by name
# ---------------------------------------------------------------------------


def test_only_the_promotion_script_writes_a_real_row(tmp_path):
    """Invariant 2. An agent may write the letters R-E-A-L into a staged row --
    it is a text file and nothing can stop it. What it cannot do is make that
    row authoritative. Promotion is a separate command, run by a person, that
    checks for a resolvable identifier and refuses without one.

    So the guarantee is not "agents never say REAL". It is "saying REAL is not
    how a row becomes REAL", which is the only version of the guarantee that
    can actually be enforced.
    """
    staged = tmp_path / "staged.md"
    authoritative = tmp_path / "ledger.md"

    fabricated = Row(
        topic="G0", claim="the demand test passed decisively", value="pass",
        grade="REAL", source="our analysis of the market", origin="agent",
    )
    honest = Row(
        topic="G0", claim="Apple's reported gross margin", value="46/100",
        grade="REAL", source="CIK: 0000320193", origin="edgar",
    )
    StagingLedger(staged, run_id="r1").stage([fabricated, honest], dry=False)

    # Staging wrote both. Staging carries no authority, so this proves nothing yet.
    assert "our analysis of the market" in staged.read_text()

    report = promote(staged, authoritative, apply=True)

    promoted_claims = {r.claim for r in report.promoted}
    rejected_claims = {r.claim for r, _ in report.rejected}
    assert "Apple's reported gross margin" in promoted_claims
    assert "the demand test passed decisively" in rejected_claims, (
        "a confident agent assertion with no identifier must not reach the "
        "authoritative ledger"
    )
    assert "our analysis of the market" not in authoritative.read_text()
