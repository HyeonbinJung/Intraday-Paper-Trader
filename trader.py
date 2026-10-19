#!/usr/bin/env python3
"""10-minute, LONG-ONLY paper trading. No brokerage/API trading orders."""
from __future__ import annotations

import json
import math
import os
import re
import traceback
import time
from datetime import datetime, timedelta, date
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pandas_market_calendars as mcal
import yfinance as yf

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
NY = ZoneInfo("America/New_York")
START_DATE = date(2026, 10, 19)
SEED = 100_000.0
FEE_RATE = 0.0005  # Assumed 5 bps per fill (paper cost).
SLIPPAGE = 0.0010  # Assumed 10 bps adverse slippage per fill.
MAX_BAR_AGE_MIN = 3  # Stale data => do NOT simulate a fill.
CAL = mcal.get_calendar("NYSE")

def read_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))

def write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

def money(x: float) -> float:
    return round(float(x), 2)

def initial_state() -> dict:
    return {"initial_capital": SEED, "cash": SEED, "positions": {},
            "realized_pnl": 0.0, "session": "", "session_start_equity": SEED,
            "losses_today": 0, "consecutive_losses": 0, "cooldowns": {},
            "last_prices": {}, "last_bar_by_symbol": {}, "last_run": None}

def initial_rules() -> dict:
    return {"version": 1, "risk_per_trade": 0.007, "max_position_pct": 0.20,
            "max_gross_exposure": 0.55, "max_positions": 3, "rvol_min": 1.30,
            "max_daily_drawdown": 0.025, "max_daily_losses": 3,
            "stop_atr": 1.5, "target_atr": 2.7, "cooldown_minutes": 30,
            "min_price": 2.0, "lessons": []}

def session_bounds(now: datetime):
    schedule = CAL.schedule(start_date=now.date(), end_date=now.date())
    if schedule.empty:
        return None
    entry = schedule.iloc[0]
    return entry["market_open"].tz_convert(NY).to_pydatetime(), entry["market_close"].tz_convert(NY).to_pydatetime()

def equity(state: dict) -> float:
    return float(state["cash"]) + sum(
        float(p["qty"]) * float(state["last_prices"].get(sym, p["entry_price"]))
        for sym, p in state["positions"].items()
    )

def usable_symbol(symbol) -> bool:
    return isinstance(symbol, str) and bool(re.fullmatch(r"[A-Z]{1,5}(\.[A-Z])?", symbol))

def discover() -> tuple[list, list]:
    """Live Yahoo Finance gainers/losers/most-active lists. No synthetic fallback."""
    found = {}
    errors = []
    for screen in ("day_gainers", "day_losers", "most_actives"):
        try:
            result = yf.screen(screen, count=35)
            quotes = result.get("quotes", []) if isinstance(result, dict) else []
            for q in quotes:
                sym = q.get("symbol", "")
                if not usable_symbol(sym):
                    continue
                price = float(q.get("regularMarketPrice") or q.get("intradayprice") or 0)
                vol = float(q.get("regularMarketVolume") or q.get("dayvolume") or 0)
                pct = float(q.get("regularMarketChangePercent") or q.get("percentchange") or 0)
                if not (2 <= price <= 500 and vol * price >= 3_000_000):
                    continue
                score = abs(pct) * math.log10(max(vol, 10))
                row = {"symbol": sym, "screener": screen, "day_change_pct": round(pct, 2),
                       "screener_price": price, "day_volume": int(vol), "discovery_score": score}
                if sym not in found or found[sym]["discovery_score"] < score:
                    found[sym] = row
        except Exception as exc:
            errors.append(f"{screen}: {type(exc).__name__}: {str(exc)[:180]}")
    ranked = sorted(found.values(), key=lambda q: q["discovery_score"], reverse=True)
    return ranked[:15], errors

def indicators(sym: str, now: datetime, pct: float = 0.0):
    """Evaluate only already-completed 5-minute bars; never fabricate market data."""
    df = yf.Ticker(sym).history(period="2d", interval="1m", prepost=False, auto_adjust=False)
    if df is None or len(df) < 25 or getattr(df.index, "tz", None) is None:
        return None
    df = df.sort_index().copy()
    df.index = df.index.tz_convert(NY)
    df = df[(df.index + pd.Timedelta(minutes=1)) <= pd.Timestamp(now)]
    if len(df) < 25:
        return None
    # Remove bars with invalid prices or zero volume.
    for field in ("Open", "High", "Low", "Close", "Volume"):
        df[field] = pd.to_numeric(df[field], errors="coerce")
    df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
    df = df[(df["Close"] > 0) & (df["Volume"] > 0)]
    if len(df) < 25:
        return None
    last = df.iloc[-1]
    bar_end = df.index[-1].to_pydatetime() + timedelta(minutes=1)
    age = (now - bar_end).total_seconds() / 60
    if age < 0 or age > MAX_BAR_AGE_MIN:
        return None
    close = df["Close"]
    diff = close.diff()
    gain = diff.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-diff.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rsi = 100 - (100 / (1 + gain.iloc[-1] / max(loss.iloc[-1], 1e-9)))
    prev = close.shift(1)
    tr = pd.concat([(df["High"]-df["Low"]).abs(),
                    (df["High"]-prev).abs(), (df["Low"]-prev).abs()], axis=1).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1])
    typical = (df["High"] + df["Low"] + df["Close"]) / 3
    today = df.index.date == now.date()
    df_day = df.loc[today]
    tp_day = typical.loc[today]
    if df_day.empty or df_day["Volume"].sum() <= 0:
        return None
    vwap = float((tp_day * df_day["Volume"]).sum() / df_day["Volume"].sum())
    baseline = float(df["Volume"].iloc[-21:-1].mean())
    rvol = float(last["Volume"]) / max(baseline, 1)
    px = float(last["Close"])
    if not all(math.isfinite(x) for x in (px, atr, rsi, rvol, vwap)) or atr <= 0:
        return None
    return {"symbol": sym, "price": round(px, 4), "ma9": round(float(close.tail(9).mean()), 4),
            "ma20": round(float(close.tail(20).mean()), 4), "vwap": round(vwap, 4),
            "rsi14": round(float(rsi), 2), "rvol20": round(rvol, 2),
            "atr14": round(atr, 4), "atr_pct": round(100*atr/px, 2),
            "day_change_pct": pct, "bar_end": bar_end.isoformat(),
            "bar_age_min": round(age, 1)}

def append_line(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as out:
        out.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")

def learn_from_loss(state: dict, rules: dict, tx: dict, now: datetime):
    """Every realized loss updates in-memory rules immediately, applied on next minute."""
    state["losses_today"] += 1
    state["consecutive_losses"] += 1
    state["cooldowns"][tx["symbol"]] = (now + timedelta(
        minutes=rules["cooldown_minutes"])).isoformat()
    hypothesis = {
        "STOP": "ATR stop crossed: volatility/trend reversal",
        "TREND_BREAK": "Moving-average trend failed",
        "SESSION_EXIT": "Entry failed to reach target before cutoff",
        "PREVIOUS_SESSION": "Delayed close may have exposed an overnight gap",
        "TAKE_PROFIT": "Round-trip costs overcame the raw profit",
    }.get(tx["reason"], "Net of costs, signal did not outperform")
    changes = []
    previous = rules["risk_per_trade"]
    scale = 0.8 if state["consecutive_losses"] >= 2 else 0.93
    rules["risk_per_trade"] = round(max(0.0025, previous * scale), 5)
    if previous != rules["risk_per_trade"]:
        changes.append(f"risk_per_trade {previous:.5f} -> {rules['risk_per_trade']:.5f}")
    prev_rvol = rules["rvol_min"]
    rules["rvol_min"] = round(min(2.5, prev_rvol + (
        0.10 if state["consecutive_losses"] >= 2 else 0.05)), 2)
    if rules["rvol_min"] != prev_rvol:
        changes.append(f"rvol_min {prev_rvol:.2f} -> {rules['rvol_min']:.2f}")
    prev_cooldown = rules["cooldown_minutes"]
    rules["cooldown_minutes"] = min(90, prev_cooldown + 5)
    if rules["cooldown_minutes"] != prev_cooldown:
        changes.append(f"cooldown_minutes {prev_cooldown} -> {rules['cooldown_minutes']}")
    rules["version"] += 1
    rules["lessons"].append({
        "time_et": now.isoformat(), "symbol": tx["symbol"],
        "net_pnl": tx["realized_pnl"], "exit_reason": tx["reason"],
        "hypothesis_not_proven_cause": hypothesis,
        "rules_changed_immediately": changes, "applies_from_next_minute": True,
        "github_archive": "next 10-minute commit",
        "rules_version": rules["version"],
    })
    rules["lessons"] = rules["lessons"][-300:]

def execute(state: dict, rules: dict, now: datetime, sym: str, side: str, qty: int,
            market_px: float, reason: str, bar_end: str, transactions: list):
    if qty <= 0:
        return
    price = market_px * (1 + SLIPPAGE if side == "BUY" else 1 - SLIPPAGE)
    gross = qty * price
    fee = gross * FEE_RATE
    tx = {"time_et": now.isoformat(), "symbol": sym, "side": side,
          "qty": int(qty), "reference_bar_close": round(market_px, 4),
          "simulated_fill": round(price, 4), "fee": money(fee),
          "slippage_bps": int(SLIPPAGE * 10000), "reason": reason,
          "bar_end": bar_end, "rules_version": rules["version"]}
    if side == "BUY":
        if gross + fee > state["cash"] or sym in state["positions"]:
            return
        state["cash"] -= gross + fee
        state["positions"][sym] = {"qty": qty, "entry_price": price, "entry_fee": fee,
            "entry_time": now.isoformat(), "highest_observed": market_px,
            "stop": market_px - rules["stop_atr"] * float(tx.get("entry_atr", 0)),
            "entry_rule_version": rules["version"]}
        tx["cash_flow"] = money(-(gross + fee))
    else:
        held = state["positions"].get(sym)
        if held is None or qty != held["qty"]:
            return
        net_proceeds = gross - fee
        pnl = net_proceeds - qty * held["entry_price"] - held["entry_fee"]
        state["cash"] += net_proceeds
        state["realized_pnl"] += pnl
        del state["positions"][sym]
        tx["cash_flow"] = money(net_proceeds)
        tx["realized_pnl"] = money(pnl)
        tx["holding_minutes"] = round((now - datetime.fromisoformat(held["entry_time"])).total_seconds()/60, 1)
        if pnl < 0:
            learn_from_loss(state, rules, tx, now)
        else:
            state["consecutive_losses"] = 0
    transactions.append(tx)

def run_once(now: datetime, state_override=None, rules_override=None, discovery_seed=None):
    DATA.mkdir(exist_ok=True)
    state_path = DATA / "portfolio.json"
    rules_path = DATA / "rules.json"
    state = state_override if state_override is not None else read_json(state_path, initial_state())
    rules = rules_override if rules_override is not None else read_json(rules_path, initial_rules())
    bucket = now.strftime("%Y-%m-%d/%H%M")
    record_path = DATA / "runs" / (bucket + ".json")
    bounds = session_bounds(now)
    transactions = []
    candidates = []
    ranked = []
    errors = []
    status = "SKIPPED"
    note = ""
    started = now.date() >= START_DATE
    is_open = started and bounds is not None and bounds[0] <= now < bounds[1]
    if not started:
        note = "Simulation starts 2026-10-20; no trades before that date."
    elif bounds is None:
        note = "NYSE closed: holiday or weekend."
    elif not is_open:
        note = "Outside NYSE regular session."
    else:
        opening, closing = bounds
        cutoff = closing - timedelta(minutes=10)
        if state["session"] != now.date().isoformat():
            state["session"] = now.date().isoformat()
            state["session_start_equity"] = equity(state)
            state["losses_today"] = 0
            state["consecutive_losses"] = 0
        try:
            if discovery_seed is None:
                ranked, errors = discover()
            else:
                ranked = discovery_seed
                errors = []
            tracked = {r["symbol"]: r for r in ranked}
            for s in state["positions"]:
                tracked.setdefault(s, {"symbol": s, "day_change_pct": 0})
            for symbol, meta in list(tracked.items()):
                try:
                    ind = indicators(symbol, now, meta["day_change_pct"])
                    if ind is not None:
                        candidates.append(ind)
                except Exception as exc:
                    errors.append(f"{symbol}: {type(exc).__name__}: {str(exc)[:150]}")
            observed = {r["symbol"]: r for r in candidates}
            for s, r in observed.items():
                state["last_prices"][s] = r["price"]
            # Liquidation signals before entries, including positions missed at a previous close.
            for sym, pos in list(state["positions"].items()):
                ind = observed.get(sym)
                if ind is None:
                    errors.append(f"NO_FRESH_BAR_FOR_OPEN_POSITION:{sym}")
                    continue
                px = ind["price"]
                pos["highest_observed"] = max(pos["highest_observed"], px)
                atr = ind["atr14"]
                stop = max(pos["stop"], pos["highest_observed"] - rules["stop_atr"] * atr)
                pos["stop"] = stop
                reason = None
                if datetime.fromisoformat(pos["entry_time"]).date() < now.date():
                    reason = "PREVIOUS_SESSION"
                elif now >= cutoff:
                    reason = "SESSION_EXIT"
                elif px <= stop:
                    reason = "STOP"
                elif px >= pos["entry_price"] + rules["target_atr"] * atr:
                    reason = "TAKE_PROFIT"
                elif px < ind["ma20"] and ind["ma9"] < ind["ma20"]:
                    reason = "TREND_BREAK"
                if reason:
                    execute(state, rules, now, sym, "SELL", pos["qty"], px,
                            reason, ind["bar_end"], transactions)
            # Top 5 volatile movers, with long-only technical confirmation.
            with_metadata = [dict(r, discovery_score=next(
                (m.get("discovery_score", 0) for m in ranked if m["symbol"] == r["symbol"]), 0))
                for r in candidates if r["symbol"] in {x["symbol"] for x in ranked}]
            top_five = sorted(with_metadata, key=lambda r:
                abs(r["day_change_pct"]) * 0.4 + min(r["rvol20"], 6) * 1.5
                + r["atr_pct"] * 2 + r["discovery_score"] * 0.05, reverse=True)[:5]
            selected = {r["symbol"] for r in top_five}
            candidates = [dict(r, top_five=(r["symbol"] in selected)) for r in candidates]
            daily_dd = (state["session_start_equity"] - equity(state)) / max(state["session_start_equity"], 1)
            halted = daily_dd >= rules["max_daily_drawdown"] or state["losses_today"] >= rules["max_daily_losses"]
            for r in top_five:
                s = r["symbol"]
                checks = {
                    "no_duplicate_position": s not in state["positions"] and s not in {tx["symbol"] for tx in transactions},
                    "trend": r["price"] > r["ma9"] > r["ma20"],
                    "above_vwap": r["price"] > r["vwap"],
                    "volume": r["rvol20"] >= rules["rvol_min"],
                    "rsi": 52 <= r["rsi14"] <= 78,
                    "price": r["price"] >= rules["min_price"],
                    "cooldown_finished": now >= datetime.fromisoformat(
                        state["cooldowns"].get(s, "2000-01-01T00:00:00-05:00")),
                    "new_bar": r["bar_end"] != state["last_bar_by_symbol"].get(s),
                    "risk_active": not halted and now < cutoff,
                    "capacity": len(state["positions"]) < rules["max_positions"]
                }
                r["entry_checks"] = checks
                if not all(checks.values()):
                    continue
                eq = equity(state)
                stop_distance = max(r["atr14"] * rules["stop_atr"], r["price"] * 0.015)
                max_qty_risk = math.floor(eq * rules["risk_per_trade"] / stop_distance)
                max_qty_cash = math.floor(state["cash"] / (r["price"] * (1 + SLIPPAGE) * (1 + FEE_RATE)))
                max_qty_single = math.floor(eq * rules["max_position_pct"] / r["price"])
                held_mv = sum(p["qty"] * state["last_prices"].get(k, p["entry_price"])
                              for k, p in state["positions"].items())
                max_qty_gross = math.floor(max(0, eq * rules["max_gross_exposure"] - held_mv) / r["price"])
                qty = min(max_qty_risk, max_qty_cash, max_qty_single, max_qty_gross)
                if qty < 1:
                    r["entry_checks"]["sufficient_capacity"] = False
                    continue
                execute(state, rules, now, s, "BUY", qty, r["price"],
                        "MOMENTUM_MA9_MA20_VWAP_RVOL", r["bar_end"], transactions)
                if s in state["positions"]:
                    state["positions"][s]["stop"] = r["price"] - rules["stop_atr"] * r["atr14"]
                    state["positions"][s]["entry_atr"] = r["atr14"]
                    r["entry_checks"]["executed"] = True
            for r in observed.values():
                state["last_bar_by_symbol"][r["symbol"]] = r["bar_end"]
            status = "EXECUTED" if transactions else "NO_TRADE"
            if not ranked:
                status = "DATA_UNAVAILABLE"
                note = "All live screener calls failed/returned no candidates; no synthetic symbols used."
            if errors:
                note += " Some data requests failed; affected symbols were not traded."
        except Exception as exc:
            status = "ERROR"
            errors.append(f"{type(exc).__name__}: {exc}")
            errors.append(traceback.format_exc(limit=5))
    state["cash"] = round(state["cash"], 6)
    state["realized_pnl"] = round(state["realized_pnl"], 6)
    state["last_run"] = now.isoformat()
    total = equity(state)
    portfolio = {"cash": money(state["cash"]), "equity": money(total),
        "market_value": money(total - state["cash"]),
        "total_return_pct": round(100 * (total / SEED - 1), 3),
        "realized_pnl": money(state["realized_pnl"]), "positions": json.loads(json.dumps(state["positions"]))}
    record = {"timestamp_et": now.isoformat(), "status": status, "note": note,
        "data_source": "Yahoo Finance via yfinance (possibly delayed/unofficial)",
        "data_quality": f"1m completed bars; max age {MAX_BAR_AGE_MIN}min; paper fills at bar close plus costs",
        "session_bounds_et": [x.isoformat() for x in bounds] if bounds else None,
        "discovery": ranked, "indicators": candidates, "transactions": transactions,
        "portfolio": portfolio, "rules_version": rules["version"], "errors": errors}
    return record, state, rules

def persist_record(record: dict, state: dict, rules: dict, now: datetime) -> None:
    record_path = DATA / "runs" / now.strftime("%Y-%m-%d/%H%M.json")
    state_path = DATA / "portfolio.json"
    rules_path = DATA / "rules.json"
    transactions = record["transactions"]
    portfolio = record["portfolio"]
    candidates = record["indicators"]
    note = record["note"]
    status = record["status"]
    errors = record["errors"]
    if record_path.exists():
        print("Previously recorded:", record_path)
        return
    write_json(record_path, record)
    write_json(state_path, state)
    write_json(rules_path, rules)
    for tx in transactions:
        append_line(DATA / "transactions.jsonl", tx)
    append_line(DATA / "snapshots.jsonl",
                {"timestamp_et": now.isoformat(), "status": status,
                 "portfolio": portfolio, "trades_this_batch": len(transactions),
                 "record": str(record_path.relative_to(ROOT))})
    positions_text = "\n".join(
        f"- {s}: {p['qty']} shares, entry \${p['entry_price']:.2f}, last \${state['last_prices'].get(s,p['entry_price']):.2f}"
        for s, p in state["positions"].items()) or "- None"
    top_text = "\n".join(
        f"- {x['symbol']}: \${x['price']:.2f}, change {x['day_change_pct']:+.2f}%, RVOL {x['rvol20']:.2f}, RSI {x['rsi14']:.1f}"
        for x in candidates if x.get("top_five")) or "- None (no usable fresh market data)"
    latest = f"""# Latest paper-trading batch

- Time (ET): {now.isoformat()}
- Status: **{status}**
- Paper equity: **\${portfolio['equity']:,.2f}**
- Cash: \${portfolio['cash']:,.2f}
- Total return: {portfolio['total_return_pct']:+.3f}%
- Realized P&L: \${portfolio['realized_pnl']:,.2f}
- Simulated orders this batch: {len(transactions)}
- Risk rule version: {rules['version']}
- Warning: **No real orders. Unofficial/delayed quotes. Backtest-quality, not execution-quality fills.**

## Top candidates
{top_text}

## Open positions
{positions_text}

## Batch note
{note or "No special note."}

Full run: \`{record_path.relative_to(ROOT)}\`.
"""
    (DATA / "LATEST.md").write_text(latest, encoding="utf-8")
    print(json.dumps({"status": status, "batch": now.strftime("%Y-%m-%d/%H%M"), "orders": len(transactions),
                      "equity": portfolio["equity"], "errors": errors[:3]}, ensure_ascii=False))

def batch_main():
    """Evaluate every ~60s inside one ~10m scheduled job; push just one 10m batch."""
    start = datetime.now(NY)
    DATA.mkdir(exist_ok=True)
    bucket_start = start.replace(minute=(start.minute // 10) * 10, second=0, microsecond=0)
    next_bucket = bucket_start + timedelta(minutes=10)
    # Don't exceed run slot: leave 55 seconds to save/commit. Max 8 evaluations.
    deadline = min(next_bucket - timedelta(seconds=55), start + timedelta(minutes=8))
    state = read_json(DATA / "portfolio.json", initial_state())
    rules = read_json(DATA / "rules.json", initial_rules())
    archive_path = DATA / "runs" / bucket_start.strftime("%Y-%m-%d/%H%M.json")
    if archive_path.exists():
        print("Batch already recorded:", archive_path)
        return
    minute_logs = []
    all_transactions = []
    discovery_seed = None
    for i in range(9):
        now = datetime.now(NY)
        if i > 0 and now > deadline:
            break
        previous_version = rules["version"]
        previous_lessons = len(rules["lessons"])
        rec, state, rules = run_once(now, state_override=state,
                                    rules_override=rules, discovery_seed=discovery_seed)
        if discovery_seed is None and rec["discovery"]:
            discovery_seed = rec["discovery"]
        all_transactions.extend(rec["transactions"])
        minute_logs.append({"time_et": now.isoformat(), "status": rec["status"],
                            "indicators": rec["indicators"],
                            "transactions": rec["transactions"],
                            "portfolio": rec["portfolio"],
                            "rules_version_before": previous_version,
                            "rules_version_after": rules["version"],
                            "risk_rules_applied": {
                                "risk_per_trade": rules["risk_per_trade"],
                                "rvol_min": rules["rvol_min"],
                                "cooldown_minutes": rules["cooldown_minutes"]
                            },
                            "new_loss_reviews": rules["lessons"][previous_lessons:],
                            "errors": rec["errors"]})
        # One-minute decisions, including exit checks and reallocation.
        if rec["status"] == "SKIPPED":
            break
        next_minute = now.replace(second=0, microsecond=0) + timedelta(minutes=1, seconds=2)
        if next_minute > deadline:
            break
        time.sleep(max(0, (next_minute - datetime.now(NY)).total_seconds()))
    if not minute_logs:
        raise RuntimeError("No minute checks completed")
    rec["timestamp_et"] = datetime.now(NY).isoformat()
    rec["transactions"] = all_transactions
    rec["minute_evaluations"] = minute_logs
    rec["evaluation_interval_seconds_target"] = 60
    rec["archive_interval_minutes"] = 10
    rec["rules_version"] = rules["version"]
    rec["rule_updates_during_batch"] = [lesson for event in minute_logs for lesson in event["new_loss_reviews"]]
    rec["expected_batch_start_et"] = bucket_start.isoformat()
    if all_transactions:
        rec["status"] = "EXECUTED"
    rec["portfolio"]["equity"] = money(equity(state))
    rec["portfolio"]["cash"] = money(state["cash"])
    rec["portfolio"]["market_value"] = money(equity(state) - state["cash"])
    rec["portfolio"]["realized_pnl"] = money(state["realized_pnl"])
    rec["portfolio"]["total_return_pct"] = round(100 * (equity(state) / SEED - 1), 3)
    persist_record(rec, state, rules, bucket_start)

if __name__ == "__main__":
    batch_main()
