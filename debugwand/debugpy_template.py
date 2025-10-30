"""
Debugpy injection script - gets injected into target process.
This is a template that will be customized per-invocation.
"""


def start_debugpy(port: int = 5679):
    """Start debugpy server in the current process."""
    try:
        import debugpy
    except ImportError:
        print("[MANTIS ERROR] debugpy not installed in target process")
        print("[MANTIS ERROR] Install with: pip install debugpy")
        return False

    try:
        # Show path information for debugging (show this first, always)
        import os
        import sys

        print(f"[MANTIS] Process working directory: {os.getcwd()}")
        print(f"[MANTIS] Python executable: {sys.executable}")
        if hasattr(sys.modules.get("__main__"), "__file__"):
            print(f"[MANTIS] Main module file: {sys.modules['__main__'].__file__}")

        # Check if already listening (connected or not)
        if debugpy.is_client_connected():
            print(f"[MANTIS] Debugpy already connected")
            return True

        # Try to check if already listening (even without client)
        try:
            # This will raise an exception if already listening
            print(f"[MANTIS] Starting debugpy on port {port}...")
            debugpy.listen(("0.0.0.0", port))
            print(f"[MANTIS] Debugpy listening on 0.0.0.0:{port}")
        except RuntimeError as e:
            error_msg = str(e).lower()
            if (
                "already been called" in error_msg
                or "already in use" in error_msg
                or "already listening" in error_msg
            ):
                print(
                    f"[MANTIS] Debugpy already listening on port {port} (reusing existing session)"
                )
                # Already listening from a previous injection - that's fine!
            else:
                raise

        # Wait for the debugger to attach (if requested)
        if {WAIT}:  # type: ignore  # Template placeholder
            print("[MANTIS] Waiting for debugger to attach...")
            debugpy.wait_for_client()
            print("[MANTIS] Debugger attached successfully!")
        else:
            print(
                "[MANTIS] Debugger can attach anytime. Breakpoints will work once connected."
            )
        return True

    except Exception as e:
        print(f"[MANTIS ERROR] Failed to start debugpy: {e}")
        import traceback

        traceback.print_exc()
        return False


# Call directly - sys.remote_exec() doesn't set __name__ to "__main__"
start_debugpy(port={PORT})  # type: ignore  # Template placeholder, replaced before injection
