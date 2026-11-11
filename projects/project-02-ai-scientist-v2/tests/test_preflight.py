import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

import launch_scientist_bfts as launcher
from ai_scientist import preflight


class PreflightTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.output_dir = self.directory / "experiments"
        self.ideas = self.directory / "ideas.json"
        idea = {
            "Name": "baseline",
            "Title": "Baseline study",
            "Abstract": "Measure performance on a small dataset.",
            "Short Hypothesis": "The baseline outperforms random guessing.",
            "Experiments": ["Measure baseline accuracy."],
            "Risk Factors and Limitations": ["Small dataset."],
        }
        self.ideas.write_text(json.dumps([idea]), encoding="utf-8")
        self.config_path = self.directory / "custom.yaml"
        default_config = Path(launcher.__file__).with_name("bfts_config.yaml")
        self.config = yaml.safe_load(default_config.read_text(encoding="utf-8"))
        self.config["agent"]["code"]["model"] = "gpt-4o"

    def run_check(self, *flags, environ=None, cuda="1", tools_available=True):
        self.config_path.write_text(yaml.safe_dump(self.config), encoding="utf-8")
        argv = [
            "launch_scientist_bfts.py",
            "--preflight",
            "--config",
            str(self.config_path),
            "--load_ideas",
            str(self.ideas),
            *flags,
        ]
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.object(sys, "argv", argv),
            patch.dict(os.environ, environ or {}, clear=True),
            patch.object(launcher, "EXPERIMENTS_DIR", self.output_dir),
            patch.object(launcher, "run_scientist") as experiment,
            patch.object(launcher, "cleanup_child_processes") as cleanup,
            patch.object(
                preflight.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, stdout=f"{cuda}\n"),
            ) as probe,
            patch.object(
                preflight.shutil, "which", return_value="/tool" if tools_available else None
            ) as which,
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            status = launcher.main()
        experiment.assert_not_called()
        cleanup.assert_not_called()
        self.assertEqual(probe.call_count, 1)
        self.assertEqual(probe.call_args.args[0][0], sys.executable)
        self.assertIn("torch.cuda.is_available()", probe.call_args.args[0][2])
        return status, stdout.getvalue() + stderr.getvalue(), which

    def test_skip_writeup_ignores_paper_tools_and_inactive_models(self):
        status, output, which = self.run_check(
            "--skip_writeup",
            "--model_writeup",
            "unsupported-model",
            "--model_review",
            "unsupported-model",
            environ={"OPENAI_API_KEY": "local-test-key"},
            cuda="0",
            tools_available=False,
        )
        self.assertEqual(status, 0, output)
        self.assertIn("CUDA is unavailable", output)
        self.assertNotIn("local-test-key", output)
        self.assertFalse(self.output_dir.exists())
        which.assert_not_called()

    def test_aggregates_input_config_credentials_and_tool_errors(self):
        self.config["agent"]["num_workers"] = 0
        self.config["exec"]["timeout"] = -3
        self.ideas.write_text('{"Name": "not an array"}', encoding="utf-8")
        self.output_dir.write_text("file blocks directory", encoding="utf-8")
        status, output, _ = self.run_check(tools_available=False)
        self.assertEqual(status, 1)
        for issue in (
            "--load_ideas", "agent.num_workers", "exec.timeout",
            "Cannot create results", "OPENAI_API_KEY", "pdflatex", "pdftotext",
        ):
            with self.subTest(issue=issue):
                self.assertIn(issue, output)

    def test_review_skip_keeps_writeup_checks(self):
        status, output, _ = self.run_check(
            "--skip_review",
            "--model_review",
            "unsupported-model",
            "--model_writeup_small",
            "gpt-4.1",
            environ={"OPENAI_API_KEY": "local-test-key"},
        )
        self.assertEqual(status, 1)
        self.assertIn("--model_writeup_small: model is unsupported", output)
        self.assertNotIn("--model_review", output)

    def test_tree_deepseek_uses_openai_key_and_bedrock_checks_partial_env(self):
        self.config["agent"]["code"]["model"] = "anthropic.claude-3-5-sonnet-test"
        self.config["agent"]["feedback"]["model"] = "deepseek-chat"
        status, output, _ = self.run_check(
            "--skip_writeup",
            "--model_agg_plots",
            "deepseek-chat",
            environ={"DEEPSEEK_API_KEY": "fake-deepseek", "AWS_ACCESS_KEY_ID": "fake-aws"},
        )
        self.assertEqual(status, 1)
        self.assertIn("Set OPENAI_API_KEY for agent.feedback.model", output)
        self.assertNotIn("or DEEPSEEK_API_KEY", output)
        self.assertIn("Set AWS_SECRET_ACCESS_KEY", output)
        self.assertNotIn("fake-aws", output)
        self.assertNotIn("fake-deepseek", output)

    def test_idea_index_and_optional_inputs_report_separately(self):
        status, output, _ = self.run_check(
            "--skip_writeup", "--idea_idx", "5", "--load_code",
            environ={"OPENAI_API_KEY": "local-test-key"},
        )
        self.assertEqual(status, 1)
        self.assertIn("--idea_idx 5 is out of range", output)
        self.assertIn("--load_code needs a readable UTF-8 file", output)

    def test_selected_idea_requires_fields_used_by_agent_manager(self):
        self.ideas.write_text('[{"Name": "baseline"}]', encoding="utf-8")
        status, output, _ = self.run_check(
            "--skip_writeup", environ={"OPENAI_API_KEY": "local-test-key"}
        )
        self.assertEqual(status, 1)
        self.assertIn("entry 0.Abstract", output)
        self.assertIn("entry 0.Experiments", output)

    def test_config_interpolations_resolve_against_runtime_schema(self):
        self.config["agent"]["code"]["model"] = "${agent.feedback.model}"
        self.config["agent"]["select_node"] = {
            "model": "${agent.feedback.model}",
            "temp": "${agent.feedback.temp}",
        }
        status, output, _ = self.run_check(
            "--skip_writeup", environ={"OPENAI_API_KEY": "local-test-key"}
        )
        self.assertEqual(status, 0, output)

    def test_cli_failure_has_nonzero_exit_and_creates_no_experiment_directory(self):
        result = subprocess.run(
            [
                sys.executable, launcher.__file__, "--preflight", "--skip_writeup",
                "--load_ideas", "missing.json", "--config", "missing.yaml",
            ],
            cwd=self.directory,
            capture_output=True,
            text=True,
            timeout=25,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("--load_ideas missing.json", result.stderr)
        self.assertIn("--config missing.yaml", result.stderr)
        self.assertFalse(self.output_dir.exists())
