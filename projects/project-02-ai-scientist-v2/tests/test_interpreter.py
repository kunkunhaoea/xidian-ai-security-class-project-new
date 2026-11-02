"""Offline regression tests; no model credentials, CUDA, or network required."""

import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from ai_scientist.treesearch.interpreter import Interpreter


class InterpreterTests(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.interpreter = Interpreter(
            self.workspace.name, timeout=2, timeout_grace_period=0.2
        )
        self.addCleanup(self.interpreter.cleanup_session)

    def assert_session_closed(self):
        self.assertIsNone(self.interpreter.process)
        self.assertIsNone(self.interpreter.code_inq)
        self.assertIsNone(self.interpreter.event_outq)
        self.assertEqual(
            list(Path(self.workspace.name).glob(".ai-scientist-output-*")), []
        )

    def test_stdout_stderr_unicode_and_exception(self):
        result = self.interpreter.run(
            "import sys\nprint('实验输出')\nprint('diagnostic', file=sys.stderr)\n"
            "raise ValueError('bad metric')"
        )
        output = "".join(result.term_out)
        self.assertIn("实验输出", output)
        self.assertIn("diagnostic", output)
        self.assertIn("ValueError: bad metric", output)
        self.assertEqual(result.exc_type, "ValueError")
        self.assertEqual(result.exc_info["args"], ["bad metric"])

    def test_persistent_session_and_output_reset(self):
        self.interpreter.run("x = 42\nprint('previous output')")
        result = self.interpreter.run("print(x)", reset_session=False)
        self.assertIn("42\n", "".join(result.term_out))
        self.assertNotIn("previous output", "".join(result.term_out))
        result = self.interpreter.run("print(x)", reset_session=True)
        self.assertEqual(result.exc_type, "NameError")

    def test_no_timeout_and_literal_old_eof_marker(self):
        self.interpreter.timeout = None
        result = self.interpreter.run("print('<|EOF|>')")
        self.assertIsNone(result.exc_type)
        self.assertIn("<|EOF|>\n", result.term_out)
        self.assertIn("unlimited", result.term_out[-1])

    def test_large_output_is_not_blocked_by_pipe_capacity(self):
        result = self.interpreter.run("print('x' * 1_000_000)")
        self.assertIsNone(result.exc_type)
        self.assertEqual(result.term_out[0], "x" * 1_000_000 + "\n")

    def test_user_timeout_error_is_not_an_execution_deadline(self):
        self.interpreter.timeout = None
        result = self.interpreter.run("raise TimeoutError('dataset unavailable')")
        self.assertEqual(result.exc_type, "TimeoutError")
        self.assertIn("dataset unavailable", "".join(result.term_out))
        self.assertNotIn("exceeded the time limit", "".join(result.term_out))

    def test_carriage_returns_are_preserved(self):
        result = self.interpreter.run("print('step 1\\rstep 2', end='')")
        self.assertEqual("".join(result.term_out[:-1]), "step 1\rstep 2")

    @unittest.skipUnless(os.name == "posix", "requires POSIX signals")
    def test_timeout_and_recovery(self):
        self.interpreter.timeout = 0.2
        result = self.interpreter.run("import time\nprint('before timeout')\ntime.sleep(30)")
        self.assertEqual(result.exc_type, "TimeoutError")
        self.assertIn("before timeout", "".join(result.term_out))
        self.assert_session_closed()
        result = self.interpreter.run("print('recovered')")
        self.assertIsNone(result.exc_type)
        self.assertIn("recovered", "".join(result.term_out))

    @unittest.skipUnless(os.name == "posix", "requires POSIX signals")
    def test_ignored_sigint_forces_cleanup_without_waiting_for_eof(self):
        self.interpreter.timeout = 0.2
        started = time.monotonic()
        result = self.interpreter.run(
            "import signal, time\nsignal.signal(signal.SIGINT, signal.SIG_IGN)\n"
            "print('partial output', end='')\ntime.sleep(30)"
        )
        self.assertEqual(result.exc_type, "TimeoutError")
        self.assertIn("partial output", "".join(result.term_out))
        self.assertLess(time.monotonic() - started, 10)
        self.assertGreaterEqual(result.exec_time, 0.2)
        self.assert_session_closed()

    @unittest.skipUnless(os.name == "posix", "requires POSIX signals")
    def test_caught_sigint_cannot_report_success(self):
        self.interpreter.timeout = 0.2
        result = self.interpreter.run(
            "import time\ntry:\n    time.sleep(30)\n"
            "except KeyboardInterrupt:\n    print('caught interrupt')"
        )
        self.assertEqual(result.exc_type, "TimeoutError")
        self.assertIn("caught interrupt", "".join(result.term_out))
        self.assert_session_closed()

    @unittest.skipUnless(os.name == "posix", "requires POSIX signals")
    def test_interactive_session_can_time_out_without_assertion(self):
        self.interpreter.run("x = 42")
        self.interpreter.timeout = 0.2
        result = self.interpreter.run("while True: pass", reset_session=False)
        self.assertEqual(result.exc_type, "TimeoutError")
        self.assert_session_closed()

    def test_abrupt_exit_preserves_diagnostics_and_cleans_up(self):
        with self.assertRaisesRegex(RuntimeError, "exit code 7.*") as error:
            self.interpreter.run("import os\nprint('crash context', end='')\nos._exit(7)")
        self.assertIn("crash context", str(error.exception))
        self.assert_session_closed()

    def test_startup_failure_is_cleaned_up(self):
        self.interpreter.agent_file_name = "missing-directory/run.py"
        with self.assertRaisesRegex(RuntimeError, "during startup"):
            self.interpreter.run("print('never reached')")
        self.assert_session_closed()

    def test_parent_interrupt_releases_active_session(self):
        self.interpreter.run("x = 1")
        with patch.object(self.interpreter.event_outq, "get", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.interpreter.run("x = 2", reset_session=False)
        self.assert_session_closed()

    def test_cleanup_is_idempotent(self):
        self.interpreter.run("print('done')")
        self.interpreter.cleanup_session()
        self.interpreter.cleanup_session()
        self.assert_session_closed()

    def test_environment_is_copied_and_isolated(self):
        env = {"AI_SCIENTIST_TEST_VALUE": "child only"}
        interpreter = Interpreter(self.workspace.name, env_vars=env)
        self.addCleanup(interpreter.cleanup_session)
        env["AI_SCIENTIST_TEST_VALUE"] = "mutated"
        result = interpreter.run("import os\nprint(os.environ['AI_SCIENTIST_TEST_VALUE'])")
        self.assertIn("child only", "".join(result.term_out))
        self.assertNotIn("AI_SCIENTIST_TEST_VALUE", os.environ)

    def test_invalid_deadlines(self):
        for timeout in (0, -1, float("nan"), float("inf")):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                Interpreter(self.workspace.name, timeout=timeout)
        for grace in (-1, float("nan"), float("inf")):
            with self.subTest(grace=grace), self.assertRaises(ValueError):
                Interpreter(self.workspace.name, timeout_grace_period=grace)


if __name__ == "__main__":
    unittest.main()
