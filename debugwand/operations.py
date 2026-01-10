"""Shared operations and utilities for debugwand."""

import socket
import subprocess
import tempfile
from pathlib import Path

from debugwand.types import ProcessInfo

# ===== Port availability =====


def is_port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def find_process_using_port(port: int) -> tuple[int, str] | None:
    """Find the process using a specific port. Returns (pid, command) or None."""
    try:
        # Try lsof with LISTEN state first (works on macOS and Linux)
        result = subprocess.run(
            ["lsof", "-iTCP:" + str(port), "-sTCP:LISTEN", "-n", "-P"],
            capture_output=True,
            text=True,
            check=False,
        )

        # If no listener found, try without LISTEN filter (for ESTABLISHED connections)
        if result.returncode != 0 or not result.stdout.strip():
            result = subprocess.run(
                ["lsof", "-i", f":{port}", "-n", "-P"],
                capture_output=True,
                text=True,
                check=False,
            )

        if result.returncode == 0 and result.stdout.strip():
            lines = result.stdout.strip().split("\n")
            # Skip header line, take first actual result
            if len(lines) > 1:
                # Format: COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME
                parts = lines[1].split()
                if len(parts) >= 2:
                    try:
                        pid = int(parts[1])
                        command = parts[0]
                        # Get full command for this PID
                        ps_result = subprocess.run(
                            ["ps", "-p", str(pid), "-o", "command="],
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                        if ps_result.returncode == 0 and ps_result.stdout.strip():
                            return pid, ps_result.stdout.strip()
                        return pid, command
                    except (ValueError, IndexError):
                        pass
    except (FileNotFoundError, ValueError):
        pass

    # Fallback: try netstat (Windows and some Linux)
    try:
        result = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, check=False
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.split()
                    pid = int(parts[-1])
                    # On Windows, use tasklist to get command
                    task_result = subprocess.run(
                        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    if task_result.returncode == 0 and task_result.stdout:
                        command = task_result.stdout.split(",")[0].strip('"')
                        return pid, command
                    return pid, ""
    except (FileNotFoundError, ValueError):
        pass

    return None


def kill_process(pid: int) -> bool:
    """Kill a process by PID. Returns True if successful."""
    try:
        subprocess.run(["kill", str(pid)], check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        # Try Windows taskkill
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], check=True)
            return True
        except (FileNotFoundError, subprocess.CalledProcessError):
            return False


# ===== Process detection patterns =====

_HELPER_PROCESS_PATTERNS = [
    "multiprocessing.resource_tracker",
    "multiprocessing.spawn",
    "from multiprocessing",
]

_MAIN_PROCESS_INDICATORS = [
    "fastapi run",
    "gunicorn",
    "uvicorn",
    "flask run",
    "python -m",
    "python app.py",
    "python main.py",
]


def _is_helper_process(proc: ProcessInfo) -> bool:
    """Check if process is a known helper/internal process."""
    return any(pattern in proc.command for pattern in _HELPER_PROCESS_PATTERNS)


def _has_main_process_indicator(proc: ProcessInfo) -> bool:
    """Check if process has main process indicators."""
    return any(indicator in proc.command for indicator in _MAIN_PROCESS_INDICATORS)


def is_main_process(proc: ProcessInfo) -> bool:
    """Determine if a process is likely the main application process.

    Returns False for known helper processes, True for main process indicators or PID 1.
    """
    if _is_helper_process(proc):
        return False

    if proc.pid == 1:
        return True

    return _has_main_process_indicator(proc)


def detect_reload_mode(
    processes: list[ProcessInfo],
) -> tuple[bool, ProcessInfo | None]:
    """Detect if processes indicate reload mode (e.g., uvicorn --reload).

    Returns (is_reload_mode, worker_process).
    """
    # Look for parent process with --reload flag
    reload_parent = None
    for proc in processes:
        if "--reload" in proc.command and proc.pid == 1:
            reload_parent = proc
            break

    if not reload_parent:
        return False, None

    # Find the spawned worker process
    for proc in processes:
        if "multiprocessing.spawn" in proc.command and "spawn_main" in proc.command:
            return True, proc

    return True, None


def select_pid(processes: list[ProcessInfo]) -> int:
    """Select a PID from a list of processes, auto-selecting in reload mode."""
    if not processes:
        raise ValueError("No Python processes found.")

    is_reload, worker_proc = detect_reload_mode(processes)
    if is_reload and worker_proc:
        return worker_proc.pid
    elif is_reload and not worker_proc:
        print(f"\n⚠️  WARNING: Reload mode detected but couldn't find worker process.")
        print(f"You may need to manually select the correct PID.\n")

    main_processes = [p for p in processes if is_main_process(p)]

    if not main_processes:
        main_processes = processes

    if len(main_processes) == 1:
        return main_processes[0].pid

    print("Multiple Python processes found. Please select one:")
    for idx, proc in enumerate(main_processes):
        cmd_short = (
            proc.command[:60] + "..." if len(proc.command) > 60 else proc.command
        )

        # Highlight if it's PID 1 (likely main process) or a worker
        marker = ""
        if proc.pid == 1:
            marker = " [MAIN]"
        elif "multiprocessing.spawn" in proc.command and "spawn_main" in proc.command:
            marker = " [WORKER]"
        print(
            f"{idx + 1}: PID {proc.pid}{marker}, User: {proc.user}, CPU%: {proc.cpu_percent}, MEM%: {proc.mem_percent}, CMD: {cmd_short}"
        )
    selection = int(input("Enter the number of the PID to select: ")) - 1
    if selection < 0 or selection >= len(main_processes):
        raise ValueError("Invalid selection.")
    return main_processes[selection].pid


# ===== Debugpy script preparation =====


def prepare_debugpy_script(port: int, wait: bool = True) -> str:
    """Prepare the debugpy injection script with the given port and wait settings."""
    template_path = Path(__file__).parent / "debugpy_template.py"
    with open(template_path, "r") as f:
        script_content = f.read()

    script_content = script_content.replace("{PORT}", str(port)).replace(
        "{WAIT}", str(wait)
    )

    with tempfile.NamedTemporaryFile("w", delete=False) as tmpfile:
        tmpfile.write(script_content)
        return tmpfile.name
