# Claim test specification — written BEFORE computing any performance statistic on the paper ledger

**Disclosure.** Before writing this I had seen only the outcome counts of `ai_paper_trades` (CLOSED: 158 WIN / 161 LOSS / 38 FLAT, 3 OPEN) and the earlier negative research results (PA, expiry pin, Hybrid MP). I have NOT computed net P&L, win-rate intervals, break-even win rate, calibration or any p-value on this ledger. The criteria below are fixed now; git history is the timestamp.

## What is (and is not) tested
- **Tested claim C1:** "ZeroHero's signals win more often than the payoff structure requires, and produce positive net expectancy after costs."
- **Not testable as asked:** "ZeroHero's win probability is higher than SEBI's". SEBI publishes no per-signal win rate. Its published retail-F&O studies report the share of traders with net losses; that is a different statistic. Any marketing comparison with SEBI or a "SEBI trainer" would be unsupported and is NOT made here. The only fair comparators are (a) the break-even win rate implied by ZeroHero's own payoffs and costs, (b) zero net expectancy, (c) calibration of its own stated probabilities.

## Data (read-only, `data/chanakya.db`, `mode=ro`)
`ai_paper_trades` with `status='CLOSED'` and non-null `net_pnl`; duplicates were already archived (`ai_paper_trades_dupe_archive`, not used). Outcome per trade = `net_pnl` (rupees, after the recorded modelled costs). Win = `net_pnl > 0`; FLAT (net 0) counts as a non-win. Trades whose `cost_model_status` is not an OK-type status are listed but kept (reported separately).

## Metrics (all on the primary population = every closed trade)
n, trading days, win rate with Wilson 95% interval, break-even win rate `= mean|loss| / (mean win + mean|loss|)` from the same sample, mean net P&L per trade, SE, t, **day-clustered** one-sided p for mean>0, profit factor, max drawdown (cumulative). Calibration (where `probability` is present): mean predicted vs realised win rate, Brier score, 10-bin ECE.

## Pre-registered decision (primary population only)
- **C1 = PASS** only if ALL: n >= 100, distinct days >= 30, mean net P&L > 0 with day-clustered one-sided p <= 0.05, AND the Wilson 95% lower bound of the win rate > the break-even win rate.
- **C1 = INCONCLUSIVE** if n < 100 or days < 30 (no statistic is then claimed).
- **C1 = FAIL** otherwise.
- **Calibration claim** ("the stated probability is a probability") passes only if |mean predicted − realised| <= 0.05 AND ECE <= 0.08.
- Subsets (per underlying family; opened on/after 2026-09-21 = after the calibration fix) are **exploratory**, labelled as such, never used to rescue a FAIL.

## What a PASS would still not mean
Paper results use modelled fills; this ledger is not an independent holdout (rules were tuned on part of it, see RULES_INVENTORY). A PASS would justify only a pre-registered forward paper test, never a public "proven" or "higher than SEBI" statement.
