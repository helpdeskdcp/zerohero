import pytest

from app.arena import channel_parse as CP

HERO = """nse #HERO_ZERO_NIFTY  _22400CE
🏌️‍♂️Buy Range : 70-80
🎯Target : 120/170/260
⛔️Stoploss : 05
📆 Horizon: Hero Zero

—————————————————
📢 For Research Services,  Stock Market Risks, Disclosures and Disclaimers, Investor Charter, Complaint Table, Grievances, MITC, Refund Policy, and Others.
Please visit our website: www.tradewithmohitagrawal.com and read everything to avoid any future conflict of interest."""
SENS = """#SENSEX  _71900 CE
🏌️‍♂️Buy Range : 430-450
🎯Target : 620/710/850
⛔️Stoploss : 300
📆 Horizon: Intraday

—————————————————
📢 For Research Services, Stock Market Risks, Disclosures and Disclaimers, Investor Charter, Complaint Table, Grievances, MITC, Refund Policy, and Others.
Please visit our website: www.tradewithmohitagrawal.com and read everything to avoid any future conflict of interest."""


def test_hero_zero_nifty_sample():
    r = CP.parse_signal(HERO); assert r["ok"], r
    s = r["signal"]; assert (s["underlying"], s["strike"], s["option_type"], s["entry_mode"], s["entry"], s["entry_range"], s["sl"], s["targets"], s["horizon"]) == ("NIFTY", 22400.0, "CE", "LIMIT", 80.0, [70.0, 80.0], 5.0, [120.0, 170.0, 260.0], "HERO ZERO")
    assert s["tag"] == "HERO_ZERO_NIFTY"


def test_sensex_sample():
    s = CP.parse_signal(SENS)["signal"]
    assert (s["underlying"], s["strike"], s["option_type"], s["entry"], s["entry_range"], s["sl"], s["targets"], s["horizon"]) == ("SENSEX", 71900.0, "CE", 450.0, [430.0, 450.0], 300.0, [620.0, 710.0, 850.0], "INTRADAY")


def test_above_single_price_and_commodity_names():
    r = CP.parse_signal("#CRUDEOIL _9500 PE\nBuy above : 520\nTarget : 560/600\nStoploss : 490")["signal"]
    assert r["underlying"] == "CRUDEOIL" and r["entry_mode"] == "ABOVE" and r["entry"] == 520.0 and r["option_type"] == "PE"
    assert CP.parse_signal("#NATURAL GAS _290 CE\nBuy Range : 9-10\nTarget : 12/14\nStoploss : 7")["signal"]["underlying"] == "NATURALGAS"
    assert CP.parse_signal("#BANKNIFTY _52000 CE\nBuy Range : 200-210\nTarget : 250\nStoploss : 150")["signal"]["underlying"] == "BANKNIFTY"       # not mistaken for NIFTY


def test_refuses_to_guess():
    for txt, frag in [("hello friends", "header"), ("#NIFTY _22400CE\nTarget : 120\nStoploss : 5", "Buy"), ("#NIFTY _22400CE\nBuy Range : 70-80\nTarget : 120", "stop-loss"), ("#NIFTY _22400CE\nBuy Range : 70-80\nTarget : 120\nStoploss : 90", "inconsistent"),
                      ("#NIFTY _22400CE\nBuy Range : 70-80\nTarget : 60\nStoploss : 5", "target"), ("#FOO _22400CE\nBuy Range : 70-80\nTarget : 120\nStoploss : 5", "unknown underlying")]:
        r = CP.parse_signal(txt); assert not r["ok"] and frag in r["reason"], (txt, r)
    r = CP.parse_signal("#BANKEX _80000 CE\nBuy Range : 70-80\nTarget : 120\nStoploss : 5"); assert r["ok"] and r["signal"]["supported"] is False


def test_goldm_mcx_samples():
    a = CP.parse_signal("POSITIONAL GOLDM 147000 PE\n\nNEAR LEVEL  2550\n\nTARGET -3050/3250/3650\n\nSTOPLOSS - 2050\n\nEXPIRY - OCT   🌁 COMMODITY MCX TRADE🌉")["signal"]
    assert (a["underlying"], a["strike"], a["option_type"], a["entry_mode"], a["entry"], a["sl"], a["targets"], a["expiry_month"], a["supported"]) == ("GOLDM", 147000.0, "PE", "NEAR", 2550.0, 2050.0, [3050.0, 3250.0, 3650.0], "OCT", True)
    b = CP.parse_signal("POSITIONAL GOLDM 146000 PE\n\nNEAR LEVEL  2600\n\nTARGET -3050/3250/3650\n\nSTOPLOSS - 2150\n\nEXPIRY - OCT")["signal"]
    assert b["strike"] == 146000.0 and b["entry"] == 2600.0 and b["sl"] == 2150.0


@pytest.mark.parametrize("txt,kind,n", [("Target 1 achieved ✅", "T_HIT", 1), ("TARGET HIT 🔥", "T_HIT", None), ("65 points achieved", "T_HIT", None), ("SL hit", "SL_HIT", None), ("Trade cancelled", "CANCEL", None), ("trade cancel", "CANCEL", None),
                                        ("Book full profit now", "EXIT", None), ("Good morning everyone", None, None)])
def test_updates(txt, kind, n):
    u = CP.parse_update(txt); assert (u["kind"], u["n_target"]) == (kind, n) if kind else u is None


def test_explicit_expiry_header_from_neeraj_sample_synthetic_body():
    # the header line is the real sample (NIFTY 17 JULY 25350 PE); the body lines are SYNTHETIC until a full sample is supplied
    r = CP.parse_signal("NIFTY 17 JULY 25350 PE\nABOVE 150\nTARGET 200/250/300\nSL 120")["signal"]
    assert (r["underlying"], r["strike"], r["option_type"], r["entry_mode"], r["entry"], r["sl"], r["targets"], r["expiry_hint"]) == ("NIFTY", 25350.0, "PE", "ABOVE", 150.0, 120.0, [200.0, 250.0, 300.0], {"day": 17, "month": 7})
    assert CP.parse_signal("BANKNIFTY 21st OCT 52000 CE\nBUY 100-110\nTGT 140\nSTOPLOSS 80")["signal"]["entry"] == 110.0
    for bad in ("NIFTY 17 JULY 25350 PE\nTARGET 200\nSL 120", "NIFTY 17 JULY 25350 PE", "NIFTY 17 JULY 25350 PE\nABOVE 150\nTARGET 200\nSL 160"):
        assert not CP.parse_signal(bad)["ok"]                                                    # missing pieces are refused, never guessed


def test_neeraj_format_colon_dash_and_unpublished_vip_stoploss():
    # real lines: "ABOVE :- 405", "STOPLOSS :- VIP", headers "SENSEX 15 JULY 82800 CE" / "NIFTY 17 JULY 25350 PE"; the TARGET line is synthetic until a full sample arrives
    r = CP.parse_signal("SENSEX 15 JULY 82800 CE\nABOVE :- 405\nTARGET :- 500/600\nSTOPLOSS :- VIP")["signal"]
    assert (r["underlying"], r["strike"], r["entry_mode"], r["entry"], r["sl"], r["sl_published"], r["targets"], r["expiry_hint"]) == ("SENSEX", 82800.0, "ABOVE", 405.0, None, False, [500.0, 600.0], {"day": 15, "month": 7})
    assert CP.parse_signal("NIFTY 17 JULY 25350 PE\nABOVE :- 160\nTARGET :- 200\nSTOPLOSS : PREMIUM")["signal"]["sl_published"] is False
    r = CP.parse_signal("NIFTY 17 JULY 25350 PE\nABOVE :- 160\nTARGET :- 200/250\nSL :- 140")["signal"]; assert r["sl"] == 140.0 and r["sl_published"] is True
    assert CP.parse_signal("NIFTY 17 JULY 25350 PE\nABOVE :- 160\nTARGET :- 200\nSTOP LOSS :- 190")["ok"] is False                      # a published SL above the entry is still refused
    e = CP.parse_signal("NIFTY 17 JULY 25350 PE\nABOVE :- 160")["signal"]                                                      # entry-only post (Neeraj publishes no stop / targets)
    assert (e["entry"], e["sl"], e["targets"], e["sl_published"], e["targets_published"]) == (160.0, None, [], False, False)
    assert CP.parse_signal("NIFTY 17 JULY 25350 PE\nABOVE :- 160\nTARGET :- 100")["ok"] is False                                  # a target line that exists must be above the entry
    assert CP.parse_signal("NIFTY 17 JULY 25350 PE\nsome chatter without an entry keyword")["ok"] is False


def test_goldm_is_supported_and_month_only_expiry_becomes_a_hint():
    r = CP.parse_signal("POSITIONAL GOLDM 146000 PE\n\nNEAR LEVEL  2450\n\nTARGET -3050/3650/4250\n\nSTOPLOSS - 1950\n\nEXPIRY - OCT")["signal"]
    assert (r["underlying"], r["strike"], r["entry_mode"], r["entry"], r["sl"], r["targets"], r["supported"], r["expiry_hint"]) == ("GOLDM", 146000.0, "NEAR", 2450.0, 1950.0, [3050.0, 3650.0, 4250.0], True, {"month": 10})


def test_neeraj_active_channel_formats_real_posts():
    # the three layouts found in the live channel "Neeraj Joshi" (history of 2026-04-30 .. 2026-10-21)
    a = CP.parse_signal("BUY NIFTY 24150 PE\n\nABOVE  \u20b9190\nSL - TGT OWN \u2705")["signal"]                                  # entry only, stop and targets are the trader's own
    assert (a["underlying"], a["strike"], a["option_type"], a["entry_mode"], a["entry"], a["sl"], a["targets"], a["sl_published"], a["targets_published"]) == ("NIFTY", 24150.0, "PE", "ABOVE", 190.0, None, [], False, False)
    b = CP.parse_signal("SENSEX 74600 CE\n\nAbove - 250\n\nTG - 280 300 320 350\n\nSL - 180\n\nWait for level")["signal"]
    assert (b["underlying"], b["strike"], b["entry_mode"], b["entry"], b["sl"], b["targets"], b["sl_published"]) == ("SENSEX", 74600.0, "ABOVE", 250.0, 180.0, [280.0, 300.0, 320.0, 350.0], True)
    c = CP.parse_signal("\U0001f538 SENSEX  77600 PE\n\U0001f4ca Buy Range :306\n\U0001f3af Target : 330/350/400\n\u26d4\ufe0fStoploss : 150\n\u2014\u2014\u2014\n\U0001f4e2 For Research Services, Stock Market Risks")["signal"]
    assert (c["entry_mode"], c["entry"], c["sl"], c["targets"]) == ("LIMIT", 306.0, 150.0, [330.0, 350.0, 400.0])
    assert CP.parse_signal("BUY NIFTY 24150 PE\nsome words but no entry keyword")["ok"] is False
    assert CP.parse_signal("BUY NIFTY 24150 PE\nABOVE 190\nSL - 200")["ok"] is False                                                        # a published stop above the entry is refused
    assert CP.parse_signal("Good morning doston")["ok"] is False


def test_signal_edge_real_posts_call_put_shortened_range_numbered_targets_and_result_posts_refused():
    a = CP.parse_signal("SENSEX 72800 CALL \\nEntry: Above 570 - 75.\\nTarget 1: 600\\nTarget 2: 630\\nTarget 3: 650\\nStop Loss: 540".replace("\\\\n", "\\n"))["signal"]
    assert (a["underlying"], a["strike"], a["option_type"], a["entry_mode"], a["entry"], a["entry_range"], a["sl"], a["targets"]) == ("SENSEX", 72800.0, "CE", "ABOVE", 570.0, [570.0, 575.0], 540.0, [600.0, 630.0, 650.0])
    b = CP.parse_signal("SENSEX 73400 PUT\\nEntry: Above 500 - 505\\nTarget 1: 530\\nTarget 2: 550\\nTarget 3: 580++\\nStop Loss: 470")["signal"]
    assert (b["option_type"], b["entry_range"], b["targets"], b["sl"]) == ("PE", [500.0, 505.0], [530.0, 550.0, 580.0], 470.0)
    for claim in ("\\U0001f4ca TODAY\\u2019S COMBINED TRADE REPORT\\n1. SENSEX 73400 PUT\\nEntry: 500 \\u2192 High: 528\\nResult: Stop-Loss Hit at 470",
                  "\\U0001f4ca SENSEX 74500 PUT (22 OCT Expiry)\\n\\u27a1 Entry: Above 500 (After Confirmation)\\nT1: 530 \\u2014 Achieved\\nT2: 550 \\u2014 Achieved"):
        r = CP.parse_signal(claim); assert r["ok"] is False and "claim" in r["reason"]                                    # results are claims, never entries


def test_finobulls_group_posts_entry_with_paid_stop_and_targets_and_the_profit_done_posts_are_claims():
    a = CP.parse_signal("SENSEX 73600 CE \\nBUY ABOVE 06\\nTGT paid \\nSL paid")["signal"]
    assert (a["underlying"], a["strike"], a["option_type"], a["entry_mode"], a["entry"], a["sl"], a["targets"], a["sl_published"], a["targets_published"]) == ("SENSEX", 73600.0, "CE", "ABOVE", 6.0, None, [], False, False)
    b = CP.parse_signal("CRUDEOIL 10400 CE\\nBUY ABOVE 250\\nTGT paid\\nSL paid")["signal"]; assert (b["underlying"], b["entry"]) == ("CRUDEOIL", 250.0)
    c = CP.parse_signal("SENSEX 22 OCT  73500  CE \\nBuy above 19\\nTARGET:paid\\nSL: paid")["signal"]; assert (c["strike"], c["entry"], c["expiry_hint"]) == (73500.0, 19.0, {"day": 22, "month": 10})
    for claim in ("Call no 07\\n\\U0001f4a5 SENSEX---- 73600 CE\\n\\U0001f449BUY --- 06\\n\\U0001f449HIGH -- 12++\\n\\U0001f449 Money double", "Call no 02\\n\\U0001f4a5 Nifty ---- 22650 CE\\n\\U0001f449BUY --- 173\\n\\U0001f449HIGH -- 190++\\n\\U0001f449 17++ points profit done"):
        assert CP.parse_signal(claim)["ok"] is False
