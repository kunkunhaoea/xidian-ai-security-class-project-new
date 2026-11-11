"""Configuration types shared by the launcher preflight and tree search."""

from dataclasses import dataclass
from pathlib import Path
from typing import Hashable, Optional


@dataclass
class ThinkingConfig:
    type: str
    budget_tokens: Optional[int] = None


@dataclass
class StageConfig:
    model: str
    temp: float
    thinking: ThinkingConfig
    betas: str
    max_tokens: Optional[int] = None


@dataclass
class SearchConfig:
    max_debug_depth: int
    debug_prob: float
    num_drafts: int


@dataclass
class DebugConfig:
    stage4: bool


@dataclass
class AgentConfig:
    steps: int
    stages: dict[str, int]
    k_fold_validation: int
    expose_prediction: bool
    data_preview: bool

    code: StageConfig
    feedback: StageConfig
    vlm_feedback: StageConfig

    search: SearchConfig
    num_workers: int
    type: str
    multi_seed_eval: dict[str, int]

    summary: Optional[StageConfig] = None
    select_node: Optional[StageConfig] = None

@dataclass
class ExecConfig:
    timeout: int
    agent_file_name: str
    format_tb_ipython: bool


@dataclass
class ExperimentConfig:
    num_syn_datasets: int


@dataclass
class Config(Hashable):
    data_dir: Path
    desc_file: Path | None

    goal: str | None
    eval: str | None

    log_dir: Path
    workspace_dir: Path

    preprocess_data: bool
    copy_data: bool

    exp_name: str

    exec: ExecConfig
    generate_report: bool
    report: StageConfig
    agent: AgentConfig
    experiment: ExperimentConfig
    debug: DebugConfig
