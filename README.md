# BitQube Balance Tracker

Tracks your BitQube (BTQ) wallet balance using the public explorer API at
https://explorer.bitqube.org, and alerts you on Telegram and/or email:

- Every time your balance crosses a **10,000 BTQ milestone**
- A **prediction** of when you'll hit **100,000 BTQ**, based on your recent growth rate
- A **twice-daily earnings summary** (8 AM and 10 PM) — coins earned since the last summary
- An **on-demand check** you can trigger any time — current balance + coins earned in the last N hours

It runs for free on **GitHub Actions**, so no server of your own is needed
(and your GPU mining box is never touched by this — it's just polling a
public API).

---

## Files

| File | What it's for |
|---|---|
| `bitqube_tracker.py` | The actual script — fetches balance, checks milestones, sends alerts |
| `requirements.txt` | The one Python package it needs (`requests`) |
| `.github/workflows/tracker.yml` | Tells GitHub Actions when/how to run the script |
| `bitqube_state.json` | Small file the script uses to remember balance history between runs (auto-created/updated) |

---

## One-time setup

### 1. Get a Telegram bot (recommended notification channel)

1. In Telegram, message **@BotFather** → send `/newbot` → follow the prompts.
2. Copy the **bot token** it gives you (looks like `123456:ABC-xyz...`).
3. Send your new bot any message (e.g. "hi") so it knows about you.
4. Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser
   and find your **chat_id** in the response (a number).

### 2. (Optional) Set up email as a backup channel

1. Use a Gmail account.
2. Go to **Google Account → Security → App Passwords** and generate one
   (needs 2-Step Verification turned on first).
3. That 16-character app password is what the script uses — not your
   normal Gmail password.

### 3. Create a private GitHub repo

1. Create a new **private** repo, e.g. `bitqube-tracker`.
2. Add these files to the repo root:
   - `bitqube_tracker.py`
   - `requirements.txt`
3. Create the folder `.github/workflows/` and put `tracker.yml` inside it.
4. Create an empty state file so the first run has something to check out:
   ```
   echo "{}" > bitqube_state.json
   ```
5. Commit and push all of the above.

### 4. Add your secrets

In the repo: **Settings → Secrets and variables → Actions → New repository secret**.
Add these (skip email ones if you're only using Telegram):

| Secret name | Value |
|---|---|
| `BITQUBE_ADDRESS` | Your BitQube wallet address (starts with `B...`) |
| `TELEGRAM_BOT_TOKEN` | From step 1 |
| `TELEGRAM_CHAT_ID` | From step 1 |
| `SMTP_USER` | Your Gmail address *(optional)* |
| `SMTP_PASSWORD` | Your Gmail app password *(optional)* |
| `ALERT_EMAIL_TO` | Where alerts should be emailed *(optional, defaults to SMTP_USER)* |

### 5. Confirm your wallet address is correct

Open `https://explorer.bitqube.org/address/<your-address>` in a browser
first, and check the balance shown matches what you expect — cheaper to
catch a typo here than to debug the script later.

---

## Running it

You don't need to do anything else — once secrets are set and the files
are pushed, GitHub runs it automatically:

| Schedule | What happens |
|---|---|
| Every 15 minutes | Checks balance; alerts you only if you've crossed a new 10,000 BTQ milestone (includes a 100k ETA prediction) |
| 8:00 AM IST | Sends an earnings summary: coins earned since the last summary |
| 10:00 PM IST | Same, for the second half of the day |

### Checking on demand

Go to the repo's **Actions** tab → **BitQube Tracker** workflow →
**Run workflow** button. It'll ask for "hours" (default 24) and send you
a message with your current balance and coins earned in that window —
independent of the scheduled alerts above.

### Testing without waiting for a schedule

Same **Run workflow** button works any time you want to sanity-check that
alerts are arriving — it doesn't wait for 8 AM/10 PM.

---

## Running it locally instead (optional)

If you ever want to run it on your own machine instead of GitHub Actions:

```bash
pip install -r requirements.txt

export BITQUBE_ADDRESS="Byour_actual_address_here"
export TELEGRAM_BOT_TOKEN="123456:ABC-your-token"
export TELEGRAM_CHAT_ID="987654321"
# optional email vars: SMTP_USER, SMTP_PASSWORD, ALERT_EMAIL_TO

python3 bitqube_tracker.py                    # normal check (milestones only)
python3 bitqube_tracker.py --summary          # force an earnings summary now
python3 bitqube_tracker.py --last24h          # on-demand: balance + last 24h earned
python3 bitqube_tracker.py --last24h --hours=6  # on-demand with a custom window
```

---

## Things worth knowing

- **The 100k prediction is a simple straight-line estimate** based on your
  last 7 days of balance history — not a guarantee. It gets more accurate
  the longer the tracker has been running, and will drift if your GPU
  hashrate or network difficulty changes a lot.
- **GitHub Actions cron times are UTC**, already converted to 2:30 AM / 4:30 PM
  UTC in `tracker.yml` to land at 8 AM / 10 PM IST. Scheduled runs can be a
  few minutes late under GitHub's load — fine for this use case.
- **The first on-demand or summary check** after setup will say "no history
  yet" since it needs at least one prior data point to compare against —
  this fills in naturally after the tracker's been running a day.
- **No GPU or paid server required** — this only makes lightweight HTTP
  calls to a public API, so it costs nothing to run.
