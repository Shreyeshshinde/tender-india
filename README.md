# Tender India

One website that collects government tenders from across India and shows them
with closing dates, values, EMD, district, and the actual tender documents.

Current coverage: **CPPP (eprocure.gov.in)**, all central government organisations.

## How it works

```
Government portal -> Scraper -> Cleaner -> PostgreSQL -> API -> Website
                                                 |
                       Document worker  ->  File storage (documents/)
```

1. **Scraper** reads "Tenders by Organisation" (no CAPTCHA), every list page, and each tender page.
2. **Cleaner** standardises dates (Indian time), amounts, and finds **district + state** from the PIN code
   (India Post data, bundled) or from place names in the text.
3. **Database** (PostgreSQL) keeps tenders, document lists, and the history of every corrigendum.
4. **Documents**: files are stored on disk (not in the database). The database only keeps their path.
   Same file used by many tenders is stored once.
5. **Website** search with state, district, organisation, category, and closing-date filters, plus
   Download buttons for every stored file.

## Folder layout

```
backend/
  app/
    scraper/
      cppp.py        portal client + page parsers
      browser.py     Playwright backup engine
      normalize.py   dates, money, Indian time
      geo.py         PIN code / place name -> district, state
      store.py       save tenders, track corrigenda, keep downloaded files
      run.py         crawler command
    documents/
      storage.py     file storage (local folder now, S3 later)
      fetcher.py     download files, detect CAPTCHA pages
      worker.py      processes "Get documents" requests, cleanup, stats
      assist.py      you solve the CAPTCHA, files are saved automatically
    data/pincodes.json.gz   India Post PIN -> district/state (19,486 PIN codes)
    models.py  main.py  db.py  migrate.py  seed_demo.py
  tests/       85 tests (run on SQLite by default, Postgres optional)
frontend/      Next.js website
docker-compose.yml   PostgreSQL 16 with pgvector (ready for AI search later)
```

## 1. Set up PostgreSQL (once)

**Option A: Docker** (from the project root):
```bash
docker compose up -d
```

**Option B: Windows installer**: install PostgreSQL 16 from postgresql.org, then in "SQL Shell (psql)":
```sql
CREATE USER tender WITH PASSWORD 'tender';
CREATE DATABASE tenders OWNER tender;
```

Then tell the app to use it:
```bash
cd backend
copy .env.example .env        # Mac/Linux: cp .env.example .env
```
The `.env` file already contains the right `DATABASE_URL`.

**Already have data in the old SQLite file?** Move it into Postgres (keeps everything, adds districts):
```bash
python -m app.migrate --copy-from sqlite:///tenders.db
```

## 2. Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate                      # Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium                 # for the browser engine and CAPTCHA assist

python -m pytest -q                         # 85 passed
python -m app.migrate --status              # shows which database is used
```

### Collect tenders
```bash
python -m app.scraper.run --portal chandigarh  # one state portal (see app/scraper/portals.py)
python -m app.scraper.run --portal cppp,chandigarh,punjab
python -m app.scraper.run --portal all         # every known NIC portal, one after another
python -m app.scraper.run                   # first run slow, later runs quick
python -m app.scraper.run --every-hours 6   # keep updating
python -m app.scraper.run --full            # re-open every tender (weekly)
python -m app.scraper.run --all-details     # open EVERY detail page (default: only titles that could be
                                            # security / ground search; works tenders are saved from the list)
python -m app.scraper.run --with-docs       # also download files for new tenders
```

### Documents
```bash
python -m app.documents.worker --loop 2                 # serve "Get documents" requests from the website
python -m app.documents.worker --tender 2026_ASI_929316_2   # one tender now
python -m app.documents.assist                          # CAPTCHA files: Chrome opens, you type the CAPTCHA
python -m app.documents.worker --cleanup-days 30        # delete files 30 days after closing
python -m app.documents.worker --stats                  # disk usage
```

| File status | Meaning |
|---|---|
| stored | File saved, Download button shown |
| captcha | Portal wants a human. Use `app.documents.assist` |
| failed | Not a file, or tender no longer on the portal |
| deleted | Removed by cleanup (tender closed long ago) |

### Start the API
```bash
uvicorn app.main:app --reload --port 8000    # http://localhost:8000/docs
```

## 3. Website
```bash
cd frontend
npm install
copy .env.example .env.local     # API_URL=http://localhost:8000
npm run dev                      # http://localhost:3000
```

## Refresh, New and Delete (website)

- **Refresh tenders** runs every scraper once (`python -m app.schedule --once`). Only one refresh
  runs at a time; pressing it again while one runs does nothing. Tenders first found by the latest
  refresh get a **New** tag.
- Each tender is saved **once** (portal + tender ID). Later refreshes only update it when a
  corrigendum changes it; old tenders stay in the list until you delete them.
- **Delete** hides a tender for good: refreshes will not add it back. The **Deleted** link (or
  status "Deleted") lists them, with **Restore**.
- Where the refresh runs is set on the API with `REFRESH_RUNNER`: `local` (default, a background
  process on the same machine), `cloudrun` (Google Cloud Run Job), or `none` (a PC running
  `python -m app.schedule --listen` picks it up).

## Hosting for free: Google Cloud Run + Supabase

```
Browser -> tender-web (Cloud Run, password) -> tender-api (Cloud Run) -> Supabase Postgres
                         Refresh button -> tender-scraper (Cloud Run Job, Playwright) ---^
```

1. **Supabase**: create a free project in region **Mumbai (ap-south-1)**. Copy the **Session pooler**
   connection string (Connect button; port 5432, works over IPv4).
2. **Google Cloud**: create a project and link a billing account (card needed; the free tier is
   not charged). Install the gcloud CLI and run `gcloud auth login`.
3. `cp deploy/gcp.env.example deploy/gcp.env`, fill it in, then from the project root (Git Bash):
   `bash deploy/gcp-deploy.sh`. It prints the website address.
4. Move your existing tenders once (from `backend/`):
   `DATABASE_URL="<supabase url>" python -m app.migrate --copy-from postgresql+psycopg://tender:tender@localhost:5432/tenders`

Free-tier notes: the services scale to zero (first page after a quiet spell takes a few seconds);
the scraper job gets ~60 free hours a month at 1 CPU / 2 GB; Supabase pauses a free project after
7 days with no activity (the scheduled refresh prevents that). "Get documents" is off on Cloud Run
(no lasting disk); "Open official portal" still works. You can also point `backend/.env` at Supabase
and run any scraper or CAPTCHA helper on your own PC; it writes to the same database.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/tenders?q=&state=&district=&organisation=&category=&status=active&closing_within_days=7&sort=closing` | Search (`status=deleted` lists deleted tenders) |
| `GET /api/tenders/{id}` | One tender, documents with file status, change history |
| `DELETE /api/tenders/{id}` | Delete (admin): hidden, never re-added by scrapers |
| `POST /api/tenders/{id}/restore` | Undo a delete (admin) |
| `GET /api/refresh` / `POST /api/refresh` | Latest refresh / start one (admin) |
| `POST /api/tenders/{id}/documents/request` | "Get documents" button |
| `GET /api/documents/{doc_id}/file` | Download a stored file |
| `GET /api/filters?state=` | Dropdown values (districts of a state) |
| `GET /api/stats` | Home page numbers |

Admin calls need the header `X-Admin-Token` when `ADMIN_TOKEN` is set on the API (always set it on
a public server). The website adds it on its server side; the browser never sees it.

## Good behaviour rules

- One request every 1.5 seconds or slower; the scraper slows itself down when the server is busy.
- Never bypasses a CAPTCHA. If a portal asks for one, a person types it (`assist`).
- Clear note on the site that it is not an official government website.

## Security & ground search focus

Every tender is checked against the product keywords in `backend/app/focus.py`
(DFMD, HHMD, shoe and liquid scanners, mine detectors, deep search metal detectors,
UXO, IED, GPR ...). The website shows only matching tenders by default; use the
"All tenders" button to see everything.

```bash
python -m app.focus --test "Supply of DFMD for BSF"    # how one title is judged
python -m app.focus --backfill                          # re-check all tenders after editing the lists
```

## Running tests on Postgres
```bash
TEST_DATABASE_URL=postgresql+psycopg://tender:tender@localhost:5432/tenders_test python -m pytest -q
```
Use an empty database for this; the tests drop and recreate tables.
