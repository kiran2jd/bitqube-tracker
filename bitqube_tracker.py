#!/usr/bin/env python3
"""
BitQube Balance Tracker
------------------------
Polls explorer.bitqube.org for your wallet balance, alerts you every time you
cross a 10,000 coin milestone (via Telegram and/or email), and predicts when
you'll reach 100,000 coins based on your recent growth rate.

Run it periodically with cron (recommended) -- see the "cron setup" notes
at the bottom of this file. Each run is stateless in memory but reads/writes
a small local JSON file (STATE_FILE) to remember history between runs.

No GPU required -- this just makes lightweight HTTP calls, so run it on your
regular DigitalOcean droplet, not your GPU mining box.
"""

import json
import os
import sys
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# CONFIG -- edit these, or better, set them as environment variables so you
# don't commit secrets to git.
# ---------------------------------------------------------------------------

BITQUBE_ADDRESS = os.environ.get("BITQUBE_ADDRESS", "PUT_YOUR_ADDRESS_HERE")
EXPLORER_BASE = "https://explorer.bitqube.org"

MILESTONE_STEP = 10_000      # alert every N coins
TARGET_COINS = 100_000       # the goal you want an ETA prediction for

STATE_FILE = Path(os.environ.get("BITQUBE_STATE_FILE", "/home/kiran/bitqube_state.json"))
MAX_HISTORY_POINTS = 1000    # cap history so the file doesn't grow forever

# --- Telegram (recommended: free, instant, mobile) ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# --- Email (optional backup) ---
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")        # your gmail address
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "") # gmail "app password"
ALERT_EMAIL_TO = os.environ.get("ALERT_EMAIL_TO", SMTP_USER)

# Only send a prediction message once every N hours (avoid spamming yourself)
PREDICTION_INTERVAL_HOURS = 24


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def fetch_balance(address: str) -> float:
    """Hits BitQube's public explorer API for the live balance of an address."""
    url = f"{EXPLORER_BASE}/ext/getbalance/{address}"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    # This endpoint returns a plain number as text, e.g. "12345.6789"
    return float(resp.text.strip())


# ---------------------------------------------------------------------------
# STATE (persisted between cron runs)
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {
        "last_milestone_notified": 0,
        "history": [],              # list of {"ts": iso8601, "balance": float}
        "last_prediction_sent_ts": None,
        "last_summary_ts": None,
        "last_summary_balance": None,
    }


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def record_history(state: dict, balance: float) -> None:
    state["history"].append({
        "ts": datetime.now(timezone.utc).isoformat(),
        "balance": balance,
    })
    if len(state["history"]) > MAX_HISTORY_POINTS:
        state["history"] = state["history"][-MAX_HISTORY_POINTS:]


# ---------------------------------------------------------------------------
# PREDICTION -- simple linear trend over recent history
# ---------------------------------------------------------------------------

def predict_eta_to_target(history: list, target: float) -> str:
    """
    Fits a straight line (balance vs. time) through your history and
    extrapolates forward to estimate when you'll reach `target`.

    This is a rough estimate, not a guarantee -- mining reward rate can
    change with network difficulty, hashrate, or if you add/remove GPUs.
    """
    if len(history) < 2:
        return "Not enough history yet to predict -- check back after a few more runs."

    # Use up to the last 7 days of points for a "recent trend" estimate
    now = datetime.now(timezone.utc)
    recent = [
        h for h in history
        if (now - datetime.fromisoformat(h["ts"])).total_seconds() <= 7 * 86400
    ]
    points = recent if len(recent) >= 2 else history

    t0 = datetime.fromisoformat(points[0]["ts"])
    xs = [(datetime.fromisoformat(p["ts"]) - t0).total_seconds() / 86400 for p in points]  # days
    ys = [p["balance"] for p in points]

    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    numerator = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    denominator = sum((xs[i] - mean_x) ** 2 for i in range(n))

    if denominator == 0:
        return "Balance hasn't changed yet -- can't estimate a growth rate."

    slope_per_day = numerator / denominator  # coins per day
    current_balance = ys[-1]

    if slope_per_day <= 0:
        return "Balance isn't growing right now, so no ETA to give."

    coins_needed = target - current_balance
    if coins_needed <= 0:
        return f"You've already reached {target:,.0f} coins!"

    days_remaining = coins_needed / slope_per_day
    eta_date = now.timestamp() + days_remaining * 86400
    eta_str = datetime.fromtimestamp(eta_date, tz=timezone.utc).strftime("%d %b %Y")

    return (
        f"At your recent rate (~{slope_per_day:,.2f} BTQ/day), "
        f"you're projected to reach {target:,.0f} BTQ around {eta_str} "
        f"(~{days_remaining:,.1f} days from now)."
    )


# ---------------------------------------------------------------------------
# ALERTS
# ---------------------------------------------------------------------------

def send_telegram(message: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=15)
    except requests.RequestException as e:
        print(f"[warn] Telegram send failed: {e}")


def send_email(subject: str, message: str) -> None:
    if not SMTP_USER or not SMTP_PASSWORD:
        return
    msg = MIMEText(message)
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = ALERT_EMAIL_TO
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
    except Exception as e:
        print(f"[warn] Email send failed: {e}")


def send_earnings_summary(state: dict, current_balance: float) -> None:
    """
    Reports coins earned since the last summary was sent. Meant to be
    triggered twice a day (e.g. 8 AM and 10 PM) by two separate cron entries
    using the --summary flag, NOT by the regular 15-min polling runs.
    """
    now = datetime.now(timezone.utc)
    last_ts = state.get("last_summary_ts")
    last_balance = state.get("last_summary_balance")

    if last_ts is None or last_balance is None:
        # First time this has ever run -- nothing to compare against yet.
        message = (
            f"Current balance: {current_balance:,.2f} BTQ.\n"
            f"This is the first summary, so there's no prior checkpoint to "
            f"compare against yet -- the next one will show coins earned "
            f"since now."
        )
    else:
        earned = current_balance - last_balance
        hours = (now - datetime.fromisoformat(last_ts)).total_seconds() / 3600
        message = (
            f"Earned in the last ~{hours:.1f} hours: {earned:,.4f} BTQ\n"
            f"Balance now: {current_balance:,.2f} BTQ "
            f"(was {last_balance:,.2f} BTQ)"
        )

    alert("BitQube earnings summary", message)
    state["last_summary_ts"] = now.isoformat()
    state["last_summary_balance"] = current_balance


def find_balance_near(history: list, target_time: datetime) -> float | None:
    """Finds the history entry closest to (but not after) target_time.
    Falls back to the earliest available entry if all entries are newer."""
    candidates = [h for h in history if datetime.fromisoformat(h["ts"]) <= target_time]
    if candidates:
        return candidates[-1]["balance"]
    if history:
        return history[0]["balance"]  # oldest we have, best effort
    return None


def send_on_demand_check(state: dict, current_balance: float, hours: int = 24) -> None:
    """
    Manual/on-demand check: shows current balance plus coins earned over the
    last `hours` hours. Triggered by --last24h, meant for a manual run
    (e.g. GitHub Actions 'Run workflow' button), not the automatic schedule.
    """
    now = datetime.now(timezone.utc)
    target_time = now.fromtimestamp(now.timestamp() - hours * 3600, tz=timezone.utc)
    past_balance = find_balance_near(state["history"], target_time)

    if past_balance is None:
        message = f"Current balance: {current_balance:,.2f} BTQ.\nNo history yet to compare against."
    else:
        earned = current_balance - past_balance
        message = (
            f"Current balance: {current_balance:,.2f} BTQ\n"
            f"Earned in the last {hours} hours: {earned:,.4f} BTQ\n"
            f"(was {past_balance:,.2f} BTQ ~{hours}h ago)"
        )

    alert(f"BitQube on-demand check ({hours}h)", message)


def alert(subject: str, message: str) -> None:
    print(f"[alert] {subject}: {message}")
    send_telegram(f"{subject}\n\n{message}")
    send_email(subject, message)


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    force_status = "--status" in sys.argv
    do_summary = "--summary" in sys.argv
    do_on_demand = "--last24h" in sys.argv

    # Optional: --hours=6 lets you ask "what did I earn in the last 6 hours"
    # instead of the default 24.
    on_demand_hours = 24
    for arg in sys.argv:
        if arg.startswith("--hours="):
            on_demand_hours = int(arg.split("=", 1)[1])

    if BITQUBE_ADDRESS == "PUT_YOUR_ADDRESS_HERE":
        print("[error] Set BITQUBE_ADDRESS (env var or in the script) first.")
        sys.exit(1)

    state = load_state()

    try:
        balance = fetch_balance(BITQUBE_ADDRESS)
    except Exception as e:
        print(f"[error] Could not fetch balance: {e}")
        sys.exit(1)

    print(f"[info] Current balance: {balance:,.4f} BTQ")
    record_history(state, balance)

    # --- Milestone check: did we cross one or more 10k lines? ---
    current_milestone = (int(balance) // MILESTONE_STEP) * MILESTONE_STEP
    last_notified = state.get("last_milestone_notified", 0)

    if current_milestone > last_notified:
        prediction = predict_eta_to_target(state["history"], TARGET_COINS)
        alert(
            f"BitQube milestone reached: {current_milestone:,} BTQ",
            f"Your balance just crossed {current_milestone:,} BTQ "
            f"(current: {balance:,.2f} BTQ).\n\n{prediction}",
        )
        state["last_milestone_notified"] = current_milestone

    # --- Optional: daily prediction summary, even without a milestone ---
    last_pred_ts = state.get("last_prediction_sent_ts")
    should_send_prediction = force_status
    if not should_send_prediction and last_pred_ts:
        hours_since = (datetime.now(timezone.utc) - datetime.fromisoformat(last_pred_ts)).total_seconds() / 3600
        should_send_prediction = hours_since >= PREDICTION_INTERVAL_HOURS
    elif not last_pred_ts:
        should_send_prediction = True

    if should_send_prediction:
        prediction = predict_eta_to_target(state["history"], TARGET_COINS)
        alert(
            "BitQube status update",
            f"Current balance: {balance:,.2f} BTQ\n\n{prediction}",
        )
        state["last_prediction_sent_ts"] = datetime.now(timezone.utc).isoformat()

    # --- Twice-daily earnings summary (only runs when called with --summary) ---
    if do_summary:
        send_earnings_summary(state, balance)

    # --- On-demand check: current balance + earnings over last N hours ---
    if do_on_demand:
        send_on_demand_check(state, balance, hours=on_demand_hours)

    save_state(state)


if __name__ == "__main__":
    main()

# ---------------------------------------------------------------------------
# DEPLOYMENT OPTION A: cron on your own server (any VM, NOT necessarily
# DigitalOcean -- Oracle Cloud's free-tier VM works the same way)
# ---------------------------------------------------------------------------
# 1. pip install requests
# 2. Set your secrets, e.g. in /etc/environment or a .env you source in cron:
#      export BITQUBE_ADDRESS="Byour_actual_address_here"
#      export TELEGRAM_BOT_TOKEN="123456:ABC-your-token"
#      export TELEGRAM_CHAT_ID="987654321"
#      export SMTP_USER="you@gmail.com"
#      export SMTP_PASSWORD="your-16-char-app-password"
# 3. Add cron entries (crontab -e):
#
#    a) Regular polling every 15 minutes, for milestone alerts:
#       */15 * * * * source /home/kiran/.bitqube_env && /usr/bin/python3 /home/kiran/bitqube_tracker.py >> /home/kiran/bitqube_tracker.log 2>&1
#
#    b) Twice-daily earnings summary at 8:00 AM and 10:00 PM server time:
#       0 8  * * * source /home/kiran/.bitqube_env && /usr/bin/python3 /home/kiran/bitqube_tracker.py --summary >> /home/kiran/bitqube_tracker.log 2>&1
#       0 22 * * * source /home/kiran/.bitqube_env && /usr/bin/python3 /home/kiran/bitqube_tracker.py --summary >> /home/kiran/bitqube_tracker.log 2>&1
#
#    Note: cron runs in server time, which may not be your local time zone.
#    Check with `timedatectl` on the droplet, or prefix the command with
#    TZ=Asia/Kolkata if your cron supports it, to get 8 AM / 10 PM IST exactly.
#
# 4. Test manually first:
#      python3 bitqube_tracker.py --status
#      python3 bitqube_tracker.py --summary
#
# ---------------------------------------------------------------------------
# DEPLOYMENT OPTION B: GitHub Actions (recommended -- no server at all)
# ---------------------------------------------------------------------------
# See tracker.yml. Put this script + requirements.txt + tracker.yml (as
# .github/workflows/tracker.yml) in a small PRIVATE repo, add these repo
# Settings -> Secrets and variables -> Actions secrets:
#   BITQUBE_ADDRESS, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
#   SMTP_USER, SMTP_PASSWORD, ALERT_EMAIL_TO
# The workflow commits bitqube_state.json back to the repo after every run
# so history/milestones persist between runs, since GitHub's runners are
# thrown away each time.
