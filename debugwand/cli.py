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
    find_replacement_pod,
    get_and_select_pod_handler,
    get_and_select_process_handler,
    get_pods_for_service_handler,
    is_port_available,
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

    script_basename = os.path.basename(script)
    copy_to_pod(pod, script, f"/tmp/{script_basename}")

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


@app.command(help="Start remote debugging session in a Python process within a pod.")
def debug(
    namespace: str = typer.Option(
        ..., "--namespace", "-n", help="The namespace to use."
    ),
    service: str = typer.Option(..., "--service", "-s", help="The service to use."),
    port: int = typer.Option(
        5679, "--port", "-p", help="The local port to forward for debugging."
    ),
    auto_forward: bool = typer.Option(
        True,
        "--auto-forward/--no-auto-forward",
        help="Automatically set up port-forwarding.",
    ),
    pid: int = typer.Option(
        None,
        "--pid",
        help="The PID of the Python process to debug. If not provided, you will be prompted to select.",
    ),
    auto_reconnect: bool = typer.Option(
        True,
        "--auto-reconnect/--no-auto-reconnect",
        help="Automatically attempt to reconnect if the debugging session is lost.",
    ),
):
    pod = get_and_select_pod_handler(service=service, namespace=namespace)
    pid = get_and_select_process_handler(pod=pod, pid=pid)

    # Prepare debugpy script on local filesystem
    temp_script_path = prepare_debugpy_script(port=port, wait=True)
    script_basename = os.path.basename(temp_script_path)

    while True:
        try:
            copy_to_pod(pod, temp_script_path, f"/tmp/{script_basename}")

            attacher_path = Path(__file__).parent / "attacher.py"
            copy_to_pod(pod, str(attacher_path), "/tmp/attacher.py")

            print_step(
                f"Injecting debugpy into PID [cyan bold]{pid}[/cyan bold] in pod [blue]{pod.name}[/blue]..."
            )
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
            print_success(
                f"Successfully injected debugpy into PID [cyan]{pid}[/cyan] in pod [blue]{pod.name}[/blue]"
            )

            port_forward_proc = None
            if auto_forward:
                if not is_port_available(port):
                    typer.echo(f"⚠️ Port {port} is already in use.", err=True)
                    typer.echo(
                        f"Tip: Either kill the process using port {port} or use a different port with --port",
                        err=True,
                    )
                    typer.echo(
                        f"You can manually set up port-forwarding with:\n  kubectl port-forward {pod.name} -n {pod.namespace} {port}:{port}"
                    )
                    return
                else:
                    print_step(
                        f"Setting up port-forwarding on port [cyan]{port}[/cyan]..."
                    )
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
                        print_success(
                            f"Port-forwarding established on port [cyan]{port}[/cyan]"
                        )
                    else:
                        typer.echo("❌ Failed to establish port-forwarding.", err=True)
                        port_forward_proc = None

                print_connection_info(port, service)

            if port_forward_proc:
                # Monitor for both port-forward exit AND worker PID changes (if --reload mode)
                try:
                    # Check if we're in reload mode (uvicorn --reload creates parent + worker)
                    processes = list_python_processes_with_details(pod)
                    is_reload, _ = (
                        detect_reload_mode(processes) if processes else (False, None)
                    )

                    if is_reload:
                        print_info(
                            "🔄 Reload mode detected - will auto-reinject debugpy on worker restarts"
                        )

                        # Monitor for worker PID changes while keeping port-forward alive
                        while port_forward_proc.poll() is None:
                            new_pid = monitor_worker_pid(pod, pid)

                            if new_pid is None:
                                # Pod gone or monitoring failed, break and try to reconnect
                                break
                            elif new_pid != pid:
                                # Worker restarted! Re-inject debugpy
                                print_step(
                                    f"🔄 Worker restarted (PID {pid} → {new_pid}), auto-reinjecting debugpy..."
                                )
                                try:
                                    reinject_script_path = prepare_debugpy_script(
                                        port=port, wait=False
                                    )
                                    reinject_basename = os.path.basename(
                                        reinject_script_path
                                    )
                                    copy_to_pod(
                                        pod,
                                        reinject_script_path,
                                        f"/tmp/{reinject_basename}",
                                    )

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
                                    )

                                    try:
                                        os.unlink(reinject_script_path)
                                    except Exception:
                                        pass

                                    pid = new_pid
                                    print_success(
                                        f"✅ Debugpy reinjected into new worker (PID {pid})"
                                    )
                                    print_info(
                                        "💡 Press F5 in your editor to reconnect the debugger"
                                    )
                                except Exception as e:
                                    print_info(f"❌ Failed to re-inject debugpy: {e}")
                                    break

                            time.sleep(2)
                    else:
                        # No reload mode, just wait for port-forward to exit
                        port_forward_proc.wait()

                except KeyboardInterrupt:
                    print_step("Stopping port-forwarding...")
                    port_forward_proc.terminate()
                    port_forward_proc.wait()
                    print_info("Port-forwarding stopped.")
                    break

            # If we get here, port-forward exited (pod likely died)
            # Clean up the old port-forward process
            if port_forward_proc and port_forward_proc.poll() is None:
                port_forward_proc.terminate()
                port_forward_proc.wait()

            if not auto_reconnect:
                break

            # Try to reconnect
            print_step("🔄 Connection lost, attempting to reconnect...")
            try:
                new_pod = find_replacement_pod(pod, service, namespace)
                if not new_pod:
                    print_info("⚠️  Could not find replacement pod, waiting...")
                    new_pod = wait_for_new_pod(service, namespace)

                pod = new_pod
                pid = get_and_select_process_handler(pod=pod, pid=None)
                print_success(f"✅ Reconnected to new pod: {pod.name}")
                print_info("💡 Press F5 in your editor to reconnect the debugger")
            except (TimeoutError, Exception) as e:
                print_info(f"❌ Failed to reconnect: {e}")
                break

        finally:
            # Clean up temporary script file (only once when we exit the loop)
            pass

    # Cleanup after exiting the loop
    try:
        os.unlink(temp_script_path)
    except Exception:
        pass

    try:
        print_step("Cleaning up injected files in the pod...", prefix="🧹")
        exec_command_in_pod(
            pod=pod,
            command=[
                "rm",
                "-f",
                f"/tmp/{script_basename}",
                "/tmp/attacher.py",
            ],
            silent_errors=True,
        )
    except Exception:
        # Silently ignore cleanup errors (pod may be gone)
        pass


if __name__ == "__main__":
    app()
