"""Offline proof of the premiere/defer state machine. No network, no disk writes.

Simulates the exact record shape that got stuck: a premiere probed while it
reports live_status=="is_live", then re-probed later once it is a plain VOD.
"""
import sys
from datetime import datetime, timedelta, timezone

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import watcher

CFG = watcher.load_config()
NOW = datetime.now(timezone.utc)
fails = []


def check(label, got, want):
    ok = got == want
    print(("  PASS  " if ok else "  FAIL  ") + f"{label}: got {got!r}, want {want!r}")
    if not ok:
        fails.append(label)


print("1. classify() decisions")
check("premiere playing (is_live)",
      watcher.classify({}, {"live_status": "is_live", "is_live": True, "duration": 1480,
                            "has_english_captions": True}, CFG)[0], "defer")
check("scheduled premiere (is_upcoming)",
      watcher.classify({}, {"live_status": "is_upcoming", "duration": None}, CFG)[0], "defer")
check("finished VOD",
      watcher.classify({}, {"live_status": "not_live", "duration": 1480,
                            "has_english_captions": True}, CFG)[0], "accept")
check("was_live VOD (premiere that ended)",
      watcher.classify({}, {"live_status": "was_live", "was_live": True, "duration": 1480,
                            "has_english_captions": True}, CFG)[0], "accept")
check("too short is still a hard skip",
      watcher.classify({}, {"live_status": "not_live", "duration": 60}, CFG)[0], "skip")
check("no captions is still a hard skip",
      watcher.classify({}, {"live_status": "not_live", "duration": 1480,
                            "has_english_captions": False}, CFG)[0], "skip")

print("\n2. skip_live=False still bypasses the live check entirely")
off = dict(CFG, skip_live=False)
check("live + skip_live off",
      watcher.classify({}, {"live_status": "is_live", "is_live": True, "duration": 1480,
                            "has_english_captions": True}, off)[0], "accept")

print("\n3. reprobe_due()")
check("no timestamp -> due", watcher.reprobe_due({}, NOW), True)
check("past timestamp -> due",
      watcher.reprobe_due({"recheck_after": (NOW - timedelta(minutes=1)).isoformat()}, NOW), True)
check("future timestamp -> not due",
      watcher.reprobe_due({"recheck_after": (NOW + timedelta(hours=2)).isoformat()}, NOW), False)
check("garbage timestamp -> due", watcher.reprobe_due({"recheck_after": "soon"}, NOW), True)

print("\n4. full triage lifecycle (probe_video stubbed)")
VID = "_wGdHNAqCO8"
state = {"videos": {VID: {"video_id": VID, "title": "Hamna bint Jahsh (ra)",
                          "status": "new", "published": "2026-07-27T14:00:06+00:00"}},
         "channels": {}, "launches": {}}

watcher.probe_video = lambda v: {"live_status": "is_live", "is_live": True, "duration": 1480,
                                 "title": "Hamna bint Jahsh (ra)", "has_english_captions": True}
watcher.triage_new(CFG, state, dry_run=True)
rec = state["videos"][VID]
check("premiere -> deferred", rec["status"], "deferred")
check("live_checks recorded", rec["live_checks"], 1)
check("recheck_after set", bool(rec.get("recheck_after")), True)

print("   -- second tick, still live but NOT yet due --")
watcher.triage_new(CFG, state, dry_run=True)
check("not re-probed early", state["videos"][VID]["live_checks"], 1)

print("   -- backdate the timer; premiere has now become a VOD --")
rec["recheck_after"] = (NOW - timedelta(minutes=1)).isoformat(timespec="seconds")
watcher.probe_video = lambda v: {"live_status": "was_live", "was_live": True, "duration": 1480,
                                 "title": "Hamna bint Jahsh (ra)", "has_english_captions": True}
watcher.triage_new(CFG, state, dry_run=True)
check("VOD -> queued", state["videos"][VID]["status"], "queued")
check("recheck_after cleared", "recheck_after" in state["videos"][VID], False)

print("\n5. re-probe budget is bounded (never defers forever)")
state2 = {"videos": {"aaaaaaaaaaa": {"video_id": "aaaaaaaaaaa", "title": "24/7 stream",
                                     "status": "new"}}, "channels": {}, "launches": {}}
watcher.probe_video = lambda v: {"live_status": "is_live", "is_live": True, "duration": None,
                                 "title": "24/7 stream", "has_english_captions": True}
for _ in range(CFG["live_recheck_max_attempts"] + 2):
    state2["videos"]["aaaaaaaaaaa"].pop("recheck_after", None)  # force every tick due
    watcher.triage_new(CFG, state2, dry_run=True)
r2 = state2["videos"]["aaaaaaaaaaa"]
check("exhausted budget -> skipped", r2["status"], "skipped")
check("capped at max_attempts", r2["live_checks"], CFG["live_recheck_max_attempts"])

print("\n" + ("ALL TESTS PASSED" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
