"""Kubernetes and debugging operations for debugwand."""

import json
import socket
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import typer

from debugwand.types import PodInfo, ProcessInfo

# Kubernetes constants
_KNATIVE_SERVICE_LABEL = "serving.knative.dev/service"


# ===== Port availability =====


def is_port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


# ===== Pod selection =====


def select_pod(pods: list[PodInfo]) -> PodInfo:
    if not pods:
        raise ValueError("No pods available to select from.")
    if len(pods) == 1:
        return pods[0]

    typer.echo("❔ Multiple pods found. Please select one:")
    for idx, pod in enumerate(pods):
        typer.echo(
            f"{idx + 1}: {pod.name} (Namespace: {pod.namespace}, Status: {pod.status})"
        )

    selection = int(typer.prompt("Enter the number of the pod to select")) - 1
    if selection < 0 or selection >= len(pods):
        raise ValueError("Invalid selection.")

    return pods[selection]


def _get_label_selector_for_service(service_json: dict[str, Any], service: str) -> str:
    service_type = service_json.get("spec", {}).get("type", "")

    # Check if it's a Knative service
    if service_type == "ExternalName":
        return f"{_KNATIVE_SERVICE_LABEL}={service}"

    # For standard services, get the selector labels
    selector = service_json.get("spec", {}).get("selector", {})
    if not selector:
        raise ValueError(f"Service {service} has no selector.")

    return ",".join(f"{key}={value}" for key, value in selector.items())


def get_pods_for_service(namespace: str, service: str) -> list[PodInfo]:
    cmd = ["kubectl", "get", "service", service, "-n", namespace, "-o", "json"]

    result = subprocess.run(cmd, capture_output=True, text=True, check=False)

    if result.returncode != 0:
        if "NotFound" in result.stderr or "not found" in result.stderr:
            raise ValueError(
                f"Service '{service}' not found in namespace '{namespace}'.\n"
                f"Tip: Check the service exists with: kubectl get svc -n {namespace}\n"
                f"For Knative services that are scaling from zero, wait a moment and try again."
            )
        else:
            # Re-raise other kubectl errors
            raise subprocess.CalledProcessError(
                result.returncode, cmd, result.stdout, result.stderr
            )

    service_json = json.loads(result.stdout)
    label_selector = _get_label_selector_for_service(service_json, service)
    return get_pods_by_label(namespace=namespace, label_selector=label_selector)


def get_pods_for_service_handler(namespace: str, service: str) -> list[PodInfo]:
    try:
        pod_list = get_pods_for_service(service=service, namespace=namespace)
    except ValueError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=1)

    if not pod_list:
        typer.echo("❌ No pods found matching the criteria.", err=True)
        raise typer.Exit(code=1)
    return pod_list


def get_pods_by_label(
    namespace: str | None, label_selector: str | None
) -> list[PodInfo]:
    cmd = ["kubectl", "get", "pods", "-o", "json"]
    if namespace:
        cmd.extend(["-n", namespace])
    if label_selector:
        cmd.extend(["-l", label_selector])

    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    pods_json = json.loads(result.stdout)

    return [
        PodInfo(
            name=item["metadata"]["name"],
            namespace=item["metadata"]["namespace"],
            node_name=item["spec"].get("nodeName", ""),
            status=item["status"]["phase"],
            labels=item["metadata"].get("labels", {}),
        )
        for item in pods_json.get("items", [])
    ]


def get_and_select_pod(service: str, namespace: str) -> PodInfo:
    pod_list = get_pods_for_service(service=service, namespace=namespace)

    if not pod_list:
        raise ValueError("No pods found matching the criteria.")
    return select_pod(pod_list)


def get_and_select_pod_handler(service: str, namespace: str) -> PodInfo:
    try:
        pod = get_and_select_pod(service=service, namespace=namespace)
        return pod
    except ValueError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=1)


# ===== Process listing and selection =====

# Process detection patterns
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


def list_python_processes_with_details(pod: PodInfo) -> list[ProcessInfo]:
    if pod.status != "Running":
        raise ValueError(f"Pod '{pod.name}' is not running (status: {pod.status})")

    cmd = ["kubectl", "exec", pod.name, "-n", pod.namespace, "--", "ps", "aux"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    processes: list[ProcessInfo] = []
    for line in result.stdout.splitlines():
        if "python" in line.lower():
            parts = line.split(None, 10)
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


def list_all_processes_with_details_handler(pod: PodInfo) -> list[ProcessInfo] | None:
    if pod.status != "Running":
        typer.echo(
            f"❌ Pod '{pod.name}' is not running (skipping process list)",
            err=True,
        )
        return None
    try:
        processes = list_python_processes_with_details(pod)
        return processes
    except subprocess.CalledProcessError as e:
        typer.echo(
            f"❌ Failed to list processes in pod '{pod.name}': {e.stderr.strip()}",
            err=True,
        )
        return None
    except Exception as e:
        typer.echo(f"❌ Error accessing pod '{pod.name}': {e}", err=True)
        return None


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
    if not processes:
        raise ValueError("No Python processes found in the pod.")

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


def get_and_select_process(pod: PodInfo, pid: int | None) -> int:
    """Get and select a Python process in the given pod. If pid is provided, validate it exists.
    Otherwise, prompt the user to select one."""
    processes = list_python_processes_with_details(pod)
    if not processes:
        raise ValueError("No Python processes found in the selected pod.")

    if pid:
        process_pids = [p.pid for p in processes]
        if pid not in process_pids:
            raise ValueError(
                f"PID {pid} not found in the Python processes of the selected pod."
            )
        return pid

    # Note: Reload mode detection and warning is handled by the UI layer
    return select_pid(processes)


def get_and_select_process_handler(pod: PodInfo, pid: int | None) -> int:
    try:
        selected_pid = get_and_select_process(pod, pid)
        return selected_pid
    except ValueError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=1)


# ===== Pod command execution =====


def exec_command_in_pod(pod: PodInfo, command: list[str], verbose: bool = False) -> str:
    cmd = ["kubectl", "exec", pod.name, "-n", pod.namespace, "--"] + command
    result = subprocess.run(cmd, capture_output=True, text=True)

    if verbose and result.stdout:
        print(f"STDOUT: {result.stdout}")

    if result.returncode != 0:
        print(f"Command failed with exit code {result.returncode}")
        print(f"STDOUT: {result.stdout}")
        print(f"STDERR: {result.stderr}")
        raise subprocess.CalledProcessError(
            result.returncode, cmd, result.stdout, result.stderr
        )
    return result.stdout


def copy_to_pod(pod: PodInfo, local_path: str, remote_path: str):
    cmd = ["kubectl", "cp", local_path, f"{pod.namespace}/{pod.name}:{remote_path}"]
    subprocess.run(cmd, check=True)


# ===== Debugpy script preparation =====


def prepare_debugpy_script(port: int, wait: bool = True) -> str:
    template_path = Path(__file__).parent / "debugpy_template.py"
    with open(template_path, "r") as f:
        script_content = f.read()

    script_content = script_content.replace("{PORT}", str(port)).replace(
        "{WAIT}", str(wait)
    )

    with tempfile.NamedTemporaryFile("w", delete=False) as tmpfile:
        tmpfile.write(script_content)
        return tmpfile.name
