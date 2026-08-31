"""Watch YouTube channels and auto-dub their new uploads into the LipSync plaza.

    python watcher.py bootstrap          # mark today's feed as seen (run once, first)
    python watcher.py check              # one poll+reconcile cycle (what the scheduler runs)
    python watcher.py check --dry-run    # show what it would do, launch nothing
    python watcher.py status             # what is queued / running / done / deferred / skipped
    python watcher.py enqueue <ID> ...   # hand-add a video, bypassing the feed filters

Design
------
This is a POLLED STATE MACHINE, not a daemon. Every invocation is short, does one
reconcile pass, and exits; all state lives in watcher_state.json. That is
deliberate: a long-lived watcher process dies at reboot, at logoff, and at every
agent tool-call timeout, and then nobody notices for a week. A scheduled task
firing a 20-second script cannot rot the same way, and a missed tick costs
nothing because the next tick re-derives everything from disk.

The dub itself is NOT run here. `launch_pipeline.py` detaches it via WMI so it
outlives this process (a 20-minute episode takes 30-60 minutes to dub). This
script only decides what to start and notices what finished.

Flow per video:
    RSS entry -> probe (yt-dlp) -> filters -> queued -> launched -> running
              -> done   (manifest.json exists with real coverage) -> manifest
                         enriched with the real title/channel -> plaza card
              -> failed (stalled past the retry budget)
              -> deferred (live/premiere right now; re-probed on a backoff until
                           it settles into a plain VOD)
              -> skipped (short/too long/no English captions, or still not a VOD
                          after the whole re-probe budget)

Adding to the plaza is not a separate publish step: /api/plaza scans
output/*/manifest.json, so a finished run is already listed. What the watcher
adds is the metadata the pipeline cannot know -- the human title, the channel,
the source URL -- patched into the manifest so the card reads like a lecture and
not like a video ID.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

# The feeds carry curly quotes and the logs carry Bangla. Windows' cp1252 stdout
# raises UnicodeEncodeError on both, and a monitoring tool that crashes on a
# title is worse than no monitoring tool.
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

os.chdir(os.path.dirname(os.path.abspath(__file__)))
PROJECT_DIR = os.getcwd()
OUTPUT_DIR = os.path.join(PROJECT_DIR, "output")
LOG_DIR = os.path.join(PROJECT_DIR, "logs")
CONFIG_PATH = os.path.join(PROJECT_DIR, "watcher_config.json")
STATE_PATH = os.path.join(PROJECT_DIR, "watcher_state.json")
EVENT_LOG = os.path.join(PROJECT_DIR, "watcher_events.jsonl")
LOCK_PATH = os.path.join(PROJECT_DIR, "watcher.lock")

FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}

DEFAULT_CONFIG = {
    "channels": [
        {
            "id": "UC3vHW2h22WE-pNi5WJtRIjg",
            "name": "Yaqeen Institute",
            "handle": "@yaqeeninstituteofficial",
            "enabled": True,
        },
        {
            "id": "UClUa7-iHJNKEM2e_zWYSPQg",
            "name": "Yasir Qadhi",
            "handle": "@YasirQadhi",
            "enabled": True,
        },
    ],
    # Master switch. False = keep watching and queueing, but never launch a dub.
    "auto_process": True,
    # Dubs are serial by design: parallel runs just deepen Edge-TTS throttling,
    # which is the single most common reason a run dies.
    "max_concurrent": 1,
    "max_new_per_check": 2,
    "max_launches_per_day": 4,
    # Shorts are noise and multi-hour podcasts cost hours of CPU and real API
    # money. Both ends are filtered, not silently attempted. Calibrated
    # 2026-07-23 against the last 15 uploads of both channels: Yaqeen runs
    # 0-27 min (8 of 15 are Shorts under 3 min), Yasir Qadhi 0-77 min with a
    # 27-min median. 180s drops the Shorts; 5400s keeps his longest lectures,
    # which a 70-min ceiling would have silently thrown away.
    "min_duration_seconds": 180,
    "max_duration_seconds": 5400,
    "require_english_captions": True,
    "skip_live": True,
    # Live content is never dubbed while it is live -- but "live" is a phase, not
    # a verdict. YouTube reports a *premiere* as live_status=="is_live" for as
    # long as it plays, so treating the first probe as final buried 27 perfectly
    # good lectures under status="skipped" where nothing ever looked at them
    # again. Live/upcoming records are parked as "deferred" and re-probed on this
    # cadence until they settle into an ordinary VOD.
    "live_recheck_minutes": 90,
    # 16 x 90min ~= 24h, which covers a premiere scheduled a day out. Past that
    # it is a genuine 24/7 stream or a cancelled premiere; skip it for real so
    # the queue does not grow a permanent tail of yt-dlp calls.
    "live_recheck_max_attempts": 16,
    # Ignore anything published before the watcher existed even if it is still in
    # the feed window; bootstrap sets this.
    "ignore_published_before": None,
    "max_attempts": 2,
    # A live run touches .work/progress.json constantly. Silence for this long
    # with no completed manifest means it died without saying so.
    "stall_minutes": 45,
    "supervise": True,
    # Optional: argv list run after each successful dub. {video_id} {title}
    # {url} {channel} are substituted. e.g.
    # ["node", "C:/Users/shaya/.openclaw/scripts/deliver-output.mjs", "{path}"]
    "notify_command": None,
}


# --- small durable-state helpers -------------------------------------------------

def write_json_atomic(path: str, data) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        write_json_atomic(CONFIG_PATH, DEFAULT_CONFIG)
        print(f"Wrote default config to {CONFIG_PATH}")
        return json.loads(json.dumps(DEFAULT_CONFIG))
    with open(CONFIG_PATH, encoding="utf-8-sig") as f:
        cfg = json.load(f)
    # Merge forward: a config written by an older version must not lose new keys.
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    return cfg


def load_state() -> dict:
    if not os.path.exists(STATE_PATH):
        return {"version": 1, "bootstrapped_at": None, "channels": {}, "videos": {}, "launches": {}}
    with open(STATE_PATH, encoding="utf-8-sig") as f:
        state = json.load(f)
    state.setdefault("channels", {})
    state.setdefault("videos", {})
    state.setdefault("launches", {})
    return state


def save_state(state: dict) -> None:
    write_json_atomic(STATE_PATH, state)


def log_event(kind: str, **fields) -> None:
    rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": kind}
    rec.update(fields)
    with open(EVENT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def acquire_lock() -> bool:
    """Refuse to run two watchers at once. Stale locks (dead PID) are taken over."""
    if os.path.exists(LOCK_PATH):
        try:
            pid = int(open(LOCK_PATH, encoding="utf-8").read().strip())
        except Exception:
            pid = None
        if pid and pid_alive(pid) and pid != os.getpid():
            return False
    with open(LOCK_PATH, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    return True


def release_lock() -> None:
    try:
        os.remove(LOCK_PATH)
    except OSError:
        pass


def pid_alive(pid: int) -> bool:
    out = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True
    ).stdout
    return str(pid) in out


# --- YouTube feed ---------------------------------------------------------------

def fetch_feed(channel_id: str, timeout: int = 30) -> list[dict]:
    """Return the channel's recent uploads, newest first.

    Uses the public RSS feed rather than the Data API: no key, no quota, and no
    OAuth token to expire silently six days from now.
    """
    req = urllib.request.Request(
        FEED_URL.format(channel_id),
        headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    root = ET.fromstring(raw)

    author = root.findtext("atom:author/atom:name", default="", namespaces=NS).strip()
    entries = []
    for entry in root.findall("atom:entry", NS):
        video_id = entry.findtext("yt:videoId", namespaces=NS)
        if not video_id:
            continue
        thumb = entry.find("media:group/media:thumbnail", NS)
        entries.append({
            "video_id": video_id,
            "title": (entry.findtext("atom:title", default="", namespaces=NS) or "").strip(),
            "published": entry.findtext("atom:published", default="", namespaces=NS),
            "updated": entry.findtext("atom:updated", default="", namespaces=NS),
            "channel_id": channel_id,
            "channel": author,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "thumbnail": thumb.get("url") if thumb is not None else None,
        })
    entries.sort(key=lambda e: e.get("published") or "", reverse=True)
    return entries


def probe_video(video_id: str) -> dict:
    """Duration / liveness / caption availability, straight from yt-dlp.

    The RSS feed carries none of these, and every one of them is a reason to not
    spend an hour of CPU and a translation bill on a video.
    """
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noprogress": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)

    subs = set(info.get("subtitles") or {})
    autos = set(info.get("automatic_captions") or {})
    has_en = any(k == "en" or k.startswith("en-") or k.startswith("en.") for k in subs | autos)
    return {
        "duration": info.get("duration"),
        "title": info.get("title"),
        "channel": info.get("channel") or info.get("uploader"),
        "channel_id": info.get("channel_id"),
        "thumbnail": info.get("thumbnail"),
        "live_status": info.get("live_status"),
        "is_live": bool(info.get("is_live")),
        "was_live": bool(info.get("was_live")),
        "has_english_captions": has_en,
        "manual_english_captions": any(k.startswith("en") for k in subs),
        "upload_date": info.get("upload_date"),
    }


# --- pipeline liveness (same definitions run_until_done.py uses) -----------------

def work_dir(video_id: str) -> str:
    return os.path.join(OUTPUT_DIR, video_id, ".work")


def phase_of(video_id: str) -> str | None:
    p = os.path.join(work_dir(video_id), "progress.json")
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8-sig") as f:
            return json.load(f).get("phase")
    except Exception:
        return None


def read_manifest(video_id: str) -> dict | None:
    p = os.path.join(OUTPUT_DIR, video_id, "manifest.json")
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return None


def is_complete(video_id: str) -> bool:
    """Done means the pipeline said done AND the manifest proves real coverage.

    Coverage is the guard that catches a 'finished' 3-minute dub of a 20-minute
    lecture. Never treat phase=='done' alone as success.
    """
    if phase_of(video_id) != "done":
        return False
    manifest = read_manifest(video_id)
    if not manifest:
        return False
    return manifest.get("coverage", 0) >= 0.5


def running_pid(video_id: str) -> int | None:
    pid_file = os.path.join(work_dir(video_id), "pid")
    if not os.path.exists(pid_file):
        return None
    try:
        pid = int(open(pid_file, encoding="utf-8").read().strip())
    except Exception:
        return None
    return pid if pid_alive(pid) else None


def minutes_since_progress(video_id: str) -> float | None:
    """Age of the run's heartbeat. progress.json is rewritten constantly by a
    live pipeline, so its mtime is the cheapest liveness signal available -- and
    it survives the gap between a supervisor's retry attempts, which a PID does
    not."""
    p = os.path.join(work_dir(video_id), "progress.json")
    if not os.path.exists(p):
        return None
    return (time.time() - os.path.getmtime(p)) / 60.0


def launch_pipeline(video_id: str, supervise: bool) -> tuple[bool, str]:
    argv = [sys.executable, "-u", os.path.join(PROJECT_DIR, "launch_pipeline.py"), video_id]
    if supervise:
        argv.append("--supervise")
    try:
        res = subprocess.run(argv, cwd=PROJECT_DIR, capture_output=True, text=True, timeout=180)
    except Exception as e:
        return False, f"launcher error: {e}"
    out = ((res.stdout or "") + (res.stderr or "")).strip()
    return res.returncode == 0, out[-800:]


# --- plaza metadata -------------------------------------------------------------

def enrich_manifest(video_id: str, meta: dict) -> bool:
    """Give the finished dub a human identity in the plaza.

    run_pipeline.py has no idea what the video is called -- it only ever sees an
    11-character ID -- so /api/plaza falls back to showing the ID as the title.
    Patching the manifest is what turns a row of opaque IDs into a library.
    Additive only: never touch the coverage/timing fields the audit tools read.
    """
    path = os.path.join(OUTPUT_DIR, video_id, "manifest.json")
    manifest = read_manifest(video_id)
    if manifest is None:
        return False
    manifest["title"] = meta.get("title") or manifest.get("title") or video_id
    manifest["channel"] = meta.get("channel")
    manifest["channel_id"] = meta.get("channel_id")
    manifest["published_at"] = meta.get("published")
    manifest["source_url"] = meta.get("url") or f"https://www.youtube.com/watch?v={video_id}"
    manifest["thumbnail"] = meta.get("thumbnail")
    manifest["added_by"] = "watcher"
    write_json_atomic(path, manifest)
    return True


def notify(cfg: dict, video_id: str, meta: dict) -> None:
    cmd = cfg.get("notify_command")
    if not cmd:
        return
    subs = {
        "video_id": video_id,
        "title": meta.get("title") or video_id,
        "channel": meta.get("channel") or "",
        "url": meta.get("url") or f"https://www.youtube.com/watch?v={video_id}",
        "path": os.path.join(OUTPUT_DIR, video_id, f"{video_id}_dubbed.mp4"),
    }
    argv = [str(a).format(**subs) for a in cmd]
    try:
        subprocess.run(argv, cwd=PROJECT_DIR, capture_output=True, text=True, timeout=300)
    except Exception as e:
        log_event("notify_failed", video_id=video_id, error=str(e))


# --- the reconcile pass ---------------------------------------------------------

def today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def launches_today(state: dict) -> int:
    return int(state.get("launches", {}).get(today_key(), 0))


def count_running(state: dict) -> int:
    return sum(1 for v in state["videos"].values() if v.get("status") == "running")


def live_state(probe: dict) -> str | None:
    """"live", "upcoming", or None for an ordinary finished VOD.

    Note that a premiere is indistinguishable from a real stream here: while it
    plays, yt-dlp reports live_status=="is_live" for both. That is precisely why
    neither one may be judged on a single probe.
    """
    status = probe.get("live_status")
    if probe.get("is_live") or status == "is_live":
        return "live"
    if status == "is_upcoming":
        return "upcoming"
    return None


def classify(entry: dict, probe: dict, cfg: dict) -> tuple[str, str]:
    """Return (decision, reason) where decision is "accept", "defer" or "skip".

    "defer" is not a rejection -- it means ask again later. Duration and captions
    are settled facts about a finished video, so failing them is final; being
    live is a temporary condition and must not be recorded as if it were final.
    """
    if cfg["skip_live"]:
        live = live_state(probe)
        if live:
            return "defer", f"live/upcoming ({probe.get('live_status')})"
    dur = probe.get("duration")
    if dur is None:
        return "skip", "no duration reported"
    if dur < cfg["min_duration_seconds"]:
        return "skip", f"too short ({int(dur)}s < {cfg['min_duration_seconds']}s)"
    if dur > cfg["max_duration_seconds"]:
        return "skip", f"too long ({int(dur)}s > {cfg['max_duration_seconds']}s)"
    if cfg["require_english_captions"] and not probe.get("has_english_captions"):
        return "skip", "no English captions"
    return "accept", "accepted"


def defer_until(cfg: dict, now: datetime) -> str:
    return (now + timedelta(minutes=cfg["live_recheck_minutes"])).isoformat(timespec="seconds")


def reprobe_due(rec: dict, now: datetime) -> bool:
    """A deferred record with a missing or unparseable timestamp is treated as
    due: the
    failure mode of re-probing one video too early is one wasted yt-dlp call,
    and the failure mode of never re-probing it is the bug this replaced."""
    at = rec.get("recheck_after")
    if not at:
        return True
    try:
        return now >= datetime.fromisoformat(at)
    except (TypeError, ValueError):
        return True


def discover(cfg: dict, state: dict, verbose: bool = True) -> list[dict]:
    """Poll every feed and record videos we have not seen before."""
    fresh = []
    cutoff = cfg.get("ignore_published_before")
    for ch in cfg["channels"]:
        if not ch.get("enabled", True):
            continue
        try:
            entries = fetch_feed(ch["id"])
        except Exception as e:
            print(f"  ! feed failed for {ch['name']}: {e}")
            log_event("feed_error", channel=ch["name"], error=str(e))
            continue
        chan_state = state["channels"].setdefault(ch["id"], {"name": ch["name"]})
        chan_state["name"] = ch["name"]
        chan_state["last_checked"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        chan_state["feed_size"] = len(entries)
        if verbose:
            print(f"  {ch['name']}: {len(entries)} entries in feed")
        for entry in entries:
            vid = entry["video_id"]
            if vid in state["videos"]:
                continue
            if cutoff and entry.get("published") and entry["published"] <= cutoff:
                state["videos"][vid] = {**entry, "status": "skipped", "reason": "published before watcher start"}
                continue
            entry["channel"] = entry.get("channel") or ch["name"]
            state["videos"][vid] = {**entry, "status": "new", "first_seen": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            fresh.append(state["videos"][vid])
            log_event("discovered", video_id=vid, title=entry["title"], channel=entry["channel"])
    fresh.sort(key=lambda e: e.get("published") or "", reverse=True)
    return fresh


def reconcile(cfg: dict, state: dict) -> None:
    """Bring recorded status back in line with what is actually on disk."""
    for vid, rec in state["videos"].items():
        status = rec.get("status")
        if status not in ("queued", "running"):
            continue
        # Nothing to reconcile for a video that has never been launched -- it has
        # no process and no heartbeat by definition, and the stall check below
        # would otherwise report a freshly queued video as having died.
        if not rec.get("launched_at"):
            continue

        if is_complete(vid):
            manifest = read_manifest(vid) or {}
            rec["status"] = "done"
            rec["completed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            rec["coverage"] = manifest.get("coverage")
            if enrich_manifest(vid, rec):
                rec["plaza"] = True
            print(f"  + finished: {rec.get('title') or vid} (coverage {rec.get('coverage')})")
            log_event("completed", video_id=vid, title=rec.get("title"), coverage=rec.get("coverage"))
            notify(cfg, vid, rec)
            continue

        pid = running_pid(vid)
        idle = minutes_since_progress(vid)
        if pid or (idle is not None and idle < cfg["stall_minutes"]):
            rec["status"] = "running"
            rec["phase"] = phase_of(vid)
            rec["idle_minutes"] = round(idle, 1) if idle is not None else None
            continue

        # No live process and no heartbeat: the run died without saying so.
        attempts = rec.get("attempts", 0)
        if attempts >= cfg["max_attempts"]:
            rec["status"] = "failed"
            rec["reason"] = f"stalled after {attempts} attempt(s); phase={phase_of(vid)}"
            print(f"  ! failed: {rec.get('title') or vid} — {rec['reason']}")
            log_event("failed", video_id=vid, title=rec.get("title"), reason=rec["reason"])
        else:
            rec["status"] = "queued"
            rec["reason"] = f"stalled at phase={phase_of(vid)}, will retry"
            print(f"  ~ stalled, requeued: {rec.get('title') or vid}")
            log_event("stalled", video_id=vid, attempts=attempts, phase=phase_of(vid))


def triage_new(cfg: dict, state: dict, dry_run: bool) -> None:
    """Probe every new -- and every due deferred -- video and decide queue vs skip.

    Deferred records are the second half of the live fix: discover() ignores any
    video_id it has already seen, so if triage does not come back for these
    nobody ever will.
    """
    now = datetime.now(timezone.utc)
    pending = [
        (v, r) for v, r in state["videos"].items()
        if r.get("status") == "new"
        or (r.get("status") == "deferred" and reprobe_due(r, now))
    ]
    pending.sort(key=lambda kv: kv[1].get("published") or "", reverse=True)
    for vid, rec in pending:
        was_deferred = rec.get("status") == "deferred"
        try:
            probe = probe_video(vid)
        except Exception as e:
            # A probe error is a transport failure, not a verdict. Keep a
            # deferred record deferred and push its next check out by one
            # interval; dropping it to "new" would retry it on every single tick.
            if was_deferred:
                rec["recheck_after"] = defer_until(cfg, now)
            else:
                rec["status"] = "new"
            rec["reason"] = f"probe failed: {e}"
            print(f"  ? probe failed for {vid}: {e}")
            log_event("probe_error", video_id=vid, error=str(e))
            continue
        rec.update({
            "duration": probe.get("duration"),
            "has_english_captions": probe.get("has_english_captions"),
            "live_status": probe.get("live_status"),
        })
        rec["title"] = probe.get("title") or rec.get("title")
        rec["channel"] = probe.get("channel") or rec.get("channel")
        rec["thumbnail"] = probe.get("thumbnail") or rec.get("thumbnail")

        decision, reason = classify(rec, probe, cfg)
        if decision == "accept":
            rec["status"] = "queued"
            rec["reason"] = "accepted"
            rec.pop("recheck_after", None)
            was = " (was deferred)" if was_deferred else ""
            print(f"  + queued: {rec['title']} [{int(rec['duration'] or 0)//60}m]{was}")
            log_event("queued", video_id=vid, title=rec["title"],
                      duration=rec.get("duration"), from_deferred=was_deferred)
        elif decision == "defer":
            checks = rec.get("live_checks", 0) + 1
            rec["live_checks"] = checks
            if checks >= cfg["live_recheck_max_attempts"]:
                rec["status"] = "skipped"
                rec["reason"] = f"{reason}; still not a VOD after {checks} re-probes"
                rec.pop("recheck_after", None)
                print(f"  - skipped: {rec['title']} — {rec['reason']}")
                log_event("skipped", video_id=vid, title=rec["title"], reason=rec["reason"])
            else:
                rec["status"] = "deferred"
                rec["recheck_after"] = defer_until(cfg, now)
                rec["reason"] = reason
                print(f"  ~ deferred: {rec['title']} — {reason}, re-probe after"
                      f" {rec['recheck_after']} ({checks}/{cfg['live_recheck_max_attempts']})")
                log_event("deferred", video_id=vid, title=rec["title"], reason=reason,
                          live_checks=checks, recheck_after=rec["recheck_after"])
        else:
            rec["status"] = "skipped"
            rec["reason"] = reason
            rec.pop("recheck_after", None)
            print(f"  - skipped: {rec['title']} — {reason}")
            log_event("skipped", video_id=vid, title=rec["title"], reason=reason)


def launch_due(cfg: dict, state: dict, dry_run: bool) -> None:
    if not cfg.get("auto_process", True):
        print("  auto_process is off — leaving the queue alone.")
        return

    slots = cfg["max_concurrent"] - count_running(state)
    if slots <= 0:
        print(f"  {count_running(state)} dub(s) already running — nothing new started.")
        return

    remaining_today = cfg["max_launches_per_day"] - launches_today(state)
    budget = min(slots, cfg["max_new_per_check"], remaining_today)
    if budget <= 0:
        print(f"  daily launch cap reached ({launches_today(state)}/{cfg['max_launches_per_day']}).")
        return

    queued = [(v, r) for v, r in state["videos"].items() if r.get("status") == "queued"]
    queued.sort(key=lambda kv: kv[1].get("published") or "")  # oldest queued first
    for vid, rec in queued[:budget]:
        if dry_run:
            print(f"  [dry-run] would launch {vid} — {rec.get('title')}")
            continue
        ok, out = launch_pipeline(vid, cfg["supervise"])
        rec["attempts"] = rec.get("attempts", 0) + 1
        rec["launched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if ok:
            rec["status"] = "running"
            state["launches"][today_key()] = launches_today(state) + 1
            print(f"  > launched: {rec.get('title') or vid}")
            log_event("launched", video_id=vid, title=rec.get("title"), attempt=rec["attempts"])
        else:
            rec["status"] = "queued"
            rec["reason"] = f"launch failed: {out[:200]}"
            print(f"  ! launch failed for {vid}: {out[:200]}")
            log_event("launch_error", video_id=vid, error=out[:400])


# --- commands -------------------------------------------------------------------

def cmd_check(args) -> int:
    cfg = load_config()
    state = load_state()
    if state.get("bootstrapped_at") is None and not args.force:
        print("Not bootstrapped yet. Run:  python watcher.py bootstrap")
        print("(That marks the current feed as seen so the watcher does not dub the whole backlog.)")
        return 2

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] LipSync watcher check"
          + (" (dry run)" if args.dry_run else ""))

    print("Polling feeds…")
    discover(cfg, state)

    print("Reconciling in-flight runs…")
    reconcile(cfg, state)

    print("Triaging new uploads…")
    triage_new(cfg, state, args.dry_run)

    print("Launching…")
    launch_due(cfg, state, args.dry_run)

    if not args.dry_run:
        save_state(state)
    print_summary(state)
    return 0


def cmd_bootstrap(args) -> int:
    """Take a baseline so the watcher only ever acts on genuinely NEW uploads.

    Without this the first check would find 30 videos in the two feeds and try to
    dub all of them -- hours of CPU and a translation bill for a backlog nobody
    asked for. --backfill N intentionally keeps the N most recent as work.
    """
    cfg = load_config()
    state = load_state()

    already = 0
    if os.path.isdir(OUTPUT_DIR):
        for entry in os.listdir(OUTPUT_DIR):
            if os.path.isdir(os.path.join(OUTPUT_DIR, entry)) and read_manifest(entry):
                state["videos"].setdefault(entry, {
                    "video_id": entry, "title": entry, "status": "done",
                    "reason": "already dubbed before the watcher existed",
                })
                already += 1

    seen = 0
    kept = []
    for ch in cfg["channels"]:
        if not ch.get("enabled", True):
            continue
        entries = fetch_feed(ch["id"])
        print(f"  {ch['name']}: {len(entries)} in feed")
        for i, entry in enumerate(entries):
            vid = entry["video_id"]
            if vid in state["videos"] and state["videos"][vid].get("status") == "done":
                continue
            if i < args.backfill:
                entry["channel"] = entry.get("channel") or ch["name"]
                state["videos"][vid] = {**entry, "status": "new"}
                kept.append(entry["title"])
                continue
            state["videos"][vid] = {**entry, "status": "skipped", "reason": "backlog at bootstrap"}
            seen += 1
        state["channels"][ch["id"]] = {
            "name": ch["name"],
            "last_checked": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "feed_size": len(entries),
        }

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    state["bootstrapped_at"] = now
    cfg["ignore_published_before"] = now
    write_json_atomic(CONFIG_PATH, cfg)
    save_state(state)
    log_event("bootstrap", backlog_marked=seen, already_dubbed=already, backfill=len(kept))

    print(f"\nBootstrapped at {now}")
    print(f"  {already} already-dubbed video(s) recorded as done")
    print(f"  {seen} backlog video(s) marked seen (will NOT be dubbed)")
    if kept:
        print(f"  {len(kept)} kept for processing:")
        for t in kept:
            print(f"    - {t}")
    print("\nFrom now on only uploads published after this moment are picked up.")
    return 0


def cmd_enqueue(args) -> int:
    cfg = load_config()
    state = load_state()
    for raw in args.video_ids:
        m = re.search(r"([A-Za-z0-9_-]{11})", raw)
        if not m:
            print(f"  ! not a video id/url: {raw}")
            continue
        vid = m.group(1)
        rec = state["videos"].get(vid, {"video_id": vid})
        rec.update({
            "video_id": vid,
            "url": f"https://www.youtube.com/watch?v={vid}",
            "status": "queued",
            "reason": "hand-queued",
            "attempts": 0,
        })
        try:
            probe = probe_video(vid)
            rec["title"] = probe.get("title") or vid
            rec["channel"] = probe.get("channel")
            rec["duration"] = probe.get("duration")
            rec["thumbnail"] = probe.get("thumbnail")
        except Exception as e:
            print(f"  ? probe failed for {vid}: {e}")
        state["videos"][vid] = rec
        print(f"  + queued {vid}: {rec.get('title')}")
        log_event("hand_queued", video_id=vid, title=rec.get("title"))
    save_state(state)
    print("\nRun `python watcher.py check` (or wait for the scheduled tick) to start it.")
    return 0


def print_summary(state: dict) -> None:
    buckets: dict[str, list] = {}
    for vid, rec in state["videos"].items():
        buckets.setdefault(rec.get("status", "?"), []).append((vid, rec))
    order = ["running", "queued", "new", "deferred", "done", "failed", "skipped"]
    print("\nStatus:", ", ".join(
        f"{k}={len(buckets.get(k, []))}" for k in order if buckets.get(k)
    ) or "empty")
    for k in ("running", "queued", "failed"):
        for vid, rec in buckets.get(k, []):
            extra = rec.get("phase") or rec.get("reason") or ""
            print(f"  [{k}] {vid}  {rec.get('title') or ''}  {extra}")


def cmd_status(args) -> int:
    cfg = load_config()
    state = load_state()
    print(f"Watcher: {'ARMED' if cfg.get('auto_process') else 'PAUSED (auto_process=false)'}"
          f" | bootstrapped {state.get('bootstrapped_at')}")
    for ch in cfg["channels"]:
        cs = state["channels"].get(ch["id"], {})
        print(f"  {ch['name']:<22} last checked {cs.get('last_checked', 'never')}"
              f" ({'on' if ch.get('enabled', True) else 'off'})")
    print(f"  launches today: {launches_today(state)}/{cfg['max_launches_per_day']}")

    # Live phase for anything actually in flight.
    for vid, rec in state["videos"].items():
        if rec.get("status") == "running":
            idle = minutes_since_progress(vid)
            rec["phase"] = phase_of(vid)
            rec["idle_minutes"] = round(idle, 1) if idle is not None else None
    print_summary(state)

    done = [(v, r) for v, r in state["videos"].items() if r.get("status") == "done" and r.get("plaza")]
    if done:
        print(f"\nIn the plaza via watcher ({len(done)}):")
        for vid, rec in sorted(done, key=lambda kv: kv[1].get("completed_at") or "", reverse=True)[:10]:
            print(f"  {rec.get('completed_at', '')[:16]}  {rec.get('title') or vid}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Watch YouTube channels and auto-dub new uploads.")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="one poll + reconcile + launch cycle")
    c.add_argument("--dry-run", action="store_true", help="decide but launch nothing")
    c.add_argument("--force", action="store_true", help="run even if not bootstrapped")
    c.set_defaults(func=cmd_check)

    b = sub.add_parser("bootstrap", help="mark the current feed as seen (run once)")
    b.add_argument("--backfill", type=int, default=0,
                   help="also process the N most recent uploads per channel")
    b.set_defaults(func=cmd_bootstrap)

    s = sub.add_parser("status", help="show what is queued/running/done")
    s.set_defaults(func=cmd_status)

    e = sub.add_parser("enqueue", help="hand-queue a video id or URL")
    e.add_argument("video_ids", nargs="+")
    e.set_defaults(func=cmd_enqueue)

    args = p.parse_args()

    if not acquire_lock():
        print("Another watcher run is in progress; exiting.")
        return 0
    try:
        return args.func(args)
    finally:
        release_lock()


if __name__ == "__main__":
    raise SystemExit(main())
