"""
Python interpreter for executing code snippets and capturing their output.
Supports:
- captures stdout and stderr
- captures exceptions and stack traces
- limits execution time
"""

import logging
import math
import os
import queue
import signal
import sys
import tempfile
import time
import traceback
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import dataclass
from multiprocessing import Process, Queue
from pathlib import Path

import humanize
from dataclasses_json import DataClassJsonMixin

from ai_scientist.utils.process_cleanup import cleanup_child_processes

logger = logging.getLogger("ai-scientist")


@dataclass
class ExecutionResult(DataClassJsonMixin):
    """
    Result of executing a code snippet in the interpreter.
    Contains the output, execution time, and exception information.
    """

    term_out: list[str]
    exec_time: float
    exc_type: str | None
    exc_info: dict | None = None
    exc_stack: list[tuple] | None = None


def exception_summary(e, working_dir, exec_file_name, format_tb_ipython):
    """Generates a string that summarizes an exception and its stack trace (either in standard python repl or in IPython format)."""
    if format_tb_ipython:
        import IPython.core.ultratb

        tb = IPython.core.ultratb.VerboseTB(tb_offset=1, color_scheme="NoColor")
        tb_str = str(tb.text(*sys.exc_info()))
    else:
        tb_lines = traceback.format_exception(e)
        # skip parts of stack trace in weflow code
        tb_str = "".join(
            [l for l in tb_lines if "treesearch/" not in l and "importlib" not in l]
        )

    # replace whole path to file with just filename (to remove agent workspace dir)
    tb_str = tb_str.replace(str(working_dir / exec_file_name), exec_file_name)

    exc_info = {}
    if hasattr(e, "args"):
        exc_info["args"] = [str(i) for i in e.args]
    for att in ["name", "msg", "obj"]:
        if hasattr(e, att):
            exc_info[att] = str(getattr(e, att))

    tb = traceback.extract_tb(e.__traceback__)
    exc_stack = [(t.filename, t.lineno, t.name, t.line) for t in tb]

    return tb_str, e.__class__.__name__, exc_info, exc_stack


class RedirectFile:
    """Flush each write so output survives a timeout or abrupt child exit."""

    def __init__(self, stream):
        self.stream = stream

    def write(self, msg):
        written = self.stream.write(msg)
        self.stream.flush()
        return written

    def flush(self):
        self.stream.flush()


class Interpreter:
    def __init__(
        self,
        working_dir: Path | str,
        timeout: float | None = 3600,
        format_tb_ipython: bool = False,
        agent_file_name: str = "runfile.py",
        env_vars: dict[str, str] | None = None,
        timeout_grace_period: float = 5.0,
    ):
        """Execute code in a persistent child with a bounded timeout grace period.

        ``timeout=None`` disables the execution deadline. After a deadline,
        SIGINT is sent once; an unresponsive child is terminated after
        ``timeout_grace_period`` seconds. Timed-out sessions are discarded.
        """
        self.working_dir = Path(working_dir).resolve()
        if not self.working_dir.is_dir():
            raise ValueError(f"Working directory {self.working_dir} does not exist")
        if timeout is not None and (not math.isfinite(timeout) or timeout <= 0):
            raise ValueError("timeout must be finite and positive, or None")
        if not math.isfinite(timeout_grace_period) or timeout_grace_period < 0:
            raise ValueError("timeout_grace_period must be finite and non-negative")
        self.timeout = timeout
        self.timeout_grace_period = timeout_grace_period
        self.format_tb_ipython = format_tb_ipython
        self.agent_file_name = agent_file_name
        self.process: Process | None = None
        self.env_vars = dict(env_vars or {})
        self._output_path: Path | None = None
        self.code_inq = None
        self.event_outq = None

    def child_proc_setup(self) -> None:
        import shutup

        shutup.mute_warnings()
        os.environ.update(self.env_vars)
        os.chdir(str(self.working_dir))
        sys.path.append(str(self.working_dir))

    def _run_session(self, code_inq: Queue, event_outq: Queue) -> None:
        self.child_proc_setup()
        global_scope: dict = {}
        while True:
            code = code_inq.get()
            os.chdir(str(self.working_dir))
            with open(self.agent_file_name, "w", encoding="utf-8") as f:
                f.write(code)

            # Output uses a file rather than a multiprocessing queue. A killed
            # writer cannot leave the parent blocked on a partial queue message
            # or on an EOF marker that will never be sent.
            with (
                open(self._output_path, "w", encoding="utf-8") as stream,
                redirect_stdout(RedirectFile(stream)),
                redirect_stderr(RedirectFile(stream)),
            ):
                event_outq.put(("state:ready",))
                try:
                    exec(compile(code, self.agent_file_name, "exec"), global_scope)
                except BaseException as e:
                    tb_str, e_cls_name, exc_info, exc_stack = exception_summary(
                        e, self.working_dir, self.agent_file_name, self.format_tb_ipython
                    )
                    stream.write(tb_str)
                    state = ("state:finished", e_cls_name, exc_info, exc_stack)
                else:
                    state = ("state:finished", None, None, None)
            event_outq.put(state)

    def create_process(self) -> None:
        with tempfile.NamedTemporaryFile(
            prefix=".ai-scientist-output-",
            suffix=".log",
            dir=self.working_dir,
            delete=False,
        ) as output_file:
            self._output_path = Path(output_file.name)
        self.code_inq, self.event_outq = Queue(), Queue()
        self.process = Process(
            target=self._run_session, args=(self.code_inq, self.event_outq)
        )
        self.process.start()

    def _stop_process(self):
        if self.process is None or self.process.pid is None:
            return
        if self.process.is_alive():
            cleanup_child_processes(self.process.pid)
            if self.process.is_alive():
                self.process.terminate()
        self.process.join(timeout=2)
        if self.process.is_alive():
            logger.warning("Child failed to terminate gracefully, killing it")
            self.process.kill()
            self.process.join(timeout=2)

    def cleanup_session(self):
        """Idempotently release the process, IPC queues, and temporary output."""
        self._stop_process()
        if self.process is not None:
            self.process.close()
            self.process = None
        for name in ("code_inq", "event_outq"):
            ipc_queue = getattr(self, name)
            if ipc_queue is not None:
                # A dead child cannot consume pending code. Do not wait for
                # the queue's feeder thread to deliver it during shutdown.
                ipc_queue.cancel_join_thread()
                ipc_queue.close()
                setattr(self, name, None)
        if self._output_path is not None:
            self._output_path.unlink(missing_ok=True)
            self._output_path = None

    def _read_output(self) -> list[str]:
        if self._output_path is None:
            return []
        with self._output_path.open(
            encoding="utf-8", errors="replace", newline=""
        ) as stream:
            return stream.read().splitlines(keepends=True)

    def _check_child(self, phase: str):
        if not self.process.is_alive():
            output = "".join(self._read_output())
            raise RuntimeError(
                f"REPL child process died {phase} "
                f"(exit code {self.process.exitcode}).\n{output}"
            )

    def run(self, code: str, reset_session=True) -> ExecutionResult:
        """Run code, preserving globals only when reset_session=False.

        Abnormal execution and parent interruption always release the session.
        A timeout returns an ExecutionResult with exc_type='TimeoutError'.
        Startup failure or an abrupt child exit raises RuntimeError.
        """
        try:
            if reset_session:
                self.cleanup_session()
                self.create_process()
            elif self.process is None:
                raise RuntimeError("No active session; use reset_session=True first")
            self._check_child("before execution")
            self.code_inq.put(code)

            startup_deadline = time.monotonic() + 10
            while True:
                try:
                    state = self.event_outq.get(timeout=0.1)
                    if state[0] != "state:ready":
                        raise RuntimeError(f"Unexpected REPL startup event: {state}")
                    break
                except queue.Empty:
                    self._check_child("during startup")
                    if time.monotonic() >= startup_deadline:
                        raise RuntimeError("REPL child process failed to start execution")

            start_time = time.monotonic()
            interrupted_at = None
            while True:
                try:
                    state = self.event_outq.get(timeout=0.1)
                    if state[0] != "state:finished":
                        raise RuntimeError(f"Unexpected REPL completion event: {state}")
                    break
                except queue.Empty:
                    now = time.monotonic()
                    if interrupted_at is not None:
                        if (
                            not self.process.is_alive()
                            or now - interrupted_at >= self.timeout_grace_period
                        ):
                            state = ("state:finished", "TimeoutError", {}, [])
                            break
                    else:
                        self._check_child("during execution")
                        if self.timeout is not None and now - start_time >= self.timeout:
                            interrupted_at = now
                            try:
                                os.kill(self.process.pid, signal.SIGINT)
                            except ProcessLookupError:
                                pass

            exec_time = time.monotonic() - start_time
            e_cls_name, exc_info, exc_stack = state[1:]
            if interrupted_at is not None:
                # Catching or ignoring SIGINT must not turn a timed-out run
                # into a successful experiment or leave state for the next run.
                e_cls_name = "TimeoutError"
                self._stop_process()
            output = self._read_output()
            if interrupted_at is not None:
                self.cleanup_session()
                output.append(
                    "TimeoutError: Execution exceeded the time limit of "
                    f"{humanize.naturaldelta(self.timeout)}"
                )
            else:
                limit = (
                    "unlimited"
                    if self.timeout is None
                    else humanize.naturaldelta(self.timeout)
                )
                output.append(
                    f"Execution time: {humanize.naturaldelta(exec_time)} "
                    f"(time limit is {limit})."
                )
            return ExecutionResult(output, exec_time, e_cls_name, exc_info, exc_stack)
        except BaseException:
            self.cleanup_session()
            raise
