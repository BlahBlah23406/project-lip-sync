"""Central yt-dlp JavaScript-runtime configuration.

Why this file exists (2026-09-01)
---------------------------------
Every watcher launch between late July and today died the same way:

    reason: "stalled after 2 attempt(s); phase=download"

...which reads like a network stall but was not one. YouTube now requires
solving a JavaScript challenge (the `n` signature) before it will serve media
bytes. yt-dlp delegates that to an *external* JS runtime and, as of the
2026.08.x releases, enables only `deno` by default. Deno is not installed on
this host, so:

  * extraction still succeeded -- title, duration and format list all came back
    looking perfectly healthy, which is why nothing upstream noticed; but
  * the format URLs were un-deciphered, so the moment the downloader asked for
    actual bytes YouTube answered `HTTP Error 403: Forbidden`.

That split is the whole reason the failure surfaced as `phase=download` rather
than as an extractor error, and why re-running never helped -- there was nothing
transient about it.

The real warning was in every log, one line above the traceback:

    WARNING: [youtube] No supported JavaScript runtime could be found.
             Only deno is enabled by default; to use another runtime add
             --js-runtimes RUNTIME[:PATH] to your command/config.

Node is already on this box (it is what OpenClaw itself runs on) and yt-dlp
supports it as a first-class runtime, so enabling it costs nothing to install.

Detection is done at call time rather than pinning "node" so that the pipeline
picks up a better runtime automatically if one is ever installed, and so that a
host without any of them fails with a legible warning instead of another
mystery 403.
"""

import shutil

# yt-dlp's own priority order, highest first. Keep in step with the runtimes in
# yt_dlp/extractor/youtube/jsc/_builtin/.
SUPPORTED_RUNTIMES = ("deno", "node", "quickjs", "bun")

_warned = False


def detect_runtimes() -> dict:
    """Return a yt-dlp `js_runtimes` dict of every supported runtime on PATH.

    Empty if none is available; callers should treat that as a hard problem, not
    a silent fallback -- an empty mapping is what produces the 403 above.
    """
    return {name: {} for name in SUPPORTED_RUNTIMES if shutil.which(name)}


def apply(opts: dict) -> dict:
    """Attach the detected JS runtimes to a yt-dlp options dict, in place.

    Returns the same dict so it can be used inline at a `YoutubeDL(...)` call.
    """
    global _warned

    runtimes = detect_runtimes()
    if runtimes:
        opts["js_runtimes"] = runtimes
    elif not _warned:
        # Leave yt-dlp's own default in place -- overriding it with {} would only
        # swap one silent failure for another. Say plainly what is about to break
        # so the next person reads a cause instead of a 403.
        _warned = True
        print(
            "  WARNING: no JavaScript runtime found (looked for: "
            f"{', '.join(SUPPORTED_RUNTIMES)}). YouTube downloads will almost "
            "certainly fail with HTTP 403. Install Node or Deno and re-run.",
            flush=True,
        )
    return opts
