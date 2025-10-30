from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from debugwand.operations import detect_reload_mode, is_main_process
from debugwand.types import PodInfo, ProcessInfo

# Global console for UI functions
_console = Console()


def render_pods_table(pods: list[PodInfo]):
    """Render a formatted table of Kubernetes pods.

    Displays pod name, namespace, and status in a rich table format.

    Args:
        pods: List of pods to display
    """
    table = Table()

    table.add_column("Pod Name", style="cyan", no_wrap=True)
    table.add_column("Namespace", style="magenta")
    table.add_column("Status", style="green")

    for pod in pods:
        table.add_row(pod.name, pod.namespace, pod.status)

    _console.print(table)


def render_processes_table(pod_processes: list[tuple[PodInfo, list[ProcessInfo]]]):
    """Render a formatted table of Python processes grouped by pod.

    Displays processes with their PID, type (MAIN/PARENT/helper/worker), and command.
    Automatically detects reload mode and highlights the recommended process to debug.

    Args:
        pod_processes: List of (PodInfo, ProcessInfo list) tuples

    Note:
        The "⭐ MAIN" indicator shows the recommended process to debug.
    """
    table = Table()

    table.add_column("Pod", style="blue", no_wrap=True, max_width=28)
    table.add_column("PID", style="cyan", no_wrap=True, justify="right", width=4)
    table.add_column("Type", style="dim", width=10)
    table.add_column("Command", style="white", no_wrap=False)

    for pod, processes in pod_processes:
        # Detect reload mode to identify recommended PID
        is_reload, worker_proc = detect_reload_mode(processes)
        recommended_pid = worker_proc.pid if worker_proc else None

        # If no reload mode, recommend the main process
        if not recommended_pid:
            for proc in processes:
                if is_main_process(proc):
                    recommended_pid = proc.pid
                    break

        for idx, proc in enumerate(processes):
            # Show shortened pod name only in the first row for this pod
            if idx == 0:
                # Shorten pod name for display (keep first ~25 chars + ...)
                pod_display = pod.name if len(pod.name) <= 28 else pod.name[:25] + "..."
            else:
                pod_display = ""

            # Determine process type label
            proc_type = ""
            if proc.pid == recommended_pid:
                proc_type = "⭐ MAIN"
            elif is_reload and proc.pid == 1:
                proc_type = "PARENT"
            elif "multiprocessing.resource_tracker" in proc.command:
                proc_type = "helper"
            elif (
                "multiprocessing.spawn" in proc.command and proc.pid != recommended_pid
            ):
                proc_type = "worker"
            elif "debugpy/adapter" in proc.command:
                proc_type = "debugger"

            # Shorten command for readability
            cmd_display = proc.command
            if len(cmd_display) > 80:
                cmd_display = cmd_display[:77] + "..."

            table.add_row(
                pod_display,
                str(proc.pid),
                proc_type,
                cmd_display,
            )

    _console.print(table)


def print_reload_mode_warning(worker_pid: int, parent_pid: int = 1):
    """Print a formatted warning about reload mode detection."""
    _console.print()
    _console.print(
        Panel(
            f"The app is running with [cyan]--reload[/cyan], which spawns worker processes.\n"
            f"You should inject into the [green bold]WORKER[/green bold] process (PID [cyan]{worker_pid}[/cyan]), "
            f"not the parent (PID [dim]{parent_pid}[/dim]).",
            border_style="yellow",
            title="Reload Mode",
            expand=False,
        )
    )
    _console.print(
        f"[green]✓[/green] Auto-selecting worker process: [cyan bold]PID {worker_pid}[/cyan bold]"
    )


def print_success(message: str, prefix: str = "✅"):
    """Print a success message."""
    _console.print(f"[green]{prefix}[/green] {message}")


def print_info(message: str, prefix: str = "ℹ️"):
    """Print an info message."""
    _console.print(f"[blue]{prefix}[/blue] {message}")


def print_step(message: str, prefix: str = "🔧"):
    """Print a step/progress message."""
    _console.print(f"[cyan]{prefix}[/cyan] {message}")


def print_connection_info(port: int, service: str):
    """Print formatted connection instructions with VSCode config."""
    _console.print()
    _console.print(
        Panel(
            f"[green bold]🎉 Ready to Debug![/green bold]\n\n"
            f"Connect your debugger to: [cyan bold]localhost:{port}[/cyan bold]",
            border_style="green",
            expand=False,
        )
    )

    _console.print("\n[dim]Press Ctrl+C to stop port-forwarding and exit.[/dim]\n")
