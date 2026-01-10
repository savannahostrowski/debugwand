# debugwand 🪄

A zero-preparation remote debugger for Python applications running in Kubernetes clusters or Docker containers.

*Made possible by the Python 3.14 [remote debugging attachment protocol](https://docs.python.org/3/howto/remote_debugging.html) and [debugpy](https://github.com/microsoft/debugpy)*

> Note: `debugwand` is experimental and not made for production. Use at your own risk.

## Features

- **Zero-preparation debugging** - No code changes or restarts required
- **Full breakpoint debugging** - Using `debugpy`
- **Kubernetes-native** - Handles pod discovery, service routing, and Knative
- **Docker container support** - Debug Python processes in local containers
- **Process selection** - Interactive selection with CPU/memory metrics

## Requirements

- **Python 3.14+** on both local machine and target
- **debugpy** installed in the target container
- **kubectl** (for Kubernetes) or **Docker CLI** (for containers)
- **SYS_PTRACE capability** - on Linux/containers (see [troubleshooting](docs/troubleshooting.md))

## Quick Start

### Kubernetes

```bash
# List pods and Python processes
wand pods -n my-namespace -s my-service --with-pids

# Debug a live process
wand debug -n my-namespace -s my-service
```

### Docker

```bash
# Debug a container (must have SYS_PTRACE capability)
wand debug --container my-container
```

> Containers must be started with `--cap-add=SYS_PTRACE` and `-p 5679:5679`

### Connect your editor

**VSCode** launch configuration:

```json
{
  "name": "Attach to debugwand",
  "type": "debugpy",
  "request": "attach",
  "connect": { "host": "localhost", "port": 5679 },
  "pathMappings": [{ "localRoot": "${workspaceFolder}", "remoteRoot": "/app" }]
}
```

**Other DAP clients**: Connect to `localhost:5679`

## Configuration

| Environment Variable | Description |
|---------------------|-------------|
| `DEBUGWAND_SIMPLE_UI` | Set to `1` for simplified output (useful for Tilt/CI) |
| `DEBUGWAND_AUTO_SELECT_POD` | Set to `1` to auto-select the newest pod |

## Additional Documentation

- **[Hot-Reload Support](docs/hot-reload.md)** - Debugging with uvicorn `--reload` mode
- **[Troubleshooting](docs/troubleshooting.md)** - Common issues and solutions

## How it works

```
┌─────────────────┐                    ┌────────────────────┐
│  Local Machine  │                    │   Pod / Container  │
│                 │                    │                    │
│  debugwand CLI  │◄─ kubectl/docker ─►│   Python App       │
└────────┬────────┘                    └────────┬───────────┘
         │                                      │
         │ 1. Discover pods (k8s only)          │
         ├─────────────────────────────────────►│
         │                                      │
         │ 2. List Python processes             │
         ├─────────────────────────────────────►│
         │                                      │
         │ 3. Select process                    │
         │                                      │
         │ 4. Inject debugpy via                │
         │    sys.remote_exec()                 │
         ├─────────────────────────────────────►│
         │                                      │
         │                    5. debugpy.listen()
         │                       ┌──────────────┤
         │ 6. Port-forward (k8s) │              │
         │    or exposed port    │              │
         │◄──────────────────────┼─────────────►│
         │    localhost:5679     │              │
         │                       └──────────────┤
         │ 7. Connect editor                    │
         ├──────────────────────────────────────┤
         │         Debugging Session            │
         │◄────────────────────────────────────►│
```

## License

MIT
