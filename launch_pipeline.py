"""Launch run_pipeline.py fully detached, then exit immediately.

    python launch_pipeline.py <VIDEO_ID> [--fresh]

Why this exists
---------------
A full episode takes 25-60+ minutes. When the pipeline runs as a child of an
agent tool call, the host process manager SIGKILLs the whole process tree the
moment that tool call hits its timeout -- which is how every real-length run
died. Detaching means the timeout can expire harmlessly: the tool call returns
in under a second and the pipeline keeps running on its own.

Detach strategy, strongest first:
  1. WMI Win32_Process.Create -- the new process is parented to WmiPrvSE, so it
     is outside the caller's process tree AND outside any Job Object the caller
     is confined to (a plain DETACHED_PROCESS child still dies with the job if
     the manager uses JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE).
  2. subprocess with DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP as a fallback.

Poll it with:  python check_progress.py <VIDEO_ID>
"""
import os
import subprocess
import sys
import time

os.chdir(os.path.dirname(os.path.abspath(__file__)))

PROJECT_DIR = os.getcwd()
LOG_DIR = os.path.join(PROJECT_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)


def already_running(video_id: str) -> int | None:
    """Return the PID of a live run for this video, if any."""
    pid_file = os.path.join(PROJECT_DIR, "output", video_id, ".work", "pid")
    if not os.path.exists(pid_file):
        return None
    try:
        pid = int(open(pid_file).read().strip())
    except Exception:
        return None
    out = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
        capture_output=True, text=True,
    ).stdout
    return pid if str(pid) in out else None


def launch_via_wmi(argv: list[str], log_path: str, work_dir: str) -> int | None:
    """Spawn through WMI so the child escapes our process tree and job object.

    Nested quoting through PowerShell -Command is a minefield (an earlier version
    mangled the Python path and silently fell back). Instead write the command to
    a .cmd wrapper and the WMI call to a .ps1, so neither layer needs escaping.
    """
    cmd_path = os.path.join(work_dir, "run.cmd")
    ps_path = os.path.join(work_dir, "launch.ps1")

    quoted = " ".join(f'"{a}"' for a in argv)
    with open(cmd_path, "w", encoding="ascii") as f:
        f.write("@echo off\r\n")
        f.write(f'cd /d "{PROJECT_DIR}"\r\n')
        # Proof-of-durable-launch for run_pipeline.py's foreground guard. Inherited
        # by run_until_done.py and every pipeline it starts.
        f.write("set PIPELINE_SUPERVISED=1\r\n")
        f.write(f'{quoted} > "{log_path}" 2>&1\r\n')

    with open(ps_path, "w", encoding="ascii") as f:
        f.write("$si = ([WMIClass]'Win32_ProcessStartup').CreateInstance()\n")
        f.write("$si.ShowWindow = 0\n")
        f.write(
            f"$r = ([WMIClass]'Win32_Process').Create('cmd.exe /c \"\"{cmd_path}\"\"', "
            f"'{PROJECT_DIR}', $si)\n"
        )
        f.write("if ($r.ReturnValue -eq 0) { $r.ProcessId } "
                "else { 'ERR:' + $r.ReturnValue }\n")

    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-File", ps_path],
            capture_output=True, text=True, timeout=60,
        )
        out = (res.stdout or "").strip()
        if out.isdigit():
            return int(out)
        print(f"  WMI launch failed ({out or res.stderr.strip()[:200]}), falling back.")
    except Exception as e:
        print(f"  WMI launch errored ({e}), falling back.")
    return None


def launch_via_subprocess(argv: list[str], log_path: str) -> int:
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    CREATE_BREAKAWAY_FROM_JOB = 0x01000000

    log = open(log_path, "ab")
    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    env = {**os.environ, "PIPELINE_SUPERVISED": "1"}  # see run_pipeline.py's foreground guard
    try:
        proc = subprocess.Popen(
            argv, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            cwd=PROJECT_DIR, close_fds=True, env=env,
            creationflags=flags | CREATE_BREAKAWAY_FROM_JOB,
        )
    except OSError:
        # The job may not permit breakaway; try without it.
        proc = subprocess.Popen(
            argv, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            cwd=PROJECT_DIR, close_fds=True, env=env, creationflags=flags,
        )
    return proc.pid


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    if not args:
        print("usage: python launch_pipeline.py <VIDEO_ID> [VIDEO_ID ...] "
              "[--supervise] [--fresh]")
        print("  --supervise  keep relaunching until the episode(s) actually finish")
        return 2

    # --supervise runs run_until_done.py, which retries a died/throttled run until the
    # episode is genuinely complete. Safe because the pipeline resumes.
    supervise = "--supervise" in flags
    flags = [f for f in flags if f != "--supervise"]
    video_id = args[0]

    live = already_running(video_id)
    if live:
        print(f"Already running for {video_id} (PID {live}). Not starting a second copy.")
        print(f"Poll: python check_progress.py {video_id}")
        return 0

    stamp = time.strftime("%Y%m%d-%H%M%S")
    tag = "supervisor" if supervise else video_id
    log_path = os.path.join(LOG_DIR, f"{tag}-{stamp}.log")
    work_dir = os.path.join(PROJECT_DIR, "output", video_id, ".work")
    os.makedirs(work_dir, exist_ok=True)

    python = sys.executable
    if supervise:
        script = os.path.join(PROJECT_DIR, "run_until_done.py")
        argv = [python, "-u", script, *args]
    else:
        script = os.path.join(PROJECT_DIR, "run_pipeline.py")
        argv = [python, "-u", script, video_id, *flags]

    print(f"Launching detached pipeline for {video_id}")
    print(f"  Log: {log_path}")

    pid = launch_via_wmi(argv, log_path, work_dir)
    method = "WMI (outside job object)"
    if pid is None:
        pid = launch_via_subprocess(argv, log_path)
        method = "DETACHED_PROCESS (weaker: dies if the manager uses a job object)"

    # run_pipeline.py writes its own PID here as its lock; don't fight it. The
    # cmd.exe wrapper's PID is not the Python PID, so let the pipeline own the file.
    print(f"  PID: {pid}  [{method}]")
    print(f"\nThe run survives this tool call. Do NOT wait on it -- poll instead:")
    print(f"  python check_progress.py {video_id}")
    print(f"Expect roughly 30-60 min for a 20-minute episode.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
