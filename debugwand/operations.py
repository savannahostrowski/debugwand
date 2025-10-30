
import tempfile
from debugwand.k8s import PodInfo, get_pods_for_service, list_python_processes_with_details, select_pid, select_pod
from pathlib import Path

def get_and_select_pod(
        service: str,
        namespace: str
    ) -> PodInfo:

    pod_list = get_pods_for_service(service=service, namespace=namespace)

    if not pod_list:
        raise ValueError("No pods found matching the criteria.")
    return select_pod(pod_list)

def get_and_select_process(
        pod: PodInfo,
        pid: int | None,
    ) -> int:
    """Get and select a Python process in the given pod. If pid is provided, validate it exists.
    Otherwise, prompt the user to select one."""
    processes = list_python_processes_with_details(pod)
    if not processes:
        raise ValueError("No Python processes found in the selected pod.")

    if pid:
        process_pids = [p.pid for p in processes]
        if pid not in process_pids:
            raise ValueError(f"PID {pid} not found in the Python processes of the selected pod.")
        return pid

    return select_pid(processes)

def prepare_debugpy_script(port: int, wait: bool = True) -> str:
    """Prepare the debugpy attachment script content."""

    template_path = Path(__file__).parent / "debugpy_template.py"
    with open(template_path, "r") as f:
        script_content = f.read()

    script_content = script_content.replace("{PORT}", str(port)).replace("{WAIT}", str(wait))

    with tempfile.NamedTemporaryFile("w", delete=False) as tmpfile:
        tmpfile.write(script_content)
        return tmpfile.name