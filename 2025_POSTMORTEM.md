# Lessons learned: 2025 Jan–Jun minute-level replay

**Canonical source:** [2025H1 summary.json](https://github.com/HyeonbinJung/2025-Intraday-Walkforward-Backtest/blob/main/results/2025H1/summary.json)

| Audit metric | Result |
| --- | ---: |
| Initial capital | $100,000.00 |
| Ending equity, June 30, 2025 | $40,809.57 |
| Net P&L | −$59,190.43 |
| Return | −59.19% |
| NYSE sessions included | 122 |
| Total fills | 31,241 |
| Losing sale fills | 9,159 |
| Winning sale fills | 8,282 |
| Estimated explicit fees/slippage | $48,052.35 |
| Mean fills per session | 256.1 |

The estimated costs equal about 48% of the initial account. This does **not** prove that eliminating fees alone would have produced a gain: changing entries/exits changes trading P&L, liquidity and fill paths. It does establish that unrestricted 1-minute rebalancing was an untenable high-turnover execution policy under these assumptions.

### Diagnosis

1. **Overtrading / excess turnover**: each new minute could cause a target shift and multiple simulated fills. A fast signal check does not mean it is optimal to transact as often.
2. **No positive expectancy demonstrated net of costs**: raw momentum/volume thresholds did not justify the cumulative spread, slippage, fee and timing burden. Introduce a minimum *potential payoff* / modeled cost gate, but do not mislabel ATR as an expected-return estimate.
3. **Adaptive sizing was not enough**: trimming the budget after losses does not stop repeated same-signal orders. Add count, churn and reentry limits.
4. **Market regime ignored**: highly volatile tickers may reverse in broad downtrends. Add SPY benchmark filter to *new entries only*.
5. **Loss streak may be inflated**: partial sells in the historical engine could count several "losses" from a single original exposure. In current live-paper engine, full-position closes produce exactly one realized-loss review for each closed position. Avoid inventing extra independent trials.
6. **Single historical window is insufficient**: backtested parameters must be assessed on walk-forward/out-of-sample forward sessions before claiming value. Track results of signal rejection, turnover and net P&L, not only account return.
7. **Execution limitations remain**: the historic replay filled at next minute open; this GitHub-based live paper simulation currently estimates fills from the most recent completed minute close plus synthetic costs. It is **not** an executable bid/ask or realized brokerage order. Quotes can be delayed and skips are expected.

### New rules, enforced 2026-10-19

- 1-minute market evaluations, **not mandatory 1-minute trades**.
- No more than **4 fresh entries** and budget for at most **10 fills** a session (forced exits exempt).
- Limit total entry-authorized daily turnover to **1.25× starting equity**, including a reserved round-trip notional.
- Simulated fees 5 bps/side and adverse slippage 10 bps/side => baseline modeled round-trip cost of 30 bps; **ATR-based target proxy must be ≥3×** that cost and at least 0.9%.
- Minimum 10-minute discretionary hold; stops/closing session overrule.
- Minimum 30-minute pause for any same-symbol reentry after a close; losing trades may extend cooldown.
- Maximum entry size no higher than 1% of volume of preceding completed minute.
- Stop accepting new entries 45 minutes before market close.
- SPY 1-minute MA9/MA20/VWAP regime check; missing data -> no new buys.
- Track actual assumed cost, turnover and rule versions per minute; after a net-negative trade with nonnegative gross reference move, mark **COST_DRAG** and raise the cost hurdle at that minute.

### What to assess over the coming sessions

Persist each 10-minute archive's one-minute decision trace, indicator data, net transaction costs, daily notional traded, and rules version. After enough untouched forward days, compare turnover, estimated cost / equity, win-rate net of costs, drawdown and net equity trend to the prior 2025 strategy. Do not choose new "optimized" settings using future data; require independent chronological holdouts.

**Do not relax cost, liquidity, risk and daily activity caps automatically simply because one trade was profitable.** New hypotheses must be tested out of sample.
