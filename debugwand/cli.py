import os
import subprocess
import time
from pathlib import Path

import typer
from rich.console import Console

from debugwand.operations import (
    copy_to_pod,
    exec_command_in_pod,
    get_and_select_pod,
    get_and_select_process,
    get_pods_for_service,
    is_port_available,
    list_python_processes_with_details,
    prepare_debugpy_script,
    select_pid,
    select_pod,
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
    # List pods and optionally their Python processes
    if with_pids:
        try:
            pod_list = get_pods_for_service(service=service, namespace=namespace)
        except ValueError as e:
            typer.echo(f"❌ {e}", err=True)
            raise typer.Exit(code=1)

        if not pod_list:
            typer.echo("❌ No pods found matching the criteria.", err=True)
            raise typer.Exit(code=1)

        # Collect all pod-process pairs
        pod_processes: list[tuple[PodInfo, list[ProcessInfo]]] = []
        for pod in pod_list:
            if pod.status != "Running":
                typer.echo(
                    f"❌ Pod '{pod.name}' is not running (skipping process list)",
                    err=True,
                )
                continue

            try:
                processes = list_python_processes_with_details(pod)
            except subprocess.CalledProcessError as e:
                typer.echo(
                    f"❌ Failed to list processes in pod '{pod.name}': {e.stderr.strip()}",
                    err=True,
                )
                continue
            except Exception as e:
                typer.echo(f"❌ Error accessing pod '{pod.name}': {e}", err=True)
                continue

            if not processes:
                typer.echo(
                    f"❌ No Python processes found in pod '{pod.name}'.", err=True
                )
                continue

            pod_processes.append((pod, processes))

        # Render all pods and processes in a single grouped table
        if pod_processes:
            render_processes_table(pod_processes)
        else:
            typer.echo("❌ No running pods with Python processes found.", err=True)
            raise typer.Exit(code=1)
    else:
        try:
            pod_list = get_pods_for_service(service=service, namespace=namespace)
        except ValueError as e:
            typer.echo(f"❌ {e}", err=True)
            raise typer.Exit(code=1)

        if not pod_list:
            typer.echo("❌ No pods found matching the criteria.", err=True)
            raise typer.Exit(code=1)

        render_pods_table(pod_list)


@app.command(help="Inject and execute a script in a Python process within a pod.")
def inject(
    namespace: str = typer.Option(
        ..., "--namespace", "-n", help="The namespace to use."
    ),
    service: str = typer.Option(..., "--service", "-s", help="The service to use."),
    script: str = typer.Option(..., "--script", "-c", help="The script to execute."),
):
    pod_list = get_pods_for_service(service=service, namespace=namespace)
    typer.echo(f"Executing script '{script}' in the selected pod...")
    if not pod_list:
        typer.echo("No pods found matching the criteria.", err=True)
        raise typer.Exit(code=1)

    pod = select_pod(pod_list)

    processes = list_python_processes_with_details(pod)
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
):
    # Select pod
    try:
        pod: PodInfo = get_and_select_pod(service=service, namespace=namespace)
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1)

    # Select PID
    try:
        pid = get_and_select_process(pod, pid)
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1)

    # Prepare debugpy script
    temp_script_path = prepare_debugpy_script(port=port, wait=True)

    try:
        script_basename = os.path.basename(temp_script_path)
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
            # Check if port is available before attempting port-forward
            if not is_port_available(port):
                typer.echo(f"⚠️  Port {port} is already in use.", err=True)
                typer.echo(
                    f"Tip: Either kill the process using port {port} or use a different port with --port",
                    err=True,
                )
                typer.echo(
                    f"You can manually set up port-forwarding with:\n  kubectl port-forward {pod.name} -n {pod.namespace} {port}:{port}"
                )
                return
            else:
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
                    print_success(
                        f"Port-forwarding established on port [cyan]{port}[/cyan]"
                    )
                else:
                    typer.echo("❌ Failed to establish port-forwarding.", err=True)
                    port_forward_proc = None

            # Show connection instructions
            print_connection_info(port, service)

        if port_forward_proc:
            try:
                port_forward_proc.wait()
            except KeyboardInterrupt:
                print_step("Stopping port-forwarding...")
                port_forward_proc.terminate()
                port_forward_proc.wait()
                print_info("Port-forwarding stopped.")
    finally:
        os.unlink(temp_script_path)


if __name__ == "__main__":
    app()
