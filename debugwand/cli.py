import os
import subprocess
import time
from pathlib import Path

import typer
from rich.console import Console

from debugwand.operations import (
    copy_to_pod,
    detect_reload_mode,
    exec_command_in_pod,
    find_process_using_port,
    find_replacement_pod,
    get_and_select_pod_handler,
    get_and_select_process_handler,
    get_pods_for_service_handler,
    is_port_available,
    kill_process,
    list_all_processes_with_details_handler,
    list_python_processes_with_details,
    monitor_worker_pid,
    prepare_debugpy_script,
    select_pid,
    select_pod,
    wait_for_new_pod,
)
from debugwand.types import PodInfo, ProcessInfo
from debugwand.ui import (
    print_connection_info,
    print_info,
    print_step,
    print_success,
    render_pods_table,
    render_processes_table,
)

app = typer.Typer()
console = Console()


@app.command(help="List pods in the specified namespace.")
def pods(
    namespace: str = typer.Option(
        ...,
        "--namespace",
        "-n",
        help="The namespace to list pods from.",
    ),
    service: str = typer.Option(
        ..., "--service", "-s", help="The service to filter pods by."
    ),
    with_pids: bool = typer.Option(
        False, "--with-pids", help="Also list Python processes in each pod."
    ),
):
    if with_pids:
        pod_list = get_pods_for_service_handler(namespace, service)

        # Collect all pod-process pairs
        pod_processes: list[tuple[PodInfo, list[ProcessInfo]]] = []
        for pod in pod_list:
            processes: list[ProcessInfo] | None = (
                list_all_processes_with_details_handler(pod)
            )
            if processes:
                pod_processes.append((pod, processes))

        # Render all pods and processes in a single grouped table
        if pod_processes:
            render_processes_table(pod_processes)
        else:
            typer.echo("❌ No running pods with Python processes found.", err=True)
            raise typer.Exit(code=1)
    else:
        pod_list = get_pods_for_service_handler(namespace, service)

        render_pods_table(pod_list)


@app.command(help="Inject and execute a script in a Python process within a pod.")
def inject(
    namespace: str = typer.Option(
        ..., "--namespace", "-n", help="The namespace to use."
    ),
    service: str = typer.Option(..., "--service", "-s", help="The service to use."),
    script: str = typer.Option(..., "--script", "-c", help="The script to execute."),
):
    typer.echo(f"Executing script '{script}' in the selected pod...")
    pod_list = get_pods_for_service_handler(namespace, service)
    pod = select_pod(pod_list)

    processes: list[ProcessInfo] | None = list_all_processes_with_details_handler(pod)
    if not processes:
        typer.echo(
            "❌ No running Python processes found in the selected pod.", err=True
        )
        raise typer.Exit(code=1)
    pid = select_pid(processes)

    # Copy the user's script into the pod
    script_basename = os.path.basename(script)
    copy_to_pod(pod, script, f"/tmp/{script_basename}")

    # Copy the attacher script into the pod (used to inject and run the user's script)
    attacher_path = Path(__file__).parent / "attacher.py"
    copy_to_pod(pod, str(attacher_path), "/tmp/attacher.py")

    exec_command_in_pod(
        pod=pod,
        command=[
            "python3",
            "/tmp/attacher.py",
            "--pid",
            str(pid),
            "--script",
            f"/tmp/{script_basename}",
        ],
    )


def _inject_debugpy_into_pod(pod: PodInfo, pid: int, script_path: str) -> None:
    """Inject a debugpy script into a specific process in a pod."""
    script_basename = os.path.basename(script_path)

    # Copy the attacher script into the pod
    attacher_path = Path(__file__).parent / "attacher.py"
    copy_to_pod(pod, str(attacher_path), "/tmp/attacher.py")

    # Copy debugpy script into the pod
    copy_to_pod(pod, script_path, f"/tmp/{script_basename}")

    print_step(
        f"Injecting debugpy into PID [cyan bold]{pid}[/cyan bold] in pod [blue]{pod.name}[/blue]..."
    )
    # Run injection in background (non-blocking)
    exec_command_in_pod(
        pod=pod,
        command=[
            "python3",
            "/tmp/attacher.py",
            "--pid",
            str(pid),
            "--script",
            f"/tmp/{script_basename}",
        ],
        background=True,
    )
    # Give debugpy time to start listening
    time.sleep(2)
    print_success(
        f"Debugpy ready in PID [cyan]{pid}[/cyan] in pod [blue]{pod.name}[/blue]"
    )
    print_info("App is running - connect your debugger anytime to hit breakpoints")


def _setup_port_forwarding(pod: PodInfo, port: int) -> subprocess.Popen[bytes] | None:
    """Set up kubectl port-forwarding to the pod. Returns the process or None if failed."""
    if not is_port_available(port):
        # Try to find the process using the port
        process_info = find_process_using_port(port)

        if process_info:
            pid, command = process_info
            # Check if it's a kubectl port-forward process
            if "kubectl" in command and "port-forward" in command:
                print_info(f"Found existing kubectl port-forward (PID {pid})")
                if typer.confirm(
                    "Kill the existing port-forward and continue?", default=True
                ):
                    if kill_process(pid):
                        print_success(f"Killed process {pid}")
                        time.sleep(0.5)  # Give the port time to be released
                        # Re-check if port is now available
                        if not is_port_available(port):
                            typer.echo(
                                f"⚠️ Port {port} is still in use after killing process.",
                                err=True,
                            )
                            return None
                    else:
                        typer.echo(f"❌ Failed to kill process {pid}", err=True)
                        return None
                else:
                    return None
            else:
                typer.echo(
                    f"⚠️ Port {port} is already in use by: {command} (PID {pid})",
                    err=True,
                )
                typer.echo(
                    f"Tip: Either kill PID {pid} or use a different port with --port",
                    err=True,
                )
                return None
        else:
            # Port is in use but we can't find the process
            # This can happen if the process just died but the port isn't released yet
            # Wait and retry (OS can take 5+ seconds to release ports on macOS)
            print_info(
                f"Port {port} is in use but process not found, waiting for port to be released..."
            )
            for attempt in range(10):  # Try for up to 10 seconds
                time.sleep(1)
                if is_port_available(port):
                    print_success("Port is now available")
                    # Continue to set up port forwarding below
                    break
                # Show progress every few seconds
                if attempt in [2, 5, 8]:
                    print_info(f"Still waiting... ({attempt + 1}s)")
            else:
                # All retries exhausted - try anyway, kubectl might succeed
                print_info(
                    f"Port {port} still not available after 10s, attempting port-forward anyway..."
                )
                # Continue to try setting up port-forward below

    print_step(f"Setting up port-forwarding on port [cyan]{port}[/cyan]...")
    port_forward_proc = subprocess.Popen(
        [
            "kubectl",
            "port-forward",
            pod.name,
            "-n",
            pod.namespace,
            f"{port}:{port}",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    time.sleep(2)

    if port_forward_proc.poll() is None:
        print_success(f"Port-forwarding established on port [cyan]{port}[/cyan]")
        return port_forward_proc
    else:
        typer.echo("❌ Failed to establish port-forwarding.", err=True)
        return None


def _monitor_and_handle_reload_mode(
    pod: PodInfo, pid: int, port: int, port_forward_proc: subprocess.Popen[bytes]
) -> tuple[int, bool]:
    """
    Monitor for worker PID changes in reload mode and handle reinjection.
    Returns (final_pid, should_break) where should_break indicates if we should exit the loop.
    """
    try:
        processes = list_python_processes_with_details(pod)
        is_reload, _ = detect_reload_mode(processes) if processes else (False, None)
    except Exception:
        # Pod might be slow to respond while debugger is attached, or pod died
        # Treat as non-reload mode and just wait for port-forward to exit
        port_forward_proc.wait()
        return pid, False

    if is_reload:
        print_info(
            "Reload mode detected - will auto-reinject debugpy on worker restarts"
        )

        # Monitor for worker PID changes while keeping port-forward alive
        while port_forward_proc.poll() is None:
            try:
                new_pid = monitor_worker_pid(pod, pid)
            except Exception as e:
                # Pod might have died or become unresponsive
                print_info(
                    f"Monitoring exception: {type(e).__name__}: {e}", prefix="❌"
                )
                return pid, False

            if new_pid is None:
                # Pod gone or monitoring failed, break and try to reconnect
                print_info(
                    "monitor_worker_pid returned None, triggering reconnect", prefix="⚠️"
                )
                return pid, False

            if new_pid != pid:
                # Worker restarted! Re-inject debugpy
                print_step(
                    f"Worker restarted (PID {pid} → {new_pid}), auto-reinjecting debugpy..."
                )
                try:
                    reinject_script_path = prepare_debugpy_script(port=port, wait=False)
                    reinject_basename = os.path.basename(reinject_script_path)
                    copy_to_pod(pod, reinject_script_path, f"/tmp/{reinject_basename}")

                    # Run injection in background (non-blocking)
                    exec_command_in_pod(
                        pod=pod,
                        command=[
                            "python3",
                            "/tmp/attacher.py",
                            "--pid",
                            str(new_pid),
                            "--script",
                            f"/tmp/{reinject_basename}",
                        ],
                        background=True,
                    )

                    # Give debugpy time to start listening before prompting user
                    time.sleep(2)

                    try:
                        os.unlink(reinject_script_path)
                    except Exception:
                        pass

                    pid = new_pid
                    print_success(f"Debugpy reinjected into new worker (PID {pid})")
                    print_info("Worker is running - reconnect your debugger to continue debugging")
                except Exception as e:
                    print_info(f" Failed to re-inject debugpy: {e}", prefix="❌")
                    return pid, False

            time.sleep(2)
    else:
        # No reload mode, just wait for port-forward to exit
        port_forward_proc.wait()

    return pid, False


def _attempt_reconnect(
    pod: PodInfo, service: str, namespace: str
) -> tuple[PodInfo | None, int | None]:
    """
    Try to reconnect to a new pod after connection loss.
    Returns (new_pod, new_pid) or (None, None) if failed.
    """
    print_step("Connection lost, attempting to reconnect...")
    try:
        new_pod = find_replacement_pod(pod, service, namespace)
        if not new_pod:
            print_info("Could not find replacement pod, waiting...")
            new_pod = wait_for_new_pod(service, namespace)

        new_pid = get_and_select_process_handler(pod=new_pod, pid=None)
        print_success(f"Reconnected to new pod: {new_pod.name}")
        print_info("App is running - connect your debugger to continue debugging")
        return new_pod, new_pid
    except (TimeoutError, Exception) as e:
        print_info(f"Failed to reconnect: {e}", prefix="❌")
        return None, None


def _cleanup_injected_files(pod: PodInfo, script_basename: str) -> None:
    """Clean up temporary files injected into the pod."""
    try:
        print_step("Cleaning up injected files in the pod...", prefix="🧹")
        exec_command_in_pod(
            pod=pod,
            command=["rm", "-f", f"/tmp/{script_basename}", "/tmp/attacher.py"],
            silent_errors=True,
        )
    except Exception:
        # Silently ignore cleanup errors (pod may be gone)
        pass


@app.command(help="Start remote debugging session in a Python process within a pod.")
def debug(
    namespace: str = typer.Option(
        ..., "--namespace", "-n", help="The namespace to use."
    ),
    service: str = typer.Option(..., "--service", "-s", help="The service to use."),
    port: int = typer.Option(
        5679, "--port", "-p", help="The local port to forward for debugging."
    ),
    pid: int = typer.Option(
        None,
        "--pid",
        help="The PID of the Python process to debug. If not provided, you will be prompted to select.",
    ),
):
    pod = get_and_select_pod_handler(service=service, namespace=namespace)
    pid = get_and_select_process_handler(pod=pod, pid=pid)

    # Prepare debugpy script on local filesystem (wait=False means app continues immediately)
    temp_script_path = prepare_debugpy_script(port=port, wait=False)
    script_basename = os.path.basename(temp_script_path)

    port_forward_proc = None
    try:
        # Main connection and injection loop
        while True:
            try:
                # Inject debugpy into the target process
                _inject_debugpy_into_pod(pod, pid, temp_script_path)

                # Set up port-forwarding
                port_forward_proc = _setup_port_forwarding(pod, port)
                if not port_forward_proc:
                    return

                print_connection_info(port)

                # Monitor for reload mode and handle worker restarts
                try:
                    pid, should_break = _monitor_and_handle_reload_mode(
                        pod, pid, port, port_forward_proc
                    )
                    if should_break:
                        break
                except KeyboardInterrupt:
                    print_step("Stopping port-forwarding...", prefix="🔧")
                    port_forward_proc.terminate()
                    port_forward_proc.wait()
                    port_forward_proc = None
                    print_info(" Port-forwarding stopped.")
                    break
                except Exception as e:
                    print_info(
                        f"Unexpected error in monitoring: {type(e).__name__}: {e}",
                        prefix="❌",
                    )
                    # Don't break, try to reconnect
                    pass

                # Clean up the old port-forward process
                if port_forward_proc:
                    if port_forward_proc.poll() is None:
                        # Process still running, terminate it
                        port_forward_proc.terminate()
                        port_forward_proc.wait()
                    port_forward_proc = None
                    # Always wait for OS to release the port (can take several seconds)
                    # This is needed even if process already died
                    time.sleep(2)

                # Attempt to reconnect to a new pod
                new_pod, new_pid = _attempt_reconnect(pod, service, namespace)
                if not new_pod or not new_pid:
                    break

                pod = new_pod
                pid = new_pid

            except KeyboardInterrupt:
                # Handle Ctrl+C anywhere in the loop
                print_step("Stopping port-forwarding...", prefix="🔧")
                if port_forward_proc and port_forward_proc.poll() is None:
                    port_forward_proc.terminate()
                    port_forward_proc.wait()
                print_info("Port-forwarding stopped.")
                raise

    finally:
        # Cleanup after exiting the loop - ALWAYS kill port-forward
        if port_forward_proc and port_forward_proc.poll() is None:
            try:
                port_forward_proc.terminate()
                port_forward_proc.wait()
            except Exception:
                pass

        try:
            os.unlink(temp_script_path)
        except Exception:
            pass

        _cleanup_injected_files(pod, script_basename)


if __name__ == "__main__":
    app()
