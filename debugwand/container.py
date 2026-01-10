"""Debugging support for containers."""

import os
import subprocess
import time
from pathlib import Path

import typer

from debugwand.operations import detect_reload_mode, prepare_debugpy_script, select_pid
from debugwand.types import ProcessInfo
from debugwand.ui import print_connection_info, print_info, print_step, print_success


def monitor_worker_pid(container: str, initial_pid: int) -> int | None:
    """Monitor for worker PID changes in a container.
    Returns None if monitoring should stop (e.g., container gone or error).
    Returns initial_pid if no change detected yet.
    Returns new_pid if worker restarted.
    """
    try:
        processes = list_python_processes(container)
        if not processes:
            return None

        is_reload, worker_proc = detect_reload_mode(processes)
        if not is_reload:
            # No longer in reload mode, stop monitoring
            return None

        if not worker_proc:
            # Worker process temporarily not found (might be frozen at breakpoint,
            # or in transition during restart). Keep monitoring.
            return initial_pid

        if worker_proc.pid != initial_pid:
            # Worker PID changed! Return new PID
            return worker_proc.pid

        # PID hasn't changed yet
        return initial_pid
    except Exception:
        # Container might be gone or other error, stop monitoring
        return None


def list_python_processes(container: str) -> list[ProcessInfo]:
    """List Python processes in a container."""
    cmd = ["docker", "exec", container, "ps", "aux"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    processes: list[ProcessInfo] = []
    for line in result.stdout.splitlines():
        if "python" in line.lower():
            parts = line.split(None, 10)
            if len(parts) >= 11:
                processes.append(
                    ProcessInfo(
                        pid=int(parts[1]),
                        user=parts[0],
                        cpu_percent=float(parts[2]),
                        mem_percent=float(parts[3]),
                        command=parts[10],
                    )
                )
    return processes


def exec_command(
    container: str, command: list[str], capture_output: bool = True
) -> subprocess.CompletedProcess[str]:
    """Execute a command in a container."""
    cmd = ["docker", "exec", container] + command
    return subprocess.run(cmd, capture_output=capture_output, text=True)


def copy_file(container: str, local_path: str, remote_path: str) -> None:
    """Copy a file into a container."""
    subprocess.run(
        ["docker", "cp", local_path, f"{container}:{remote_path}"],
        check=True,
        capture_output=True,
    )


def inject_debugpy(container: str, pid: int, script_path: str) -> None:
    """Inject debugpy into a process in a container."""
    script_basename = os.path.basename(script_path)

    # Copy the attacher script into the container
    attacher_path = Path(__file__).parent / "attacher.py"
    copy_file(container, str(attacher_path), "/tmp/attacher.py")

    # Copy debugpy script into the container
    copy_file(container, script_path, f"/tmp/{script_basename}")

    print_step(
        f"Injecting debugpy into PID [cyan bold]{pid}[/cyan bold] in container [blue]{container}[/blue]..."
    )

    # Run injection synchronously to capture errors
    result = exec_command(
        container,
        [
            "python3",
            "/tmp/attacher.py",
            "--pid",
            str(pid),
            "--script",
            f"/tmp/{script_basename}",
        ],
    )

    if result.returncode != 0:
        output = result.stdout + result.stderr
        if "CAP_SYS_PTRACE" in output or "Permission denied" in output:
            typer.echo("\n❌ Permission denied: Cannot attach to process.", err=True)
            typer.echo(
                "   On Linux/containers, you need CAP_SYS_PTRACE capability.", err=True
            )
            typer.echo(
                "   Run your container with: docker run --cap-add=SYS_PTRACE ...",
                err=True,
            )
            typer.echo(
                "   Or in docker-compose.yml:",
                err=True,
            )
            typer.echo("     cap_add:", err=True)
            typer.echo("       - SYS_PTRACE", err=True)
            typer.echo(
                "\n   See: https://docs.python.org/3/howto/remote_debugging.html",
                err=True,
            )
            raise typer.Exit(1)
        else:
            typer.echo(f"\n❌ Failed to inject debugpy: {output}", err=True)
            raise typer.Exit(1)

    # Give debugpy time to start listening
    time.sleep(2)
    print_success(
        f"Debugpy ready in PID [cyan]{pid}[/cyan] in container [blue]{container}[/blue]"
    )
    print_info("App is running - connect your debugger anytime to hit breakpoints")


def debug(container: str, port: int, pid: int | None) -> None:
    """Debug a Python process in a container."""
    # List Python processes in the container
    try:
        processes = list_python_processes(container)
    except subprocess.CalledProcessError as e:
        typer.echo(f"❌ Failed to list processes in container: {e.stderr}", err=True)
        raise typer.Exit(1)

    if not processes:
        typer.echo("❌ No Python processes found in container.", err=True)
        raise typer.Exit(1)

    # Select PID
    if pid is None:
        if len(processes) == 1:
            pid = processes[0].pid
            print_info(f"Auto-selecting only Python process: PID {pid}")
        else:
            # Let user select
            pid = select_pid(processes)
    else:
        # Verify PID exists
        if not any(p.pid == pid for p in processes):
            typer.echo(f"❌ PID {pid} not found in container.", err=True)
            raise typer.Exit(1)

    # Prepare debugpy script
    temp_script_path = prepare_debugpy_script(port=port, wait=False)

    try:
        # Inject debugpy
        inject_debugpy(container, pid, temp_script_path)

        print_connection_info(port)
        print_info(
            f"Note: Ensure the container exposes port {port} (e.g., ports: ['{port}:{port}'])"
        )

        # Check for reload mode and monitor for worker restarts
        try:
            is_reload, _ = detect_reload_mode(processes)
            if is_reload:
                print_info(
                    "Reload mode detected - will auto-reinject debugpy on worker restarts"
                )

                while True:
                    try:
                        new_pid = monitor_worker_pid(container, pid)
                    except Exception as e:
                        print_info(
                            f"Monitoring exception: {type(e).__name__}: {e}",
                            prefix="❌",
                        )
                        break

                    if new_pid is None:
                        # Container gone or no longer in reload mode
                        print_info("Container no longer available or reload mode ended.")
                        break

                    if new_pid != pid:
                        # Worker restarted! Re-inject debugpy
                        print_step(
                            f"Worker restarted (PID {pid} → {new_pid}), auto-reinjecting debugpy..."
                        )
                        try:
                            reinject_script_path = prepare_debugpy_script(
                                port=port, wait=False
                            )
                            inject_debugpy(container, new_pid, reinject_script_path)
                            try:
                                os.unlink(reinject_script_path)
                            except Exception:
                                pass
                            pid = new_pid
                            print_info(
                                "Worker is running - reconnect your debugger to continue debugging"
                            )
                        except Exception as e:
                            print_info(f"Failed to re-inject debugpy: {e}", prefix="❌")
                            break

                    time.sleep(2)
            else:
                # No reload mode, just wait for Ctrl+C
                while True:
                    time.sleep(1)
        except KeyboardInterrupt:
            print_info("Exiting.")

    finally:
        try:
            os.unlink(temp_script_path)
        except Exception:
            pass
