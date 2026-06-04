# Vanthir

Property-intelligence platform. Address-keyed public-records snapshots + structured
resident data → reports, evidence packages, alerts. Django / Postgres / Railway.

## The data model in one breath

`Parcel` (folio) is the spine. Every record keys to it. Five invariants are baked
into `properties/models.py` (see the module docstring): address-is-the-spine,
resolve-owners-later, source-on-everything, the FCRA wall, and scores-decompose.
Events dedup on `(source, source_record_id)`; state-over-time is append-only dated
rows (the "time machine"). Don't erode these.

## Run locally

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

python manage.py migrate          # sqlite locally (no DATABASE_URL set)
python manage.py createsuperuser
python manage.py runserver
# admin at http://127.0.0.1:8000/admin/   health at /healthz
```

## Test

```bash
python manage.py test properties   # the four invariant tests
```

## Push to GitHub

```bash
git init
git add .
git commit -m "Vanthir: property-intelligence data model + scaffold"
git branch -M main
git remote add origin git@github.com:YOURUSER/vanthir.git
git push -u origin main
```

## Deploy on Railway

```bash
# one-time
npm i -g @railway/cli
railway login

railway init                 # create the project
railway add --database postgres   # provisions Postgres, sets DATABASE_URL
railway up                   # deploy (or connect the GitHub repo in the dashboard)
```

Then set these variables in the Railway service (Variables tab or `railway variables set`):

| Variable | Value |
|---|---|
| `SECRET_KEY` | a long random string |
| `DEBUG` | `0` |
| `ALLOWED_HOSTS` | your `*.up.railway.app` domain (also auto-detected) |
| `CSRF_TRUSTED_ORIGINS` | `https://your-app.up.railway.app` |

`DATABASE_URL` is set automatically by the Postgres plugin. The `release` command
in the `Procfile` runs migrations on every deploy. Health check hits `/healthz`.

## What's next (not built yet)

1. **Appraiser ingestion adapter** — Miami-Dade parcel + ownership + assessment
   (the spine). Needs a sample of their actual data to map onto the models.
2. **Page generation** — auto-built address / building / HOA pages (the free,
   indexable SEO/AEO layer).
3. **Buyer / HOA due-diligence report** — the first monetizable surface; runs on
   public records alone, no reviews required.
4. **Management-company entity + LLC resolution** — the fast-follow, where the
   hard entity-resolution work happens.
