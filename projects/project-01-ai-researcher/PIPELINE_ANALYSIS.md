# Project 01（AI-Researcher）全流程梳理与优化分析

> 日期：2026-11-10。本文与 `REPRODUCTION.md` 互补：后者记录复现期"修了什么"（修复 1–16），本文回答"完整流程是什么 / 仍存在哪些不足 / 该怎么优化"。所有论断尽量给 `file:line` 证据，路径相对项目根 `projects/project-01-ai-researcher/`。区分【已修】【未修】【设计取舍】三类，避免把已修项当成现存缺陷。

---

## 1. 项目是什么

一句话：**多 Agent 自动科研系统**。输入一个创新 idea ＋ 若干参考论文，系统自动完成"找参考代码库 → 调研论文 → 制定编码计划 → 写模型/训练/测试代码 → 在容器里跑实验 → 评审实现 → 分析结果并迭代"，最终产出一个可运行的项目与实验报告（另有论文生成模式）。

- 框架：自研 **MetaChain**（swarm 式多 Agent＋工具循环，`research_agent/inno/core.py`），通过 **LiteLLM** 路由到不同模型。
- 执行：ML 代码在 **Docker 容器**里跑，宿主机经**自定义 TCP 协议**下发 shell 命令（`inno/environment/docker_env.py` ↔ 容器 `/app/tcp_server.py`）。
- 浏览：**Playwright/BrowserGym**（`browser_env.py`，带 JS）＋ **RequestsMarkdownBrowser**（`requests_markdown_browser.py`，无 JS、抓网页转 Markdown）。
- 检索：本地 embedding（all-MiniLM-L6-v2）＋ GitHub/arXiv 搜索工具。

三种运行模式（`main_ai_researcher.py:54-133`）：

| 模式 | 入口 | 用途 |
| --- | --- | --- |
| `Detailed Idea Description` | `run_infer_plan.main`（:80） | 给定详细 idea，直接做计划→实现→实验（本文主线） |
| `Reference-Based Ideation` | `run_infer_idea.main`（:117） | 先基于参考论文自主构思 idea，再实现 |
| `Paper Generation Agent` | `paper_agent.writing`（:132） | 把实验结果写成论文 |

---

## 2. 完整流程（Detailed Idea Description 主线）

### 2.1 入口与配置

`main_ai_researcher()` 从 `.env` 读全部配置（`CATEGORY/INSTANCE_ID/TASK_LEVEL/CONTAINER_NAME/WORKPLACE_NAME/CACHE_PATH/PORT/MAX_ITER_TIMES`），用 `global_state.INIT_FLAG` 防重入并 `os.chdir(research_agent/)`，再调 `run_infer_plan.main()`。`main()`（`run_infer_plan.py:556-601`）建 `DockerEnv`→`init_container()`→`setup_dataset()`→建 `BrowserEnv`/`RequestsMarkdownBrowser`→构造 `InnoFlow`→`asyncio.run(flow(...))`。

**双模型配置**（`run_infer_plan.py:139-145`）：ML Agent 用 `COMPLETION_MODEL`（强模型），Prepare/Survey/CodingPlan/Judge/ExpAnalyser 用 `CHEEP_MODEL`（廉价模型）。本机 `.env` 两者都设 `openai/deepseek-flash`。

### 2.2 阶段链与数据流

`InnoFlow.forward()`（`run_infer_plan.py:146-552`）顺序如下。`context_variables` 是贯穿全链的共享字典，各阶段往里写键、后续阶段读取：

| # | 阶段 | 执行体 | 关键产出 / 写入的 context 键 | 终止方式 |
| --- | --- | --- | --- | --- |
| 1 | 加载实例 | `ToolModule(load_instance)` :147 | `source_papers/task_instructions/date/date_limit` | 工具缓存 |
| 2 | GitHub 搜索 | `ToolModule(github_search)` :153 | 每篇论文 top-10 仓库 | 工具缓存 |
| 3 | **Prepare Agent** | :170 | clone 并选 5–8 参考库；写 `prepare_result` | `case_resolved` |
| 4 | arXiv 源码下载 | `ToolModule(download_arxiv_source_by_title)` :174 | 参考论文 `.tex` 落盘 | 工具缓存 |
| 5 | **Survey Agent** | :192 | 子代理 Paper/Code Survey 写 `notes`；汇总写 `model_survey`(:194) | `case_resolved` |
| 6 | 数据集描述 | `importlib` 加载 `benchmark.process.dataset_candidate.{category}.metaprompt` :196 | `DATASET/BASELINE/COMPARISON/EVALUATION/REF` | 静态 |
| 7 | **Coding Plan Agent** | :239 | `plan_dataset/training/testing` 工具写 `dataset_plan/training_plan/testing_plan` | `case_resolved` |
| 8 | **ML Agent（实现）** | :394 | 在容器写 model/training/testing/data_processing＋`run_training_testing.py`，跑 2 epoch | `case_resolved`/`case_not_resolved` |
| 9 | **Judge Agent** | :423 | 内嵌 Code Review 子代理；写 `suggestion_dict`，给 `fully_correct` 判定 | `case_resolved` |
| 10 | 返工环 | `for i in range(MAX_ITER_TIMES)` :426-490 | ML 按建议改→Judge 再评，`fully_correct:true` 提前跳出 | 轮数上限 |
| 11 | **ML Agent（提交）** | :504 | 只改 epoch 重跑 `run_training_testing.py` 取统计结果 | `case_resolved`/`case_not_resolved` |
| 12 | 实验迭代环 | `for i in range(EXP_ITER_TIMES)` :507-552 | **ExpAnalyser**(:528) 写 `experiment_report` 列表→**ML refine**(:551) 按 `further_plan` 补实验 | `case_resolved` |

> `MAX_ITER_TIMES` 本机 `.env`=0 → 跳过返工环（阶段 10），Judge 即便 `fully_correct=False` 也直接进 Submit。`EXP_ITER_TIMES` 正常=2、冒烟=1（:507）。

**数据流简图：**

```
idea + references
   │
   ▼
load_instance ──► github_search ──► Prepare Agent ──► prepare_result
   │                                     │
   │                              download_arxiv (.tex)
   ▼                                     ▼
Survey Agent ──► notes / model_survey ──► Coding Plan Agent ──► dataset/training/testing_plan
   │                                                              │
   ▼                                                              ▼
dataset_description (metaprompt) ─────────────────────────► ML Agent (实现)
                                                              │ 容器内写代码 + 跑 2 epoch
                                                              ▼
                                                         Judge Agent ──► suggestion_dict
                                                              │  [MAX_ITER 返工环]
                                                              ▼
                                                         ML Agent (提交) ──► submit_res
                                                              │  [EXP_ITER 环]
                                                              ▼
                                                  ExpAnalyser ──► experiment_report
                                                              │  (analysis_report + further_plan)
                                                              ▼
                                                         ML Agent (refine) ──► 最终结果
```

### 2.3 MetaChain 工具循环（`core.py`）

每个 Agent 由 `run_async()`（:567）驱动一个"LLM 补全 → 执行工具 → 回填上下文"的循环：

1. `get_chat_completion_async()`（:428）把 `instructions(context_variables)` 作 system，拼 history，注册工具 schema，经 LiteLLM `acompletion` 调模型；DeepSeek 系附加 `extra_body={"thinking":{"type":"disabled"}}`（:469-470）。
2. 终止判定（:611-665）：`tool_choice != "required"` 时，无 tool_calls 且回到入口 Agent 即结束；`tool_choice == "required"` 时，**只看 `tool_calls[0].function.name`** 是否 `case_resolved`(:616)/`case_not_resolved`(:633)，执行后末条消息非 `[Tool Call Error]` 才 break。
3. `handle_tool_calls()`（:195）逐个执行工具，更新 `context_variables`，支持 `transfer_to_*` 切换子代理；含**反空转守卫**（:98,300-313）：同工具同参数连续 3 次相同输出即拉黑短路。
4. 重试：`@retry(stop_after_attempt(6), wait_exponential(min=30,max=1200))`（:422-427），`should_retry_error`（:43-80）按异常类型/消息匹配（连接错误、超时、429、overloaded、context_length 等；**402 余额不足不重试**）。
5. 上下文超限：`try_completion_with_truncation()`（:523-565）只把**最后一条**消息截到 10000 token（`truncate_message` :83-95）再重试一次。

### 2.4 缓存与断点续跑（`flowcache.py`）

- `AgentModule.__call__`（:15）先 `check_cache`：缓存存在则弹 `Yes/Resume/No` 菜单（:70）。`Yes`=直接复用缓存消息＋context、**跳过模型调用**；`Resume`=在缓存基础上续跑；`No`=全新跑。
- 缓存文件名按 `agent.name.replace(" ","_").lower()`（:47,58），如 `machine_learning_agent_iter_submit.json`。
- **无论成功失败都 `save_cache`**（:29,40），随后若末条 `role=="error"` 才 `raise`（:32-33,43-44）。
- `ToolModule`（:81）同理缓存工具结果（load_instance/github_search/arxiv 下载）。

### 2.5 执行环境层

- `DockerEnv.init_container()`（`docker_env.py:42`）：`docker run -d --user root -p {port}:8000 -v {local}:/workplace ...`（:92-97）；`GPUS` 非空才拼 `--gpus`（:91）。
- `run_command()`（:142）：原始 TCP 连 `localhost:{port}`，发命令文本→`shutdown(SHUT_WR)` 半关闭（:167，修复 14）→按行收 JSON `type=chunk/final`（:186-195）。**socket 全程无 `settimeout`**。
- 容器 `/app/tcp_server.py`：`receive_all` 读到 EOF（修复 14），`bash -c 'conda activate autogpt && cd /{workplace} && cmd'` 执行，流式回 chunk＋final。
- 两份 HTML→Markdown 转换器：`environment/mdconvert.py`（1207 行，web 浏览腿，函数内内嵌 `_CustomMarkdownify`）与 `markdown_browser/mdconvert.py`（1045 行，本地文件腿），diff 达 2003 行（修复 11/16 各修一份）。

---

## 3. 不足清单（带证据＋影响）

### 3.1 稳定性 / 控制流

1. **【未修】Agent 循环无轮数上限。** `max_turns=float("inf")`（`core.py:359,575`），且无调用方传入（`flowcache.py:24,35`）。开放式任务会无限自我实验：实测首轮冒烟 ML Agent 在 toy 数据上不停做超参扫描/消融，47 轮不收尾；更早一次 `read_file` 同参数重复 274 次直到余额耗尽（`core.py:118-119` 注释）。反空转守卫只拦"同参数同输出"，拦不住"每轮换新实验"的过度探索。**影响：烧 token、链路卡死。** 当前靠提示词显式设界缓解（见 §5），非治本。
2. **【未修】终止判定只看第一个工具调用。** `run_async` 仅检查 `tool_calls[0].function.name`（`core.py:616,633`）。若模型把 `case_resolved` 放在第二个调用、或并行发多个调用，终止分支被跳过，Agent 继续空转。**影响：收尾不可靠。**
3. **【未修】同步 `run()` 不识别 `case_resolved`。** 只在无 tool_calls 时 break（`core.py:398`，旧判断被注释 :401-403），与 `run_async` 行为不一致。生产走 `run_async`，但两条路径语义分裂是隐患。
4. **【未修】单次补全异常即整链终止。** `run_async` 捕获补全异常→append `role:error`→break（`core.py:599-602`）→flowcache `raise`（:43-44）→整个 forward 崩溃。重试 6 次耗尽后，一个瞬时错误就杀掉前面所有阶段的成果（虽有缓存可续，但需人工删坏缓存）。**影响：脆弱、恢复成本高。**
5. **【未修】`defaultdict(str)` 掩盖缺键。** `get_chat_completion_async` 把 context 包成 `defaultdict(str,...)`（`core.py:437`），instructions 引用未写入的键时静默返回空串而非报错。**影响：bug 被隐藏，下游拿到空计划/空调研。**
6. **【未修】Prepare 输出解析脆弱。** `extract_json_from_output`（`run_infer_plan.py:64-91`）用括号计数找 JSON，失败返回 `{}`，随后 `prepare_dict["reference_papers"]`（:173）直接 KeyError。**影响：Prepare 一旦没输出合法 JSON 即崩。**

### 3.2 缓存与可复现

7. **【未修】失败结果也被固化进缓存。** `save_cache` 在 raise 前执行（`flowcache.py:29,40`），把含错误的消息写盘；`ToolModule` 同样缓存失败工具结果（REPRODUCTION 已知遗留：arXiv `IncompleteRead` 被固化）。复用坏缓存会连锁报错——本轮冒烟即因 `experiment_analysis_agent_iter_refine_1.json` 缺 `experiment_report` 而在 `run_infer_plan.py:531` KeyError，需手动删缓存。**影响：重跑前必须人工甄别删缓存。**
8. **【未修】缓存无版本/完整性校验。** 仅 Prepare Agent 有完整性检查（`flowcache.py:60-69`，本轮加的），其余 Agent 缓存只要文件存在就复用。缓存名只按 agent name＋iter，**不绑定代码版本/提示词/模型**——改了提示词或模型后旧缓存仍被当真。**影响：静默用过期结果，复现失真。**
9. **【未修】Agent 命名冲突致缓存串用风险。** `idea_agent.py:151` 的 Agent name 仍是 `"Paper Survey Agent"`，与 survey 子代理重名；缓存按 name 命名（`flowcache.py:47,58`）。在 `Reference-Based Ideation` 模式下可能互相覆盖缓存。**影响：跨阶段缓存污染。**

### 3.3 环境与网络

10. **【未修】真实数据集下载阻塞。** `benchmark/process/dataset_candidate/vq/` 缺真实 `cifar-10-python.tar.gz` 与 `edm/README.md`（只有 FID 统计 `cifar10-32x32.npz`＋metaprompt）；容器直连 HTTPS 大文件被 `SSL: UNEXPECTED_EOF` 截断；toronto 源 ~55KB/s。**影响：真实复现卡在 ML 下载。** 本轮被 toy 冒烟绕开。
11. **【未修】长命令 ~120s `WinError 10060` 根因未定位。** `run_command` 的 socket **无 `settimeout`**（`docker_env.py:142-199`），容器 tcp_server 也无超时；已排除全局 socket 默认超时（=None）。嫌疑在 Windows TCP 空闲重置或异步层。**影响：长/静默命令在宿主机端超时。** toy 模式用短命令绕开。
12. **【设计取舍/未修】GPU sm_120 静默全零。** 镜像 `torch 1.13.1+cu116` 最高 `sm_86`，本机 RTX 5060 是 `sm_120`；`cuda.is_available()=True` 但张量运算返回 0.0 不抛异常（REPRODUCTION 实测）。**影响：用 `--gpus` 跑会产出全零假结果，比报错更危险。** 当前 `.env` `GPUS=`（空）走 CPU 规避；治本需重建镜像换 torch 2.7+cu128。
13. **【未修】`GPUS=None` 字符串陷阱。** `gpu_cmd=["--gpus",GPUS] if GPUS else []`（`docker_env.py:91`），dotenv 把 `GPUS=None` 读成字符串 `'None'`（真值）→`docker run --gpus None` 失败。正确禁用写法是 `GPUS=`（空）。**影响：配置易踩坑。**

### 3.4 安全

14. **【未修】token 拼进 git URL＋`shell=True`。** `docker_env.py:52` `git clone https://{AI_USER}:{GITHUB_AI_TOKEN}@github.com/...` 经 `subprocess.run(...,shell=True)`。token 可能泄漏进进程列表/git 报错/.git remote；插值进 shell 有命令注入面。**减轻：`git_clone` 默认 False，正常路径不触发。** 建议改 credential helper／环境变量＋`shell=False`。
15. **【未修】关闭 TLS 校验＋硬编码代理。** `inno_tools/web_tools.py:679,705` `verify=False`，:666-668 硬编码 `127.0.0.1:7890`，:656 硬编码 HKU 图书馆代理。**减轻：该文件是未注册副本，正常路径不走它。** 仍是代码库内的安全/可移植隐患。
16. **【设计取舍】容器 `--user root`＋任意代码执行。** `docker_env.py:94`。ML Agent 在容器内执行任意 shell/python，root 放大逃逸面。有 `--userns=host` 但容器仍 root。建议非 root 用户＋只读挂载参考库＋资源限额。
17. **【已修／良好】密钥管理 posture 良好。** 全部配置走 `os.getenv`（`constant.py`），无硬编码密钥（全库 `sk-`/`api_key=` 字面量扫描 0 命中）；`.gitignore` 排除 `.env`/`*.key`/cache/logs/venv。`browser_env.py:275` 含 Cloudflare 人机验证绕过，属合规灰色地带，留意目标站点 ToS。

### 3.5 工程质量 / 代码异味

18. **【未修】大量重复代码。** 两份 `mdconvert.py`（diff 2003 行）；`web_tools.py` 与 `inno_tools/web_tools.py`、`code_search.py` 与 `inno_tools/code_search.py`、`arxiv_source.py` 与 `inno_tools/arxiv.py` 各两份；`idea_agent.py`≈`survey_agent.py`（连 `get_survey_agent` 都复制 :237）；`docker_container.py:7-95`≈`docker_env.init_container` 且硬编码 `12345:12345`(:62)。**影响：修一处漏一处（修复 11→16 正是漏网副本）。**
19. **【未修】裸 except／死代码／游离提示词。** `browser_env.py:268,334` `except: pass`；`terminal_tools.py:277,296` `print_stream` 定义两次；`plan_agent.py:107-118` 游离 f-string 旧提示词；`plan_model` 导入却未挂载（`plan_agent.py:2`）→`model_plan` 键可能从不被写；多处大段注释逻辑（`tools/__init__.py:1-26`、`core.py:401-403,650-652` 等）。
20. **【未修】疑似 bug。** `github_ops.py:110` `get_current_branch()` 缺 env 参数；`inner.py:27` `case_resolved()` 缺参。
21. **【未修】配置小瑕疵。** `str_to_bool` 对无法识别的值默认返回 True（`constant.py:23`，如 `DEBUG=maybe`→True）；`LOG_PATH` 赋值即被 `global_state.LOG_PATH` 覆盖（:34-35，死赋值）；`main_ai_researcher.py` 重复 import、`init_ai_researcher()` 空函数。
22. **【未修】可观测性弱。** 日志是 verbose pretty-print，无结构化指标（每阶段 token/耗时/工具调用分布/成本）；排障靠人肉数日志（REPRODUCTION 里 274 次/571 轮都是手数出来的）。

### 3.6 提示词与评测语义

23. **【设计取舍】提示词冗长＋硬约束冲突。** `ml_dev_query`（`run_infer_plan.py:243-358`）~115 行，密布 MUST/IMPORTANT，硬性要求"真实数据集＋GPU＋实现每个原子概念＋2 epoch"。无 GPU/无真实数据时与运行环境冲突，模型反复试 cuda 浪费轮次。冒烟模式靠 `_smoke_relax` 字符串替换放宽（:39-63）——**字符串替换脆弱**，提示词一改就失配。
24. **【设计取舍】非函数调用回退硬编码模型。** `NOT_USE_FN_CALL` 路径把模型硬编码成 `"deepseek-chat"`（`core.py:507`），靠文本解析还原 tool_calls，脆弱且与配置解耦。本机走 `openai/deepseek-flash` 真函数调用，不触发此路径。

---

## 4. 优化建议（分优先级）

### P0 — 稳定性与正确性（先做，直接决定能否无人值守跑完）

- **给 Agent 循环加预算上限。** 在 `run_async`/`run` 把 `max_turns` 设为有限值（如 40–60），并由 `AgentModule` 按阶段传入；超限走优雅收尾（注入"请用 case_resolved/case_not_resolved 总结当前进展"）而非硬崩。落点：`core.py:359,575,587`＋`flowcache.py:24,35`。配合现有反空转守卫，治"过度探索"。
- **健壮化终止判定。** 遍历**所有** `tool_calls` 检测 `case_resolved/case_not_resolved`（而非只看 `[0]`），并统一 `run()` 与 `run_async()` 语义。落点：`core.py:616,633,398`。
- **失败不固化＋缓存校验。** `save_cache` 仅在末条非 error 时写盘；复用前校验关键 context 键存在（仿 Prepare 的 :60-69 推广到所有 Agent）；缓存名/内容绑定提示词＋模型＋代码版本哈希，变更即失效。落点：`flowcache.py:29,40,47,58`。
- **错误恢复而非整链终止。** 补全异常重试耗尽后，尝试降级（截断上下文/换 CHEEP_MODEL/跳过当前子任务）并保留已完成阶段，而非 `raise` 杀掉全链。落点：`core.py:599-602`＋`flowcache.py:43-44`。
- **修 Prepare 解析与缺键。** `extract_json_from_output` 失败时给明确错误并触发重试，而非 `{}`→KeyError；评估去掉 `defaultdict(str)` 改显式键校验。落点：`run_infer_plan.py:172-173`、`core.py:437`。

### P1 — 可复现与环境（决定能否做真实实验）

- **数据集就位。** 把真实 `cifar-10-python.tar.gz` 等预置进 `dataset_candidate/`（或提供离线下载脚本＋校验和），修正 metaprompt 里不存在的路径（`edm/README.md`）。落点：`benchmark/process/dataset_candidate/vq/`。
- **定位并修 120s 超时。** 给 `run_command` 的 socket 加 `settimeout`＋应用层心跳/keepalive，长命令改容器内后台执行＋轮询日志（避免宿主机长连接空闲）。落点：`docker_env.py:158-199`、容器 `tcp_server.py`。
- **GPU 镜像重建。** `docker/Dockerfile` 换 torch 2.7+cu128（支持 sm_120），用国内 wheel 源（REPRODUCTION 已验证 mirror.sjtu.edu.cn 可达）；或文档明确"本镜像只能 CPU"。落点：`docker/Dockerfile`。
- **配置防呆。** `GPUS` 解析把字符串 `'None'` 归一为禁用；`str_to_bool` 无法识别时抛错或默认 False。落点：`docker_env.py:91`、`constant.py:7-23`。

### P2 — 工程质量与安全（长期可维护）

- **去重。** 合并两份 mdconvert/web_tools/code_search/arxiv；删 `docker_container.py` 重复实现与死代码（重复 `print_stream`、游离提示词、注释块）；修 `idea_agent` 命名冲突。落点：见 §3.5。
- **安全加固。** git 改 credential helper＋`shell=False`；删 `verify=False`、代理走配置；容器非 root＋资源限额＋参考库只读挂载。落点：`docker_env.py:52,94`、`inno_tools/web_tools.py:666-706`。
- **可观测性。** 每阶段记录结构化指标（token/耗时/工具调用直方图/成本/重试次数）到 JSON，便于自动检测空转与预算告警。落点：`core.py` 循环＋`logger.py`。
- **提示词工程。** 把硬约束（真实数据/GPU/epoch）参数化由配置注入，替代 `_smoke_relax` 字符串替换；精简冗长 MUST 段。落点：`run_infer_plan.py:243-358`。

---

## 5. 已修复项回顾（详见 REPRODUCTION.md）

复现期已修 16 处（不重复列为现存缺陷）：arXiv 双重编码/https/限流重试（1,12,13）、Windows file URI 与纯文本/二进制读取（2,3,4,7）、未保护 json.loads（5）、cp936 编码往返（6）、**反空转守卫**（8）、read_file 附父目录 ls（10）、mdconvert 签名兼容（11,16）、**TCP 半关闭分帧**（14）、**NO_PROXY IPv6 导入崩溃**（15）。

本轮（2026-11-10）另修：`core.py:227` 非法 JSON 兜底分支误用未定义 `messages`→改 `partial_response.messages`（曾致 ExpAnalyser 截断参数时 NameError 崩整链）；并给冒烟模式 refine 阶段加"跑一次即停"约束。

**冒烟验证结论：** `AI_RESEARCHER_SMOKE=1` 下整链 Prepare→…→ExpAnalyser→ML refine 端到端跑通（`logs/smoke-run-20261110-attempt3.log`，exit 0，1.0 min，致命错误 0、防抖 0 触发）；toy VQ-VAE 真实训练 2 epoch（loss 0.2257→0.0814，test recon MSE 0.03255），产物经 `docker exec` 落盘核验非臆造。**真实数据下载（§3.3）仍未修，被 toy 绕开。**

---

## 6. 一句话总结

链路设计与多 Agent 分工是完整的，冒烟已证明"能跑通"；但**无人值守稳定性（无轮数上限、终止只看首个调用、失败固化缓存、单点异常整链崩）**与**真实复现环境（数据集缺失、120s 超时、GPU 静默全零）**是两大短板，应优先按 P0→P1 推进；工程质量（重复代码、安全细节、可观测性）按 P2 持续收敛。