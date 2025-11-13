import os

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from debugwand.operations import detect_reload_mode, is_main_process
from debugwand.types import PodInfo, ProcessInfo

# Global console for UI functions
_console = Console()

# Check if we should use simple UI (e.g., when running in Tilt)
_use_simple_ui = os.getenv("DEBUGWAND_SIMPLE_UI") == "1"


def render_pods_table(pods: list[PodInfo]):
    table = Table()

    table.add_column("Pod Name", style="cyan", no_wrap=True)
    table.add_column("Namespace", style="magenta")
    table.add_column("Status", style="green")

    for pod in pods:
        table.add_row(pod.name, pod.namespace, pod.status)

    _console.print(table)


def render_processes_table(pod_processes: list[tuple[PodInfo, list[ProcessInfo]]]):
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


def print_reload_mode_warning(worker_pid: int):
    """Print a formatted warning about reload mode detection."""
    _console.print()

    if _use_simple_ui:
        # Simple output for environments like Tilt
        _console.print(
            "[yellow]=============================== Reload Mode ===============================[/yellow]"
        )
        _console.print(
            "The app is running with [cyan]--reload[/cyan], which spawns worker processes."
        )
        _console.print(
            f"Injecting into the [green bold]WORKER[/green bold] process (PID [cyan]{worker_pid}[/cyan])."
        )
        _console.print(
            "Process monitoring enabled - debugpy will auto-reinject on worker restarts."
        )
        _console.print("[yellow]" + "=" * 75 + "[/yellow]")
    else:
        # Fancy panel for normal terminals
        _console.print(
            Panel(
                f"The app is running with [cyan]--reload[/cyan], which spawns worker processes.\n"
                f"Injecting into the [green bold]WORKER[/green bold] process (PID [cyan]{worker_pid}[/cyan]).\n"
                f"Process monitoring enabled - debugpy will auto-reinject on worker restarts.",
                border_style="yellow",
                title="Reload Mode",
                expand=False,
            )
        )

    _console.print(
        f"[green]✅[/green] Auto-selecting worker process: [cyan bold]PID {worker_pid}[/cyan bold]"
    )


def print_success(message: str, prefix: str = "✅"):
    """Print a success message."""
    _console.print(f"[green]{prefix}[/green] {message}")


def print_info(message: str, prefix: str = "ℹ️"):
    """Print an info message."""
    _console.print(f"[blue]{prefix}[/blue]  {message}")


def print_step(message: str, prefix: str = "🔧"):
    """Print a step/progress message."""
    _console.print(f"[cyan]{prefix}[/cyan] {message}")


def print_connection_info(port: int):
    """Print formatted connection instructions with VSCode config."""
    _console.print()

    if _use_simple_ui:
        # Simple output for environments like Tilt
        _console.print("[green]" + "=" * 42 + "[/green]")
        _console.print("[green bold]🎉 Ready to Debug![/green bold]")
        _console.print()
        _console.print(
            f"Connect your debugger to: [cyan bold]localhost:{port}[/cyan bold]"
        )
        _console.print("[green]" + "=" * 42 + "[/green]")
    else:
        # Fancy panel for normal terminals
        _console.print(
            Panel(
                f"[green bold]🎉 Ready to Debug![/green bold]\n\n"
                f"Connect your debugger to: [cyan bold]localhost:{port}[/cyan bold]",
                border_style="green",
                expand=False,
            )
        )

    _console.print("\n[dim]Press Ctrl+C to stop port-forwarding and exit.[/dim]\n")
