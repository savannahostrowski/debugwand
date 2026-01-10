"""Kubernetes operations for debugwand."""

import json
import os
import subprocess
import time
from typing import Any

import typer

from debugwand.types import PodInfo, ProcessInfo

_KNATIVE_SERVICE_LABEL = "serving.knative.dev/service"


# ===== Pod selection =====


def select_pod(pods: list[PodInfo]) -> PodInfo:
    # Filter to only Running pods (exclude Succeeded, Failed, Pending, etc.)
    running_pods = [pod for pod in pods if pod.status == "Running"]

    if not running_pods:
        typer.echo("❌ No running pods found.", err=True)
        raise typer.Exit(code=1)

    if len(running_pods) == 1:
        return running_pods[0]

    # Auto-select newest pod if environment variable is set to "1"
    if os.environ.get("DEBUGWAND_AUTO_SELECT_POD") == "1":
        # Sort by creation_time (ISO 8601 strings sort correctly lexicographically)
        sorted_pods = sorted(running_pods, key=lambda p: p.creation_time, reverse=True)
        newest_pod = sorted_pods[0]
        typer.echo(f"🎯 Auto-selected newest pod: {newest_pod.name}")
        return newest_pod

    typer.echo("❔ Multiple pods found. Please select one:")
    for idx, pod in enumerate(running_pods):
        typer.echo(
            f"{idx + 1}: {pod.name} (Namespace: {pod.namespace}, Status: {pod.status})"
        )

    selection = int(typer.prompt("Enter the number of the pod to select")) - 1
    if selection < 0 or selection >= len(running_pods):
        raise ValueError("Invalid selection.")

    return running_pods[selection]


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
            creation_time=item["metadata"].get("creationTimestamp", ""),
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


def list_python_processes(pod: PodInfo) -> list[ProcessInfo]:
    """List Python processes in a pod."""
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


def list_python_processes_handler(pod: PodInfo) -> list[ProcessInfo] | None:
    if pod.status != "Running":
        typer.echo(
            f"❌ Pod '{pod.name}' is not running (skipping process list)",
            err=True,
        )
        return None
    try:
        processes = list_python_processes(pod)
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


def get_and_select_process(pod: PodInfo, pid: int | None) -> int:
    """Get and select a Python process in the given pod. If pid is provided, validate it exists.
    Otherwise, prompt the user to select one."""
    from debugwand.operations import select_pid

    processes = list_python_processes(pod)
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
        from debugwand.operations import detect_reload_mode
        from debugwand.ui import print_reload_mode_warning

        # Get processes to check for reload mode
        processes = list_python_processes(pod)
        if not processes:
            raise ValueError("No Python processes found in the selected pod.")

        # Now do the actual selection
        selected_pid = get_and_select_process(pod, pid)

        # Check for reload mode and show warning after selection
        is_reload, worker_proc = detect_reload_mode(processes)
        if is_reload and worker_proc and not pid:
            print_reload_mode_warning(worker_proc.pid)
        return selected_pid
    except ValueError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=1)


# ===== Pod command execution =====


def exec_command(
    pod: PodInfo,
    command: list[str],
    verbose: bool = False,
    silent_errors: bool = False,
    background: bool = False,
) -> str:
    cmd = ["kubectl", "exec", pod.name, "-n", pod.namespace, "--"] + command

    if background:
        # Run in background without waiting for completion
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # Detach from parent
        )
        return ""

    result = subprocess.run(cmd, capture_output=True, text=True)

    if verbose and result.stdout:
        print(f"STDOUT: {result.stdout}")

    if result.returncode != 0:
        if not silent_errors:
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


# ===== Auto-reconnect helpers =====


def find_replacement_pod(
    old_pod: PodInfo, service: str, namespace: str
) -> PodInfo | None:
    """Find a replacement pod after the old one has terminated.
    Tries to match by labels/revisions first, falls back to newest running pod."""
    try:
        pods = get_pods_for_service(namespace=namespace, service=service)
    except Exception:
        return None

    candidate_pods = [
        p for p in pods if p.status == "Running" and p.name != old_pod.name
    ]
    if not candidate_pods:
        return None

    # Try to match by revision/generation (Knative)
    if "serving.knative.dev/revision" in old_pod.labels:
        same_service_pods = [
            p
            for p in candidate_pods
            if p.labels.get("serving.knative.dev/service")
            == old_pod.labels.get("serving.knative.dev/service")
        ]
        if same_service_pods:
            # Return the newest (by name, which includes generation)
            return sorted(same_service_pods, key=lambda p: p.name, reverse=True)[0]

    # Fall back to newest pod by name
    return sorted(candidate_pods, key=lambda p: p.name, reverse=True)[0]


def wait_for_new_pod(service: str, namespace: str, timeout: int = 300) -> PodInfo:
    """Wait for a new pod to become ready after the old one has terminated."""
    start_time = time.time()
    wait_interval = 5

    while time.time() - start_time < timeout:
        try:
            pods = get_pods_for_service(namespace=namespace, service=service)
            running_pods = [p for p in pods if p.status == "Running"]
            if running_pods:
                for pod in running_pods:
                    try:
                        processes = list_python_processes(pod)
                        if processes:
                            return pod
                    except Exception:
                        continue
        except Exception:
            pass
        time.sleep(wait_interval)

    raise TimeoutError(f"Timed out waiting for a new pod in service '{service}'")


def monitor_worker_pid(pod: PodInfo, initial_pid: int) -> int | None:
    """Monitor for worker PID changes and return new PID if detected.
    Returns None if monitoring should stop (e.g., pod gone or error).
    Returns initial_pid if no change detected yet.
    """
    from debugwand.operations import detect_reload_mode

    try:
        processes = list_python_processes(pod)
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
        # Pod might be gone or other error, stop monitoring
        return None
