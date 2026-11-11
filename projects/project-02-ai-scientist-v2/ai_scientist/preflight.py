"""Local checks for the experiment launcher."""

import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _check_idea(args, errors):
    path = Path(args.load_ideas)
    try:
        ideas = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        errors.append(f"--load_ideas {path}: {exc.strerror}")
    except ValueError:
        errors.append(f"--load_ideas {path}: expected a UTF-8 JSON file")
    else:
        if not isinstance(ideas, list) or not ideas:
            errors.append("--load_ideas must contain a nonempty JSON array")
        elif not -len(ideas) <= args.idea_idx < len(ideas):
            errors.append(f"--idea_idx {args.idea_idx} is out of range for {len(ideas)} ideas")
        else:
            idea = ideas[args.idea_idx]
            entry = f"--load_ideas entry {args.idea_idx}"
            if not isinstance(idea, dict):
                errors.append(f"{entry} must be a JSON object")
            else:
                for field in ("Name", "Title", "Abstract", "Short Hypothesis"):
                    setting = idea.get(field)
                    if not isinstance(setting, str) or not setting.strip():
                        errors.append(f"{entry}.{field} must be nonempty text")
                name = idea.get("Name")
                if isinstance(name, str) and any(char in name for char in ("/", "\\", "\0")):
                    errors.append(f"{entry}.Name must be safe to use in an output directory name")
                for field, types in (
                    ("Experiments", (str, dict)),
                    ("Risk Factors and Limitations", (str,)),
                ):
                    setting = idea.get(field)
                    is_list = (
                        isinstance(setting, list)
                        and (bool(setting) or field != "Experiments")
                        and any(all(isinstance(item, kind) for item in setting) for kind in types)
                    )
                    if not isinstance(setting, str) and not is_list:
                        errors.append(f"{entry}.{field} must be text or a supported list")

    optional_inputs = (
        (args.load_code, "--load_code", Path(args.load_ideas.rsplit(".", 1)[0] + ".py")),
        (args.add_dataset_ref, "--add_dataset_ref", Path("hf_dataset_reference.py")),
    )
    for enabled, flag, source in optional_inputs:
        if enabled:
            try:
                with source.open("r", encoding="utf-8") as stream:
                    stream.read()
            except (OSError, UnicodeError):
                errors.append(f"{flag} needs a readable UTF-8 file at {source}")


def _check_output(path, errors):
    # Probe the nearest existing directory without creating experiment output.
    ancestor = path.absolute()
    try:
        while not ancestor.exists() and not ancestor.is_symlink():
            ancestor = ancestor.parent
        with tempfile.TemporaryFile(dir=ancestor):
            pass
    except OSError as exc:
        errors.append(f"Cannot create results under {path} ({ancestor}): {exc.strerror}")


def _config_models(path, errors):
    from omegaconf import OmegaConf
    from omegaconf.errors import OmegaConfBaseException
    import yaml

    from ai_scientist.treesearch.utils.config_schema import Config

    try:
        with Path(path).open(encoding="utf-8") as stream:
            data = yaml.load(stream, Loader=yaml.FullLoader)
    except OSError as exc:
        errors.append(f"--config {path}: {exc.strerror}")
        return {}
    except (yaml.YAMLError, UnicodeError):
        errors.append(f"--config {path}: expected a UTF-8 YAML mapping")
        return {}
    if not isinstance(data, dict):
        errors.append(f"--config {path}: expected a YAML mapping")
        return {}

    # These values are supplied by edit_bfts_config_file at launch time.
    data.update(
        desc_file="idea.json", data_dir="data", workspace_dir="workspace", log_dir="logs"
    )
    if "exp_name" not in data:
        errors.append("exp_name must be set (use run for the default)")
    elif data["exp_name"] is None:
        # prep_cfg uses a generated slug when exp_name is null.
        data["exp_name"] = "run"
    try:
        cfg = OmegaConf.create(data)
    except (OmegaConfBaseException, RecursionError):
        errors.append(f"--config {path}: contains values unsupported by OmegaConf")
        return {}
    try:
        cfg = OmegaConf.merge(OmegaConf.structured(Config), cfg)
    except OmegaConfBaseException as exc:
        field = getattr(exc, "full_key", None) or "root"
        errors.append(f"--config {path}: {field} does not match the tree-search schema")

    def value(field):
        try:
            return OmegaConf.select(cfg, field, throw_on_resolution_failure=True)
        except OmegaConfBaseException:
            errors.append(f"{field}: unresolved configuration value")
            return None

    if value("agent.type") not in ("parallel", "sequential"):
        errors.append("agent.type must be parallel or sequential")
    for field in (
        "generate_report",
        "preprocess_data",
        "copy_data",
        "agent.data_preview",
        "exec.format_tb_ipython",
    ):
        if not isinstance(value(field), bool):
            errors.append(f"{field} must be true or false")

    minimums = {
        "exec.timeout": 1,
        "agent.num_workers": 1,
        "agent.steps": 1,
        "agent.k_fold_validation": 1,
        "agent.multi_seed_eval.num_seeds": 1,
        "agent.search.num_drafts": 1,
        "agent.search.max_debug_depth": 0,
    }
    stages = value("agent.stages")
    if not OmegaConf.is_dict(stages):
        errors.append("agent.stages must be a mapping")
    else:
        minimums.update({f"agent.stages.{key}": 1 for key in stages.keys()})
    for field, minimum in minimums.items():
        setting = value(field)
        if type(setting) is not int or setting < minimum:
            errors.append(f"{field} must be an integer >= {minimum}")

    filename = value("exec.agent_file_name")
    if not isinstance(filename, str) or not filename.strip():
        errors.append("exec.agent_file_name must be a nonempty file name")
    debug_prob = value("agent.search.debug_prob")
    if type(debug_prob) not in (float, int) or not 0 <= debug_prob <= 1:
        errors.append("agent.search.debug_prob must be between 0 and 1")

    models = {}
    for role in ("code", "feedback", "vlm_feedback", "summary", "select_node"):
        if role in ("summary", "select_node") and value(f"agent.{role}") is None:
            continue  # The runtime uses agent.feedback.model for these roles.
        role_path = f"agent.{role}"
        model = value(f"{role_path}.model")
        if not isinstance(model, str) or not model.strip():
            errors.append(f"{role_path}.model must be a nonempty model name")
        else:
            models[f"{role_path}.model"] = model
        temp = value(f"{role_path}.temp")
        if type(temp) not in (float, int) or not math.isfinite(temp):
            errors.append(f"{role_path}.temp must be a finite number")
    return models


def _check_models(args, tree_models, errors, warnings):
    from ai_scientist.model_provider import llm_provider, vision_provider

    requirements = {}
    cloud_roles = {}
    keys = {
        "openai": ("OPENAI_API_KEY",),
        "anthropic": ("ANTHROPIC_API_KEY",),
        "deepseek_compatible": ("OPENAI_API_KEY", "DEEPSEEK_API_KEY"),
        "deepseek": ("DEEPSEEK_API_KEY",),
        "huggingface": ("HUGGINGFACE_API_KEY",),
        "openrouter": ("OPENROUTER_API_KEY",),
        "gemini": ("GEMINI_API_KEY",),
    }

    def require(provider, field):
        if provider in keys:
            requirements.setdefault(keys[provider], set()).add(field)
        elif provider in ("bedrock", "vertex"):
            cloud_roles.setdefault(provider, set()).add(field)
        elif provider is None:
            errors.append(f"{field}: model is unsupported by this stage's client")

    for field, model in tree_models.items():
        if "claude-" in model:
            if field in (
                "agent.feedback.model",
                "agent.vlm_feedback.model",
                "agent.select_node.model",
            ):
                errors.append(
                    f"{field}: this stage needs tool calls, which the tree-search "
                    "Claude backend does not support"
                )
            require("bedrock", field)
        else:
            # backend_openai gets its key from OpenAI(), even for Ollama.
            require("openai", field)

    require(llm_provider(args.model_agg_plots), "--model_agg_plots")
    if not args.skip_writeup:
        for field in ("model_writeup", "model_citation"):
            require(llm_provider(getattr(args, field)), f"--{field}")
        require(vision_provider(args.model_writeup_small), "--model_writeup_small")
        if not args.skip_review:
            require(llm_provider(args.model_review), "--model_review")

    for alternatives, fields in requirements.items():
        if not any(os.environ.get(key, "").strip() for key in alternatives):
            errors.append(f"Set {' or '.join(alternatives)} for {', '.join(sorted(fields))}")
    if "bedrock" in cloud_roles:
        access_key = os.environ.get("AWS_ACCESS_KEY_ID", "").strip()
        secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY", "").strip()
        if access_key and not secret_key:
            errors.append("Set AWS_SECRET_ACCESS_KEY when AWS_ACCESS_KEY_ID is set")
        fields = ", ".join(sorted(cloud_roles["bedrock"]))
        # The AWS SDK can also resolve credentials through workload identity.
        if not (access_key and secret_key) and not any(
            os.environ.get(name)
            for name in (
                "AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_SHARED_CREDENTIALS_FILE",
                "AWS_CONFIG_FILE", "AWS_WEB_IDENTITY_TOKEN_FILE",
                "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "AWS_CONTAINER_CREDENTIALS_FULL_URI",
            )
        ) and not any((Path.home() / ".aws" / name).is_file() for name in ("credentials", "config")):
            warnings.append(f"{fields}: no local AWS credential settings detected. Confirm the Bedrock credential source.")
        if not any(os.environ.get(name) for name in ("AWS_REGION", "AWS_DEFAULT_REGION")):
            warnings.append(f"{fields}: confirm the Bedrock region in the AWS environment or profile.")
    if "vertex" in cloud_roles:
        warnings.append(
            f"Confirm Google application credentials, project and Vertex model access for "
            f"{', '.join(sorted(cloud_roles['vertex']))}"
        )


def _check_cuda(errors, warnings):
    try:
        probe = subprocess.run(
            [sys.executable, "-c", "import torch; print(int(torch.cuda.is_available()))"],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        errors.append(
            "PyTorch/CUDA probe failed. Verify that PyTorch can import "
            "in this Python environment."
        )
        return
    if probe.stdout.splitlines()[-1:] != ["1"]:
        warnings.append("CUDA is unavailable. The experiment may run on CPU if its code supports it.")


def run_preflight(args, output_dir):
    errors = []
    warnings = []
    _check_idea(args, errors)
    _check_output(output_dir, errors)
    try:
        tree_models = _config_models(args.config, errors)
    except ModuleNotFoundError as exc:
        if exc.name not in ("yaml", "omegaconf"):
            raise
        errors.append("Install PyYAML and OmegaConf to check --config")
        tree_models = {}
    _check_models(args, tree_models, errors, warnings)
    _check_cuda(errors, warnings)
    if not args.skip_writeup:
        for command in ("pdflatex", "bibtex", "pdftotext", "chktex"):
            if not shutil.which(command):
                errors.append(f"Writeup requires {command} on PATH (or use --skip_writeup)")
    for message in errors:
        print(f"ERROR: {message}", file=sys.stderr)
    for message in warnings:
        print(f"WARNING: {message}", file=sys.stderr)
    if errors:
        print(f"Preflight failed with {len(errors)} error(s).", file=sys.stderr)
        return 1
    print("Preflight passed the local checks. Model access was not tested.")
    return 0
