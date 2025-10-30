# debugwand 🪄

A (very experimental) basic CLI for connecting your debugger to live Python processes running in Kubernetes.

Built on Python 3.14+'s [remote debugging attachment protocol](https://docs.python.org/3/howto/remote_debugging.html) and [debugpy](https://github.com/microsoft/debugpy)

> Note: debugwand is experimental and not made for production. Use at your own risk.

## Features

- **Zero-preparation debugging** - No code changes or restarts required
- **Live process injection** - Attach to running production processes
- **Full breakpoint debugging** - Using debugpy
- **Kubernetes-native** - Handles pod discovery, service routing, and Knative
- **Process selection** - Interactive selection with CPU/memory metrics
- **Script execution** - Run arbitrary Python code in remote processes

## Configuration

You must have `SYS_PTRACE` capability enabled on target containers. This is a requirement of the [remote debugger attachment protocol](https://docs.python.org/3/howto/remote_debugging.html). You can validate your setup with:

```bash
uv run wand validate -n <namespace> -s <service>
```

TBD

## Quick Start

### 1. List pods and processes

```bash

# List pods for a specific service
wand pods -n my-namespace -s my-service

# Show Python processes in pods
wand pods -n my-namespace -s my-service --with-pids
```

### 2. Debug a live process

```bash
wand debug -n my-namespace -s my-service

Options:
  -n, --namespace TEXT        Kubernetes namespace
  -s, --service TEXT          Service name
  -p, --port INTEGER = 5679   Debug server port
  --auto-forward / --no-auto-forward
                              Automatically start kubectl port-forward (default: true)
  --pid, --pid INTEGER          Process ID to attach to (optional)
```

This will:
1. Find pods for the service
2. Show Python processes with CPU/memory usage
3. Let you select which process to debug
4. Inject debugpy into the process
5. Automatically port-forward to your local machine
6. Show connection instructions for your editor

### 3. Connect your editor

**VSCode**: Press F5 or use this launch configuration:

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "Attach to Kubernetes Pod",
      "type": "debugpy",
      "request": "attach",
      "connect": {
        "host": "localhost",
        "port": 5679
      },
      "pathMappings": [
        {
          "localRoot": "${workspaceFolder}",
          "remoteRoot": "/app"
        }
      ]
    }
  ]
}
```

**Neovim/Other DAP clients**: Connect to `localhost:5679`


## How It Works

debugwand uses Python 3.13+'s remote debugging attachment protocol:

1. **Discover** - Finds pods via Kubernetes API (supports Knative services)
2. **Select** - Shows Python processes with CPU/memory metrics
3. **Inject** - Uses `sys.remote_exec()` to inject code into the target process
4. **Debug** - Starts debugpy server in the target process
5. **Connect** - Editor attaches via DAP protocol for full debugging

The target process continues running normally - your injected code executes asynchronously at the next safe opportunity (similar to signal handlers).

## Requirements

### Local Machine (debugwand CLI)
- **Python 3.14+** (uses `sys.remote_exec()`)
- **kubectl** configured with cluster access

### Target Pods
- **Python 3.14+** runtime
- **debugpy** installed in the container (for `debug` command)
- **CAP_SYS_PTRACE** capability enabled (check with `wand validate`)


## Other notes

### Knative Services

debugwand automatically handles Knative services by detecting ExternalName services and finding pods via `serving.knative.dev/service` labels.

### Multiple Pods

If a service has multiple pods, debugwand will prompt you to select one. Use the CPU/memory metrics to choose the right instance.

## Troubleshooting

### "No module named 'debugpy'"

The target pod doesn't have debugpy installed. Either:
1. Add debugpy to your application dependencies
2. Use `wand exec` to install it: `pip install debugpy`

### "Permission denied" or "Operation not permitted"

The pod needs `SYS_PTRACE` capability. Check with:

```bash
wand validate -n <namespace> -s <service>
```

Add to your pod spec:

```yaml
securityContext:
  capabilities:
    add:
      - SYS_PTRACE
```

### Debugger won't attach

1. Check port-forward is running: `lsof -i :5679` (or use https://github.com/savannahostrowski/gruyere 🤗)
2. Check debugpy is listening: `kubectl logs <pod> | grep debugpy`
3. Verify path mappings in `launch.json` or DAP config
4. Check Python version compatibility (3.14+ required)

### Breakpoints not hitting

**Reload mode detection:** If your app runs with `--reload` (FastAPI, Flask, etc.), debugwand automatically detects this and injects debugpy into the **worker process** instead of the parent. You'll see:

```
⚠️  RELOAD MODE DETECTED
Auto-selecting worker process: PID 145
```

**Path mappings:** Ensure your `launch.json` maps local to remote paths correctly:

```json
{
  "pathMappings": [
    {
      "localRoot": "${workspaceFolder}/backend/app",
      "remoteRoot": "/app/backend/app"
    }
  ]
}
```

To find the correct remote path, check the debugwand output for:
```
[MANTIS] Process working directory: /app/backend
```

**Multiple pods:** If you have multiple replicas, requests may be load-balanced to a different pod than the one you're debugging. Scale down to 1 replica during debugging:

```bash
kubectl scale deployment <deployment-name> -n <namespace> --replicas=1
```


## Architecture

```
┌─────────────────┐                    ┌──────────────────┐
│  Local Machine  │                    │  Kubernetes Pod  │
│                 │                    │                  │
│  debugwand CLI     │◄───── kubectl ────►│   Python App     │
└────────┬────────┘                    └────────┬─────────┘
         │                                      │
         │ 1. Discover pods                     │
         ├─────────────────────────────────────►│
         │                                      │
         │ 2. List Python processes             │
         │◄─────────────────────────────────────┤
         │                                      │
         │ 3. Select process (auto-detect       │
         │    reload mode and choose worker)    │
         │                                      │
         │ 4. Inject debugpy via sys.remote_exec│
         ├─────────────────────────────────────►│
         │                                      │
         │                   5. debugpy.listen()│
         │                    ┌─────────────────┤
         │                    │                 │
         │ 6. Port-forward    │                 │
         │◄───────────────────┼────────────────►│
         │    localhost:5679  │                 │
         │                    └─────────────────┤
         │                                      │
         │ 7. Connect VS Code                   │
         ├──────────────────────────────────────┤
         │         Debugging Session            │
         │◄────────────────────────────────────►│
         │                                      │
```

## Contributing

Contributions welcome! This project is built with:
- [Typer](https://typer.tiangolo.com/) - CLI framework
- [Rich](https://rich.readthedocs.io/) - Terminal formatting
- Python 3.14+ remote debugging protocol

## License

MIT
