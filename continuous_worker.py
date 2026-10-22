#!/usr/bin/env python3
"""Forward-only, continuous 1-minute paper-trading worker.

A long-lived process removes GitHub cron timing from the market-data evaluation
path. It NEVER sends brokerage orders. Ten-minute Git commits are optional.
"""
from __future__ import annotations

import base64
import os
import subprocess
import time
from pathlib import Path
from datetime import datetime, timedelta

import trader

CHECKPOINT = Path(os.environ.get("PAPER_CHECKPOINT_DIR", str(trader.DATA))) / "worker_checkpoint.json"
PUBLISH = os.environ.get("PAPER_GIT_PUSH", "0") == "1"


def git_env() -> dict:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("PAPER_GIT_PUSH=1 requires GITHUB_TOKEN (repo Contents: read/write)")
    auth = base64.b64encode(("x-access-token:" + token).encode()).decode()
    env = os.environ.copy()
    env.update({
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_0": "AUTHORIZATION: basic " + auth,
    })
    return env


def git(args: list, *, env: dict, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=trader.ROOT, env=env,
                          check=check, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True)


def publish_data() -> None:
    """Keep Github a ten-minute audit store, not the execution clock."""
    if not PUBLISH:
        print("PAPER_GIT_PUSH=0: files saved locally; no GitHub upload", flush=True)
        return
    env = git_env()
    git(["config", "user.name", "github-actions[bot]"], env=env)
    git(["config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], env=env)
    git(["add", "-A", "data/"], env=env)
    changed = git(["diff", "--cached", "--quiet"], env=env, check=False)
    if changed.returncode == 0:
        return
    if changed.returncode != 1:
        raise RuntimeError("Could not inspect staged changes: " + changed.stderr)
    git(["commit", "-m", "paper-trade: continuous market batch"], env=env)
    for attempt in range(3):
        pull = git(["pull", "--rebase", "origin", "main"], env=env, check=False)
        if pull.returncode:
            raise RuntimeError("Git rebase failed: " + pull.stderr[-800:])
        pushed = git(["push", "origin", "HEAD:main"], env=env, check=False)
        if pushed.returncode == 0:
            print("PAPER_BATCH_PUBLISHED", flush=True)
            return
        print("PUSH_RETRY:", attempt + 1, pushed.stderr[-300:], flush=True)
        time.sleep(2)
    raise RuntimeError("Could not push paper-trading records after 3 attempts")


def fresh_context() -> dict:
    return {
        "state": trader.read_json(trader.DATA / "portfolio.json", trader.initial_state()),
        "rules": trader.read_json(trader.DATA / "rules.json", trader.initial_rules()),
        "bucket": None,
        "minute_logs": [],
        "all_transactions": [],
        "last_record": None,
        "discovery_seed": None,
    }


def restore_context() -> dict:
    checkpoint = trader.read_json(CHECKPOINT, {})
    if not checkpoint or not checkpoint.get("bucket"):
        return fresh_context()
    archive = trader.DATA / "runs" / (
        datetime.fromisoformat(checkpoint["bucket"]).strftime("%Y-%m-%d/%H%M.json"))
    if archive.exists():
        CHECKPOINT.unlink(missing_ok=True)
        return fresh_context()
    print("RECOVERING_UNCOMMITTED_BATCH:", checkpoint["bucket"], flush=True)
    return checkpoint


def save_checkpoint(ctx: dict) -> None:
    trader.write_json(CHECKPOINT, ctx)


def minute_event(rec: dict, state: dict, rules: dict, now: datetime,
                 previous_version: int, previous_lessons: int) -> dict:
    return {
        "time_et": now.isoformat(), "status": rec["status"],
        "indicators": rec["indicators"],
        "transactions": rec["transactions"],
        "portfolio": rec["portfolio"],
        "turnover_risk": rec["turnover_risk"],
        "rules_version_before": previous_version,
        "rules_version_after": rules["version"],
        "risk_rules_applied": {
            "risk_per_trade": rules["risk_per_trade"],
            "rvol_min": rules["rvol_min"],
            "cooldown_minutes": rules["cooldown_minutes"],
        },
        "new_loss_reviews": rules["lessons"][previous_lessons:],
        "errors": rec["errors"],
    }


def flush(ctx: dict) -> None:
    if not ctx.get("minute_logs"):
        return
    bucket = datetime.fromisoformat(ctx["bucket"])
    record = trader.build_batch_record(
        ctx["last_record"], ctx["minute_logs"], ctx["all_transactions"],
        ctx["state"], ctx["rules"], bucket)
    trader.persist_record(record, ctx["state"], ctx["rules"], bucket)
    publish_data()
    CHECKPOINT.unlink(missing_ok=True)


def next_minute_sleep() -> None:
    now = datetime.now(trader.NY)
    boundary = now.replace(second=0, microsecond=0) + timedelta(minutes=1, seconds=2)
    time.sleep(max(0.2, min(60, (boundary - now).total_seconds())))


def run_forever() -> None:
    # Fail fast when GitHub persistence was requested but credentials are missing.
    if PUBLISH:
        env = git_env()
        git(["pull", "--rebase", "origin", "main"], env=env)
    trader.DATA.mkdir(parents=True, exist_ok=True)
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    ctx = restore_context()
    last_evaluated_minute = None
    print("CONTINUOUS_WORKER_READY: virtual orders only, US regular session", flush=True)
    while True:
        now = datetime.now(trader.NY)
        bounds = trader.effective_session_bounds(now)
        in_market = (now.date() >= trader.START_DATE and bounds is not None
                     and bounds[0] <= now < bounds[1])
        if not in_market:
            if ctx.get("minute_logs"):
                flush(ctx)
                ctx = fresh_context()
            next_minute_sleep()
            continue

        minute_id = now.strftime("%Y-%m-%dT%H:%M")
        if minute_id == last_evaluated_minute:
            next_minute_sleep()
            continue

        bucket = now.replace(minute=(now.minute // 10) * 10,
                             second=0, microsecond=0)
        bucket_id = bucket.isoformat()
        if ctx.get("bucket") and ctx["bucket"] != bucket_id:
            flush(ctx)
            ctx = fresh_context()
        if ctx.get("bucket") is None:
            ctx["bucket"] = bucket_id

        if not ctx.get("minute_logs"):
            archive = trader.DATA / "runs" / bucket.strftime("%Y-%m-%d/%H%M.json")
            if archive.exists():
                # Another runner has archived the slot. Never create duplicate fills.
                last_evaluated_minute = minute_id
                next_minute_sleep()
                continue

        previous_version = ctx["rules"]["version"]
        previous_lessons = len(ctx["rules"]["lessons"])
        rec, state, rules = trader.run_once(
            now, state_override=ctx["state"], rules_override=ctx["rules"],
            discovery_seed=ctx["discovery_seed"])
        ctx["state"], ctx["rules"] = state, rules
        if ctx["discovery_seed"] is None and rec["discovery"]:
            ctx["discovery_seed"] = rec["discovery"]
        ctx["all_transactions"].extend(rec["transactions"])
        ctx["minute_logs"].append(minute_event(
            rec, state, rules, now, previous_version, previous_lessons))
        ctx["last_record"] = rec
        save_checkpoint(ctx)
        last_evaluated_minute = minute_id
        print("MINUTE_EVALUATED:", minute_id, rec["status"],
              "fills=", len(rec["transactions"]), flush=True)
        next_minute_sleep()


if __name__ == "__main__":
    try:
        run_forever()
    except KeyboardInterrupt:
        print("WORKER_STOPPED", flush=True)
