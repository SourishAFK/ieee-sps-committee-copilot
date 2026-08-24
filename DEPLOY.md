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
4. Open **Advanced settings → Secrets** and paste this, filling in your values:

```toml
DATABASE_URL = "postgresql://...paste from Neon..."
APP_PASSWORD = "pick-something-and-share-it-with-the-committee"

CHAPTER_NAME = "IEEE Signal Processing Society Student Branch Chapter, MIT Bengaluru"
INSTITUTION  = "Manipal Institute of Technology, Bengaluru"
CITY         = "Bengaluru"
COUNTRY      = "India"
IEEE_REGION  = "10"
IEEE_SECTION = "IEEE Bangalore Section"
CHAIR_NAME   = "Sourish Maheshwari"
CHAIR_EMAIL  = "sourishmaheshwari@outlook.com"
VENUE_CAPACITY = "500"

# The hosted app sleeps, so CI does the scheduling instead.
ENABLE_SCHEDULER = "0"

# Optional - written feedback and drafted emails
GEMINI_API_KEY = ""
```

5. **Deploy**. First build takes 3–5 minutes.
6. Open the app, log in with your `APP_PASSWORD`, go to **Settings → Run a crawl
   now**, then **Refresh past-event knowledge**. This fills the fresh database.

Your committee URL will look like
`https://ieee-sps-committee-copilot-xxxx.streamlit.app`.

> **Secrets are not public.** The repository is public but Streamlit secrets are
> stored separately and never appear in the code.

---

## 3. Scheduled crawl and alerts (GitHub Actions) — 5 min

The workflow file is in your local folder but **is not pushed yet** — the GitHub
token that created the repo lacks the `workflow` permission. Grant it once:

```powershell
gh auth refresh -h github.com -s workflow
```

That opens a browser to approve. Then, from the project folder:

```powershell
git add .github/workflows/scheduled-jobs.yml
git commit -m "Add scheduled crawl and digest workflow"
git push
```

Then in the repo on GitHub → **Settings → Secrets and variables → Actions**:

**Secrets** (the sensitive ones):

| Name | Value |
|---|---|
| `DATABASE_URL` | the same Neon string |
| `TELEGRAM_BOT_TOKEN` | from @BotFather, optional |
| `TELEGRAM_CHAT_ID` | your committee group id, optional |
| `SMTP_USER` / `SMTP_PASSWORD` / `DIGEST_TO` | for email digests, optional |
| `GEMINI_API_KEY` | optional |

**Variables** (not sensitive) — same chapter details as the Streamlit secrets:
`CHAPTER_NAME`, `INSTITUTION`, `CITY`, `COUNTRY`, `IEEE_SECTION`, `CHAIR_NAME`,
`CHAIR_EMAIL`, `VENUE_CAPACITY`, `ALERT_THRESHOLD`.

Test it: **Actions → Scheduled jobs → Run workflow → `status`**. A green run means
it reached your database. After that it runs itself: crawl daily at 07:00 IST,
digest Mondays at 09:15 IST.

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
