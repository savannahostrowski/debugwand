import os
import tempfile
from unittest.mock import MagicMock, patch

from typer import Exit
from typer.testing import CliRunner

from debugwand.cli import app
from debugwand.types import PodInfo, ProcessInfo

runner = CliRunner()


class TestPodsCommand:
    """Tests for the 'pods' command."""

    @patch("debugwand.cli.get_pods_for_service_handler")
    def test_pods_no_pods_found(self, mock_get_pods: MagicMock):
        mock_get_pods.side_effect = Exit(1)
        result = runner.invoke(
            app, ["pods", "--namespace", "default", "--service", "test-service"]
        )
        assert result.exit_code == 1

        mock_get_pods.assert_called_once_with("default", "test-service")

    @patch("debugwand.cli.get_pods_for_service_handler")
    @patch("debugwand.cli.list_all_processes_with_details_handler")
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

    @patch("debugwand.cli.get_pods_for_service_handler")
    @patch("debugwand.cli.list_all_processes_with_details_handler")
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

    @patch("debugwand.cli.get_pods_for_service_handler")
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

    @patch("debugwand.cli.get_pods_for_service_handler")
    @patch("debugwand.cli.list_all_processes_with_details_handler")
    @patch("debugwand.cli.select_pod")
    @patch("debugwand.cli.select_pid")
    @patch("debugwand.cli.copy_to_pod")
    @patch("debugwand.cli.exec_command_in_pod")
    def test_inject_successful_injection(
        self,
        mock_exec_cmd: MagicMock,
        mock_copy_to_pod: MagicMock,
        mock_select_pid: MagicMock,
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
        mock_select_pid.return_value = 1234
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

    @patch("debugwand.cli.get_and_select_pod_handler")
    def test_debug_no_pods_found(self, mock_get_and_select_pod_handler: MagicMock):
        # Handler raises Exit when no pods found
        mock_get_and_select_pod_handler.side_effect = Exit(1)

        result = runner.invoke(
            app, ["debug", "--namespace", "default", "--service", "my-service"]
        )

        assert result.exit_code == 1

    @patch("debugwand.cli.get_and_select_pod_handler")
    @patch("debugwand.cli.get_and_select_process_handler")
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
