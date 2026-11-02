# 2026-11-02 · AI-Scientist-v2 执行可靠性与进程清理改进

## 本次目标

阅读项目的科研流水线，选择无需 GPU 和付费模型即可实际验证的问题进行改进，优先保证实验超时能退出、任务结束不误停其他研究任务。

本课程记录的上游版本：`96bd51617cfdbb494a9fc283af00fe090edfae48`。本次修改基于课程仓库提交 `fe20fc7`，仅改动 `project-02-ai-scientist-v2`。

## 代码阅读

主要调用关系：

1. `ai_scientist/perform_ideation_temp_free.py`：生成、反思研究想法，写入想法 JSON。
2. `launch_scientist_bfts.py`：加载一个想法、建立实验目录、生成配置，串联实验、绘图、论文和审阅。
3. `ai_scientist/treesearch/perform_experiments_bfts_with_agentmanager.py` 与 `agent_manager.py`：组织分阶段实验树搜索。
4. `ai_scientist/treesearch/parallel_agent.py`：生成、调试和改进实验代码，调用解释器完成实验、指标解析和绘图。
5. `ai_scientist/treesearch/interpreter.py`：在子进程执行代码，向树搜索返回输出、耗时和异常。

另外阅读了配置、实验日志与指标表示、Token 统计和文献检索等模块。本次聚焦解释器和启动器的进程生命周期，因为它们直接决定长时间科研任务能否停止、恢复，以及是否影响同机其他实验。

## 发现的问题与修改前后行为

| 问题 | 修改前 | 修改后 |
| --- | --- | --- |
| 清理范围过大 | 启动器扫描整机进程，按 `python`、`torch`、`mp` 等命令行关键词终止进程，可能误停其他任务，甚至命中自己 | 只选择指定根进程的后代，先 terminate，等待后再 kill 未退出者 |
| 异常后未清理 | 清理仅位于正常流程末尾，实验报错或 Ctrl+C 可跳过 | `main()` 通过 `try/finally` 清理，原异常继续向上传递 |
| 强制终止后卡死 | 杀死子进程后，父进程仍阻塞读取输出队列，等待永远不会发送的 `<\|EOF\|>` 标记 | 输出改用逐次刷新的临时文件；父进程不依赖输出结束标记 |
| 超时控制不稳定 | 使用系统时钟，超时后重复 SIGINT，交互会话超时直接断言失败；强制终止路径把耗时写成配置时限 | 使用单调时钟，SIGINT 仅发送一次，经过有限宽限期后终止，记录实际等待到结束判定的耗时 |
| 超时被伪装为成功 | 用户代码捕获中断后正常返回，可能被视为成功执行 | 已触发截止时间的运行统一标记为 `TimeoutError`，清理并丢弃会话 |
| 崩溃诊断不足 | 意外退出主要报告通用错误，相关资源不保证立即释放 | 错误包含退出码及已刷新的输出，异常路径清理进程、IPC 队列和临时文件 |

## 修改文件

- `ai_scientist/utils/process_cleanup.py`：新增共享的后代进程清理函数；容忍进程提前退出，并对权限不足和未完成清理给出警告。
- `ai_scientist/treesearch/interpreter.py`：重构输出收集与超时处理；支持 `timeout=None`，拒绝非正数、NaN 和无穷时限；取消共享可变环境变量默认值并复制输入；保留 stdout、stderr、Unicode、回车和异常信息。
- `launch_scientist_bfts.py`：拆分 `run_scientist()` 与 `main()`，在 finally 清理当前任务后代；延迟导入 GPU、模型和论文依赖，使帮助命令和清理测试可在轻量环境执行。
- `requirements.txt`：显式声明已有启动流程使用、现在也用于解释器清理的 `psutil`。
- `tests/test_interpreter.py` 与 `tests/test_process_cleanup.py`：新增离线回归测试。
- `README.md`：补充改进说明和离线验证命令。

公共接口方面，`Interpreter.run()` 和 `ExecutionResult` 的返回字段不变。`term_out` 仍是文本列表，但现在按文本行分段，调用方仍可使用 `"".join(term_out)`。新增可选构造参数 `timeout_grace_period=5.0`，不改变现有调用方式；该参数目前不通过 YAML 暴露。普通 Python 异常仍通过 `ExecutionResult` 返回，启动失败和异常进程退出仍抛出 `RuntimeError`。

## 验证环境与命令

验证环境：macOS / ARM64，Python 3.12.14，默认 multiprocessing spawn 启动方式。只在 `/private/tmp/scientist-v2-validation` 创建临时虚拟环境，未安装完整 GPU 环境。

本次实际安装的直接依赖：`humanize==4.16.0`、`dataclasses-json==0.6.7`、`shutup==0.3.0`、`psutil==7.2.2`。以下命令在项目目录执行，可在已准备的 Python 3.11+ 环境中复现：

```bash
python -m pip install humanize==4.16.0 dataclasses-json==0.6.7 shutup==0.3.0 psutil==7.2.2
python -m unittest discover -s tests -v
python launch_scientist_bfts.py --help
python -m compileall -q ai_scientist launch_scientist_bfts.py tests
git -c core.whitespace=cr-at-eol diff --check
```

沿用上游被修改文件的 CRLF 换行，差异检查通过命令级选项识别行尾 CR，未修改仓库 Git 配置。

## 实际结果

22 项回归测试全部通过，最终完整功能测试耗时约 2.2 秒。覆盖：

- 正常输出、stderr、Unicode、Python 异常信息、100 万字符输出及回车保真。
- 会话变量保留、重置和上一轮输出清空；原结束标记现在可以作为普通文本输出。
- 正常超时、忽略 SIGINT 后强制终止、捕获 SIGINT 后仍判超时、交互会话超时，以及超时后重新执行。
- `os._exit(7)` 突然退出时保留输出和退出码；启动失败、父进程中断、重复清理和临时文件删除。
- 无限时模式、用户主动抛出 `TimeoutError`、非法时限和环境变量隔离。
- 清理只选择后代、强制终止未响应者、进程提前消失、权限不足，以及启动器正常/异常/Ctrl+C 的 finally 行为。
- 真实进程集成测试：一个测试根进程启动忽略 SIGTERM 的子进程，旁边另启一个 Python 任务。清理根进程的后代后，目标子进程被杀死并回收，根进程和旁边任务继续运行；测试结束后回收所有测试进程。

另用修改前的 `HEAD` 解释器在独立临时目录复现旧问题：仅加速旧父进程的时钟，让其提前进入原本超时 60 秒后的强制终止分支。子进程忽略 SIGINT，随后被终止；旧父进程仍阻塞，最终被外层 4 秒监督超时结束。新实现由对应回归测试验证可及时返回 `TimeoutError`，不依赖该时钟替换。

帮助命令和语法编译检查通过。全项目编译还报告了原有 `perform_icbinb_writeup.py` 中 LaTeX 字符串的无效转义 `SyntaxWarning`，没有编译错误；该文件未在本次改动范围内。模拟启动失败的测试会故意产生 `FileNotFoundError` 子进程回溯，该用例最终通过。

首次在受限 macOS 沙箱中运行时，进程树查询被系统拒绝，发现底层可能直接抛出 `PermissionError`。已补充该异常的处理和回归测试，并清理该轮测试进程；随后在允许查询本用户进程树的环境下完成全部真实进程测试。没有调用模型 API，也没有产生模型调用费用。

## 局限与下一步

1. 尚未验证 Linux + Python 3.11 + CUDA、完整模型调用、树搜索、指标分析或论文生成，不能将本次测试视为完整科研复现或研究质量提升的证明。
2. 后代进程清理基于当时的进程树快照；已脱离该树的进程、权限不足的进程或父进程被 SIGKILL 时的善后，仍需由外层作业管理器或隔离环境负责。没有恢复关键词扫描作为兜底。
3. 临时输出逐次刷新会增加磁盘 I/O；目前没有输出大小配额，且最终输出仍需读入内存。启动器或解释器被强制杀死时，临时文件可能残留。
4. 宽限期、后代清理等待和进程 join 都有额外耗时，执行时限不等于整个 `run()` 调用的绝对墙钟上限。
5. 下一步建议在 Linux GPU 环境中运行一个小规模受控想法，确认工作进程、指标解析和绘图调用与本次生命周期改动兼容，再扩展运行前配置校验和中断后恢复能力。
