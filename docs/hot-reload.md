# Hot-Reload Support

## Debugging with `--reload`

debugwand automatically handles uvicorn's `--reload` mode:
- Detects when your app runs with `--reload` (FastAPI, Flask, etc.)
- Monitors for worker process restarts
- Auto-reinjects debugpy when the worker PID changes
- Keeps port-forward alive across worker restarts

**Note:** When the worker restarts, VSCode will detach (because the process dies). You'll need to press F5 to reconnect. The worker continues serving requests immediately - debugpy is ready and waiting for you to reconnect.

## How It Works

1. **Start debugging:**
   ```bash
   wand debug -n my-namespace -s my-service
   ```

2. **Connect VSCode** (press F5)

3. **Edit your code:**
   - Tilt syncs files to pod
   - uvicorn detects changes and restarts worker
   - debugwand detects PID change and reinjects debugpy
   - Press F5 in VSCode to reconnect
   - Your breakpoints keep working!

## Example Session

```bash
$ wand debug -n fastapicloud -s api
🔧 Injecting debugpy into PID 82 in pod api-00002...
✅ Debugpy ready in PID 82 in pod api-00002
ℹ️  App is running - connect your debugger anytime to hit breakpoints
🔄 Reload mode detected - will auto-reinject debugpy on worker restarts
🚀 Port-forwarding established on port 5679

# Your app is serving requests! Connect when ready.

# You edit a file...
🔄 Worker restarted (PID 82 → 125), auto-reinjecting debugpy...
✅ Debugpy reinjected into new worker (PID 125)
ℹ️  Worker is running - reconnect your debugger to continue debugging

# Worker keeps serving requests! Reconnect when ready.
# Keep coding! The cycle repeats
```

## VSCode Configuration

Standard debugpy attach configuration works:
```json
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
```

## What You Get

- ✅ **Non-blocking debugging** - App continues serving requests immediately
- ✅ Keep uvicorn `--reload` enabled for fast iteration
- ✅ Workers restart and serve traffic without waiting for debugger
- ✅ Debugpy automatically reinjects on worker restarts
- ✅ Simple F5 press to connect/reconnect anytime
- ✅ Port-forward stays alive across worker restarts
- ✅ Auto-reconnect if entire pod restarts
