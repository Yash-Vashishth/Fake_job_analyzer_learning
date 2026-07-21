# Deploying the Fake Job Offer Forensic Analyzer

Two services: a Flask **backend** (API + SQLite storage) and a static **frontend**
(HTML/JS). Deploy them separately — the frontend just needs to know the
backend's public URL.

The one thing to get right: **storage persistence.** SQLite writes to a file
on local disk. Most free PaaS tiers wipe local disk on every redeploy/restart
unless you attach a persistent volume. Pick accordingly below.

---

## Option A — Render (render.yaml included)

Render's free web-service plan does **not** include a persistent disk (disks
require a paid instance, ~$0.25/GB/mo, min plan applies). If you're fine
starting on a paid tier, this is the simplest path since `render.yaml` in the
repo root already defines both services.

1. Push this repo to GitHub.
2. In Render: **New → Blueprint**, point it at the repo. It reads `render.yaml`
   and creates both services automatically.
3. On the `fakejob-backend` service, set the `ANTHROPIC_API_KEY` env var in
   the dashboard (never commit it — the blueprint deliberately leaves it
   `sync: false`).
4. After both deploy, copy the backend's public URL into
   `frontend/config.js`:
   ```js
   window.API_BASE = "https://fakejob-backend.onrender.com";
   ```
   Commit and push — Render redeploys the static site automatically.
5. If you skip the paid disk, the SQLite DB and uploaded files reset on every
   deploy/restart. Fine for demos; not fine for building a real labeled
   dataset over time. See Option B for a free persistent alternative.

## Option B — Railway (free persistent volumes)

Railway's free tier includes volumes, which is the better fit here if you
want `/flag` + `/retrain` data to actually survive.

1. `railway init` in the repo root, or connect the GitHub repo via the
   Railway dashboard.
2. Add two services from the same repo:
   - **backend**: root directory `backend`, build with
     `pip install -r ../requirements.txt`, start command
     `gunicorn -w 2 -b 0.0.0.0:$PORT app:app`.
   - **frontend**: root directory `frontend`, deploy as a static site
     (Railway's static-site template, or a tiny `serve`/nginx buildpack).
3. On the backend service: **Settings → Volumes**, mount a volume at
   `/app/backend/data` (or wherever your working directory resolves to —
   check the deploy logs). This is what keeps `forensics.db` and uploaded
   offer letters across restarts.
4. Set `ANTHROPIC_API_KEY` in the backend service's variables tab.
5. Set `frontend/config.js` → `window.API_BASE` to the backend's public
   Railway URL, same as Option A step 4.

## Option C — Fly.io (free persistent volumes)

Similar shape to Railway: `fly volumes create fakejob_data --size 1`, mount
it at `/app/backend/data` in `fly.toml`, deploy backend and frontend as two
separate Fly apps, then point `config.js` at the backend app's `.fly.dev` URL.

---

## Before you deploy, locally

```bash
cd fakejob
python -m venv venv && source venv/bin/activate    # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp backend/.env.example backend/.env                # fill in ANTHROPIC_API_KEY
cd backend && gunicorn -w 2 -b 0.0.0.0:5000 app:app  # production-style run
```

Confirm before deploying:
- `curl http://localhost:5000/health` → `{"status":"ok",...}`
- `curl -X POST http://localhost:5000/analyze -F "email_text=test" -F "company_domain=infosys.com"`
  returns scores including `domain_age` (needs real internet access — RDAP/WHOIS
  lookups will fail inside network-restricted sandboxes but work on any normal
  host).

## Environment variables (backend)

| Variable            | Required | Notes                                                             |
|----------------------|----------|--------------------------------------------------------------------|
| `ANTHROPIC_API_KEY`  | Yes      | Used for LLM-based semantic enrichment (`llm_assist.py`).          |
| `PORT`               | No       | Defaults to 5000; most PaaS providers inject this automatically.   |
| `FRONTEND_ORIGIN`    | No       | Restricts CORS to your deployed frontend URL. Defaults to `*`.     |

## Data growth over time

- `/analyze` now persists every submission (email text, uploaded file,
  domains, full score breakdown) to SQLite.
- Use `/flag` to mark a past submission's real-world outcome
  (`scam` / `legit` / `unsure`) once you know it.
- Call `/retrain` periodically (or wire it to a cron/scheduled job) to
  recompute parameter weights from everything labeled so far. It writes
  `backend/data/learned_weights.json` and hot-reloads it into the running
  process — no restart needed.
- `/learning/status` tells you how much labeled data you have and whether
  each parameter has crossed the minimum sample threshold to be trusted.
