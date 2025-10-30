# This gets copied to all pods to allow for debugging

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(
        description="Attach to a Python process in a pod and inject a script."
    )
    parser.add_argument(
        "--pid",
        type=int,
        required=True,
        help="The PID of the Python process in the pod.",
    )
    parser.add_argument(
        "--script", type=str, required=True, help="The path to the script to inject."
    )

    args = parser.parse_args()
    pid = args.pid
    script = args.script

    print(f"Attaching to PID {pid} to inject script {script}...")

    try:
        # Use sys.remote_exec to inject the script into the target process
        # New in Python 3.14, no stubs yet
        # https://docs.python.org/3/library/sys.html#sys.remote_exec
        sys.remote_exec(args.pid, args.script)  # type: ignore

    except AttributeError as e:
        print(f"ERROR: sys.remote_exec not available: {e}")
        exit(1)
    except PermissionError as e:
        print(f"ERROR: Permission denied (need CAP_SYS_PTRACE): {e}")
        exit(1)
    except Exception as e:
        print(f"Error during attachment: {type(e).__name__}: {e}")
        import traceback

        traceback.print_exc()
        exit(1)


if __name__ == "__main__":
    main()
