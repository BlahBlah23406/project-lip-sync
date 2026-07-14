"""Central resolver for the ffmpeg/ffprobe binaries.

The pipeline used to hardcode the WinGet install path in two places. That breaks
whenever ffmpeg is upgraded (the version is in the path). Resolve once, prefer
PATH, fall back to the known WinGet location.
"""
import os
import shutil

_WINGET_BIN = (
    r"C:\Users\shaya\AppData\Local\Microsoft\WinGet\Packages"
    r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
    r"\ffmpeg-8.1.2-full_build\bin"
)


def _resolve(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    candidate = os.path.join(_WINGET_BIN, f"{name}.exe")
    if os.path.exists(candidate):
        return candidate
    raise FileNotFoundError(
        f"{name} not found on PATH or at {candidate}. Install ffmpeg (winget install Gyan.FFmpeg)."
    )


FFMPEG = _resolve("ffmpeg")
FFPROBE = _resolve("ffprobe")
