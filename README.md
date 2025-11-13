# debugwand 🪄

A zero-preparation remote debugger for Python applications running in local Kubernetes clusters.

*Made possible by the Python 3.14 [remote debugging attachment protocol](https://docs.python.org/3/howto/remote_debugging.html) and [debugpy](https://github.com/microsoft/debugpy)*

> Note: `debugwand` is experimental and not made for production. Use at your own risk.

## Features

- **Zero-preparation debugging** - No code changes or restarts required
- **Full breakpoint debugging** - Using `debugpy`
- **Kubernetes-native** - Handles pod discovery, service routing, and Knative
- **Process selection** - Interactive selection with CPU/memory metrics
- **Script execution** - Run arbitrary Python code in remote processes

## Quick Start

### 1. List pods and processes

```bash
# List pods for a specific service
wand pods -n my-namespace -s my-service

# Show Python processes in pods
wand pods -n my-namespace -s my-service --with-pids
```

### 2. Debug a live process

To start a debugging session, run:
```bash
wand debug -n my-namespace -s my-service
```

This will:
1. Find pods for the service
2. Let you select which process to debug
3. Inject `debugpy` into the process (non-blocking - app continues running)
4. Automatically port-forward to your local machine
5. Your app continues serving requests - connect your debugger anytime!

![](debug.png)

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

## Requirements

### Local Machine (debugwand CLI)
- **Python 3.14+** (uses `sys.remote_exec()`)
- **kubectl** configured with cluster access

### Target Pods
- **Python 3.14+** runtime
- **debugpy** installed in the container (for `debug` command)


## Configuration

### Environment Variables

- **`DEBUGWAND_SIMPLE_UI`**: Set to `1` to enable simplified UI output (useful for CI/CD or Tilt)
- **`DEBUGWAND_AUTO_SELECT_POD`**: Set to `1` to automatically select the newest pod when multiple are found
  - `1`: Auto-select newest pod by creation time
  - `0` or unset: Prompt user to select (default behavior)

Example:
```bash
export DEBUGWAND_SIMPLE_UI=1
export DEBUGWAND_AUTO_SELECT_POD=1
wand debug -n my-namespace -s my-service
```

This is especially useful for non-interactive environments like Tilt or CI/CD pipelines.

## Other notes

### Knative Services

debugwand automatically handles Knative services by detecting ExternalName services and finding pods via `serving.knative.dev/service` labels.

### Multiple Pods

If a service has multiple pods, debugwand will prompt you to select one (unless `DEBUGWAND_AUTO_SELECT_POD` is set). Use the CPU/memory metrics to choose the right instance.

When `DEBUGWAND_AUTO_SELECT_POD=1` is set, debugwand automatically selects the most recently created pod. This is useful for **Knative deployments** with multiple revisions during rollouts, **CI/CD pipelines** that need non-interactive pod selection, and **development workflows** (like Tilt) where you typically want the newest deployment.

## Additional Documentation

- **[Hot-Reload Support](docs/hot-reload.md)** - Debugging with uvicorn `--reload` mode
- **[Troubleshooting](docs/troubleshooting.md)** - Common issues and solutions

## Architecture

```
┌─────────────────┐                    ┌──────────────────┐
│  Local Machine  │                    │  Kubernetes Pod  │
│                 │                    │                  │
│  debugwand CLI  │◄───── kubectl ────►│   Python App     │
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
         │4. Inject `debugpy script via         │
         │  (`sys.remote_exec()`)               │
         │                                      │
         ├─────────────────────────────────────►│
         │                                      │
         │                 5. `debugpy.listen()`│
         │                    ┌─────────────────┤
         │                    │                 │
         │ 6. Port-forward    │                 │
         │◄───────────────────┼────────────────►│
         │    localhost:5679  │                 │
         │                    └─────────────────┤
         │                                      │
         │ 7. Connect editor                    │
         ├──────────────────────────────────────┤
         │         Debugging Session            │
         │◄────────────────────────────────────►│
         │                                      │
```

## License

MIT
