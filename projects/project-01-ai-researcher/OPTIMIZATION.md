# Project 01 AI-Researcher 优化说明

日期：2026-11-12。本轮按 [PIPELINE_ANALYSIS.md](PIPELINE_ANALYSIS.md) 给出的 P0→P1→P2 优先级**依次实施优化**，并配套无模型验证。本文逐项说明：**优化了什么、改了哪个文件哪一行、用了什么函数、如何验证**，以及**暂缓项与原因**。

与既有文档的关系：
- [REPRODUCTION.md](REPRODUCTION.md) 记录的是更早的链路缺陷修复 1–16（arXiv 编码、Windows file URI、cp936 往返、TCP 分帧、NO_PROXY、mdconvert 签名等），本轮不重复。
- [PIPELINE_ANALYSIS.md](PIPELINE_ANALYSIS.md) 是流程梳理与 24 项不足归纳，本轮落地其中的 P0/P1/P2 建议。
- 本轮新增验证脚本 [logs/test-optimizations.py](logs/test-optimizations.py)，冒烟重跑日志 [logs/smoke-run-20261112-postopt.log](logs/smoke-run-20261112-postopt.log)。

所有改动都在本课程副本内，`git diff` 可查；密钥、`.env`、`logs/`、`.venv`、缓存均不提交。

---

## 0. 验证汇总（先给结论）

| 验证 | 结果 | 是否调用模型 |
| --- | --- | --- |
| `logs/test-optimizations.py`（本轮新增，12 组 64 项断言） | **PASS=64 / FAIL=0** | 否（mock 模型调用） |
| `logs/test-flow-fixes.py`（既有回归，fix 1–13） | **FAILED: none** | 否 |
| 8 个修改文件 `py_compile` + 逐模块 `import` | 全部通过 | 否 |
| toy 冒烟重跑 `logs/run-smoke-chain.py` | **EXIT=0，"SMOKE finished in 0.9 min"**，11 次缓存复用，0 错误 / 0 预算耗尽 / 0 模型调用 | 否（全缓存命中） |

冒烟重跑覆盖完整编排链路：Prepare → Survey → CodingPlan → ML → Judge → ML(submit) → ExpAnalyser → ML(refine)，全部阶段缓存通过**新校验**后被复用，证明优化未破坏集成路径。`run_async` 的循环逻辑（预算/终止/守卫/重试）由 mock 测试 B/C/D/E 直接驱动真实代码覆盖。

---

## 1. P0 —— 核心控制流与缓存（最高优先级：直接决定能否跑完、是否烧钱）

### P0-1 轮数预算 + 终止判定健壮化

**文件**：`research_agent/inno/core.py`

**问题**：
- `max_turns` 原为 `float("inf")`，模型一旦不收尾就**无限空转**直到 API 余额耗尽（实测曾单阶段 571 轮）。
- 终止判定只看 `message.tool_calls[0]`，模型若把 `case_resolved`/`case_not_resolved` 放在**第二个或并行槽位**会漏判，Agent 继续空转。
- `tool_choice=="required"` 分支里 `case_resolved` 与 `case_not_resolved` 两段逻辑**完全重复**。
- 终止分支无 `tool_calls` 时直接取 `messages[-1]`，有**越界隐患**。

**修改与所用函数**：
- 新增模块常量 `DEFAULT_MAX_TURNS = int(os.getenv("MAX_AGENT_TURNS", "80"))`（`core.py:106`）。用 `os.getenv` + `int` 把上限做成**可配置**，默认 80 轮。
- `run()`（`core.py:370-380`）与 `run_async()`（`core.py:595-605`）的 `max_turns` 默认值改为 `DEFAULT_MAX_TURNS`。
- 新增静态方法 `MetaChain._terminal_call_name(message)`（`core.py:359-368`）：用 `getattr(message, "tool_calls", None)` **遍历所有** tool_calls，命中终止函数返回其名，否则 `None`。这是终止判定的唯一入口。
- 两个循环改为按轮计数 `turns = 0` / `while turns < max_turns and active_agent:` / `turns += 1`（`run` 在 `399-400`，`run_async` 在 `620-621`）。
- `run_async` 的 required 分支合并重复逻辑为一处（`core.py:653-671`）：`terminal = self._terminal_call_name(message)`，加 `if not message.tool_calls: break` 越界守卫（`656-657`）与 `if partial_response.messages and ...` 空列表守卫（`664`）。`run()` 同步分支同样改用 `_terminal_call_name`（`420`）。
- 预算耗尽时**优雅停止并告警**（`run` 在 `443`，`run_async` 在 `692-697`），提示可提高 `MAX_AGENT_TURNS`。

**验证**：`test-optimizations.py` A（`_terminal_call_name` 6 项，含"第二槽位 case_not_resolved"）、B（非终止调用循环到 `max_turns=5` 停止、`hit_budget=True`）、C（`case_resolved` 在第二槽位仍 1 轮收尾）、D（`execute_tools=False` 且无 tool_calls 直接 break、不越界）、E（终止调用返回 `[Tool Call Error]` 时 continue 而非误收尾）。

### P0-2 缓存健壮化：失败不固化 + 版本戳 + 完整性校验

**文件**：`research_agent/inno/workflow/flowcache.py`

**问题**：
- 崩溃时写坏的缓存（末条 `role=="error"`、或缺 `experiment_report`）被**固化**，下次复用导致下游 `KeyError`（实测 `run_infer_plan.py:531` 取 `experiment_report` 崩溃）。
- 代码/提示词变更后**静默复用过期结果**，无版本管理。

**修改与所用函数**：
- 新增 `CACHE_VERSION = 1`（`flowcache.py:12`）与 `_REQUIRED_CONTEXT_KEYS`（`16-19`：`Judge Agent→suggestion_dict`、`Experiment Analysis Agent→experiment_report`）。
- `AgentModule.__call__`（`26-57`）的 Resume 与 fresh 两分支都改为**先判错再落盘**：`if ret_messages[-1]["role"] == "error": raise Exception(...)`（`40-41`、`51-52`）**不写缓存**，保留已有好缓存；成功才 append success 消息并 `save_cache`。
- `save_cache`（`58-65`）用 `json.dump` 写入 `{"version": CACHE_VERSION, "messages": ..., "context_variables": ...}`，打**版本戳**。
- `check_cache`（`66-98`）：用 `dict.get` 做版本校验，不符即 `return None, False`（`75-77`，**缺失版本按 legacy 接受**，向后兼容旧缓存）；Prepare 用 `isinstance`/`all` 做结构校验（`79-86`）；通用必需 context 键校验（`87-90`）。

**验证**：`test-optimizations.py` F（版本不符 / 缺 `suggestion_dict` / 缺 `experiment_report` / Prepare 结构不全 → 忽略重跑；legacy 无版本戳但完整 → 接受；Prepare 完整 → 接受）、G（`save_cache` 落盘带 `version`）、H（末条 error → 抛出且**缓存文件不存在**；成功 → 落盘带版本与 context、返回 context 已更新）。冒烟重跑中 8 个 legacy 缓存全部通过新校验被复用。

### P0-3 Prepare 解析改用结构化 context

**文件**：`research_agent/run_infer_plan.py`

**问题**：原来靠 `extract_json_from_output` 解析 Prepare 的**自由文本末条**拿 `reference_papers`，模型输出被截断/格式漂移时拿不到，裸 `KeyError` 或下错论文。

**修改与所用函数**（`run_infer_plan.py:175-185`）：
- 优先用 `context_variables.get("prepare_result")`（Prepare 经 `case_resolved` 写入的**结构化结果**）取 `reference_papers`（`175-176`）。
- 自由文本 JSON 仅作**回退**：`extract_json_from_output(prepare_res)`（`177-179`）。
- 两者都拿不到 → 抛**明确** `ValueError`（`180-185`），而非裸 KeyError。

**验证**：冒烟重跑 Prepare 缓存复用 + 新解析路径走通（`prepare_result` 结构完整）；`test-optimizations.py` F 的 Prepare 结构校验间接保障上游缓存质量。

---

## 2. P1 —— 执行层与配置防呆（决定长命令是否挂起、CPU 路径是否可复现）

### P1-1 容器命令超时 + TCP keepalive

**文件**：`research_agent/inno/environment/docker_env.py`

**问题**：`run_command` 的原始 TCP **无超时**，容器内长命令（训练/大文件）若服务端卡死，客户端 `recv` **永久阻塞**，整条流水线挂起；无 keepalive，NAT/防火墙静默断连后察觉不到。

**修改与所用函数**（`docker_env.py:142-180`，连接前）：
- `recv_timeout = float(os.getenv("COMMAND_RECV_TIMEOUT", "900"))`（`162`），`s.settimeout(recv_timeout)`（`179-180`）——**可配置**接收超时，默认 900s。
- `s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)`（`165`）开启 keepalive。
- Linux：`for opt, val in (("TCP_KEEPIDLE",60),("TCP_KEEPINTVL",15),("TCP_KEEPCNT",8))` 用 `hasattr`/`getattr` 逐项 `setsockopt`（`167-171`）。
- Windows：`s.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 60000, 15000))`（`174-177`），`try/except OSError` 兜底。

**验证**：`py_compile` + `import` 通过；既有 `test-tcp-framing-fix.py`（fix 14 分帧，容器在运行）不受影响；超时常量可经 `COMMAND_RECV_TIMEOUT` 配置。

### P1-2 配置防呆：GPUS 归一 + str_to_bool fail-safe

**文件**：`research_agent/constant.py`

**问题**：
- dotenv 把 `GPUS=None` 读成**字符串** `'None'`（真值），`docker_env` 拼出非法 `docker run --gpus None` 而失败（REPRODUCTION.md 已记录此坑）。
- `str_to_bool` 对**无法识别**的值原来 `return True`（如 `DEBUG=maybe→True`），会误开调试/评测开关。

**修改与所用函数**：
- GPUS 归一（`constant.py:49-54`）：`if isinstance(GPUS, str) and GPUS.strip().lower() in ("none","null","false","0",""): GPUS = None`。用 `isinstance` + `str.strip/lower` 把伪禁用值归一为真正的 `None`。
- `str_to_bool`（`constant.py:7-26`）：无法识别时 `print` 告警 + `return False`（`25-26`），**fail-safe**。

**验证**：`test-optimizations.py` I（`str_to_bool` 12 项：`maybe→False`、true/1/yes/on→True、false/0/off→False、bool 原样、空/None→False）、J（GPUS 7 项：`None/null/''/0/false→None`，`all`/`"device=0"` 保留；用 `importlib.reload` 注入 env 实测）。

---

## 3. P2 —— 可观测性、安全加固、死代码清理

### P2-1 每阶段运行指标汇总

**文件**：`research_agent/inno/core.py`

**问题**：每阶段无运行指标，空转/耗时/被短路调用数不可见，排障只能翻日志。

**修改与所用函数**（`run_async`，`core.py:612-710`）：
- 起始记 `run_start = time.time()`、`turns = 0`、`tool_call_count = 0`、`blocked_before = len(self._blocked_tool_calls)`（`613-616`）。
- 每轮 `tool_call_count += len(message.tool_calls or [])`（`643`）。
- 结束写 `self.last_run_stats`（`698-705`）：`agent / turns / tool_calls / blocked_calls / elapsed_sec / hit_budget`，用 `time.time`、`len`、`round`。
- `logger.info("Run summary: ...")`（`706-710`）输出一行汇总。

**验证**：`test-optimizations.py` B/C/E 断言 `last_run_stats.turns / tool_calls / hit_budget` 正确。

### P2-2 安全加固：git clone 去 shell 注入 + web 下载 TLS/代理可配置

**文件**：`research_agent/inno/environment/docker_env.py`、`research_agent/inno/tools/inno_tools/web_tools.py`

**问题与修改**：
- **git clone**（`docker_env.py:50-71`）：原 `["cd", ..., "&&", "git", ...]` join 后 `shell=True`，`task_name`/分支名可**注入 shell**，且 token 进 shell 字符串。改为 `shell=False` + 显式 argv + `cwd`：`subprocess.run(git_command, cwd=self.local_workplace, ...)`（`57`）、分支操作 `cwd=metachain_dir`（`62`、`65`）。token 只在 argv，不进 shell。用 `subprocess.run(cwd=...)`。
- **web 下载** `download_from_pdf_link`（`web_tools.py:660-727`）：原 `verify=False`（**禁用 TLS 校验＝MITM 风险**）+ 硬编码代理 `127.0.0.1:7890`。改为 `verify_ssl = os.getenv("PDF_DOWNLOAD_VERIFY_SSL","1")...`（**默认开启校验**）、`proxy_url = os.getenv("PDF_DOWNLOAD_PROXY","")`（默认尊重环境代理），两处 `verify=False → verify=verify_ssl`。用 `os.getenv`。

**验证**：`test-optimizations.py` L 源码断言（clone 用 cwd、不再 shell 拼接；web 无 `verify=False`、含两个可配置 env、无硬编码 7890）；`import` 通过。
**风险说明**：`git_clone` 默认 `False`、`download_from_pdf_link` 的 `web_tool_list` 在 `idea_agent.py:141-148` 已整段注释，二者**均不在 plan 主链路**，改动零风险。

### P2-3 死代码清理

**文件**：`research_agent/inno/tools/terminal_tools.py`、`research_agent/inno/agents/inno_agent/plan_agent.py`

**修改**：
- `terminal_tools.py`：删除**重复的第二处** `print_stream` 定义（原 296-298，与 277-279 完全相同、后者覆盖前者），保留 `execute_command` 之前的第一处（`277-279`）。
- `plan_agent.py`：删除模块级**裸 f-string**（原 107-118，import 时构造即丢弃的死代码）；移除**未使用**的 `plan_model` 导入（`2`，已确认仅 `get_coding_plan_agent` 被外部导入，`plan_model` 未被再导出）。

**验证**：`test-optimizations.py` L（`print_stream` 仅 1 处定义；`plan_model` 不在源码；裸 f-string 文本不在源码）；`import` 通过；冒烟 CodingPlan 缓存复用正常。

---

## 4. 暂缓项及原因（本轮不做，明确记录）

| 项 | 原因 |
| --- | --- |
| **GPU 镜像重建**（torch 1.13+cu116 不支持 sm_120，`--gpus` 静默全零） | 需重建 23.5GB 镜像换 torch 2.7+cu128，耗时；当前 CPU 路径已验证可跑。本轮用 **GPUS 归一**确保 CPU 路径防呆，重建留待确需 GPU 时。 |
| **真实数据集下载 / 真实实验** | 用户明确只验证链路跑通，toy 冒烟已覆盖；真实下载受网络/限流影响（arXiv 429、CIFAR 55KB/s），不在本轮范围。 |
| **mdconvert 两份副本完全去重** | fix 11/16 已分别修好两份的 `convert_a/img` 签名，功能正常；合并为一份属重构，风险大于收益，暂缓。 |
| **移除 `defaultdict(str)`**（`core.py:140` `get_chat_completion`） | 它让缺失 context 键返回空串而非 KeyError，是上游容错设计；贸然移除可能引入新 KeyError，需全链路审计，暂缓。 |
| **提示词参数化**（硬编码 GPU 要求、metaprompt 路径） | 属上游 prompt 工程，改动影响模型行为需真实实验验证，本轮不动。 |

---

## 5. 本轮修改文件清单

| 文件 | 涉及优化 |
| --- | --- |
| `research_agent/inno/core.py` | P0-1 控制流、P2-1 可观测性 |
| `research_agent/inno/workflow/flowcache.py` | P0-2 缓存健壮化 |
| `research_agent/run_infer_plan.py` | P0-3 Prepare 结构化解析 |
| `research_agent/inno/environment/docker_env.py` | P1-1 超时/keepalive、P2-2 git clone 加固 |
| `research_agent/constant.py` | P1-2 GPUS 归一 / str_to_bool fail-safe |
| `research_agent/inno/tools/inno_tools/web_tools.py` | P2-2 web 下载 TLS/代理可配置 |
| `research_agent/inno/tools/terminal_tools.py` | P2-3 print_stream 去重 |
| `research_agent/inno/agents/inno_agent/plan_agent.py` | P2-3 死代码 / 未用导入清理 |
| `logs/test-optimizations.py`（新增） | 无模型验证脚本（12 组 64 项） |

**复跑验证命令**（均不花钱）：

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe logs/test-optimizations.py      # 64 项断言
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe logs/test-flow-fixes.py         # 既有回归
PYTHONIOENCODING=utf-8 PYTHONUNBUFFERED=1 .venv/Scripts/python.exe logs/run-smoke-chain.py  # toy 冒烟（全缓存命中）
```