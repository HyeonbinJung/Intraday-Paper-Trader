# Intraday Paper Trader — $100,000 virtual portfolio

**Paper trading only. No brokerage connections, no real-money orders.**

## October 19 user-directed 14:00 ET stop

The user requested *actual forward paper trading through 14:00 ET, not historical fast-forwarding*. The request was received **after 14:00**, so missing intraday intervals were **not** reconstructed. The engine enforces a one-day hard stop at 14:00 ET on 2026-10-19 while retaining ordinary market hours on later trading days.

**Verified report:** [2026-10-19 through 14:00 forward-run audit](reports/2026-10-19_through_1400_ET.md) — partial coverage, zero recorded fills, no fabricated post-hoc valuations.

Automated US equities volatility / momentum experiment:
- **Starts:** Monday, 2026-10-19
- **Market:** NYSE regular session only (America/New_York, 09:30–16:00 on actual exchange trading days)
- **Evaluation cadence:** Every approximately **1 minute inside each GitHub Actions job**, using Yahoo Finance 1-minute historical OHLCV candles (latest *completed* bar only)
- **GitHub commits:** Every approximately **10 minutes** — including all 1-minute evaluations, each simulated fill, a portfolio snapshot, and any rule changes made within the interval
- **Initial account value:** USD $100,000 virtual, cash only
- **Execution model:** Long-only, fills simulated at last confirmed 1-minute close plus 10 bps adverse slippage and 5 bps trading fees each side
- **Technical metrics:** MA9, MA20, VWAP, RSI14, ATR14, 20-bar relative volume; day percent change and screen liquidity
- **Screening:** Yahoo Finance public screens (day gainers, day losers and most active), then top five by a composite of movement, ATR%, relative volume and discovery score

## 2025 H1 postmortem and protective constraints

The earlier [2025 minute-by-minute walk-forward backtest](https://github.com/HyeonbinJung/2025-Intraday-Walkforward-Backtest/blob/main/results/2025H1/summary.json) completed 122 sessions, starting at $100,000 and ending at **$40,809.57 (−59.19%)**, with **31,241 fills** and **$48,052.35 in estimated costs**. This is a cautionary sample, not evidence that any new rule will create a profitable strategy.

Key failures and direct corrections:
- **Minute decision ≠ minute trade**: 1m evaluations continue, but maximum **4 entries/day** and at most **10 fills/day** (risk exits always allowed), plus total daily turnover limit **125% of start-of-day equity**.
- **Transaction cost ate the signal**: each new entry's ATR-derived target *proxy* must exceed **3× modeled round-trip costs**, and at least **0.9% of entry notional**. This is an affordability hurdle, **not** a statistically validated expected-return forecast.
- **Premature churn**: min **10 minutes holding** for discretionary trend/target exits; stops and session liquidation override minimum holding; **30-minute** no-reentry period after any exit (longer after losing exits).
- **Liquidity and market impact**: no buy larger than **1% of previous completed 1-minute bar's volume**; cumulative estimated daily fees/slippage limited to **0.4% of day-opening equity for new entries**. Forced exits can exceed cost/turnover caps.
- **Market-regime blindness**: use completed 1m SPY prices with rising MA9/MA20 and VWAP confirmation before NEW buys; no SPY data means no new entries, but exits remain possible.
- **Too little reaction to net-negative cost losses**: label \`COST_DRAG\` if gross mark-to-mark is nonnegative but net P&L is negative; on that very minute, tighten the cost-coverage multiplier for subsequent entries.
- **Single period was not an out-of-sample proof**: retain detailed candidate rejections, cost budgets, and per-minute risk versions so future forward testing can compare baseline vs cost-controlled settings. Do not optimize rules on future bars.

See [2025_POSTMORTEM.md](2025_POSTMORTEM.md) for numbers, assumptions, ongoing checks, and residual risks.

## Adaptive rules

In each 1-minute evaluation, exits are assessed first. After **every realized losing exit**, the engine immediately:
1. writes a timestamped, tentative loss diagnosis into the in-memory lesson history;
2. reduces per-trade risk (initially 0.7% of account, floored at 0.25%) and tightens relative-volume minimum; consecutive losses tighten them more;
3. increases cooldown duration and temporarily blocks re-entry into that symbol;
4. increments the risk-rule version, and applies those new parameters to **the next 1-minute evaluation**.

All lesson records, risk-rule changes and each minute's decision trace are exported together in the **next 10-minute GitHub commit**. A rule update does not magically guarantee avoiding future losses; cause labels are hypotheses only.

### Initial risk limits
| Parameter | Starting value |
| --- | ---: |
| Capital | $100,000 |
| Max single position | 20% of account |
| Max combined gross exposure | 55% |
| Max simultaneous symbols | 3 |
| Max risk per entry | 0.7% of account |
| Daily mark-to-market drawdown stop for NEW orders | 2.5% |
| Max losing closes/day before pausing NEW orders | 3 |
| ATR stop / target | 1.5 × ATR / 2.7 × ATR |
| Cooldown after losing close | 30 min |
| Minimum relative volume | 1.30× |
| Entry | Price > MA9 > MA20 and > VWAP, RSI 52–78 |
| Maximum new entries per day | 4 |
| Maximum total fills per day | 10 (never blocks risk exits) |
| Maximum daily traded notional | 125% of day-opening equity for new entries |
| Max permitted daily estimated costs | 0.4% of day-opening equity for new entries |
| Cost-to-target hurdle | ATR-based target ≥ 3× modeled round-trip costs and ≥ 0.9% |
| Minimum discretionary holding time | 10 minutes; stops/day close can exit immediately |
| Reentry after any exit | 30 minutes minimum |
| Prior minute volume participation | ≤1% |
| Broad-market entry filter | SPY MA9 ≥ MA20 and price ≥ MA20 and VWAP |
| Last permitted new entry | 45 minutes before regular market close |

The engine attempts to close positions during the last 10 minutes of the trading day. Market-data or scheduler outages can prevent that attempt: paper positions can persist and be flagged/closed on the next available valid bar. No order is simulated from a stale/missing bar.

## GitHub files

| Path | Contents |
| --- | --- |
| [data/LATEST.md](data/LATEST.md) | Most recent **in-session** batch, rejection statistics and holdings |
| [data/portfolio.json](data/portfolio.json) | Current simulated cash, positions, realized P&L |
| [data/rules.json](data/rules.json) | Current adaptive risk settings, version and losses/lessons |
| [data/transactions.jsonl](data/transactions.jsonl) | Append-only simulated fills, with timestamps, qty, fees, net P&L |
| [data/snapshots.jsonl](data/snapshots.jsonl) | One portfolio snapshot per archived 10-minute batch |
| data/runs/YYYY-MM-DD/HHMM.json | Full ten-minute record, including **per-minute** indicators, trades, loss reviews, rule version and final portfolio |
| [trader.py](trader.py) | Strategy, accounting, rules and persistence |
| [.github/workflows/paper-trade.yml](.github/workflows/paper-trade.yml) | 10-minute scheduled GitHub Actions job |
| [tests/test_core.py](tests/test_core.py) | Risk controls and full mocked BUY→SELL integration tests |
| [continuous_worker.py](continuous_worker.py) | Continuous minute-level forward worker |
| [render.yaml](render.yaml) | Optional single-worker Render deployment blueprint |
| [data/HEALTH.md](data/HEALTH.md) | Observed per-minute coverage (generated after next session batch) |

## Continuous forward worker (recommended)

GitHub's `schedule` trigger is **best effort**: it may arrive late or be dropped. A green Actions check only proves that the job exited successfully; it does **not** prove a one-minute trading cadence or any fills. The fallback workflow remains available until a dedicated worker is actually running.

A long-lived [continuous_worker.py](continuous_worker.py) is provided for regular one-minute forward evaluations during the NYSE session. It never backfills missed market minutes or sends real brokerage orders. It checkpoints each minute and aggregates/publishes 10-minute batches. [data/HEALTH.md](data/HEALTH.md) (generated on the next market batch) reports the observed minute coverage, while [data/LATEST.md](data/LATEST.md) records counts of which entry checks blocked the top-five candidates.

### Render background-worker deployment

The [render.yaml](render.yaml) Blueprint is prepared but **does not deploy itself**. Review the Render worker and persistent-disk charges before activating it:

1. Connect this repo to Render and create a Blueprint from `render.yaml`, using **one instance only**. Set `GITHUB_TOKEN` as a **secret** with fine-grained **Contents: Read and write** access scoped exclusively to this repository. Do not commit the token. The worker's `PAPER_GIT_PUSH=1` is enabled in the Blueprint. You can run locally without uploads using `PAPER_GIT_PUSH=0 python continuous_worker.py`.
2. Confirm the worker logs show `CONTINUOUS_WORKER_READY`, repeated `MINUTE_EVALUATED` during actual market hours, and `PAPER_BATCH_PUBLISHED` at the end of a ten-minute bucket. Verify commits, data coverage, and the persistent checkpoint mount. This is necessary before claiming the service is live.
3. **Only after step 2**, set the GitHub repository Actions variable `PAPER_TRADER_EXTERNAL_WORKER=true` under Settings → Secrets and variables → Actions → Variables. This disables scheduled fallback jobs and prevents two engines from trading the same virtual portfolio.
4. The Blueprint deliberately sets `autoDeployTrigger: off` because the bot commits data frequently; otherwise every batch could redeploy the worker. Manually redeploy after deliberate code changes.

The checkpoint is placed on the mounted persistent disk, separate from Git-tracked `data/`. After a crash the worker can recover an uncommitted batch if the disk remains available. Even so, an internet/data-provider outage may reduce minute coverage. Keep checking the health report and process logs.

### What is and is not fixed

- The test suite now covers a complete synthetic BUY → STOP/SELL path using fresh, mocked candles. It also verifies rejection diagnostics.
- After-hours GitHub runs no longer overwrite the most recent regular-session `LATEST.md` with `SKIPPED`.
- The 2025-loss-protection filters remain intact. The engine is allowed to make **zero** trades if no candidate passes all rules. An absence of fills is not proof of scheduler failure.
- The Yahoo Finance source remains unofficial and may have delayed/missing candles. Paper fills are estimates, not executable prices. A professional data feed is a separate integration.

## Critical operational limits

**This is not tick trading or exchange-grade execution.** Yahoo Finance's unofficial screens and historical 1-minute candles can be delayed, incomplete, rate-limited or unavailable. If a bar is missing or more than 3 minutes stale, the engine **does not simulate a fill**. A screen failure results in no new candidate entries.

GitHub scheduled workflows can start late or occasionally be skipped; jobs require runtime to install dependencies and screen stocks. As a result, the achieved minute-by-minute cadence is **best effort, not guaranteed**. Each ten-minute job keeps its rules and positions in memory and writes them once as its job ends; a failed runner may lose intermediate decisions from that interval. Realized paper fills from candles may differ substantially from tradable prices, especially in fast-moving micro-cap stocks.

**GitHub settings:** Repo Settings > Actions > General > Workflow permissions must allow **Read and write permissions** for this workflow to commit its own data. Private repo Actions usage is subject to the account's included minutes and billing rules.

**Do not equate simulated returns with live trade performance.** Actual trading requires separately authorized market-data and brokerage connections, which this project intentionally does not have.

## Local checks

\`\`\`bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python trader.py
\`\`\`

The script will skip live trades before October 19, 2026 and outside NYSE regular session.
