# 项目 02：AI-Scientist-v2 使用指南

本项目从研究主题生成想法，再通过实验树搜索执行实验，并可生成论文与审阅结果。建议先完成“生成一个研究想法”，再尝试完整实验。

[课程任务](COURSE.md) · [课程总说明](../../README.md) · [上游原始 README](README.original.md) · [许可证](LICENSE)

## 1. 环境要求

- 上游面向 **Linux + NVIDIA GPU + CUDA/PyTorch**；Windows 用户使用具备 GPU 支持的 Linux 环境或服务器。
- 准备 Conda、Python 3.11、模型服务权限，以及论文流程所需的 PDF/LaTeX 工具。
- 系统会执行模型生成的代码，应在受控、隔离的实验环境运行。模型调用可能持续产生费用，先缩小实验范围。

以下命令使用 **Linux Bash**。从课程根目录开始：

```bash
cd projects/project-02-ai-scientist-v2
conda create -n course-scientist-v2 python=3.11
conda activate course-scientist-v2
conda install pytorch torchvision torchaudio pytorch-cuda=12.4 -c pytorch -c nvidia
conda install anaconda::poppler
conda install conda-forge::chktex
pip install -r requirements.txt
```

CUDA 版本应结合驱动和环境调整；上述 PyTorch 命令来自上游，不保证适用所有驱动。需要生成 PDF 时还要准备可用的 LaTeX 编译环境，仅安装 `chktex` 并不等于安装完整 TeX。

## 2. 模型配置

通过当前终端的环境变量提供凭据，不要直接写入源码：

```bash
export OPENAI_API_KEY="替换为自己的密钥"
# 使用 Semantic Scholar 更高配额时再填写：
export S2_API_KEY="替换为自己的密钥"
```

不用 Semantic Scholar 密钥时，省略第二条；默认可匿名访问，但可能限流。使用 Gemini 时按上游设置 `GEMINI_API_KEY`；使用 AWS Bedrock 时需安装 `anthropic[bedrock]`，配置 AWS 凭据、区域及模型访问权限。

尤其要检查 [bfts_config.yaml](bfts_config.yaml)：当前实验代码模型默认为 AWS Bedrock 的 Claude 标识，仅设置 OpenAI Key 并不足以运行该默认流程。逐项核对 `agent.code.model`、反馈模型和报告模型等配置。旧模型可能退役，应使用当前账户可访问且项目适配层支持的模型。

## 3. 第一次运行：只生成一个想法

```bash
cp ai_scientist/ideas/i_cant_believe_its_not_better.md ai_scientist/ideas/course_topic.md
```

编辑新文件，保留示例结构并填写自己的研究主题、关键词、概要与摘要。随后运行：

```bash
python ai_scientist/perform_ideation_temp_free.py \
  --workshop-file ai_scientist/ideas/course_topic.md \
  --model gpt-4o-2024-05-13 \
  --max-num-generations 1 \
  --num-reflections 1
```

命令中的模型名为上游示例，运行前验证可用性。完成后检查同名 `course_topic.json`，人工核验假设、数据需求和引用；没有有效 JSON 就不要进入下一步。

## 4. 运行实验

先审阅 `bfts_config.yaml` 中的并行数、各阶段迭代次数、执行超时和模型设置。`agent.stages` 各阶段上限可能覆盖通用 `steps`，不能只修改 `steps` 就认为已限制总量。

先跳过论文生成和审阅，运行第一个想法：

```bash
python launch_scientist_bfts.py \
  --load_ideas ai_scientist/ideas/course_topic.json \
  --idea_idx 0 \
  --skip_writeup \
  --skip_review
```

该命令仍会调用实验模型、生成和执行代码，并可能调用绘图等模型；它不是无费用试运行。请同时检查启动脚本里的 `--model_agg_plots` 默认值及相关配置。

需要论文生成时，依据 [原始 README](README.original.md) 配置 `--model_writeup`、`--model_writeup_small`、`--model_citation`、`--model_review` 等参数，再去掉跳过选项。只有存在与想法 JSON 同名的 `.py` 初始代码时才使用 `--load_code`。

## 5. 结果、停止与排错

结果目录为 `experiments/<时间>_<想法名称>_attempt_<编号>/`，具体路径见终端输出。保留实际存在的实验日志、指标、代码、token 统计及论文文件，区分生成结果与人工验证结论。

按 Ctrl+C 中断主进程后，检查本次任务启动的子进程和 GPU 占用，确认实验确实停止。

| 问题 | 处理方向 |
| --- | --- |
| 配置了 OpenAI 仍报 AWS 权限错误 | 默认实验模型使用 Bedrock，需配置对应服务或采用代码支持的替代模型 |
| CUDA out of memory | 缩小实验模型、数据规模与并行数 |
| Semantic Scholar 限流 | 降低请求频率或配置自己的 S2 Key |
| 论文 PDF 生成失败 | 检查 LaTeX、Poppler 和模型输出，先单独完成实验流程 |
| 找不到想法文件 | 检查 JSON 是否生成、路径及当前工作目录 |

## 6. 课程提交

提交配置变更（不含密钥）、代码、实验记录摘要及复现说明，保留 [许可证](LICENSE)。本项目与项目 04 的实验适配层没有完成兼容验证，不能直接假设可互接。本课程源码整理时的上游版本为 `96bd51617cfdbb494a9fc283af00fe090edfae48`；本说明经源码核对，未执行完整实验。

## 7. 执行可靠性改进与离线测试

启动器会在正常结束、异常和 Ctrl+C 时清理本任务的子进程，清理范围限定为当前进程树。解释器超时后先发送一次 SIGINT，默认等待最多 5 秒，再终止未响应的进程；超时会返回 `TimeoutError` 并丢弃该会话，下一次使用默认的 `reset_session=True` 重新启动。

实验输出暂存于工作目录中的临时文件，清理会话时删除；`ExecutionResult.term_out` 仍返回文本列表。输出在异常退出前逐次刷新，因此强制终止后也能读取已有输出。`timeout=None` 可关闭执行时限，有限时限必须为正数。

仅验证执行器和进程清理时，无需安装 PyTorch、配置 API Key 或调用模型。在 Python 3.11+ 的独立环境中，从本项目目录执行：

```bash
python -m pip install humanize dataclasses-json shutup psutil
python -m unittest discover -s tests -v
python launch_scientist_bfts.py --help
```

真实进程清理测试需要系统允许查询本用户的进程树。完整科研流程仍需按前文配置 GPU、模型和论文工具。改动依据、测试结果和局限见 [开发日志](log/2026-11-02-execution-reliability.md)。
