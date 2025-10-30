import os
import tempfile
from typer.testing import CliRunner
from unittest.mock import patch, MagicMock
from debugwand.cli import app
from debugwand.k8s import PodInfo, ProcessInfo

runner = CliRunner()


class TestPodsCommand:
    """Tests for the 'pods' command."""
    @patch('debugwand.cli.get_pods_for_service')
    def test_pods_no_pods_found(self, mock_get_pods: MagicMock):
        """Test the 'pods' command when no pods are found."""
        mock_get_pods.return_value = []
        result = runner.invoke(app, [
            'pods', 
            '--namespace',
            'default',
            '--service', 
            'test-service']
            )
        assert result.exit_code == 1
        assert "No pods found" in result.stderr

        mock_get_pods.assert_called_once_with(service='test-service', namespace='default')

    @patch('debugwand.cli.get_pods_for_service')
    def test_pods_with_pids_no_processes(self, mock_get_pods: MagicMock):
        """Test the 'pods' command with --with-pids when pods have no Python processes."""
        mock_pod = PodInfo(
            name="pod-1",
            namespace="default",
            node_name="node-1",
            status="Running",
            labels={"app": "test-app"},
        )

        mock_get_pods.return_value = [mock_pod]

        with patch('debugwand.cli.list_python_processes_with_details', return_value=[]) as mock_list_procs:
            result = runner.invoke(app, [
                'pods', 
                '--namespace',
                'default',
                '--service', 
                'test-service',
                '--with-pids']
                )

        assert result.exit_code == 0
        assert "No Python processes found." in result.stderr

        mock_get_pods.assert_called_once_with(service='test-service', namespace='default')
        mock_list_procs.assert_called_once_with(mock_pod)

    @patch('debugwand.cli.get_pods_for_service')
    @patch('debugwand.cli.list_python_processes_with_details')
    def test_pods_with_pids_with_processes(self, mock_list_procs: MagicMock, mock_get_pods: MagicMock):
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
            command="python app.py"
            
        )

        mock_get_pods.return_value = [mock_pod]
        mock_list_procs.return_value = [mock_process]

        result = runner.invoke(app, [
            'pods', 
            '--namespace',
            'default',
            '--service', 
            'test-service',
            '--with-pids']
            )

        assert result.exit_code == 0
        assert "PID: 1234" in result.stdout
        assert "User: root" in result.stdout
        assert "CMD: python app.py" in result.stdout

        mock_get_pods.assert_called_once_with(service='test-service', namespace='default')
        mock_list_procs.assert_called_once_with(mock_pod)


class TestValidateCommand:
    """Tests for the 'validate' command."""
    @patch('debugwand.cli.get_pods_for_service')
    def test_validate_missing_namespace(self, mock_get_pods: MagicMock):
        result = runner.invoke(app, [
            'validate', 
            '--service', 
            'test-service']
            )
        assert result.exit_code == 1
        assert "Error: --namespace is required." in result.stderr

    @patch('debugwand.cli.get_pods_for_service')
    def test_validate_missing_service(self, mock_get_pods: MagicMock):
        result = runner.invoke(app, [
            'validate', 
            '--namespace', 
            'default']
            )
        assert result.exit_code == 1
        assert "Error: --service is required." in result.stderr

    @patch('debugwand.cli.get_pods_for_service')
    def test_validate_no_pods_found(self, mock_get_pods: MagicMock):
        mock_get_pods.return_value = []
        result = runner.invoke(app, [
            'validate', 
            '--namespace',
            'default',
            '--service', 
            'test-service']
            )
        assert result.exit_code == 1
        assert "No pods found" in result.stderr

        mock_get_pods.assert_called_once_with(service='test-service', namespace='default')


class TestInjectCommand:
    """Tests for the 'inject' command."""
    @patch('debugwand.cli.get_pods_for_service')
    def test_inject_no_pods_found(self, mock_get_pods: MagicMock):
        mock_get_pods.return_value = []
        result = runner.invoke(app, [
            'inject', 
            '--namespace',
            'default',
            '--service', 
            'test-service',
            '--script',
            '/path/to/script.py']
            )
        assert result.exit_code == 1
        assert "No pods found" in result.stderr

        mock_get_pods.assert_called_once_with(service='test-service', namespace='default')

    @patch('debugwand.cli.get_pods_for_service')
    @patch('debugwand.cli.list_python_processes_with_details')
    @patch('debugwand.cli.select_pod')
    @patch('debugwand.cli.select_pid')
    @patch('debugwand.cli.copy_to_pod')
    @patch('debugwand.cli.exec_command_in_pod')
    def test_inject_successful_injection(
        self,
        mock_exec_cmd: MagicMock,
        mock_copy_to_pod: MagicMock,
        mock_select_pid: MagicMock,
        mock_select_pod: MagicMock,
        mock_list_procs: MagicMock,
        mock_get_pods: MagicMock ):
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
            command="python app.py"
        )

        mock_get_pods.return_value = [mock_pod]
        mock_list_procs.return_value = [mock_process]
        mock_select_pod.return_value = mock_pod
        mock_select_pid.return_value = 1234
        mock_exec_cmd.return_value = "Injection successful"

        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as temp_script:
            temp_script.write("print('Hello from injected script')")
            temp_script_path = temp_script.name
        
        try:

            result = runner.invoke(app, [
                'inject', 
                '--namespace',
                'default',
                '--service', 
                'test-service',
                '--script',
                '/path/to/script.py']
                )
            assert result.exit_code == 0
            assert "Executing script '/path/to/script.py'" in result.stdout

            mock_copy_to_pod.assert_called()
            mock_exec_cmd.assert_called()
        finally:
            os.unlink(temp_script_path)


class TestDebugCommand:
    """Tests for the 'debug' command."""
    @patch('debugwand.cli.get_and_select_pod')
    def test_debug_no_pods_found(self, mock_get_and_select_pod: MagicMock):
        # Make it raise ValueError like the real function does
        mock_get_and_select_pod.side_effect = ValueError("No pods found matching the criteria.")

        result = runner.invoke(app, [
            'debug',
            '--namespace', 'default',
            '--service', 'my-service'
        ])
        
        assert result.exit_code == 1
        assert "No pods found" in result.stderr

    @patch('debugwand.cli.get_and_select_pod')
    @patch('debugwand.cli.get_and_select_process')
    def test_debug_invalid_pid(self, mock_get_and_select_process: MagicMock, mock_get_and_select_pod: MagicMock):
        """Test debug with an invalid PID."""
        mock_pod = PodInfo(
            name="pod-1",
            namespace="default",
            node_name="node-1",
            status="Running",
            labels={"app": "test-app"},
        )
        
        # get_and_select_pod returns a pod
        mock_get_and_select_pod.return_value = mock_pod
        
        # get_and_select_process raises ValueError for invalid PID
        mock_get_and_select_process.side_effect = ValueError("PID 9999 not found in the Python processes of the selected pod.")
        
        result = runner.invoke(app, [
            'debug',
            '--namespace', 'default',
            '--service', 'test-service',
            '--pid', '9999'
        ])
        
        assert result.exit_code == 1
        assert "PID 9999 not found" in result.stderr

