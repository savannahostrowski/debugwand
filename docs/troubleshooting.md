# Troubleshooting

## "Permission denied" / "CAP_SYS_PTRACE"

On Linux and in containers, you need the `CAP_SYS_PTRACE` capability to attach to a running process. See the [Python remote debugging docs](https://docs.python.org/3/howto/remote_debugging.html) for details.

**Kubernetes:** Add to your deployment:
```yaml
securityContext:
  capabilities:
    add:
      - SYS_PTRACE
```

**Docker:** Run with `--cap-add=SYS_PTRACE`:
```bash
docker run --cap-add=SYS_PTRACE ...
```

**Note:** macOS may require running with elevated privileges (`sudo`) instead of `CAP_SYS_PTRACE`.

## "No module named 'debugpy'"

The target pod doesn't have debugpy installed. Add debugpy to your application dependencies.

## Debugger won't attach

1. Check port-forward is running: `lsof -i :5679` (or use https://github.com/savannahostrowski/gruyere 🤗)
2. Check debugpy is listening: `kubectl logs <pod> | grep debugpy`
3. Verify path mappings in `launch.json` or DAP config
4. Check Python version compatibility (3.14+ required)



## Breakpoints not hitting

**Path mappings:** Ensure your `launch.json` maps local to remote paths correctly.

**Multiple pods:** If you have multiple replicas, requests may be load-balanced to a different pod than the one you're debugging. You can:
- Set `DEBUGWAND_AUTO_SELECT_POD=1` to automatically select the newest pod
- Scale down to a single replica during debugging
- Use pod selection to choose the specific pod handling your traffic