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
    assert (a["underlying"], a["strike"], a["option_type"], a["entry_mode"], a["entry"], a["sl"], a["targets"], a["expiry_month"], a["supported"]) == ("GOLDM", 147000.0, "PE", "NEAR", 2550.0, 2050.0, [3050.0, 3250.0, 3650.0], "OCT", False)
    b = CP.parse_signal("POSITIONAL GOLDM 146000 PE\n\nNEAR LEVEL  2600\n\nTARGET -3050/3250/3650\n\nSTOPLOSS - 2150\n\nEXPIRY - OCT")["signal"]
    assert b["strike"] == 146000.0 and b["entry"] == 2600.0 and b["sl"] == 2150.0


@pytest.mark.parametrize("txt,kind,n", [("Target 1 achieved ✅", "T_HIT", 1), ("TARGET HIT 🔥", "T_HIT", None), ("65 points achieved", "T_HIT", None), ("SL hit", "SL_HIT", None), ("Trade cancelled", "CANCEL", None), ("trade cancel", "CANCEL", None),
                                        ("Book full profit now", "EXIT", None), ("Good morning everyone", None, None)])
def test_updates(txt, kind, n):
    u = CP.parse_update(txt); assert (u["kind"], u["n_target"]) == (kind, n) if kind else u is None
