r"""
One refresh: new sessions as original .fit files, and health data as a CSV, into one folder the athlete drags onto
their training app's Upload page as a whole. The app skips what it already has.

    <export folder>\
        Activities\                 every session's .fit so far
            2026-11-21 0712 running 12345678901.fit     (a name never changes, so a re-drop is skipped by name)
        Health\                     one CSV per refresh, holding only that refresh's date window
            health-2026-11-14-2026-11-21 0712.csv
        export-state.json           what is already downloaded (ids and dates only)
        export log.txt              one line per refresh, counts only

Sessions and health data are two parts the athlete can switch on or off. The first export's start (how far back) is
asked once; after that each part picks up where it got to, and a part switched on later reaches back to that same
start. Health is tracked per kind, so a kind switched on later (weight, say) catches up the same way.

A health file is written once and never changed. Its name carries its date window (start-end) and the refresh's
time, so every refresh's file has a name of its own: an app that skips repeats by name still takes the new one, and
reads the window end from the name to let the newest export win a day. Every window reaches 7 days back past the
last export, because Garmin re-scores a night or two after it ends.

A refresh can be stopped, or stopped by Garmin's rate limit, at any point: every downloaded file is recorded at once,
and the next refresh carries on.

Older layouts (v0.1/v0.2: "All activities" or "All sessions", plus a dated "New ..." folder per refresh) are folded
into Activities and Health on the next refresh, so nobody is left with two copies.
"""
import csv
import datetime as dt
import io
import json
import os
import re
import time
from pathlib import Path

from . import garmin, health

STATE_FILE = "export-state.json"
LOG_FILE = "export log.txt"
LOCK_FILE = ".export.lock"
ACT_DIR = "Activities"
HEALTH_DIR = "Health"
OLD_ACT_DIRS = ("All sessions", "All activities")     # v0.2 and v0.1 names, folded into Activities
OLD_BATCH_RE = re.compile(r"New \d{4}-\d{2}-\d{2} \d{4}$")
HEALTH_OVERLAP_DAYS = 7
ACTIVITY_OVERLAP_DAYS = 3     # a watch that syncs a day late still has its session picked up
LOCK_STALE_S = 12 * 3600
MAX_TRIES = 3                 # a download that fails this many refreshes running is left out (said in the log)
EARLIEST = dt.date(2005, 1, 1)


class Busy(RuntimeError):
    """Another refresh (the window, or a scheduled one) is running on this folder."""


# -- small helpers ---------------------------------------------------------------------------------------------------
def months_ago(today: dt.date, months: int) -> dt.date:
    """The same day `months` back (28th at most, so every month has it). 0 means everything Garmin has."""
    if months <= 0:
        return EARLIEST
    y, m = divmod(today.month - 1 - months, 12)
    return dt.date(today.year + y, m + 1, min(today.day, 28))


def file_name(a: dict) -> str:
    """'2026-11-21 0712 running 12345678901.fit': sorts by date, says the sport, never collides, never changes."""
    start = (a.get("startTimeLocal") or "")[:16]
    stamp = start[:10] + " " + start[11:16].replace(":", "") if len(start) >= 16 else "unknown-date"
    kind = ((a.get("activityType") or {}).get("typeKey") or "activity").replace("_", " ")
    kind = re.sub(r"[^a-z0-9 ]", "", kind.lower()).strip() or "activity"
    return f"{stamp} {kind} {int(a['activityId'])}.fit"


def health_name(start: dt.date, end: dt.date, now: dt.datetime) -> str:
    """'health-2026-11-14-2026-11-21 0712.csv': the window, then the refresh's time, so no two refreshes share a name."""
    return f"health-{start.isoformat()}-{end.isoformat()} {now:%H%M}.csv"


def write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)


def health_csv(rows: list) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\n")
    w.writerow(["Timestamp", "Type", "Value"])
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _first_session_date(folder: Path):
    names = sorted(p.name for p in folder.glob("*.fit")) if folder.exists() else []
    return names[0][:10] if names and re.match(r"\d{4}-\d{2}-\d{2}", names[0]) else None


def _acts_dir(root: Path) -> Path:
    """Activities, or before the first refresh of this version, the old folder that holds the sessions."""
    for name in (ACT_DIR,) + OLD_ACT_DIRS:
        if (root / name).is_dir():
            return root / name
    return root / ACT_DIR


# -- folding the old layout in ---------------------------------------------------------------------------------------
def _move_into(src: Path, dst: Path, pattern: str) -> None:
    """Move src's files matching pattern into dst. A file dst already has (same name and size) is a duplicate and
    goes; one that differs is left where it is, never overwritten."""
    for f in sorted(src.glob(pattern)):
        target = dst / f.name
        try:
            if target.exists():
                if target.stat().st_size == f.stat().st_size:
                    f.unlink()
                continue
            dst.mkdir(exist_ok=True)
            os.replace(f, target)
        except OSError:
            pass


def _remove_if_empty(folder: Path) -> None:
    try:
        folder.rmdir()
    except OSError:
        pass


def fold_old_layout(root: Path) -> None:
    """Older versions' folders into Activities and Health; each old folder goes once it is empty."""
    for name in OLD_ACT_DIRS:
        old = root / name
        if old.is_dir():
            _move_into(old, root / ACT_DIR, "*.fit")
            _remove_if_empty(old)
    for batch in sorted(root.glob("New *")):
        if batch.is_dir() and OLD_BATCH_RE.match(batch.name):
            _move_into(batch, root / ACT_DIR, "*.fit")
            _move_into(batch, root / HEALTH_DIR, "health-*.csv")
            _remove_if_empty(batch)


# -- state -----------------------------------------------------------------------------------------------------------
def load_state(root: Path) -> dict:
    try:
        s = json.loads((root / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        s = {}
    s.setdefault("done_ids", [])
    s.setdefault("failures", {})          # session id -> {"date": day, "tries": n}: tried again next refresh
    for k in ("last_export", "first_from", "activities_through", "pending"):
        s.setdefault(k, None)
    lc = s.get("last_counts")             # what the latest refresh brought; None before this version's first one
    s["last_counts"] = lc if isinstance(lc, dict) else None
    ht = s.get("health_through")
    if isinstance(ht, str):               # v0.1.0 kept one date for every kind (all were on)
        ht = {k: ht for k in health.ALL_KINDS}
    s["health_through"] = ht if isinstance(ht, dict) else {}
    if not s["first_from"] and s["activities_through"]:     # v0.1.0 did not record where the export started
        s["first_from"] = _first_session_date(_acts_dir(root)) or s["activities_through"]
    s["pending"] = bool(s["pending"])     # v0.2 kept the "New" folder's name here
    return s


def save_state(root: Path, state: dict) -> None:
    write_atomic(root / STATE_FILE, json.dumps(state, indent=1).encode("utf-8"))


def summary(root: Path) -> dict:
    """What the window shows: the latest export, what it brought, what the folder holds."""
    s = load_state(root)
    acts = _acts_dir(root)
    return {
        "folder": str(root),
        "folder_name": root.name,
        "last_export": s["last_export"],
        "first_from": s["first_from"],
        "health_through": max(s["health_through"].values(), default=None),
        "first_session": _first_session_date(acts),
        "total_sessions": sum(1 for _ in acts.glob("*.fit")) if acts.exists() else 0,
        "new_sessions": (s["last_counts"] or {}).get("sessions"),
        "new_health_days": (s["last_counts"] or {}).get("health_days"),
        "unfinished": s["pending"],
    }


# -- lock ------------------------------------------------------------------------------------------------------------
class Lock:
    def __init__(self, root: Path):
        self.path = root / LOCK_FILE
        self.fd = None

    def __enter__(self):
        try:
            if self.path.exists() and time.time() - self.path.stat().st_mtime > LOCK_STALE_S:
                self.path.unlink()
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise Busy("A refresh is already running for this folder.") from None
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
            try:
                self.path.unlink()
            except OSError:
                pass


# -- the refresh -----------------------------------------------------------------------------------------------------
def _from(through, first: dt.date, overlap: int) -> dt.date:
    """Where a part picks up: a few days before what it already has, or the first export's start."""
    return dt.date.fromisoformat(through) - dt.timedelta(days=overlap) if through else first


def refresh(client, root: Path, kinds, first_months: int, sessions: bool = True, now: dt.datetime = None,
            progress=None, should_stop=None) -> dict:
    """Run one refresh: session files (when `sessions`) and the health kinds in `kinds` (none: no health file).
    Returns counts for the window; raises garmin.GarminError (KeyRejected, RateLimited) or Busy. Whatever was
    downloaded before an error or a stop is kept, and the next refresh carries on.

    `first_months` only matters the first time: it sets where the export starts."""
    now = now or dt.datetime.now()
    today = now.date()
    progress = progress or (lambda *a: None)
    should_stop = should_stop or (lambda: False)
    kinds = [k for k in health.ALL_KINDS if k in (kinds or [])]
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)

    with Lock(root):
        fold_old_layout(root)
        state = load_state(root)
        acts_dir, health_dir = root / ACT_DIR, root / HEALTH_DIR
        if not state["first_from"]:
            state["first_from"] = months_ago(today, first_months).isoformat()
        first = dt.date.fromisoformat(state["first_from"])
        if not state["pending"] or not state["last_counts"]:   # a fresh refresh; a stopped one keeps counting
            state.update(pending=True, last_counts={"sessions": 0, "health_days": 0})
        save_state(root, state)
        counts = state["last_counts"]

        act_from = _from(state["activities_through"], first, ACTIVITY_OVERLAP_DAYS)
        failures = state["failures"]
        retry = [f["date"] for f in failures.values() if f["tries"] < MAX_TRIES]
        if retry:                                   # reach back far enough to try earlier failed downloads again
            act_from = min(act_from, dt.date.fromisoformat(min(retry)))
        health_from = min((_from(state["health_through"].get(k), first, HEALTH_OVERLAP_DAYS) for k in kinds),
                          default=today)

        result = {"folder": str(root), "new_sessions": 0, "failed": 0, "health_rows": 0, "health_days": 0,
                  "stopped": False}
        error = None
        try:
            acts = []
            everything = kinds and health_from <= EARLIEST
            if sessions or everything:
                progress("Looking for sessions", 0, 0)
                listed = client.activities(min(act_from, health_from) if everything else act_from, today)
                if everything:          # "everything Garmin has": health from the first session, not from 2005
                    dates = [a["startTimeLocal"][:10] for a in listed if a.get("startTimeLocal")]
                    health_from = dt.date.fromisoformat(min(dates)) if dates else months_ago(today, 24)
                if sessions:
                    done = set(state["done_ids"])
                    gave_up = {k for k, f in failures.items() if f["tries"] >= MAX_TRIES}
                    acts = [a for a in listed
                            if a.get("activityId") and int(a["activityId"]) not in done
                            and not a.get("manualActivity") and str(int(a["activityId"])) not in gave_up
                            and (a.get("startTimeLocal") or "")[:10] >= act_from.isoformat()]
                    acts.sort(key=lambda a: a.get("startTimeLocal") or "")

            if acts:
                acts_dir.mkdir(exist_ok=True)
            for i, a in enumerate(acts, 1):
                if should_stop():
                    result["stopped"] = True
                    break
                progress("Session", i, len(acts))
                aid = str(int(a["activityId"]))
                try:
                    data = client.download_fit(a["activityId"])
                    if not garmin.is_fit(data):
                        raise garmin.GarminError("not a .fit file")
                except (garmin.KeyRejected, garmin.RateLimited):
                    raise
                except garmin.GarminError:
                    result["failed"] += 1      # tried again next refresh: it is not recorded as done
                    f = failures.setdefault(aid, {"date": (a.get("startTimeLocal") or today.isoformat())[:10],
                                                  "tries": 0})
                    f["tries"] += 1
                    save_state(root, state)
                    continue
                write_atomic(acts_dir / file_name(a), data)
                state["done_ids"].append(int(a["activityId"]))
                failures.pop(aid, None)
                counts["sessions"] += 1
                save_state(root, state)
                result["new_sessions"] += 1

            if not result["stopped"] and kinds:
                progress("Health data", 0, 0)
                rows = health.rows(client, health_from, today, kinds)
                if rows:
                    health_dir.mkdir(exist_ok=True)
                    write_atomic(health_dir / health_name(health_from, today, now), health_csv(rows))
                result["health_rows"] = len(rows)
                result["health_days"] = len({r[0][:10] for r in rows})
                counts["health_days"] = result["health_days"]

            if not result["stopped"]:
                state.update(pending=False, last_export=now.isoformat(timespec="seconds"))
                if sessions:
                    state["activities_through"] = today.isoformat()
                for k in kinds:
                    state["health_through"][k] = today.isoformat()
                save_state(root, state)
        except Exception as e:
            error = e
            save_state(root, state)
            raise
        finally:
            _log(root, now, result, error)
        return result


def _log(root: Path, now: dt.datetime, r: dict, error) -> None:
    """Counts only: never a value, a key or a token."""
    if error is not None:
        what = f"stopped: {type(error).__name__}"
    elif r["stopped"]:
        what = "stopped by you"
    else:
        what = "done"
    line = (f"{now:%Y-%m-%d %H:%M} {what}; {r['new_sessions']} new sessions, {r['failed']} failed, "
            f"{r['health_days']} days of health data\n")
    try:
        with open(root / LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass
