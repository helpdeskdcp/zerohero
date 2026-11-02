import json
from datetime import datetime

from app.research_engines.hybrid_sr_mtf import forward_tag as F


def sig(i, day, hhmm, und="NIFTY", ot="CE"):
    return {"id": i, "participant": "INSTITUTION_TRADER", "underlying": und, "option_type": ot, "t": datetime.fromisoformat(f"{day}T{hhmm}:00+05:30").timestamp()}


def test_the_frozen_rule_accepts_only_early_index_calls_whose_bias_is_not_against():
    assert F.decide("NIFTY", 9 * 60 + 20, 1, 1)["decision"] == "ACCEPT"
    assert F.decide("SENSEX", 10 * 60 + 15, -1, 0)["decision"] == "ACCEPT"                         # neutral bias is fine, the window edge is inclusive
    assert F.decide("NIFTY", 9 * 60 + 20, 1, -1) == {"decision": "REJECT", "reasons": ["15M_BIAS_AGAINST"], "bias15": -1}
    r = F.decide("CRUDEOIL", 11 * 60, 1, None); assert r["decision"] == "REJECT" and r["reasons"] == ["NOT_NSE_BSE_INDEX", "POSTED_OUTSIDE_0915_1015"]
    assert F.decide("NIFTY", 10 * 60 + 16, 1, 1)["reasons"] == ["POSTED_OUTSIDE_0915_1015"]
    assert F.decide("NIFTY", 9 * 60 + 30, 1, None)["decision"] == "UNTAGGED_NO_INDEX_DATA"           # no index candles: neither accepted nor rejected


def test_tags_are_written_once_never_rewritten_and_split_into_in_sample_and_forward(tmp_path):
    p = str(tmp_path / "tags.jsonl"); S = [sig("A", "2026-10-29", "09:30"), sig("B", "2026-11-02", "09:30"), sig("C", "2026-11-02", "13:00", ot="PE")]
    new = F.tag_new(S, p, bias_fn=lambda s, side: side)
    assert {r["id"]: (r["period"], r["decision"]) for r in new} == {"A": ("IN_SAMPLE", "ACCEPT"), "B": ("FORWARD", "ACCEPT"), "C": ("FORWARD", "REJECT")}
    first = open(p).read()
    assert F.tag_new(S + [sig("D", "2026-11-03", "09:40", ot="PE")], p, bias_fn=lambda s, side: -side)[0]["id"] == "D"       # only the new call is tagged, with the bias function of that moment
    lines = [json.loads(l) for l in open(p)]; assert [l["id"] for l in lines] == ["A", "B", "C", "D"] and open(p).read().startswith(first)


def test_summary_counts_only_settled_calls_and_reports_the_gate_as_not_yet(tmp_path):
    p = str(tmp_path / "tags.jsonl"); S = [sig(f"F{i}", "2026-11-0%d" % (2 + i % 5), "09:30") for i in range(6)] + [sig("R1", "2026-11-02", "12:00")]
    F.tag_new(S, p, bias_fn=lambda s, side: 0)
    res = {f"F{i}": {"status": "SETTLED", "net_rs": 1000.0 if i % 2 else -500.0} for i in range(5)}; res["R1"] = {"status": "SETTLED", "net_rs": -2000.0}
    out = F.summarize(p, res); fa = out["groups"]["FORWARD/ACCEPT"]
    assert fa["calls"] == 6 and fa["settled"] == 5 and fa["not_settled"] == 1 and out["groups"]["FORWARD/REJECT"]["settled"] == 1
    assert out["gate"]["status"] == "NOT_YET" and out["gate"]["have"] == 5


def test_the_rule_file_is_frozen_with_a_hash():
    r = json.load(open(F.DIR + "/RULE_FROZEN_2026-10-30.json")); assert r["version"] == F.RULE_VERSION and r["forward_from"] == F.FORWARD_FROM and len(r["sha256_of_rule_text"]) == 64
