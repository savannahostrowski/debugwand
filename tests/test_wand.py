import os
import subprocess
import tempfile
from unittest.mock import MagicMock, patch

from typer import Exit
from typer.testing import CliRunner

from debugwand.cli import app
from debugwand.container import list_python_processes, monitor_worker_pid
from debugwand.operations import detect_reload_mode
from debugwand.types import PodInfo, ProcessInfo

runner = CliRunner()


class TestPodsCommand:
    """Tests for the 'pods' command."""

    @patch("debugwand.kubernetes.get_pods_for_service_handler")
    def test_pods_no_pods_found(self, mock_get_pods: MagicMock):
        mock_get_pods.side_effect = Exit(1)
        result = runner.invoke(
            app, ["pods", "--namespace", "default", "--service", "test-service"]
        )
        assert result.exit_code == 1

        mock_get_pods.assert_called_once_with("default", "test-service")

    @patch("debugwand.kubernetes.get_pods_for_service_handler")
    @patch("debugwand.kubernetes.list_python_processes_handler")
    def test_pods_with_pids_no_processes(
        self, mock_list_procs_handler: MagicMock, mock_get_pods: MagicMock
    ):
        """Test the 'pods' command with --with-pids when pods have no Python processes."""
        mock_pod = PodInfo(
            name="pod-1",
            namespace="default",
            node_name="node-1",
            status="Running",
            labels={"app": "test-app"},
            creation_time="2025-01-01T00:00:00Z",
        )

        mock_get_pods.return_value = [mock_pod]
        mock_list_procs_handler.return_value = None

        result = runner.invoke(
            app,
            [
                "pods",
                "--namespace",
                "default",
                "--service",
                "test-service",
                "--with-pids",
            ],
        )

        assert result.exit_code == 1
        assert "No running pods with Python processes found" in result.stderr

        mock_get_pods.assert_called_once_with("default", "test-service")
        mock_list_procs_handler.assert_called_once_with(mock_pod)

    @patch("debugwand.kubernetes.get_pods_for_service_handler")
    @patch("debugwand.kubernetes.list_python_processes_handler")
    def test_pods_with_pids_with_processes(
        self, mock_list_procs_handler: MagicMock, mock_get_pods: MagicMock
    ):
        """Test the 'pods' command with --with-pids when pods have Python processes."""
        mock_pod = PodInfo(
            name="pod-1",
            namespace="default",
            node_name="node-1",
            status="Running",
            labels={"app": "test-app"},
            creation_time="2025-01-01T00:00:00Z",
        )

        mock_process = ProcessInfo(
            pid=1234,
            user="root",
            cpu_percent=0.5,
            mem_percent=1.2,
            command="python app.py",
        )

        mock_get_pods.return_value = [mock_pod]
        mock_list_procs_handler.return_value = [mock_process]

        result = runner.invoke(
            app,
            [
                "pods",
                "--namespace",
                "default",
                "--service",
                "test-service",
                "--with-pids",
            ],
        )

        assert result.exit_code == 0
        assert "1234" in result.stdout
        assert "pod-1" in result.stdout
        assert "python app.py" in result.stdout

        mock_get_pods.assert_called_once_with("default", "test-service")
        mock_list_procs_handler.assert_called_once_with(mock_pod)


class TestInjectCommand:
    """Tests for the 'inject' command."""

    @patch("debugwand.kubernetes.get_pods_for_service_handler")
    def test_inject_no_pods_found(self, mock_get_pods_handler: MagicMock):
        mock_get_pods_handler.side_effect = Exit(1)
        result = runner.invoke(
            app,
            [
                "inject",
                "--namespace",
                "default",
                "--service",
                "test-service",
                "--script",
                "/path/to/script.py",
            ],
        )
        assert result.exit_code == 1

        mock_get_pods_handler.assert_called_once_with("default", "test-service")

    @patch("debugwand.kubernetes.get_pods_for_service_handler")
    @patch("debugwand.kubernetes.list_python_processes_handler")
    @patch("debugwand.kubernetes.select_pod")
    @patch("debugwand.kubernetes.get_and_select_process")
    @patch("debugwand.kubernetes.copy_to_pod")
    @patch("debugwand.kubernetes.exec_command")
    def test_inject_successful_injection(
        self,
        mock_exec_cmd: MagicMock,
        mock_copy_to_pod: MagicMock,
        mock_select_process: MagicMock,
        mock_select_pod: MagicMock,
        mock_list_procs: MagicMock,
        mock_get_pods_handler: MagicMock,
    ):
        mock_pod = PodInfo(
            name="pod-1",
            namespace="default",
            node_name="node-1",
            status="Running",
            labels={"app": "test-app"},
            creation_time="2025-01-01T00:00:00Z",
        )

        mock_process = ProcessInfo(
            pid=1234,
            user="root",
            cpu_percent=0.5,
            mem_percent=1.2,
            command="python app.py",
        )

        mock_get_pods_handler.return_value = [mock_pod]
        mock_list_procs.return_value = [mock_process]
        mock_select_pod.return_value = mock_pod
        mock_select_process.return_value = 1234
        mock_exec_cmd.return_value = "Injection successful"

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as temp_script:
            temp_script.write("print('Hello from injected script')")
            temp_script_path = temp_script.name

        try:
            result = runner.invoke(
                app,
                [
                    "inject",
                    "--namespace",
                    "default",
                    "--service",
                    "test-service",
                    "--script",
                    "/path/to/script.py",
                ],
            )
            assert result.exit_code == 0
            assert "Executing script '/path/to/script.py'" in result.stdout

            mock_copy_to_pod.assert_called()
            mock_exec_cmd.assert_called()
        finally:
            os.unlink(temp_script_path)


class TestDebugCommand:
    """Tests for the 'debug' command."""

    @patch("debugwand.kubernetes.get_and_select_pod_handler")
    def test_debug_no_pods_found(self, mock_get_and_select_pod_handler: MagicMock):
        # Handler raises Exit when no pods found
        mock_get_and_select_pod_handler.side_effect = Exit(1)

        result = runner.invoke(
            app, ["debug", "--namespace", "default", "--service", "my-service"]
        )

        assert result.exit_code == 1

    @patch("debugwand.kubernetes.get_and_select_pod_handler")
    @patch("debugwand.kubernetes.get_and_select_process_handler")
    def test_debug_invalid_pid(
        self,
        mock_get_and_select_process_handler: MagicMock,
        mock_get_and_select_pod_handler: MagicMock,
    ):
        """Test debug with an invalid PID."""
        mock_pod = PodInfo(
            name="pod-1",
            namespace="default",
            node_name="node-1",
            status="Running",
            labels={"app": "test-app"},
            creation_time="2025-01-01T00:00:00Z",
        )

        mock_get_and_select_pod_handler.return_value = mock_pod
        mock_get_and_select_process_handler.side_effect = Exit(1)

        result = runner.invoke(
            app,
            [
                "debug",
                "--namespace",
                "default",
                "--service",
                "test-service",
                "--pid",
                "9999",
            ],
        )

        assert result.exit_code == 1

    def test_debug_requires_container_or_namespace_service(self):
        """Test that debug command requires either --container or --namespace + --service."""
        result = runner.invoke(app, ["debug"])
        assert result.exit_code == 1
        assert "Either --container or both --namespace and --service are required" in result.stderr

    def test_debug_requires_both_namespace_and_service(self):
        """Test that debug command requires both namespace and service for k8s mode."""
        result = runner.invoke(app, ["debug", "--namespace", "default"])
        assert result.exit_code == 1
        assert "Either --container or both --namespace and --service are required" in result.stderr

        result = runner.invoke(app, ["debug", "--service", "my-service"])
        assert result.exit_code == 1
        assert "Either --container or both --namespace and --service are required" in result.stderr

    def test_debug_container_cannot_mix_with_k8s_options(self):
        """Test that debug --container cannot be used with --namespace or --service."""
        result = runner.invoke(
            app, ["debug", "--container", "my-container", "--namespace", "default"]
        )
        assert result.exit_code == 1
        assert "Cannot use --namespace or --service with --container" in result.stderr

        result = runner.invoke(
            app, ["debug", "--container", "my-container", "--service", "my-service"]
        )
        assert result.exit_code == 1
        assert "Cannot use --namespace or --service with --container" in result.stderr


class TestContainerSupport:
    """Tests for container debugging support."""

    @patch("debugwand.container.subprocess.run")
    def test_list_python_processes_in_container(self, mock_run: MagicMock):
        """Test listing Python processes in a container."""
        mock_run.return_value = MagicMock(
            stdout="""USER       PID %CPU %MEM    VSZ   RSS TTY      STAT START   TIME COMMAND
root         1  0.5  1.2 123456 12345 ?        Ss   10:00   0:01 python app.py
root        10  0.1  0.5  65432  6543 ?        S    10:01   0:00 python worker.py
""",
            returncode=0,
        )

        processes = list_python_processes("test-container")

        assert len(processes) == 2
        assert processes[0].pid == 1
        assert processes[0].command == "python app.py"
        assert processes[1].pid == 10
        assert processes[1].command == "python worker.py"

        mock_run.assert_called_once_with(
            ["docker", "exec", "test-container", "ps", "aux"],
            capture_output=True,
            text=True,
            check=True,
        )

    @patch("debugwand.container.subprocess.run")
    def test_list_python_processes_in_container_no_python(self, mock_run: MagicMock):
        """Test listing processes when no Python processes exist."""
        mock_run.return_value = MagicMock(
            stdout="""USER       PID %CPU %MEM    VSZ   RSS TTY      STAT START   TIME COMMAND
root         1  0.5  1.2 123456 12345 ?        Ss   10:00   0:01 nginx
""",
            returncode=0,
        )

        processes = list_python_processes("test-container")
        assert len(processes) == 0

    @patch("debugwand.container.list_python_processes")
    def test_debug_container_no_processes(self, mock_list_procs: MagicMock):
        """Test debug --container when no Python processes found."""
        mock_list_procs.return_value = []

        result = runner.invoke(app, ["debug", "--container", "test-container"])

        assert result.exit_code == 1
        assert "No Python processes found" in result.stderr

    @patch("debugwand.container.list_python_processes")
    def test_debug_container_invalid_pid(self, mock_list_procs: MagicMock):
        """Test debug --container with a PID that doesn't exist."""
        mock_list_procs.return_value = [
            ProcessInfo(
                pid=1, user="root", cpu_percent=0.5, mem_percent=1.2, command="python app.py"
            )
        ]

        result = runner.invoke(
            app, ["debug", "--container", "test-container", "--pid", "999"]
        )

        assert result.exit_code == 1
        assert "PID 999 not found" in result.stderr

    @patch("debugwand.container.list_python_processes")
    @patch("debugwand.container.inject_debugpy")
    @patch("debugwand.container.prepare_debugpy_script")
    @patch("debugwand.container.print_connection_info")
    def test_debug_container_auto_selects_single_process(
        self,
        mock_print_conn: MagicMock,
        mock_prepare_script: MagicMock,
        mock_inject: MagicMock,
        mock_list_procs: MagicMock,
    ):
        """Test that debug --container auto-selects when only one Python process."""
        mock_list_procs.return_value = [
            ProcessInfo(
                pid=1, user="root", cpu_percent=0.5, mem_percent=1.2, command="python app.py"
            )
        ]
        mock_prepare_script.return_value = "/tmp/test_script.py"

        # Use catch_exceptions=False so KeyboardInterrupt propagates
        # but we'll mock the sleep to raise it immediately
        with patch("debugwand.container.time.sleep", side_effect=KeyboardInterrupt):
            runner.invoke(app, ["debug", "--container", "test-container"])

        # Should have called inject with PID 1
        mock_inject.assert_called_once()
        call_args = mock_inject.call_args
        assert call_args[0][0] == "test-container"
        assert call_args[0][1] == 1

    @patch("debugwand.container.list_python_processes")
    def test_debug_container_container_not_found(self, mock_list_procs: MagicMock):
        """Test debug --container when container doesn't exist."""
        mock_list_procs.side_effect = subprocess.CalledProcessError(
            1, "docker exec", stderr="Error: No such container: bad-container"
        )

        result = runner.invoke(app, ["debug", "--container", "bad-container"])

        assert result.exit_code == 1
        assert "Failed to list processes" in result.stderr


class TestContainerReloadMode:
    """Tests for container reload mode detection and monitoring."""

    def test_detect_reload_mode_with_reload_flag(self):
        """Test detecting reload mode when --reload is present."""
        processes = [
            ProcessInfo(
                pid=1,
                user="root",
                cpu_percent=0.5,
                mem_percent=1.2,
                command="python -m uvicorn app:app --reload",
            ),
            ProcessInfo(
                pid=10,
                user="root",
                cpu_percent=0.1,
                mem_percent=0.5,
                command="python -c from multiprocessing.spawn import spawn_main",
            ),
        ]

        is_reload, worker = detect_reload_mode(processes)

        assert is_reload is True
        assert worker is not None
        assert worker.pid == 10

    def test_detect_reload_mode_no_reload_flag(self):
        """Test that reload mode is not detected without --reload."""
        processes = [
            ProcessInfo(
                pid=1,
                user="root",
                cpu_percent=0.5,
                mem_percent=1.2,
                command="python -m uvicorn app:app",
            ),
        ]

        is_reload, worker = detect_reload_mode(processes)

        assert is_reload is False
        assert worker is None

    def test_detect_reload_mode_reload_but_no_worker(self):
        """Test reload mode detected but worker not yet spawned."""
        processes = [
            ProcessInfo(
                pid=1,
                user="root",
                cpu_percent=0.5,
                mem_percent=1.2,
                command="python -m uvicorn app:app --reload",
            ),
        ]

        is_reload, worker = detect_reload_mode(processes)

        assert is_reload is True
        assert worker is None

    @patch("debugwand.container.list_python_processes")
    def test_monitor_worker_pid_no_change(self, mock_list_procs: MagicMock):
        """Test monitoring returns same PID when worker hasn't changed."""
        mock_list_procs.return_value = [
            ProcessInfo(
                pid=1,
                user="root",
                cpu_percent=0.5,
                mem_percent=1.2,
                command="python -m uvicorn app:app --reload",
            ),
            ProcessInfo(
                pid=10,
                user="root",
                cpu_percent=0.1,
                mem_percent=0.5,
                command="python -c from multiprocessing.spawn import spawn_main",
            ),
        ]

        result = monitor_worker_pid("test-container", 10)

        assert result == 10

    @patch("debugwand.container.list_python_processes")
    def test_monitor_worker_pid_changed(self, mock_list_procs: MagicMock):
        """Test monitoring detects when worker PID changes."""
        mock_list_procs.return_value = [
            ProcessInfo(
                pid=1,
                user="root",
                cpu_percent=0.5,
                mem_percent=1.2,
                command="python -m uvicorn app:app --reload",
            ),
            ProcessInfo(
                pid=20,
                user="root",
                cpu_percent=0.1,
                mem_percent=0.5,
                command="python -c from multiprocessing.spawn import spawn_main",
            ),
        ]

        result = monitor_worker_pid("test-container", 10)

        assert result == 20

    @patch("debugwand.container.list_python_processes")
    def test_monitor_worker_pid_container_gone(self, mock_list_procs: MagicMock):
        """Test monitoring returns None when container is gone."""
        mock_list_procs.return_value = []

        result = monitor_worker_pid("test-container", 10)

        assert result is None

    @patch("debugwand.container.list_python_processes")
    def test_monitor_worker_pid_no_longer_reload_mode(self, mock_list_procs: MagicMock):
        """Test monitoring returns None when no longer in reload mode."""
        mock_list_procs.return_value = [
            ProcessInfo(
                pid=1,
                user="root",
                cpu_percent=0.5,
                mem_percent=1.2,
                command="python -m uvicorn app:app",  # No --reload
            ),
        ]

        result = monitor_worker_pid("test-container", 10)

        assert result is None

    @patch("debugwand.container.list_python_processes")
    def test_monitor_worker_pid_exception(self, mock_list_procs: MagicMock):
        """Test monitoring returns None on exception."""
        mock_list_procs.side_effect = subprocess.CalledProcessError(1, "docker exec")

        result = monitor_worker_pid("test-container", 10)

        assert result is None
