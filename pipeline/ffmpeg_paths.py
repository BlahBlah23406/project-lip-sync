"""Central resolver for the ffmpeg/ffprobe binaries.

Prefer PATH. Fall back to the WinGet install location, globbed rather than
pinned: the ffmpeg version is baked into that path, so a pinned one breaks on
every upgrade.
"""
import glob
import os
import shutil

_LOCALAPPDATA = os.environ.get("LOCALAPPDATA")
_WINGET_GLOB = (
    os.path.join(_LOCALAPPDATA, "Microsoft", "WinGet", "Packages",
                 "Gyan.FFmpeg_*", "ffmpeg-*-full_build", "bin")
    if _LOCALAPPDATA else None
)


def _resolve(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    if _WINGET_GLOB:
        # reverse-sorted so the newest installed version wins
        for bin_dir in sorted(glob.glob(_WINGET_GLOB), reverse=True):
            candidate = os.path.join(bin_dir, f"{name}.exe")
            if os.path.exists(candidate):
                return candidate
    raise FileNotFoundError(
        f"{name} not found on PATH. Install ffmpeg and make sure it is on PATH "
        "(Windows: winget install Gyan.FFmpeg)."
    )


FFMPEG = _resolve("ffmpeg")
FFPROBE = _resolve("ffprobe")
