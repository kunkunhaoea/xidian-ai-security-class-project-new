import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

import psutil

from ai_scientist.utils.process_cleanup import cleanup_child_processes
import launch_scientist_bfts as launcher


class ProcessCleanupTests(unittest.TestCase):
    def test_only_descendants_are_selected_and_survivors_are_killed(self):
        child, grandchild = Mock(), Mock()
        with patch("ai_scientist.utils.process_cleanup.psutil.Process") as process, patch(
            "ai_scientist.utils.process_cleanup.psutil.wait_procs",
            side_effect=[([grandchild], [child]), ([child], [])],
        ), patch("ai_scientist.utils.process_cleanup.psutil.process_iter") as process_iter:
            process.return_value.children.return_value = [child, grandchild]
            cleanup_child_processes(timeout=0.1)
        process.assert_called_once_with(None)
        process.return_value.children.assert_called_once_with(recursive=True)
        child.terminate.assert_called_once()
        grandchild.terminate.assert_called_once()
        child.kill.assert_called_once()
        grandchild.kill.assert_not_called()
        process_iter.assert_not_called()

    def test_already_exited_children_are_tolerated(self):
        child = Mock()
        child.terminate.side_effect = psutil.NoSuchProcess(123)
        with patch("ai_scientist.utils.process_cleanup.psutil.Process") as process, patch(
            "ai_scientist.utils.process_cleanup.psutil.wait_procs", return_value=([], [])
        ):
            process.return_value.children.return_value = [child]
            cleanup_child_processes()

    def test_missing_parent_is_tolerated(self):
        with patch(
            "ai_scientist.utils.process_cleanup.psutil.Process",
            side_effect=psutil.NoSuchProcess(123),
        ):
            cleanup_child_processes(123)

    def test_denied_process_inspection_is_tolerated(self):
        for error in (psutil.AccessDenied(123), PermissionError("sandbox")):
            with self.subTest(error=error), patch(
                "ai_scientist.utils.process_cleanup.psutil.Process", side_effect=error
            ), self.assertLogs("ai-scientist", level="WARNING"):
                cleanup_child_processes(123)

    def test_real_cleanup_leaves_a_sibling_python_job_running(self):
        # Both jobs are children of this test runner. Select one job as the
        # cleanup root, so the other must remain alive throughout the cleanup.
        child_code = (
            "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "print('ready', flush=True); time.sleep(30)"
        )
        root_code = (
            "import subprocess, sys, time\n"
            f"child = subprocess.Popen([sys.executable, '-c', {child_code!r}], "
            "stdout=subprocess.PIPE, text=True)\n"
            "child.stdout.readline()\nprint(child.pid, flush=True)\n"
            "child.wait()\ntime.sleep(30)"
        )
        root = subprocess.Popen(
            [sys.executable, "-c", root_code], stdout=subprocess.PIPE, text=True
        )
        sibling = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            child_pid = int(root.stdout.readline())
            cleanup_child_processes(root.pid, timeout=0.1)
            self.assertFalse(psutil.pid_exists(child_pid))
            self.assertIsNone(root.poll())
            self.assertIsNone(sibling.poll())
        finally:
            for process in (root, sibling):
                process.terminate()
                process.wait(timeout=5)
            root.stdout.close()


class LauncherCleanupTests(unittest.TestCase):
    def test_cleanup_runs_on_success_error_and_keyboard_interrupt(self):
        for error in (None, RuntimeError("experiment failed"), KeyboardInterrupt()):
            with self.subTest(error=error), patch.object(launcher, "parse_arguments"), patch.object(
                launcher, "run_scientist", side_effect=error
            ), patch.object(launcher, "cleanup_child_processes") as cleanup:
                if error is None:
                    launcher.main()
                else:
                    with self.assertRaises(type(error)):
                        launcher.main()
                cleanup.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
