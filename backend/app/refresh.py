"""
Refresh = one round of every scraper (app/schedule.py), started by the website's
Refresh button, by a schedule, or by hand.

Only one round at a time: a second click while a round is queued or running gets
the round that is already going (see ScrapeRun.active in models.py).

Where the round runs is chosen with REFRESH_RUNNER:
  local     (default) a background process on this machine: python -m app.schedule --once
  cloudrun  the Google Cloud Run Job named in SCRAPER_JOB (see deploy/gcp-deploy.sh)
  github    the GitHub Actions workflow .github/workflows/refresh.yml (GITHUB_REPO, GITHUB_TOKEN)
  none      nothing is started here; a worker that runs `python -m app.schedule --listen`
            (for example on your own PC) picks the request up within a minute
"""
import logging
import os
import subprocess
import sys
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .db import BACKEND_DIR
from .models import ScrapeRun, Tender
from .scraper.normalize import now_ist

log = logging.getLogger("refresh")

# A round that has been "running" this long is assumed dead (machine switched off,
# job killed) and no longer blocks new refreshes.
STALE_HOURS = float(os.getenv("REFRESH_STALE_HOURS", "8"))


def run_dict(run):
    if run is None:
        return None
    iso = lambda v: v.isoformat() if v else None  # noqa: E731
    return {"id": run.id, "trigger": run.trigger, "status": run.status,
            "active": run.active is not None, "requested_at": iso(run.requested_at),
            "started_at": iso(run.started_at), "finished_at": iso(run.finished_at),
            "new_count": run.new_count, "message": run.message}


def latest_run(db):
    return db.scalar(select(ScrapeRun).order_by(ScrapeRun.id.desc()).limit(1))


def _expire_stale(db):
    cutoff = now_ist() - timedelta(hours=STALE_HOURS)
    stale = db.scalars(select(ScrapeRun).where(ScrapeRun.active.is_not(None),
                                               ScrapeRun.requested_at < cutoff)).all()
    for run in stale:
        run.status, run.active, run.finished_at = "failed", None, now_ist()
        run.message = f"No news from this refresh for {STALE_HOURS:g} hours; assumed stopped."
    if stale:
        db.commit()


def active_run(db):
    """The round that is queued or running now, or None."""
    _expire_stale(db)
    return db.scalar(select(ScrapeRun).where(ScrapeRun.active.is_not(None)))


def _create(db, trigger, status):
    now = now_ist()
    run = ScrapeRun(trigger=trigger, status=status, active=1, requested_at=now,
                    started_at=now if status == "running" else None)
    db.add(run)
    try:
        db.commit()
        return run
    except IntegrityError:          # another round became active at the same moment
        db.rollback()
        return None


def request_refresh(db, trigger="button"):
    """Website button. Returns (run, started_now). Never starts a second round."""
    run = active_run(db)
    if run is not None:
        return run, False
    run = _create(db, trigger, "queued")
    if run is None:
        return active_run(db), False
    try:
        dispatch()
    except Exception as exc:
        log.exception("Could not start the refresh")
        finish(db, run.id, ok=False, message=f"Could not start: {exc}")
        raise
    return run, True


def claim(db, trigger="cli"):
    """Called by app.schedule when a round begins. Takes the queued request (button)
    or creates a new round. Returns None if another round is already running."""
    run = active_run(db)
    if run is not None:
        if run.status != "queued":
            return None
        run.status, run.started_at = "running", now_ist()
        db.commit()
        return run
    return _create(db, trigger, "running")


def finish(db, run_id, ok, message=None):
    run = db.get(ScrapeRun, run_id)
    if run is None:
        return None
    since = run.started_at or run.requested_at
    run.new_count = db.scalar(select(func.count()).select_from(Tender)
                              .where(Tender.first_seen_at >= since))
    run.status = "done" if ok else "failed"
    run.active, run.finished_at = None, now_ist()
    if message:
        run.message = message[:2000]
    db.commit()
    return run


# --------------------------------------------------------------------------
# Starting the round
# --------------------------------------------------------------------------
def dispatch():
    runner = os.getenv("REFRESH_RUNNER", "local").strip().lower()
    if runner == "cloudrun":
        _start_cloud_run_job()
    elif runner == "github":
        _start_github_workflow()
    elif runner == "local":
        _start_local()
    elif runner != "none":
        raise RuntimeError(f"Unknown REFRESH_RUNNER '{runner}' (use local, cloudrun or none)")


def _start_local():
    logs = BACKEND_DIR / "logs"
    logs.mkdir(exist_ok=True)
    kwargs = {"cwd": BACKEND_DIR, "stdin": subprocess.DEVNULL, "stderr": subprocess.STDOUT}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    with open(logs / "refresh.log", "a", encoding="utf-8") as out:
        subprocess.Popen([sys.executable, "-m", "app.schedule", "--once"], stdout=out, **kwargs)


def _start_github_workflow():
    """Start .github/workflows/refresh.yml. GITHUB_TOKEN: fine-grained token for this one
    repository with "Actions: Read and write" permission."""
    import requests
    repo = os.environ["GITHUB_REPO"]                       # owner/name
    workflow = os.getenv("GITHUB_WORKFLOW", "refresh.yml")
    r = requests.post(
        f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches",
        headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
                 "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
        json={"ref": os.getenv("GITHUB_REF_NAME", "main")}, timeout=30)
    if r.status_code >= 300:
        raise RuntimeError(f"GitHub answered {r.status_code}: {r.text[:300]}")


def _start_cloud_run_job():
    """Start one execution of the scraper job, using the API service's own Google identity
    (no keys stored anywhere). Needs permission run.jobs.run on the job."""
    import requests
    project, region = os.environ["GCP_PROJECT"], os.environ["GCP_REGION"]
    job = os.getenv("SCRAPER_JOB", "tender-scraper")
    token = requests.get(
        "http://metadata.google.internal/computeMetadata/v1/instance/service-account/default/token",
        headers={"Metadata-Flavor": "Google"}, timeout=10).json()["access_token"]
    r = requests.post(f"https://run.googleapis.com/v2/projects/{project}/locations/{region}/jobs/{job}:run",
                      headers={"Authorization": f"Bearer {token}"}, json={}, timeout=30)
    if r.status_code >= 300:
        raise RuntimeError(f"Cloud Run answered {r.status_code}: {r.text[:300]}")
