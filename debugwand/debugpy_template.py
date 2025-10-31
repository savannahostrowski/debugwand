"""
Debugpy injection script - gets injected into target process.
This is a template that will be customized per-invocation.
"""


def start_debugpy(port: int = 5679):
    """Start debugpy server in the current process."""
    try:
        import debugpy
    except ImportError:
        print("[WAND ERROR] debugpy not installed in target process")
        print("[WAND ERROR] Install with: pip install debugpy")
        return False

    try:
        # Show path information for debugging (show this first, always)
        import os
        import sys

        print(f"[WAND] Process working directory: {os.getcwd()}")
        print(f"[WAND] Python executable: {sys.executable}")
        if hasattr(sys.modules.get("__main__"), "__file__"):
            print(f"[WAND] Main module file: {sys.modules['__main__'].__file__}")

        # Check if already listening (connected or not)
        if debugpy.is_client_connected():
            print(f"[WAND] Debugpy already connected")
            return True

        # Try to check if already listening (even without client)
        try:
            # This will raise an exception if already listening
            print(f"[WAND] Starting debugpy on port {port}...")
            debugpy.listen(("0.0.0.0", port))
            print(f"[WAND] Debugpy listening on 0.0.0.0:{port}")
        except RuntimeError as e:
            error_msg = str(e).lower()
            if (
                "already been called" in error_msg
                or "already in use" in error_msg
                or "already listening" in error_msg
            ):
                print(
                    f"[WAND] Debugpy already listening on port {port} (reusing existing session)"
                )
                # Already listening from a previous injection - that's fine!
            else:
                raise

        # Wait for the debugger to attach (if requested)
        # This is a template placeholder, replaced before injection
        if {WAIT}:  # type: ignore
            print("[WAND] Waiting for debugger to attach...")
            debugpy.wait_for_client()
            print("[WAND] Debugger attached successfully!")
        else:
            print(
                "[WAND] Debugger can attach anytime. Breakpoints will work once connected."
            )
        return True

    except Exception as e:
        print(f"[WAND ERROR] Failed to start debugpy: {e}")
        import traceback

        traceback.print_exc()
        return False


# Call directly - sys.remote_exec() doesn't set __name__ to "__main__"
start_debugpy(port={PORT})  # type: ignore  # Template placeholder, replaced before injection
