# Intraday Paper Trader — $100,000 virtual portfolio

**Paper trading only. No brokerage connections, no real-money orders.**

Automated US equities volatility / momentum experiment:
- **Starts:** Monday, 2026-10-19
- **Market:** NYSE regular session only (America/New_York, 09:30–16:00 on actual exchange trading days)
- **Evaluation cadence:** Every approximately **1 minute inside each GitHub Actions job**, using Yahoo Finance 1-minute historical OHLCV candles (latest *completed* bar only)
- **GitHub commits:** Every approximately **10 minutes** — including all 1-minute evaluations, each simulated fill, a portfolio snapshot, and any rule changes made within the interval
- **Initial account value:** USD $100,000 virtual, cash only
- **Execution model:** Long-only, fills simulated at last confirmed 1-minute close plus 10 bps adverse slippage and 5 bps trading fees each side
- **Technical metrics:** MA9, MA20, VWAP, RSI14, ATR14, 20-bar relative volume; day percent change and screen liquidity
- **Screening:** Yahoo Finance public screens (day gainers, day losers and most active), then top five by a composite of movement, ATR%, relative volume and discovery score

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

The engine attempts to close positions during the last 10 minutes of the trading day. Market-data or scheduler outages can prevent that attempt: paper positions can persist and be flagged/closed on the next available valid bar. No order is simulated from a stale/missing bar.

## GitHub files

| Path | Contents |
| --- | --- |
| [data/LATEST.md](data/LATEST.md) | Most recent batch status / top candidates / holdings |
| [data/portfolio.json](data/portfolio.json) | Current simulated cash, positions, realized P&L |
| [data/rules.json](data/rules.json) | Current adaptive risk settings, version and losses/lessons |
| [data/transactions.jsonl](data/transactions.jsonl) | Append-only simulated fills, with timestamps, qty, fees, net P&L |
| [data/snapshots.jsonl](data/snapshots.jsonl) | One portfolio snapshot per archived 10-minute batch |
| data/runs/YYYY-MM-DD/HHMM.json | Full ten-minute record, including **per-minute** indicators, trades, loss reviews, rule version and final portfolio |
| [trader.py](trader.py) | Strategy, accounting, rules and persistence |
| [.github/workflows/paper-trade.yml](.github/workflows/paper-trade.yml) | 10-minute scheduled GitHub Actions job |
| [tests/test_core.py](tests/test_core.py) | Unit tests, run before any job updates data |

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
