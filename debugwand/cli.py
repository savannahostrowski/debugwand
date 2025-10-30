import os
import subprocess
import time
from pathlib import Path

import typer

from debugwand.k8s import (
    PodInfo,
    copy_to_pod,
    exec_command_in_pod,
    get_pods_for_service,
    list_python_processes_with_details,
    select_pid,
    select_pod,
)
from debugwand.operations import (
    get_and_select_pod,
    get_and_select_process,
    prepare_debugpy_script,
)

app = typer.Typer()


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
    typer.echo(f"Finding pods for service '{service}' in namespace '{namespace}'...")

    if with_pids:
        typer.echo("Listing Python processes in each pod:")
        pod_list = get_pods_for_service(service=service, namespace=namespace)
        if not pod_list:
            typer.echo("No pods found matching the criteria.", err=True)
            raise typer.Exit(code=1)
        for pod in pod_list:
            typer.echo(
                f"\nPod: {pod.name}, Namespace: {pod.namespace}, Status: {pod.status}"
            )
            if pod.status != "Running":
                typer.echo(f"  Pod is not running (skipping process list)", err=True)
                continue

            processes = list_python_processes_with_details(pod)
            if not processes:
                typer.echo("  No Python processes found.", err=True)
                continue

            for proc in processes:
                typer.echo(
                    f"  PID: {proc.pid}, User: {proc.user}, CPU%: {proc.cpu_percent}, MEM%: {proc.mem_percent}, CMD: {proc.command}"
                )
    else:
        pod_list = get_pods_for_service(service=service, namespace=namespace)

        if not pod_list:
            typer.echo("No pods found matching the criteria.", err=True)
            raise typer.Exit(code=1)

        for pod in pod_list:
            typer.echo(
                f"Pod: {pod.name}, Namespace: {pod.namespace}, Status: {pod.status}"
            )


@app.command(help="Validate that pods have CAP_SYS_PTRACE capability.")
def validate(
    namespace: str = typer.Option(
        None, "--namespace", "-n", help="The namespace to use."
    ),
    service: str = typer.Option(None, "--service", "-s", help="The service to use."),
):
    if not namespace:
        typer.echo("Error: --namespace is required.", err=True)
        raise typer.Exit(code=1)

    if not service:
        typer.echo("Error: --service is required.", err=True)
        raise typer.Exit(code=1)

    # Check to see if pods have CAP_SYS_PTRACE
    typer.echo(f"Validating pods for service '{service}' in namespace '{namespace}'...")
    pod_list = get_pods_for_service(service=service, namespace=namespace)

    if not pod_list:
        typer.echo("No pods found matching the criteria.", err=True)
        raise typer.Exit(code=1)

    for pod in pod_list:
        typer.echo(f"Validating pod '{pod.name}'...")

        # Try capsh first (most reliable if available)
        try:
            capabilities = exec_command_in_pod(pod, command=["capsh", "--print"])
            has_ptrace = "cap_sys_ptrace" in capabilities.lower()
        except subprocess.CalledProcessError:
            # Fallback: check /proc/1/status (works without additional tools)
            typer.echo("  capsh not found, using /proc fallback...")
            try:
                capabilities = exec_command_in_pod(
                    pod, command=["cat", "/proc/1/status"]
                )
                # Look for CapEff (effective capabilities) line
                # SYS_PTRACE is bit 19, which is 0x80000 in hex
                has_ptrace = False
                for line in capabilities.split("\n"):
                    if line.startswith("CapEff:"):
                        cap_value = int(line.split(":")[1].strip(), 16)
                        # Check if bit 19 (SYS_PTRACE) is set
                        has_ptrace = bool(cap_value & (1 << 19))
                        break
            except Exception as e:
                typer.echo(f"  Could not validate capabilities: {e}", err=True)
                typer.echo(
                    "  Validation inconclusive - try running mantis debug to test",
                    err=True,
                )
                continue

        if has_ptrace:
            typer.echo(f"✅ Pod '{pod.name}' has CAP_SYS_PTRACE.")
        else:
            typer.echo(f"⚠️  Pod '{pod.name}' does NOT have CAP_SYS_PTRACE.")
            typer.echo(
                "  Note: sys.remote_exec() may still work in local dev clusters (k3d, Docker Desktop, etc.)"
            )
            typer.echo("  In production clusters, you may need to add:")
            typer.echo("    securityContext:")
            typer.echo("      capabilities:")
            typer.echo("        add: [SYS_PTRACE]")
            typer.echo(
                "  Run 'mantis debug' to test if injection works in your environment."
            )


@app.command(help="Inject and execute a script in a Python process within a pod.")
def inject(
    namespace: str = typer.Option(
        None, "--namespace", "-n", help="The namespace to use."
    ),
    service: str = typer.Option(None, "--service", "-s", help="The service to use."),
    script: str = typer.Option(None, "--script", "-c", help="The script to execute."),
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
    typer.echo(f"Using provided PID {pid} for debugging.")

    # Prepare debugpy script
    temp_script_path = prepare_debugpy_script(port=port, wait=True)

    try:
        script_basename = os.path.basename(temp_script_path)
        typer.echo(f"Copying injection script to pod '{pod.name}'...")
        copy_to_pod(pod, temp_script_path, f"/tmp/{script_basename}")

        attacher_path = Path(__file__).parent / "attacher.py"
        copy_to_pod(pod, str(attacher_path), "/tmp/attacher.py")

        typer.echo(f"Injecting debugpy into PID {pid} in pod '{pod.name}'...")
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
        typer.echo(f"Successfully injected debugpy into PID {pid} in pod '{pod.name}'.")
        typer.echo(
            f"Set up port-forwarding with: kubectl port-forward {pod.name} -n {pod.namespace} {port}:{port}"
        )

        port_forward_proc = None
        if auto_forward:
            typer.echo(f"Setting up port-forwarding on port {port}...")
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
                typer.echo(f"Port-forwarding established on port {port}.")
            else:
                typer.echo("Failed to establish port-forwarding.", err=True)
                port_forward_proc = None
            # Show connection instructions
            typer.echo(f"\n{'='*60}\n")
            typer.echo(
                f"You can now connect your debugger to the following address: localhost:{port}"
            )
            typer.echo(f"\n{'='*60}\n")
            typer.echo(
                f"\n Connect your editor to localhost:{port} to start debugging.\n"
            )
            typer.echo(f"\n📝 VSCode launch.json configuration:")
        typer.echo(f"""
{{
  "version": "0.2.0",
  "configurations": [
    {{
      "name": "Debug {service}",
      "type": "debugpy",
      "request": "attach",
      "connect": {{
        "host": "localhost",
        "port": {port}
      }},
      "pathMappings": [
        {{
          "localRoot": "${{workspaceFolder}}",
          "remoteRoot": "/app"
        }}
      ]
    }}
  ]
}}""")

        if port_forward_proc:
            typer.echo("Press Ctrl+C to stop port-forwarding and exit.")
            try:
                port_forward_proc.wait()
            except KeyboardInterrupt:
                typer.echo("Stopping port-forwarding...")
                port_forward_proc.terminate()
                port_forward_proc.wait()
                typer.echo("Port-forwarding stopped.")
    finally:
        os.unlink(temp_script_path)


if __name__ == "__main__":
    app()
