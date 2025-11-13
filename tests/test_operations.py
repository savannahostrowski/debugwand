from unittest.mock import MagicMock, mock_open, patch

import pytest

from debugwand.operations import (
    get_and_select_pod,
    get_and_select_process,
    monitor_worker_pid,
    prepare_debugpy_script,
)
from debugwand.types import PodInfo, ProcessInfo


class TestGetAndSelectPod:
    """Tests for get_and_select_pod function."""

    @patch("debugwand.operations.get_pods_for_service")
    def test_no_pods_raises_error(self, mock_get_pods: MagicMock):
        """Test that ValueError is raised when no pods are found."""
        mock_get_pods.return_value = []

        with pytest.raises(ValueError, match="No pods found matching the criteria"):
            get_and_select_pod(service="test-service", namespace="default")

    @patch("debugwand.operations.get_pods_for_service")
    @patch("debugwand.operations.select_pod")
    def test_single_pod_returned(
        self, mock_select: MagicMock, mock_get_pods: MagicMock
    ):
        """Test that a single pod is returned successfully."""
        pod = PodInfo(
            name="pod-1",
            namespace="default",
            node_name="node-1",
            status="Running",
            labels={"app": "test"},
            creation_time="2025-01-01T00:00:00Z",
        )
        mock_get_pods.return_value = [pod]
        mock_select.return_value = pod

        result = get_and_select_pod(service="test-service", namespace="default")

        assert result == pod
        mock_get_pods.assert_called_once_with(
            service="test-service", namespace="default"
        )
        mock_select.assert_called_once_with([pod])

    @patch("debugwand.operations.get_pods_for_service")
    @patch("debugwand.operations.select_pod")
    def test_multiple_pods_calls_select(
        self, mock_select: MagicMock, mock_get_pods: MagicMock
    ):
        """Test that select_pod is called when multiple pods exist."""
        pod1 = PodInfo(
            "pod-1",
            "default",
            "node-1",
            "Running",
            {"app": "test"},
            "2025-01-01T00:00:00Z",
        )
        pod2 = PodInfo(
            "pod-2",
            "default",
            "node-2",
            "Running",
            {"app": "test"},
            "2025-01-01T01:00:00Z",
        )

        mock_get_pods.return_value = [pod1, pod2]
        mock_select.return_value = pod1  # User selects first pod

        result = get_and_select_pod(service="test-service", namespace="default")

        assert result == pod1
        mock_select.assert_called_once_with([pod1, pod2])


class TestGetAndSelectProcess:
    """Tests for get_and_select_process function."""

    @patch("debugwand.operations.list_python_processes_with_details")
    def test_no_processes_raises_error(self, mock_list: MagicMock):
        """Test that ValueError is raised when no processes are found."""
        mock_list.return_value = []
        pod = PodInfo(
            "pod-1", "default", "node-1", "Running", {}, "2025-01-01T00:00:00Z"
        )

        with pytest.raises(ValueError, match="No Python processes found"):
            get_and_select_process(pod, None)

    @patch("debugwand.operations.list_python_processes_with_details")
    def test_valid_pid_returned(self, mock_list: MagicMock):
        """Test that a valid PID is accepted and returned."""
        process = ProcessInfo(
            pid=1234,
            user="root",
            cpu_percent=0.5,
            mem_percent=1.2,
            command="python app.py",
        )
        mock_list.return_value = [process]
        pod = PodInfo(
            "pod-1", "default", "node-1", "Running", {}, "2025-01-01T00:00:00Z"
        )

        result = get_and_select_process(pod, 1234)

        assert result == 1234
        mock_list.assert_called_once_with(pod)

    @patch("debugwand.operations.list_python_processes_with_details")
    def test_invalid_pid_raises_error(self, mock_list: MagicMock):
        """Test that ValueError is raised for invalid PID."""
        process = ProcessInfo(1234, "root", 0.5, 1.2, "python app.py")
        mock_list.return_value = [process]
        pod = PodInfo(
            "pod-1", "default", "node-1", "Running", {}, "2025-01-01T00:00:00Z"
        )

        with pytest.raises(ValueError, match="PID 9999 not found"):
            get_and_select_process(pod, 9999)

    @patch("debugwand.operations.list_python_processes_with_details")
    @patch("debugwand.operations.select_pid")
    def test_no_pid_provided_calls_select(
        self, mock_select: MagicMock, mock_list: MagicMock
    ):
        """Test that select_pid is called when no PID is provided."""
        process = ProcessInfo(1234, "root", 0.5, 1.2, "python app.py")
        mock_list.return_value = [process]
        mock_select.return_value = 1234
        pod = PodInfo(
            "pod-1", "default", "node-1", "Running", {}, "2025-01-01T00:00:00Z"
        )

        result = get_and_select_process(pod, None)

        assert result == 1234
        mock_select.assert_called_once_with([process])


class TestPrepareDebugpyScript:
    """Tests for prepare_debugpy_script function."""

    @patch(
        "builtins.open",
        new_callable=mock_open,
        read_data="debugpy.listen({PORT})\nif {WAIT}: debugpy.wait_for_client()",
    )
    @patch("tempfile.NamedTemporaryFile")
    def test_script_preparation_with_defaults(
        self, mock_temp: MagicMock, mock_file: MagicMock
    ):
        """Test that script is prepared correctly with default values."""
        # Mock temp file
        temp_file = MagicMock()
        temp_file.name = "/tmp/test_script.py"
        mock_temp.return_value.__enter__.return_value = temp_file

        result = prepare_debugpy_script(port=5679)

        assert result == "/tmp/test_script.py"
        # Verify the script content was modified correctly
        temp_file.write.assert_called_once()
        written_content = temp_file.write.call_args[0][0]
        assert "5679" in written_content
        assert "True" in written_content

    @patch(
        "builtins.open",
        new_callable=mock_open,
        read_data="debugpy.listen({PORT})\nif {WAIT}: debugpy.wait_for_client()",
    )
    @patch("tempfile.NamedTemporaryFile")
    def test_script_preparation_custom_values(
        self, mock_temp: MagicMock, mock_file: MagicMock
    ):
        """Test that script is prepared with custom port and wait values."""
        temp_file = MagicMock()
        temp_file.name = "/tmp/custom_script.py"
        mock_temp.return_value.__enter__.return_value = temp_file

        result = prepare_debugpy_script(port=8080, wait=False)

        assert result == "/tmp/custom_script.py"
        written_content = temp_file.write.call_args[0][0]
        assert "8080" in written_content
        assert "False" in written_content


class TestMonitorWorkerPid:
    """Tests for monitor_worker_pid function."""

    @patch("debugwand.operations.list_python_processes_with_details")
    @patch("debugwand.operations.detect_reload_mode")
    def test_keeps_monitoring_when_worker_not_found(
        self, mock_detect: MagicMock, mock_list_procs: MagicMock
    ):
        """Test that monitoring continues when worker process is temporarily not found.

        This handles the case where a worker is frozen at a breakpoint or in transition,
        and detect_reload_mode returns (True, None). The session should stay alive.
        """
        pod = PodInfo(
            name="test-pod",
            namespace="default",
            node_name="node-1",
            status="Running",
            labels={"app": "test"},
            creation_time="2025-01-01T00:00:00Z",
        )

        # Simulate reload mode detected but worker process not found
        # (e.g., worker frozen at breakpoint)
        mock_list_procs.return_value = [
            ProcessInfo(
                pid=1,
                user="root",
                cpu_percent=0.1,
                mem_percent=0.5,
                command="python -m fastapi run app/api/main.py --reload",
            )
        ]
        mock_detect.return_value = (True, None)  # is_reload=True, worker_proc=None

        result = monitor_worker_pid(pod, initial_pid=123)

        # Should return initial_pid to keep monitoring, not None
        assert result == 123
        mock_list_procs.assert_called_once_with(pod)
        mock_detect.assert_called_once()

    @patch("debugwand.operations.list_python_processes_with_details")
    @patch("debugwand.operations.detect_reload_mode")
    def test_detects_pid_change(
        self, mock_detect: MagicMock, mock_list_procs: MagicMock
    ):
        """Test that PID change is detected when worker restarts."""
        pod = PodInfo(
            name="test-pod",
            namespace="default",
            node_name="node-1",
            status="Running",
            labels={"app": "test"},
            creation_time="2025-01-01T00:00:00Z",
        )

        new_worker = ProcessInfo(
            pid=456,
            user="root",
            cpu_percent=0.2,
            mem_percent=1.0,
            command="python -c from multiprocessing.spawn import spawn_main",
        )

        mock_list_procs.return_value = [new_worker]
        mock_detect.return_value = (True, new_worker)

        result = monitor_worker_pid(pod, initial_pid=123)

        # Should return new PID
        assert result == 456

    @patch("debugwand.operations.list_python_processes_with_details")
    @patch("debugwand.operations.detect_reload_mode")
    def test_stops_monitoring_when_not_reload_mode(
        self, mock_detect: MagicMock, mock_list_procs: MagicMock
    ):
        """Test that monitoring stops when reload mode is no longer detected."""
        pod = PodInfo(
            name="test-pod",
            namespace="default",
            node_name="node-1",
            status="Running",
            labels={"app": "test"},
            creation_time="2025-01-01T00:00:00Z",
        )

        mock_list_procs.return_value = [
            ProcessInfo(
                pid=1,
                user="root",
                cpu_percent=0.1,
                mem_percent=0.5,
                command="python app.py",  # No --reload
            )
        ]
        mock_detect.return_value = (False, None)

        result = monitor_worker_pid(pod, initial_pid=123)

        # Should return None to stop monitoring
        assert result is None
