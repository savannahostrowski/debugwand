"""Kubernetes and debugging operations for debugwand."""

import json
import socket
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from debugwand.types import PodInfo, ProcessInfo

# Kubernetes constants
_KNATIVE_SERVICE_LABEL = "serving.knative.dev/service"


# ===== Port availability =====


def is_port_available(port: int) -> bool:
    """Check if a local port is available for binding.

    Args:
        port: Port number to check (1-65535)

    Returns:
        True if the port is available, False if already in use
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


# ===== Pod selection =====


def select_pod(pods: list[PodInfo]) -> PodInfo:
    """Select a pod from a list interactively or automatically.

    If only one pod is available, it's automatically selected.
    Otherwise, displays options and prompts for user selection.

    Args:
        pods: List of pods to select from

    Returns:
        Selected PodInfo object

    Raises:
        ValueError: If pod list is empty or selection is invalid
    """
    if not pods:
        raise ValueError("No pods available to select from.")
    if len(pods) == 1:
        return pods[0]

    print("Multiple pods found. Please select one:")
    for idx, pod in enumerate(pods):
        print(
            f"{idx + 1}: {pod.name} (Namespace: {pod.namespace}, Status: {pod.status})"
        )

    selection = int(input("Enter the number of the pod to select: ")) - 1
    if selection < 0 or selection >= len(pods):
        raise ValueError("Invalid selection.")

    return pods[selection]


def _get_label_selector_for_service(service_json: dict[str, Any], service: str) -> str:
    """Extract the appropriate label selector from service spec.

    Args:
        service_json: Parsed JSON from kubectl get service
        service: Service name (used for Knative services)

    Returns:
        Label selector string (e.g., "app=myapp,tier=frontend")

    Raises:
        ValueError: If service has no selector
    """
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
    """Get all pods associated with a Kubernetes service.

    Handles both standard Kubernetes services and Knative services.

    Args:
        namespace: Kubernetes namespace containing the service
        service: Name of the service

    Returns:
        List of PodInfo objects for pods backing the service

    Raises:
        ValueError: If the service is not found or has no selector
        subprocess.CalledProcessError: If kubectl command fails
    """
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


def get_pods_by_label(
    namespace: str | None, label_selector: str | None
) -> list[PodInfo]:
    """Get pods filtered by namespace and label selector.

    Args:
        namespace: Kubernetes namespace (None for all namespaces)
        label_selector: Label selector string (e.g., "app=myapp,tier=frontend")

    Returns:
        List of PodInfo objects matching the criteria

    Raises:
        subprocess.CalledProcessError: If kubectl command fails
    """
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


# ===== Process listing and selection =====


def list_python_processes_with_details(pod: PodInfo) -> list[ProcessInfo]:
    """List Python processes in a pod with CPU/memory details.

    Uses `ps aux` to get process information and filters for Python processes.

    Args:
        pod: Pod to list processes in

    Returns:
        List of ProcessInfo objects for Python processes

    Raises:
        ValueError: If pod is not in Running state
        subprocess.CalledProcessError: If kubectl exec fails
    """
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
    """Detect if app is running in reload mode and find the worker process.

    Returns:
        (is_reload_mode, worker_process)
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
    if not processes:
        raise ValueError("No Python processes found in the pod.")

    # Check for reload mode
    is_reload, worker_proc = detect_reload_mode(processes)
    if is_reload and worker_proc:
        # Just return the worker PID - let caller handle UI
        return worker_proc.pid
    elif is_reload and not worker_proc:
        print(f"\n⚠️  WARNING: Reload mode detected but couldn't find worker process.")
        print(f"You may need to manually select the correct PID.\n")

    # Filter to main processes only
    main_processes = [p for p in processes if is_main_process(p)]

    # If filtering removed everything, use all processes
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

    # Check for reload mode and show warning if detected
    is_reload, worker_proc = detect_reload_mode(processes)
    if is_reload and worker_proc:
        from debugwand.ui import print_reload_mode_warning

        print_reload_mode_warning(worker_proc.pid, parent_pid=1)

    return select_pid(processes)


# ===== Pod command execution =====


def exec_command_in_pod(pod: PodInfo, command: list[str], verbose: bool = False) -> str:
    """Execute a command in a pod.

    Args:
        pod: The pod to execute the command in
        command: The command to execute
        verbose: If True, print stdout/stderr. Defaults to False.
    """
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
    """Copy a file from local filesystem to a pod.

    Uses kubectl cp under the hood. The remote path should be an absolute path
    within the pod's filesystem.

    Args:
        pod: Target pod
        local_path: Absolute path to local file
        remote_path: Absolute path in pod where file should be copied

    Raises:
        subprocess.CalledProcessError: If kubectl cp fails
    """
    cmd = ["kubectl", "cp", local_path, f"{pod.namespace}/{pod.name}:{remote_path}"]
    subprocess.run(cmd, check=True)


# ===== Debugpy script preparation =====


def prepare_debugpy_script(port: int, wait: bool = True) -> str:
    """Prepare the debugpy attachment script content.

    Generates a temporary script that will be injected into the target process
    to start a debugpy server.

    Args:
        port: Port number for debugpy server to listen on
        wait: Whether debugpy should wait for client before continuing

    Returns:
        Path to temporary script file (caller should clean up)
    """

    template_path = Path(__file__).parent / "debugpy_template.py"
    with open(template_path, "r") as f:
        script_content = f.read()

    script_content = script_content.replace("{PORT}", str(port)).replace(
        "{WAIT}", str(wait)
    )

    with tempfile.NamedTemporaryFile("w", delete=False) as tmpfile:
        tmpfile.write(script_content)
        return tmpfile.name
