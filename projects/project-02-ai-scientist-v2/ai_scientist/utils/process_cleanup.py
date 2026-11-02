"""Clean up descendants of one process without selecting unrelated jobs."""

import logging

import psutil


logger = logging.getLogger("ai-scientist")


def cleanup_child_processes(pid: int | None = None, timeout: float = 3.0) -> None:
    """Terminate a snapshot of descendants, then kill any that ignore SIGTERM.

    With no PID, only descendants of the calling process are selected. Never
    select processes by executable name or command-line keywords.
    """
    try:
        children = psutil.Process(pid).children(recursive=True)
    except psutil.NoSuchProcess:
        return
    except (psutil.AccessDenied, PermissionError):
        logger.warning("Cannot inspect child processes of PID %s", pid)
        return

    # Visit descendants in reverse order so parents can reap their children.
    for child in reversed(children):
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            continue
        except (psutil.AccessDenied, PermissionError):
            logger.warning("Cannot terminate child process %s", child.pid)

    _, alive = psutil.wait_procs(children, timeout=timeout)
    for child in alive:
        try:
            child.kill()
        except psutil.NoSuchProcess:
            continue
        except (psutil.AccessDenied, PermissionError):
            logger.warning("Cannot kill child process %s", child.pid)

    _, alive = psutil.wait_procs(alive, timeout=timeout)
    for child in alive:
        logger.warning("Child process %s has not exited after cleanup", child.pid)
