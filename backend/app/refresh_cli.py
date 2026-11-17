"""
Parallel refresh (GitHub Actions, .github/workflows/refresh.yml).

    python -m app.refresh_cli start            # take the refresh lock, print groups for the job matrix
    python -m app.refresh_cli group nic-3      # run one group of scrapers
    python -m app.refresh_cli finish --run-id 7 --ok

The same scrapers as app/schedule.py, split into groups that run at the same time,
so a full refresh takes about an hour instead of 6+. Only one refresh at a time:
`start` refuses while another one is running (see app/refresh.py).
"""
import argparse
import json
import logging
import os
import subprocess
import sys

log = logging.getLogger("refresh_cli")

NIC_PER_GROUP = 4


def groups():
    from .scraper.portals import PORTALS
    g = {
        "gem": [["-m", "app.scraper.gem"]],
        "states": [["-m", "app.scraper.telangana"], ["-m", "app.scraper.telangana", "--portal", "andhra"],
                   ["-m", "app.scraper.bihar"], ["-m", "app.scraper.gujarat"]],
        "websites": [["-m", "app.scraper.adani"], ["-m", "app.scraper.sites"], ["-m", "app.scraper.custom"]],
    }
    names = list(PORTALS)
    for i in range(0, len(names), NIC_PER_GROUP):
        chunk = names[i:i + NIC_PER_GROUP]
        g[f"nic-{i // NIC_PER_GROUP + 1}"] = [["-m", "app.scraper.run", "--portal", ",".join(chunk)]]
    return g


def _output(**values):
    path = os.getenv("GITHUB_OUTPUT")
    for k, v in values.items():
        print(f"{k}={v}")
        if path:
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"{k}={v}\n")


def cmd_start(_args):
    from .db import SessionLocal, init_db
    from .refresh import claim
    init_db()
    trigger = "schedule" if os.getenv("GITHUB_EVENT_NAME") == "schedule" else "cli"
    with SessionLocal() as db:
        run = claim(db, trigger)
    if run is None:
        log.info("Another refresh is running; not starting a second one.")
        _output(run_id="", groups="[]")
        return
    log.info("Refresh #%d started (%s)", run.id, run.trigger)
    _output(run_id=run.id, groups=json.dumps(list(groups())))


def cmd_group(args):
    cmds = groups()[args.name]
    failed = 0
    for cmd in cmds:
        log.info("Running %s", " ".join(cmd))
        code = subprocess.call([sys.executable, *cmd])
        log.info("Exit %s", code)
        failed += code != 0
    sys.exit(1 if failed else 0)


def cmd_finish(args):
    from .db import SessionLocal
    from .refresh import finish
    with SessionLocal() as db:
        run = finish(db, args.run_id, ok=args.ok, message=args.message)
    log.info("Refresh #%s %s: %s new tenders", args.run_id, "done" if args.ok else "failed",
             run.new_count if run else "?")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("start").set_defaults(fn=cmd_start)
    g = sub.add_parser("group")
    g.add_argument("name")
    g.set_defaults(fn=cmd_group)
    f = sub.add_parser("finish")
    f.add_argument("--run-id", type=int, required=True)
    f.add_argument("--ok", action="store_true")
    f.add_argument("--message")
    f.set_defaults(fn=cmd_finish)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
