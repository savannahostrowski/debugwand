"""Tests for k8s module - focusing on logic and parsing."""

import json
from unittest.mock import MagicMock, patch

import pytest
import typer

from debugwand.operations import (
    copy_to_pod,
    exec_command_in_pod,
    get_pods_by_label,
    get_pods_for_service,
    list_python_processes_with_details,
    select_pid,
    select_pod,
)
from debugwand.types import PodInfo, ProcessInfo


class TestSelectPod:
    """Tests for select_pod function."""

    def test_empty_list_raises_error(self):
        """Test that typer.Exit is raised when pod list is empty."""
        with pytest.raises(typer.Exit) as exc_info:
            select_pod([])
        assert exc_info.value.exit_code == 1

    def test_single_pod_auto_selected(self):
        """Test that a single pod is automatically selected without prompting."""
        pod = PodInfo(
            "pod-1",
            "default",
            "node-1",
            "Running",
            {"app": "test"},
            "2025-01-01T00:00:00Z",
        )

        result = select_pod([pod])

        assert result == pod

    # Note: Can't easily test interactive selection without mocking input()


class TestSelectPid:
    """Tests for select_pid function."""

    def test_empty_list_raises_error(self):
        """Test that ValueError is raised when process list is empty."""
        with pytest.raises(ValueError, match="No Python processes found"):
            select_pid([])

    def test_single_process_auto_selected(self):
        """Test that a single process PID is automatically returned."""
        process = ProcessInfo(1234, "root", 0.5, 1.2, "python app.py")

        result = select_pid([process])

        assert result == 1234


class TestGetPodsByLabel:
    """Tests for get_pods_by_label function - focuses on JSON parsing."""

    @patch("subprocess.run")
    def test_parses_pods_correctly(self, mock_run: MagicMock):
        """Test that kubectl JSON output is parsed correctly into PodInfo objects."""
        mock_output = {
            "items": [
                {
                    "metadata": {
                        "name": "app-pod-1",
                        "namespace": "production",
                        "labels": {"app": "myapp", "version": "v1"},
                        "creationTimestamp": "2025-01-01T10:00:00Z",
                    },
                    "spec": {"nodeName": "node-1"},
                    "status": {"phase": "Running"},
                },
                {
                    "metadata": {
                        "name": "app-pod-2",
                        "namespace": "production",
                        "labels": {"app": "myapp", "version": "v2"},
                        "creationTimestamp": "2025-01-01T11:00:00Z",
                    },
                    "spec": {"nodeName": "node-2"},
                    "status": {"phase": "Pending"},
                },
            ]
        }

        mock_result = MagicMock()
        mock_result.stdout = json.dumps(mock_output)
        mock_run.return_value = mock_result

        pods = get_pods_by_label(namespace="production", label_selector="app=myapp")

        assert len(pods) == 2
        assert pods[0].name == "app-pod-1"
        assert pods[0].namespace == "production"
        assert pods[0].node_name == "node-1"
        assert pods[0].status == "Running"
        assert pods[0].labels == {"app": "myapp", "version": "v1"}

        assert pods[1].name == "app-pod-2"
        assert pods[1].status == "Pending"

    @patch("subprocess.run")
    def test_handles_empty_items(self, mock_run: MagicMock):
        """Test that empty items list returns empty pod list."""
        mock_output: dict[str, list[dict[str, object]]] = {"items": []}

        mock_result = MagicMock()
        mock_result.stdout = json.dumps(mock_output)
        mock_run.return_value = mock_result

        pods = get_pods_by_label(namespace="default", label_selector="app=test")

        assert pods == []

    @patch("subprocess.run")
    def test_handles_missing_node_name(self, mock_run: MagicMock):
        """Test that missing nodeName is handled gracefully."""
        mock_output: dict[str, list[dict[str, object]]] = {
            "items": [
                {
                    "metadata": {
                        "name": "pending-pod",
                        "namespace": "default",
                        "labels": {},
                        "creationTimestamp": "2025-01-01T00:00:00Z",
                    },
                    "spec": {},  # No nodeName - pod not scheduled yet
                    "status": {"phase": "Pending"},
                }
            ]
        }

        mock_result = MagicMock()
        mock_result.stdout = json.dumps(mock_output)
        mock_run.return_value = mock_result

        pods = get_pods_by_label(namespace="default", label_selector=None)

        assert len(pods) == 1
        assert pods[0].node_name == ""  # Empty string for missing nodeName


class TestGetPodsForService:
    """Tests for get_pods_for_service - service type handling."""

    @patch("subprocess.run")
    def test_standard_service_with_selector(self, mock_run: MagicMock):
        """Test standard ClusterIP service with selector."""
        service_output = {
            "spec": {
                "type": "ClusterIP",
                "selector": {"app": "myapp", "tier": "frontend"},
            }
        }

        pods_output: dict[str, list[dict[str, object]]] = {
            "items": [
                {
                    "metadata": {
                        "name": "pod-1",
                        "namespace": "default",
                        "labels": {},
                        "creationTimestamp": "2025-01-01T00:00:00Z",
                    },
                    "spec": {"nodeName": "node-1"},
                    "status": {"phase": "Running"},
                }
            ]
        }

        # First call: get service, second call: get pods
        mock_run.side_effect = [
            MagicMock(stdout=json.dumps(service_output), returncode=0, stderr=""),
            MagicMock(stdout=json.dumps(pods_output), returncode=0, stderr=""),
        ]

        pods = get_pods_for_service(namespace="default", service="myapp-service")

        assert len(pods) == 1
        # Verify kubectl was called with correct label selector
        second_call_args = mock_run.call_args_list[1][0][0]
        assert "-l" in second_call_args
        label_idx = second_call_args.index("-l") + 1
        assert second_call_args[label_idx] == "app=myapp,tier=frontend"

    @patch("subprocess.run")
    def test_knative_external_name_service(self, mock_run: MagicMock):
        """Test Knative ExternalName service uses different label selector."""
        service_output = {"spec": {"type": "ExternalName"}}

        pods_output: dict[str, list[dict[str, object]]] = {"items": []}

        mock_run.side_effect = [
            MagicMock(stdout=json.dumps(service_output), returncode=0, stderr=""),
            MagicMock(stdout=json.dumps(pods_output), returncode=0, stderr=""),
        ]

        get_pods_for_service(namespace="default", service="knative-service")

        # Verify kubectl was called with Knative label selector
        second_call_args = mock_run.call_args_list[1][0][0]
        assert "-l" in second_call_args
        label_idx = second_call_args.index("-l") + 1
        assert (
            second_call_args[label_idx] == "serving.knative.dev/service=knative-service"
        )


class TestListPythonProcessesWithDetails:
    """Tests for list_python_processes_with_details - ps aux parsing."""

    @patch("subprocess.run")
    def test_parses_ps_aux_output(self, mock_run: MagicMock):
        """Test that ps aux output is parsed correctly."""
        ps_output = """USER       PID %CPU %MEM    VSZ   RSS TTY      STAT START   TIME COMMAND
root         1  0.1  0.5  12345  6789 ?        Ss   10:00   0:01 /usr/bin/python3 /app/main.py --port 8080
appuser     42  1.5  2.3  45678 12345 ?        Sl   10:01   0:15 python3 -m gunicorn app:application
root       123  0.0  0.0   5678  1234 ?        R    10:30   0:00 ps aux"""

        mock_result = MagicMock()
        mock_result.stdout = ps_output
        mock_run.return_value = mock_result

        pod = PodInfo(
            "test-pod", "default", "node-1", "Running", {}, "2025-01-01T00:00:00Z"
        )
        processes = list_python_processes_with_details(pod)

        assert len(processes) == 2  # Only Python processes, not 'ps aux'

        # First process
        assert processes[0].pid == 1
        assert processes[0].user == "root"
        assert processes[0].cpu_percent == 0.1
        assert processes[0].mem_percent == 0.5
        assert "/usr/bin/python3 /app/main.py --port 8080" in processes[0].command

        # Second process
        assert processes[1].pid == 42
        assert processes[1].user == "appuser"
        assert processes[1].cpu_percent == 1.5
        assert processes[1].mem_percent == 2.3
        assert "gunicorn" in processes[1].command

    @patch("subprocess.run")
    def test_handles_no_python_processes(self, mock_run: MagicMock):
        """Test that empty list is returned when no Python processes found."""
        ps_output = """USER       PID %CPU %MEM    VSZ   RSS TTY      STAT START   TIME COMMAND
root         1  0.0  0.1  12345  6789 ?        Ss   10:00   0:01 /bin/bash
root        42  0.0  0.0   5678  1234 ?        R    10:30   0:00 ps aux"""

        mock_result = MagicMock()
        mock_result.stdout = ps_output
        mock_run.return_value = mock_result

        pod = PodInfo(
            "test-pod", "default", "node-1", "Running", {}, "2025-01-01T00:00:00Z"
        )
        processes = list_python_processes_with_details(pod)

        assert processes == []

    def test_raises_error_for_non_running_pod(self):
        """Test that ValueError is raised for non-running pods."""
        pod = PodInfo(
            "pending-pod", "default", "node-1", "Pending", {}, "2025-01-01T00:00:00Z"
        )

        with pytest.raises(ValueError, match="not running"):
            list_python_processes_with_details(pod)


class TestExecCommandInPod:
    """Tests for exec_command_in_pod."""

    @patch("subprocess.run")
    def test_successful_command_execution(self, mock_run: MagicMock):
        """Test that successful command execution returns stdout."""
        mock_result = MagicMock()
        mock_result.stdout = "Command output\n"
        mock_result.stderr = ""
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        pod = PodInfo(
            "test-pod", "default", "node-1", "Running", {}, "2025-01-01T00:00:00Z"
        )
        output = exec_command_in_pod(pod, ["echo", "hello"])

        assert output == "Command output\n"

        # Verify kubectl exec was called correctly
        call_args = mock_run.call_args[0][0]
        assert call_args[:5] == ["kubectl", "exec", "test-pod", "-n", "default"]
        assert call_args[5:] == ["--", "echo", "hello"]

    @patch("subprocess.run")
    def test_failed_command_raises_error(self, mock_run: MagicMock):
        """Test that failed command raises CalledProcessError."""
        mock_result = MagicMock()
        mock_result.stdout = ""
        mock_result.stderr = "Error: command not found"
        mock_result.returncode = 127
        mock_run.return_value = mock_result

        pod = PodInfo(
            "test-pod", "default", "node-1", "Running", {}, "2025-01-01T00:00:00Z"
        )

        with pytest.raises(Exception):  # CalledProcessError
            exec_command_in_pod(pod, ["nonexistent-command"])


class TestCopyToPod:
    """Tests for copy_to_pod."""

    @patch("subprocess.run")
    def test_copy_to_pod_calls_kubectl_cp(self, mock_run: MagicMock):
        """Test that kubectl cp is called with correct arguments."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        pod = PodInfo(
            "test-pod", "production", "node-1", "Running", {}, "2025-01-01T00:00:00Z"
        )
        copy_to_pod(pod, "/local/path/file.py", "/tmp/file.py")

        # Verify kubectl cp was called correctly
        call_args = mock_run.call_args[0][0]
        assert call_args == [
            "kubectl",
            "cp",
            "/local/path/file.py",
            "production/test-pod:/tmp/file.py",
        ]
