"""
Run every automatic scraper, one after another, and repeat.

    python -m app.schedule                 # run everything every 6 hours (keep the window open)
    python -m app.schedule --every-hours 4
    python -m app.schedule --once          # one full round, then stop (Refresh button, Cloud Run Job,
                                           # Windows Task Scheduler)
    python -m app.schedule --listen        # wait for the website's Refresh button (checks every minute)
    python -m app.schedule --only gem,sites

Order: GeM first (most security tenders), then the quick custom sites, then the
big NIC portals. One scraper failing never stops the others. CAPTCHA portals
(Chhattisgarh, Karnataka) are not included: they need a person
(python -m app.scraper.assist_list).

Every round is recorded in the scrape_runs table, and only one round runs at a
time: if a round is already running (for example started from the website),
a new one is skipped instead of scraping the same portals twice.
"""
import argparse
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger("schedule")

JOBS = [
    ("gem", ["-m", "app.scraper.gem"]),                       # also reads bid PDFs of new bids
    ("telangana", ["-m", "app.scraper.telangana"]),
    ("andhra", ["-m", "app.scraper.telangana", "--portal", "andhra"]),
    ("bihar", ["-m", "app.scraper.bihar"]),
    ("gujarat", ["-m", "app.scraper.gujarat"]),
    ("adani", ["-m", "app.scraper.adani"]),
    ("sites", ["-m", "app.scraper.sites"]),                   # ISRO, BARC, SBI, Shirdi, Kolkata Metro
    ("nic", ["-m", "app.scraper.run", "--portal", "all"]),    # CPPP, Defence, PSUs, NTPC, BHEL, all NIC states
]

# On Cloud Run every line printed goes to Cloud Logging, so don't hide it in files.
LOG_TO_STDOUT = bool(os.getenv("CLOUD_RUN_JOB") or os.getenv("LOG_TO_STDOUT"))


def run_round(only=None):
    """Run the scrapers. Returns {name: exit code}."""
    logs = Path("logs")
    logs.mkdir(exist_ok=True)
    results = {}
    for name, cmd in JOBS:
        if only and name not in only:
            continue
        start = time.time()
        log.info("Starting %s", name)
        try:
            if LOG_TO_STDOUT:
                code = subprocess.call([sys.executable, *cmd])
            else:
                with open(logs / f"{name}.log", "a", encoding="utf-8") as f:
                    f.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M} =====\n")
                    f.flush()
                    code = subprocess.call([sys.executable, *cmd], stdout=f, stderr=subprocess.STDOUT)
        except Exception as exc:
            code = f"error {exc}"
        results[name] = code
        log.info("Finished %s in %.0f min (exit %s)", name, (time.time() - start) / 60, code)
    return results


def tracked_round(only=None, trigger="cli"):
    """One round, recorded in scrape_runs. Skipped if another round is running."""
    from .db import SessionLocal, init_db
    from .refresh import claim, finish
    init_db()
    with SessionLocal() as db:
        run = claim(db, trigger)
        run_id = run.id if run else None
    if run_id is None:
        log.info("Another refresh is already running. Not starting a second one.")
        return None
    log.info("Refresh #%d started", run_id)
    results, ok, message = {}, False, None
    try:
        results = run_round(only)
        failed = [n for n, c in results.items() if c != 0]
        ok = len(failed) < len(results)          # done unless every scraper failed
        message = (f"Problems: {', '.join(failed)} (see logs)" if failed else "All scrapers finished")
    except BaseException as exc:                 # includes Ctrl+C: never leave the lock behind
        message = f"Stopped: {exc.__class__.__name__} {exc}"
        raise
    finally:
        with SessionLocal() as db:
            run = finish(db, run_id, ok, message)
        log.info("Refresh #%d %s: %s new tenders. %s", run_id, "done" if ok else "failed",
                 run.new_count if run else "?", message)
    return results


def listen(only=None, every_hours=None, poll_seconds=60):
    """Wait for Refresh clicks on the website (REFRESH_RUNNER=none) and, optionally,
    also run a round every N hours."""
    from .db import SessionLocal, init_db
    from .refresh import active_run
    init_db()
    log.info("Waiting for Refresh requests%s. Ctrl+C to stop.",
             f" (and a round every {every_hours:g} hours)" if every_hours else "")
    next_timed = time.time() if every_hours else None
    while True:
        with SessionLocal() as db:
            run = active_run(db)
            queued = run is not None and run.status == "queued"
        if queued:
            tracked_round(only, trigger="button")
        elif next_timed is not None and time.time() >= next_timed:
            tracked_round(only, trigger="schedule")
            next_timed = time.time() + every_hours * 3600
        time.sleep(poll_seconds)


def main():
    p = argparse.ArgumentParser(description="Run all scrapers on a schedule")
    p.add_argument("--every-hours", type=float, default=None,
                   help="Repeat every N hours (default 6 when neither --once nor --listen)")
    p.add_argument("--once", action="store_true")
    p.add_argument("--listen", action="store_true",
                   help="Wait for the website's Refresh button (use with REFRESH_RUNNER=none on the API)")
    p.add_argument("--only", help="Comma list of jobs: " + ", ".join(n for n, _ in JOBS))
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    only = {x.strip() for x in args.only.split(",")} if args.only else None
    trigger = "schedule" if os.getenv("CLOUD_RUN_JOB") else "cli"

    if args.listen:
        return listen(only, args.every_hours)
    every = args.every_hours or 6
    while True:
        round_start = time.time()
        tracked_round(only, trigger)
        if args.once:
            break
        wait = max(0, every * 3600 - (time.time() - round_start))
        log.info("Round done. Next round in %.1f hours.", wait / 3600)
        time.sleep(wait)


if __name__ == "__main__":
    main()
