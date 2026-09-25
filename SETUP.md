# Setup checklist: project `esnugiumktntfmvxkvwa`

The dashboard is already wired to your new Supabase project (`nexus-config.js` holds the URL and the **anon** key, which is safe to publish).
The **service_role** key goes in exactly two places: GitHub secrets, and a local `.env` if you run the crawler locally. Never commit it.

## 1. Supabase (2 min)
1. Open https://supabase.com/dashboard/project/esnugiumktntfmvxkvwa/sql/new
2. Paste all of `database/schema.sql` and click **Run**. "Success. No rows returned" is expected.

## 2. GitHub (5 min)
1. Create a new **private** repo and push this folder:
   ```bash
   git init && git add -A && git commit -m "Nexus v5"
   git branch -M main
   git remote add origin https://github.com/<you>/<repo>.git
   git push -u origin main
   ```
2. Go to repo → Settings → Secrets and variables → Actions → **New repository secret**:

   | Name | Value |
   |---|---|
   | `SUPABASE_URL` | `https://esnugiumktntfmvxkvwa.supabase.co` |
   | `SUPABASE_SERVICE_ROLE_KEY` | your service_role key (Supabase → Settings → API) |
   | `SEC_USER_AGENT` | `Nexus Asia Research <your email>` |
   | `GEMINI_API_KEY` | *(optional)* a new key from aistudio.google.com |
   | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | *(optional)* |

3. Go to Actions → "Nexus CRE Intelligence — Crawler" → **Run workflow**. For the first run, set days = `3`.
4. When it finishes, open the run → **Artifacts** → `crawl-report-…`. `run_report.json` shows which sources worked.

## 3. Vercel (3 min)
1. Import the GitHub repo. No build settings are needed.
2. The dashboard works right away through `nexus-config.js`. For the chat, add Environment Variables `SUPABASE_URL`, `SUPABASE_ANON_KEY` (the anon key) and `GEMINI_API_KEY`, then redeploy.
3. Optional: set the GitHub Actions variable `DASHBOARD_URL` to the Vercel URL so Telegram alerts link to it.

## 4. Check everything (optional, local)
```bash
pip install -r requirements.txt
cp .env.example .env        # paste the service_role key into .env (it's git-ignored)
python tools/check_setup.py # verifies tables, views, and anon read-only / service write
```

## If NSE or BSE show FAILED in Source Health
GitHub's servers are in the US, and NSE blocks most cloud IPs. Run a self-hosted runner on a machine in India (repo → Settings → Actions → Runners → New self-hosted runner), then change `runs-on: ubuntu-latest` to `runs-on: self-hosted` in `.github/workflows/crawl.yml`.
