"""Static ASCEND new-hire training dashboard mockup (tools/ascend_training_dashboard_example.py)."""
import json
import re
import sys
from pathlib import Path

# The generator lives in tools/ (a script dir, not a package) — put it on the path.
TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import ascend_training_dashboard_example as ex  # noqa: E402


def test_renders_doctype_and_section_headers():
    html = ex.render_html(ex.build_sample(), ex.build_meta())
    assert html.startswith("<!doctype html")
    for header in ["ASCEND New-Hire Training", "EXAMPLE DATA",
                   "Re-training recommendations", "Learning-block progress",
                   "Modality breakdown", "Behavior / skill scores",
                   "Expert actions", "Trainer actions", "Coach actions"]:
        assert header in html, f"missing: {header}"
    assert '<script id="hires" type="application/json">' in html


def test_retraining_section_is_above_the_other_sections():
    html = ex.render_html(ex.build_sample(), ex.build_meta())
    # "moved up near the top" — the re-training section must render before the others.
    assert (html.index("Re-training recommendations")
            < html.index("Learning-block progress")
            < html.index("Behavior / skill scores"))


def test_sample_has_expected_shape():
    hires = ex.build_sample()
    assert len(hires) >= 8
    h = hires[0]
    assert [b["name"] for b in h["blocks"]] == ex.BLOCKS
    assert all({"skill", "cat", "score"} <= set(s) for s in h["skills"])


def test_retraining_hires_are_flagged_below_benchmark():
    rt = [h for h in ex.build_sample() if h["status"] == "retraining"]
    assert rt, "expected at least one re-training hire in the sample"
    for h in rt:
        r = h["retrain"]
        assert r and r["behaviors"], "re-training hire needs focus behaviors"
        assert h["test_call"] is not None and h["test_call"] < ex.PASS_BENCHMARK
        # role-split recommendations: expert / trainer (support) / coach (support)
        assert r["expert_actions"] and r["trainer_actions"] and r["coach_actions"]


def test_embedded_json_roundtrips():
    html = ex.render_html(ex.build_sample(), ex.build_meta())
    m = re.search(r'<script id="hires" type="application/json">(.*?)</script>', html, re.S)
    data = json.loads(m.group(1))
    assert len(data) == len(ex.build_sample())
    assert {h["id"] for h in data} == {h["id"] for h in ex.build_sample()}


def test_json_export_envelope_and_record_schema():
    exp = ex.build_export(ex.build_sample(), ex.build_meta())
    assert exp["schema_version"] == ex.SCHEMA_VERSION
    assert exp["pass_benchmark"]["value"] == ex.PASS_BENCHMARK
    assert len(exp["experts"]) == len(ex.build_sample())
    # serializable
    json.dumps(exp)
    e = exp["experts"][0]
    for key in ["id", "identity", "status", "current_block", "latest_test_call",
                "learning_blocks", "modalities", "skill_scores", "retraining"]:
        assert key in e, f"missing {key}"
    assert {"name", "class", "trainer", "icp_client", "tenure_group"} <= set(e["identity"])
    # status is tagged (code/label/severity); skill scores tagged + below_benchmark flag
    assert {"code", "label", "severity"} <= set(e["status"])
    s0 = e["skill_scores"][0]
    assert {"metric", "value", "display"} <= set(s0["score"]) and "below_benchmark" in s0


def test_json_export_retraining_recommendations_split_by_role():
    exp = ex.build_export(ex.build_sample(), ex.build_meta())
    rts = [e for e in exp["experts"] if e["retraining"]]
    assert rts, "expected re-training experts in the export"
    rec = rts[0]["retraining"]["recommendations"]
    assert set(rec) == {"expert", "trainer", "coach"}
    assert rec["expert"]["support"] is False
    assert rec["trainer"]["support"] is True and rec["coach"]["support"] is True
    assert rec["expert"]["actions"] and rec["trainer"]["actions"] and rec["coach"]["actions"]


def test_json_export_omits_internal_compact_keys():
    e = ex.to_export_record(ex.build_sample()[0])
    for leaked in ["cls", "blk", "skills", "retrain", "test_call"]:
        assert leaked not in e, f"internal key leaked into export: {leaked}"
