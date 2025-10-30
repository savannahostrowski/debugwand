"""Kubernetes operations for mantis."""

import json
import subprocess
from dataclasses import dataclass


@dataclass
class PodInfo:
    name: str
    namespace: str
    node_name: str
    status: str
    labels: dict[str, str]


@dataclass
class ProcessInfo:
    pid: int
    user: str
    cpu_percent: float
    mem_percent: float
    command: str


def select_pod(pods: list[PodInfo]) -> PodInfo:
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


def get_pods_for_service(namespace: str, service: str) -> list[PodInfo]:
    cmd = ["kubectl", "get", "service", service, "-n", namespace, "-o", "json"]

    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    service_json = json.loads(result.stdout)

    service_type = service_json.get("spec", {}).get("type", "")

    # Check if it's a Knative service
    if service_type == "ExternalName":
        label_selector = f"serving.knative.dev/service={service}"
        return get_pods_by_label(namespace=namespace, label_selector=label_selector)

    # For standard services, get the selector labels
    selector = service_json.get("spec", {}).get("selector", {})
    if not selector:
        raise ValueError(f"Service {service} has no selector.")

    label_selector = ",".join(f"{key}={value}" for key, value in selector.items())
    return get_pods_by_label(namespace=namespace, label_selector=label_selector)


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

    pods: list[PodInfo] = []
    for item in pods_json.get("items", []):
        pod = PodInfo(
            name=item["metadata"]["name"],
            namespace=item["metadata"]["namespace"],
            node_name=item["spec"].get("nodeName", ""),
            status=item["status"]["phase"],
            labels=item["metadata"].get("labels", {}),
        )
        pods.append(pod)

    return pods


def list_python_processes_with_details(pod: PodInfo) -> list[ProcessInfo]:
    """List Python processes in a pod with CPU/memory details.

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


def _is_main_process(proc: ProcessInfo) -> bool:
    """Determine if a process is likely the main application process."""
    # Skip known helper/internal processes
    helper_patterns = [
        "multiprocessing.resource_tracker",
        "multiprocessing.spawn",
        "from multiprocessing",
    ]

    for pattern in helper_patterns:
        if pattern in proc.command:
            return False

    # Main process indicators
    main_indicators = [
        "fastapi run",
        "gunicorn",
        "uvicorn",
        "flask run",
        "python -m",
        "python app.py",
        "python main.py",
    ]

    for indicator in main_indicators:
        if indicator in proc.command:
            return True

    # If it's PID 1, it's usually the main process
    if proc.pid == 1:
        return True

    return True  # Default to including it


def _detect_reload_mode(
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
    is_reload, worker_proc = _detect_reload_mode(processes)
    if is_reload and worker_proc:
        print(f"\n⚠️  RELOAD MODE DETECTED")
        print(f"The app is running with --reload, which spawns worker processes.")
        print(
            f"You should inject into the WORKER process (PID {worker_proc.pid}), not the parent (PID 1)."
        )
        print(f"\nAuto-selecting worker process: PID {worker_proc.pid}\n")
        return worker_proc.pid
    elif is_reload and not worker_proc:
        print(f"\n⚠️  WARNING: Reload mode detected but couldn't find worker process.")
        print(f"You may need to manually select the correct PID.\n")

    # Filter to main processes only
    main_processes = [p for p in processes if _is_main_process(p)]

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


def exec_command_in_pod(pod: PodInfo, command: list[str]) -> str:
    """Execute a command in a pod."""
    cmd = ["kubectl", "exec", pod.name, "-n", pod.namespace, "--"] + command
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.stdout:
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
    """Copy a file from local to pod."""
    cmd = ["kubectl", "cp", local_path, f"{pod.namespace}/{pod.name}:{remote_path}"]
    subprocess.run(cmd, check=True)
