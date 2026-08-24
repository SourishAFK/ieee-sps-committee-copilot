# Deploying (for the chapter chair)

Three free pieces, about 20 minutes total:

| Piece | Does what | Cost |
|---|---|---|
| **Neon** Postgres | Holds the data permanently | Free |
| **Streamlit Community Cloud** | Runs the app your committee opens | Free |
| **GitHub Actions** | Daily crawl + alerts while the app sleeps | Free |

Do them in this order — Streamlit needs the database URL.

---

## 1. Database (Neon) — 5 min

Free hosting gives you a disposable disk, so a plain file database would be
**wiped every restart** and your committee would lose its outreach notes. That is
the only reason this step exists.

1. Go to <https://neon.tech> → sign up with GitHub
2. Create a project, any name, region **Singapore** or **Mumbai** (closest to India)
3. Copy the **connection string**. It looks like:
   ```
   postgresql://user:PASSWORD@ep-xxxx.ap-southeast-1.aws.neon.tech/neondb?sslmode=require
   ```
4. Keep it somewhere safe for the next two steps.

> Supabase works identically if you prefer it — any Postgres URL is fine.

---

## 2. The app (Streamlit Community Cloud) — 10 min

1. Go to <https://share.streamlit.io> → **Sign in with GitHub**
2. **Create app** → **Deploy a public app from GitHub**
3. Fill in:
   - Repository: `SourishAFK/ieee-sps-committee-copilot`
   - Branch: `main`
   - **Main file path: `frontend/app.py`**
4. Set **Python version** to `3.12`. Newer versions usually work, but 3.12 is the
   one this project is tested on and the one CI uses.
5. Open **Advanced settings → Secrets** and paste the block below.

> **Copy the lines only — not the ``` fences.** They are markdown formatting, and
> Streamlit rejects the whole box with *"Invalid format: please enter valid TOML"*
> if they come along. Same goes for curly "smart quotes": TOML needs straight `"`.
> If a browser extension such as Grammarly is active in the box, turn it off first
> — it can rewrite quote characters as you paste.

```toml
DATABASE_URL = "postgresql://PASTE-YOUR-NEON-STRING-HERE"
APP_PASSWORD = "signalProcessingIsNotBoring"
CHAPTER_NAME = "IEEE Signal Processing Society Student Branch Chapter, MIT Bengaluru"
INSTITUTION = "Manipal Institute of Technology, Bengaluru"
CITY = "Bengaluru"
COUNTRY = "India"
IEEE_REGION = "10"
IEEE_SECTION = "IEEE Bangalore Section"
CHAIR_NAME = "Sourish Maheshwari"
CHAIR_EMAIL = "sourishmaheshwari@outlook.com"
VENUE_CAPACITY = "500"
ENABLE_SCHEDULER = "0"
GEMINI_API_KEY = ""
```

6. **Deploy**. First build takes 3–5 minutes.
7. Open the app, log in with your `APP_PASSWORD`, go to **Settings → Run a crawl
   now**, then **Refresh past-event knowledge**. This fills the fresh database.

Your committee URL will look like
`https://ieee-sps-committee-copilot-xxxx.streamlit.app`.

> **Secrets are not public.** The repository is public but Streamlit secrets are
> stored separately and never appear in the code.

---

## 3. Scheduled crawl and alerts (GitHub Actions) — 5 min

The workflow is already in the repo and active — you only need to give it the
settings it runs with.

In the repo on GitHub → **Settings → Secrets and variables → Actions**:

> **Each row below is its own separate entry.** Press *New repository secret* (or
> *New variable*) once per row and type the **Name** exactly as written — the
> workflow looks these names up literally, so `DATABASE_URL` works and anything
> else is ignored in silence.

**Secrets tab** — only one is required:

| Name | Value | Required |
|---|---|---|
| `DATABASE_URL` | your Neon connection string | **yes** |
| `TELEGRAM_BOT_TOKEN` | from @BotFather | no |
| `TELEGRAM_CHAT_ID` | your committee group id | no |
| `SMTP_USER` | sending email address | no |
| `SMTP_PASSWORD` | app password for that address | no |
| `DIGEST_TO` | who gets the digest, comma separated | no |
| `GEMINI_API_KEY` | for written feedback | no |

**Variables tab** — nine entries, the same chapter details as your Streamlit secrets:

| Name | Value |
|---|---|
| `CHAPTER_NAME` | IEEE Signal Processing Society Student Branch Chapter, MIT Bengaluru |
| `INSTITUTION` | Manipal Institute of Technology, Bengaluru |
| `CITY` | Bengaluru |
| `COUNTRY` | India |
| `IEEE_REGION` | 10 |
| `IEEE_SECTION` | IEEE Bangalore Section |
| `CHAIR_NAME` | Sourish Maheshwari |
| `CHAIR_EMAIL` | sourishmaheshwari@outlook.com |
| `VENUE_CAPACITY` | 500 |
| `ALERT_THRESHOLD` | 70 |

### Testing it

**Actions → Scheduled jobs → Run workflow → pick `status` → Run workflow.**

Green tick is not enough on its own — open the run, expand **Run status**, and read
the first block:

```
--- database ---
{ "engine": "postgres", "location": "ep-xxxx.aws.neon.tech", "persistent": true }
```

`"engine": "postgres"` means CI reached the same database the app uses. If it says
`sqlite` the job prints a warning: `DATABASE_URL` is missing or misspelled, and the
job is writing to a throwaway file the app will never read.

Once `status` is clean, run it again with `crawl` to prove the whole path works.
After that it runs itself: crawl daily at 07:00 IST, digest Mondays 09:15 IST.

---

## Telegram alerts (optional, 5 min)

1. Message [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token
2. Add the bot to your committee group, send any message there
3. In the app: **Settings → Find chat id** → copy the number
4. Put both into Streamlit secrets **and** GitHub secrets

---

## Keeping it awake

Streamlit free apps sleep after a few days of no visits and show a "wake up"
button on the next visit — that is normal, and it does not affect the data or the
scheduled alerts, which run on GitHub's servers regardless.

---

## Running it locally as well

Local runs keep using SQLite unless you set `DATABASE_URL`. To work against the
same shared data as the deployed app, put the Neon URL in your local `.env`.

```powershell
./run.ps1
```

## Updating the deployed app

```powershell
git add -A
git commit -m "what changed"
git push
```

Streamlit Cloud redeploys automatically within a minute or two.
