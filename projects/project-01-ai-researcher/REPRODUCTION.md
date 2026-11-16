# Project 01 首次复现记录

## 2026-11-04 实验镜像拉取成功，容器可用性验证

结论：`tjbtech1/airesearcher:v1` 已在本机就位（`docker images` 显示 23.5 GB，digest `sha256:82ac65712eb504f100bff34448004004bb6e16fb6bf1e8f4cf8d37a60b0f8345`）。此前三次失败的根因是网络路径，不是 Dockerfile 或磁盘。新增的临时脚本与日志都在 `logs/`，研究算法源码未改动。仍未运行任何研究任务，7039 网页本次未启动。

网络诊断（本次实测）：

- Docker Desktop 未运行，已启动；Server 29.1.3，VM 内存 7.559 GiB，`Docker Root Dir=/var/lib/docker`。
- 直连失败：`registry-1.docker.io` 与 `auth.docker.io` 均 TCP 层超时（12 s 无响应），当时 Windows 代理开关为 `ProxyEnable=0`，7897 端口无人监听。
- 镜像站不可用于本镜像：`docker.m.daocloud.io` 对该仓库返回 403（只代理官方 library 镜像），`docker.xuanyuan.me`、`docker.aityp.com` 403，`dockerproxy.net` 400，`hub.rat.dev` 实际转发到 `docker.1ms.run`。
- 唯一可用的 `docker.1ms.run`：manifest 与 18 个小层（0.58 GiB）可下载，但大层被重定向到 CloudFront，对 `Range: bytes=2000000000-...` 返回整文件 `200 Content-Length: 4025548083`，即**不支持断点续传**，且单流实测仅 0.04–0.5 MiB/s。两个大层 3455 MiB + 3839 MiB 按此速度需数小时且中断即整层重来，因此“靠镜像站硬磨”不可行。
- 打开代理客户端（系统代理 `127.0.0.1:7897`）后 `docker pull tjbtech1/airesearcher:v1` 直接成功，落盘速率约 4–7.5 MiB/s。Docker Desktop 的 `settings-store.json` 无自定义代理键，即 `system` 模式跟随系统代理。
- 排障经验：`docker pull` 中途会静默停滞（45 s 内 vhdx 零增长、无新日志）。`taskkill //IM docker.exe //F` 后重新 `pull`，已完成的层显示 `Already exists`，可按层粒度续传。本次靠这一步避免了重来。

容器验证（按 `research_agent/inno/environment/docker_env.py:90-96` 的实际参数，用临时名 `paper_eval_smoke`，验证后已删除）：

- `docker run -d --platform linux/amd64 --userns=host --gpus '"device=0"' --user root -v <本地目录>:/workplace_paper -w /workplace_paper -p 7020:8000 --restart unless-stopped` 启动成功；Git Bash 下需 `MSYS_NO_PATHCONV=1` 且用 `D:/...` 形式路径，否则 `-w` 被改写成 `C:/Program Files/Git/...`。
- 镜像内容：Ubuntu 22.04.3、conda Python 3.10.13、`torch 1.13.1+cu116`、`torchvision 0.14.1+cu116`、`torch_geometric 2.7.0`，`/app/tcp_server.py` 与 supervisord 配置齐备。
- supervisord 拉起 `tcp_server` 监听 `0.0.0.0:8000`；宿主机用原始 TCP 连 `127.0.0.1:7020` 可连通（HTTP 探测必失败，它是自定义协议：客户端发 shell 命令文本，服务端按 JSON `{"type":"chunk","data":...}` 行回传）。挂载双向可见。
- 容器资源：24 核可见，GPU 透传成功（`nvidia-smi -L` 列出 RTX 5060 Laptop）。

必须处理的问题（只有一个）：

1. **GPU 结果静默错误。** 镜像内 `torch.cuda.get_arch_list()` 最高 `sm_86`，本机 GPU 是 `sm_120`。`torch.cuda.is_available()` 返回 `True`、`torch.cuda.synchronize()` 不报错，但 `torch.randn(64,64,device='cuda').abs().sum()` 返回 **0.0**（CPU 同一操作为 3276.945），矩阵乘同样得 0。也就是说在这个镜像里用 GPU 跑实验会产出全零而不抛异常，比直接报错更危险。可选方案：`.env` 设 `GPUS=None` 走 CPU（README 已说明支持无 GPU 设置），或用 `docker/Dockerfile` 重建并把 torch 换成支持 sm_120 的版本（2.7+cu128 级别）。本次只做了判定，未验证训练能否跑通。
工作目录一条已排除（先前判断有误，现按代码更正）：容器挂载点不是由 `DOCKER_WORKPLACE_NAME` 决定的。`research_agent/run_infer_plan.py:499-508` 用 `WORKPLACE_NAME`（`.env` 里为 `workplace`）拼出 `DockerConfig.workplace_name`，于是 `docker_workplace=/workplace`，正好等于 `docker/tcp_server.py` 的默认 `--workplace /workplace`，终端命令与文件工具落在同一目录。`DOCKER_WORKPLACE_NAME=workplace_paper` 在本流程中只被 `inno/environment/utils.py:12` 的 `setup_metachain()` 引用，而 `setup_package`/`git_clone` 默认 `False`，不会被调用；它误导了本次冒烟测试的挂载路径，值本身可以不改。本地任务目录是 `research_agent/workplace_paper/task_<INSTANCE_ID>_<COMPLETION_MODEL>/workplace`（其中 `workplace_paper` 一段在 `run_infer_plan.py:499` 硬编码），容器名还会追加 `_<instance_id>_<model>` 后缀，与 `.env` 的 `CONTAINER_NAME=paper_eval` 不完全一致。

容器网络出口实测（临时容器 `egress_test`，验证后已删除）：容器内直连可达 `pypi.org`（200，首包约 10 s）、`github.com`、`arxiv.org`、`download.pytorch.org`、`api.deepseek.com`，`pip download six` 成功；`huggingface.co` 直连超时，但经宿主代理 `http://host.docker.internal:7897` 返回 200（1.3 s）。国内源从容器内直连均可用：`mirrors.aliyun.com/pypi`（0.45 s）、`pypi.tuna.tsinghua.edu.cn`（1.55 s）、`mirror.sjtu.edu.cn/pytorch-wheels/cu116`（0.47 s，重建镜像时可用作 wheel 源）、`hf-mirror.com`（308 跳转）。`www.cs.toronto.edu` 的 CIFAR-10 原始包实测约 55 KB/s（约 170 MB 需 50 分钟）。容器可见 24 核、7 GiB 内存（Docker VM 上限 7.559 GiB）。

`vq` 类别的数据不在网上下载：`benchmark/process/dataset_candidate/vq/cifar10-32x32.npz` 名字有误导性，实际内容是拟合出的高斯参数 `mu (2048,)` 与 `sigma (2048, 2048)`，正是 `one_layer_vq` / `rotation_vq` 任务所用的合成隐变量数据集；`setup_dataset()` 只把这个目录复制到任务工作区，不联网。

无 GPU 与 GPU 两种容器行为对比（重要）：不带 `--gpus` 启动时 `torch.cuda.is_available()` 为 `False`、`device_count()` 为 0，`torch.randn(..., device='cuda')` 抛出明确的 `RuntimeError: Found no NVIDIA driver`，容器内也看不到 `/dev/nvidia*`。也就是说缺 GPU 会**大声失败**，而透传 `--gpus` 反而**静默返回全零**。结论：这个镜像绝不能用 `--gpus` 跑实验，宁可 CPU。

`.env` 的 `GPUS` 取值有坑。上游 `docker/Dockerfile` 同级模板注释写“None for no gpu”，但 python-dotenv 把 `GPUS=None` 读成字符串 `'None'`，而 `docker_env.py:90` 是 `gpu_cmd = ["--gpus", GPUS] if GPUS else []`，于是会执行 `docker run --gpus None` 并失败。本次实测三种写法：`GPUS=None` → `'None'`（真值，生成非法 `--gpus None`）；`GPUS=`（空值）→ `''`（假值，不拼 `--gpus`，这才是禁用 GPU 的正确写法）；`GPUS='"device=0"'` → `device=0`。

CPU 可行性实测（`logs/cpu-bench.py`，容器内 `-m 6g`）：按上述 `mu/sigma` 采样 4096×2048 高斯向量（0.9 s），用一个约 107 万参数的 MLP VQ-VAE（codebook 512×16）跑 2 个 epoch 共 128 步只需 **0.6 s**，约 14000 samples/s，峰值 RSS 533 MB，loss 从 0.126 降到 0.103。结论：`vq` 类别在 CPU 上完全跑得动，不需要 GPU；把 `.env` 的 `GPUS` 改成空值即可进入可复现路径。代价是 `run_infer_plan.py` 给 ML Agent 的提示词硬性要求“model should be train on GPU device”，无 GPU 时模型可能反复尝试 cuda 而浪费轮次，本次未验证该提示词冲突的实际影响。

本次证据：`logs/pull-hub-direct.log`（成功记录）、`logs/pull-daocloud.log`、`logs/pull-1ms.log`（403 与不支持 Range 的失败记录）、`logs/pull-full.log`；临时脚本 `logs/pull-full.mjs`（按层 sha256 校验）、`logs/pack-load.mjs`（打包直接灌 `docker load`，本次未使用）、`logs/smoke-client.mjs`（控制通道往返测试）、`logs/mirror-probe.mjs`、`logs/avail-test.mjs`。镜像站下载缓存 `logs/image-cache/`（约 4 GB）验证完成后已删除。

### 2026-11-04（续）受限示例运行与五处源码修复

运行方式：`logs/run-restricted-example.py`，等价于网页里 `main_ai_researcher(question, reference, "Detailed Idea Description")`；`question` 取 `benchmark/final/vq/one_layer_vq.json` 的 `task1`，`reference` 按 `run_infer_idea.py:warp_source_papers` 的格式拼接 8 篇 `source_papers`。容器 `paper_eval_one_layer_vq_openai__deepseek-flash` 由程序自建，7020 端口。

`.env` 改动两处（原文件已备份到 `logs/.env.backup-20261104-before-cpu`）：`GPUS=`（空值，禁用 `--gpus`，避免 sm_120 静默全零）、`GITHUB_AI_TOKEN=`（占位符 `your_github_ai_token` 会被当成真 token 发出，GitHub 返回 401 Bad credentials，`search_github_repos` 直接抛异常；实测不带 Authorization 头可用）。

无头运行有两个坑：一是 `BrowserEnv` 在 `__init__` 里调用 `multiprocessing.set_start_method('spawn', force=True)`，子进程会重新导入驱动模块，缺 `if __name__ == "__main__"` 就递归重启整个流程（实测一次生成 3 个 python 进程、日志涨到 50 KB，已在 BrowserEnv 阶段自我了断，未产生模型调用）；二是 `inno/workflow/flowcache.py` 发现缓存文件时用 inquirer 弹 Yes/Resume/No，无 TTY 会永久卡住，驱动脚本里把 `single_select_menu` 替换成自动返回第一项（Yes）。

五处源码修复（都在本课程副本内，`git diff` 可看全部改动）：

1. `inno/tools/arxiv_source.py` arXiv 标题搜索双重编码。原代码先 `urllib.parse.quote(query)` 再把结果交给 `urlencode(params)`，空格被编码两次（`%20` → `%2520`），arXiv 收到字面 `%20` 零命中，于是 8 篇参考文献里 6 篇报 `Cannot find the paper ... in arxiv`，论文 TeX 一个都没下到。改为只让 `urlencode` 转义一次，并用 `ti:"..."` 短语匹配。修复前后：0/8 → 8/8 命中，且 `download_arxiv_source_by_title` 能真实落地 `.tex`。
2. `markdown_browser/requests_markdown_browser.py` Windows file URI 解析。`open_local_file` 用 `pathlib.Path.as_uri()` 得到 `file:///D:/...`，而 `_fetch_page` 用 `url[7:]` + `normcase/normpath` 解析，得到 `\d:\...`（前导反斜杠、小写盘符），`os.path.isfile` 为 False，puremagic 抛 `PureError("Not a regular file")`，所有本地文件读取全部失败（一次 Paper Survey 阶段重复报错 274 次，代理空转烧 token）。改用标准库 `url2pathname(urlparse(url).path)`，并且不能再套 `normcase`：Windows 上它会把盘符转小写，而第 153 行的越界断言是大小写敏感的子串匹配。
3. 同文件 `_fetch_page` 的纯文本支持。`.tex` 不被 markitdown 支持，会抛 `UnsupportedFormatException` 并被上层当成“下载”，页面内容变成 `# Download complete / Saved file to ...`，代理拿不到论文正文。现在对一批纯文本后缀（tex/bib/py/log 等）直接以 `utf-8` + `errors="replace"` 读取；实测某篇论文的 `.tex` 含 `0xA1` 字节，严格解码会整页失败。修复后 `open_local_file` 对 `.tex`/`.py` 都返回正常内容（4 KB 视口 + 页头）。
4. 同文件 `_convert_local_to_docker` 的沙箱断言。`env.address` 读本地文件后仍是 `file:///D:/...`，直接对它断言 `local_workplace in path` 在 Windows 必然失败（Linux 上 URI 与本地路径分隔符一致才侥幸通过），`_get_browser_state` 每次抛“路径必须是从 workplace 目录出发的绝对路径”。现在先把 URI 归一为本地规范路径再断言。
5. 两处未受保护的 `json.loads`（防御性修复）。`inno/logger.py:_warp_args` 和 `inno/core.py:208` 都直接解析模型给出的工具调用参数；模型输出一旦被截断，`JSONDecodeError` 会把整个任务打挂（第 5 次运行在 Paper Survey 第 15 轮 EXIT=1）。日志改成降级为原样截断显示，`core.py` 改成回一条 `[Tool Call Error]` 的 tool 消息让代理重试，与上面“工具不存在”的处理方式一致。

已知遗留：`download_arxiv_source_by_title` 偶发 `IncompleteRead`（arXiv e-print 下载不稳定），而 ToolModule 会把这次失败结果写进缓存固化下来，需要手动删 `research_agent/cache_<instance>_<model>/tools/download_arxiv_source_by_title.json` 才会重下。

成本记录：第 3、4、5 次运行都在 Paper Survey 阶段被中止（第 4 次日志 190 KB、355 轮工具调用后人工停止，当时在反复读不存在的 `/workplace/papers/*.tex`）；`prepare_agent` 与 `github_search` 的缓存保留复用，重跑不重复这部分调用。

### 2026-11-04（再续）针对“反复报错重试”的流程优化

第 6 次运行跑到 Coding Plan Agent 阶段后因 DeepSeek 返回 HTTP 402 `Insufficient Balance` 退出（EXIT=1，18:30）。日志 1.2 MB / 约 6.5 万行，其中 Coding Plan 阶段 571 轮，工具调用分布严重异常：

| 调用 | 次数 | 说明 |
| --- | --- | --- |
| `read_file('/workplace/VQGAN-pytorch/vqgan.py')` | 274 | 每次都返回同一句解码报错 |
| `read_file('/workplace/VQGAN-pytorch/README.md')` | 74 | 同上 |
| `read_file('/workplace/dataset_candidate/edm/README.md')` | 44 | 该文件根本不存在，但 benchmark 的 metaprompt 里写了这个路径 |
| `read_file('.../utils.py'`、`encoder.py` 等) | 44 / 36 / 32 | 同类重复 |
| `open_local_terminal_output` 报错 | 277 | `'utf-8' codec can't decode byte 0xa6 / 0xa9` |

根因是一条 Windows 编码往返链：`read_file` → 容器内 `cat` → `terminal_tools.process_terminal_response` 把输出写到 `terminal_tmp/*.txt` 时用**平台默认编码（中文 Windows 是 cp936）** → `open_local_terminal_output` 经文本浏览器按 **utf-8 严格解码**读回 → `UnicodeDecodeError` → 工具只返回一句报错 → 模型看不到内容，于是原样重试同一个调用。274 次重复调用几乎全是这样烧掉的。

五处修复（都在本课程副本内，`git diff` 可查）：

6. `inno/tools/terminal_tools.py:process_terminal_response` 写临时文件改为 `encoding="utf-8", errors="replace"`，消除编码往返不一致。
7. `markdown_browser/requests_markdown_browser.py:_fetch_page` 的本地文件读取改成“永不硬失败”：二进制后缀（npz/pt/png/gz 等）直接回一行说明并明确劝停——转换器原本对二进制不报错，而是返回 `# Download complete / Saved file to ...` 存根，模型既拿不到内容也看不到错误，只会重试；已知纯文本后缀（tex/bib/py/log/txt 等）有损直读；其余先交转换器，失败**或**返回下载存根时用 `errors="replace"` 兜底。新增 `_set_local_text_page()` 辅助方法与 `_BINARY_SUFFIXES` 集合。
8. `inno/core.py` 增加通用反空转机制（这是治本的一条）：`MetaChain` 为每个「工具名 + 完全相同的参数」记录输出指纹，连续 `_MAX_IDENTICAL_TOOL_RESULTS = 3` 次输出一模一样就在结果尾部追加劝停说明并把该调用拉黑，第 4 次起**不再执行工具**，直接回 `[Blocked repeated call]` 并提示改用别的工具/参数/翻页。因为判据是“输出完全相同”，分页、执行命令、写文件这类正常会变化的调用不受影响。
9. `inno/tools/arxiv_source.py:download_arxiv_source` 重写下载：双源（`https://arxiv.org/src/<id>` 失败后退到 `https://export.arxiv.org/e-print/<id>`）、超时 `(15, 180)`、每源 3 次退避重试、流式写盘、`.tex` 用 utf-8 落盘（原实现用平台默认编码，正是后续 `0xA1` 解码错误的源头）、arXiv 返回单个 tex 而非 tar.gz 时兜底直读；新增 `_safe_slug()` 处理标题里的冒号等 Windows 非法字符（`Finite scalar quantization: VQ-VAE made simple` 原来直接 `[Errno 22] Invalid argument`）。
10. `inno/tools/terminal_tools.py:read_file` 在返回 `No such file` 时附带父目录 `ls -la` 结果，让模型一次就知道实际有什么，而不是照着 metaprompt 里的路径反复试（实测 44 次）。
11. `markdown_browser/mdconvert.py` 的 `convert_a` / `convert_img` 签名与已安装的 markdownify 1.2.3 不兼容：新版以 `convert_fn(node, text, parent_tags=parent_tags)` 调用，旧签名 `(el, text, convert_as_inline)` 直接抛 `TypeError: convert_a() got an unexpected keyword argument 'parent_tags'`，任何带 `<a>` 的网页都转不成 Markdown（网页浏览这条腿是断的，本次运行报 3 次）。改成同时接受新旧两种协议后，实测 `<a>`/`<img>`/`<h2>`/`javascript:` 链接都能正确转换。
12. `inno/tools/arxiv_source.py:search_arxiv` 的 `base_url` 原来用 `http://export.arxiv.org`，该端点现在对 http 返回 301，而 `feedparser.parse(url)` 不跟这个跳转，拿到空 feed → 上层报 `Cannot find the paper ... in arxiv`（实测一次 8 篇里 7 篇这样失败，与修复 1 的双重编码是两个独立原因）。改为 https，并用 `requests`（超时 15/60 s）取回内容再交给 feedparser，零命中也退避重试 3 次；改后 8/8 标题都能解析到正确的 arXiv ID。
13. arXiv 限流与错误信息处理：`download_arxiv_source` 对 429/5xx 退避重试（10 s × 次数）而不是直接放弃，换源前再缓 3 s；`download_arxiv_source_by_title` 在每篇之间加 3 s 礼貌延时（arXiv 官方建议间隔）并打印 `i/N` 进度；`search_arxiv` 对 429 与「HTTP 200 但 0 条结果」（限流的另一种表现）退避重试 4 次，并把真实失败原因记在 `search_arxiv.last_error` 上；`_safe_slug` 去掉文件名尾部的 `.`（Windows 不允许，实测会生成 `auto-encoding_variational_bayes..tex`）。**错误信息本身也是修复对象**：原来一律回 `Cannot find the paper '<title>' in arxiv`，而实测多数是限流或 TLS 中断，模型据此以为“换个说法再搜就能找到”，于是反复重试；现在回的是 `Could not retrieve '<title>' from arXiv (<真实原因>). This is usually arXiv rate limiting ... Do not retry this download`。
    复验结果：标题搜索 7/8 成功（1 次是 `SSLError: UNEXPECTED_EOF_WHILE_READING`，与拉镜像时遇到的 TLS 中断同类，属本机网络抖动，且现在会如实报原因）；源码下载在连续请求触发限流的情况下 4/8 成功（含 35747 字符的 VQ-VAE、61212 字符的 FSQ、54818 字符的 VAE、37506 字符的 Gumbel-Softmax），其余为 429 / ReadTimeout / SSLError。arXiv 侧的稳定性受本机网络与限流影响，无法保证 8/8，但失败现在都有明确原因且不会诱发重试循环。

验证：`logs/test-flow-fixes.py`，11 项本地检查全部 PASS，且**不调用模型、不花钱**——覆盖混合/非法字节与 cp936 写入的临时文件读取、tex 真源码读取（不再是下载存根）、二进制文件只回短说明、重复调用第 3 次提示第 4 次短路、输出会变化的工具 6 次调用不被误杀、含冒号标题的文件名合法化。arXiv 8 篇参考文献的重下结果见 `logs/test-arxiv-network.log`。

重跑注意：`prepare_agent`、`survey_agent`、`coding_plan_agent` 的缓存都在，可复用；但 `cache_one_layer_vq_openai__deepseek-flash/tools/download_arxiv_source_by_title.json` 固化的是修复前的失败结果，重跑前要删掉才会用新的下载逻辑。（原注“仍未处理：网页 HTML 转换的 `_CustomMarkdownify.convert_a()` 版本不匹配”已于 2026-11-10 修复，见下文修复 16。）

### 2026-11-10 链路复查：命令分帧、导入崩溃、web 转换三处缺陷

本次不花模型 token，对 ML / Judge / ExpAnalyser 都要走的执行 backbone 与 web 浏览腿做静态＋无模型实测复查，又定位并修掉三处会在自动运行期触发错误的缺陷。

14. **容器 tcp_server 大命令截断（ML Agent 写代码的必经路径）。** `create_file`/`write_file` 经 `write_file_in_chunks` 把内容 base64 后以 `echo "<b64>" | base64 -d > file` 发送，单条命令可达 ~100 KB。容器内 `/app/tcp_server.py`（由 `docker/tcp_server.py` 经 Dockerfile `COPY tcp_server.py /app/tcp_server.py` 进镜像）的 `receive_all` 用 `if len(part) < buffer_size: break` 判定命令结束——但 TCP 是字节流，`recv` 在任何分段边界都可能返回不足 4096 字节（即使后面还有数据在途），于是命令被从中间截断，bash 收到未闭合引号报 `unexpected EOF while looking for matching '"'`，或写出损坏文件，模型反复重写同一文件形成死循环。无模型实测（`research_agent/logs/probe-tcp-framing.py`，复刻客户端线协议）：修复前 15 次 50 KB 大写入有 1 次截断并报上述 bash 错误。修法＝半关闭分帧：客户端 `DockerEnv.run_command` 发完命令后 `s.shutdown(socket.SHUT_WR)`，服务端 `receive_all` 改为读到真正 EOF（`b""`）才停（`buffer_size` 提到 65536，`decode("utf-8", errors="replace")`）。三处服务端源同步修：`docker/tcp_server.py`（重建镜像用）、`inno/environment/tcp_server.py`，并把补丁 `docker cp` 进运行中的容器后 kill 旧进程由 supervisord（`autorestart=true`）拉起。修复后复验：probe 30/30 完整；走**真实工具函数**的 `logs/test-tcp-framing-fix.py`（create_file/write_file/read_file/execute_command → 真实 `DockerEnv.run_command`）12/12 轮 120 KB 多分块写入 md5 全部一致、含中文注释的文件读回无损、2000 行长输出正常分帧。客户端读循环也换成增量 UTF-8 解码器，避免多字节字符被 recv 边界切断时 decode 抛异常。
    注意：容器补丁在容器可写层，`docker restart` 保留，但 `docker rm` 重建容器会丢；重建镜像（`docker/Dockerfile`）则因 `docker/tcp_server.py` 已修而自带修复。原容器服务端已备份到 `research_agent/logs/tcp_server_container_original.py`。

15. **NO_PROXY 含方括号 IPv6 导致整包导入即崩溃（与模型余额无关的启动级阻塞）。** `import research_agent.inno` → `inno/__init__.py` 的 `from .core import MetaChain` → `core.py` 的 `import litellm`，litellm 在导入时就构造 `httpx.AsyncClient(trust_env=True)`；httpx 0.27.2 会把 `NO_PROXY` 的每个条目当 URL 模式解析，本机 `NO_PROXY=localhost,.localhost,127.0.0.1,127.0.0.0/8,[::1],::1` 里的方括号 IPv6 `[::1]` 被误读成端口 `':1]'`，抛 `httpx.InvalidURL: Invalid port: ':1]'`，于是整个包导入失败、流水线还没跑就挂。逐项实测确认只有方括号形式触发（裸 `::1`、CIDR `127.0.0.0/8`、`localhost` 都能被 httpx 接受）。修法：在 `inno/__init__.py` 导入 core 之前加一个兜底归一化，把 `NO_PROXY`/`no_proxy` 里的 `[IPv6]` 去掉方括号成裸 IPv6（保留“本地地址不走代理”原意），`try/except` 保证任何异常都不阻断导入。修后 `import research_agent.inno` 成功，`NO_PROXY` 变为 `...,::1,::1`。

16. **web 浏览腿的 HTML→Markdown 转换器版本不匹配（修复 11 的漏网副本）。** 修复 11 只改了 `markdown_browser/mdconvert.py`（本地文件/terminal 浏览用），但 `browser_env.py` 的 `_get_page_markdown` 用的是**另一份** `inno/environment/mdconvert.py`，其函数体内局部定义的 `_CustomMarkdownify.convert_a` / `convert_img` 仍是旧签名 `(el, text, convert_as_inline)`。markdownify 1.2.3 统一以 `convert_fn(node, text, parent_tags=...)` 调用（实测源码第 324 行），旧签名直接 `TypeError`，survey/idea/judge 走 web 浏览时整页转换失败。给这两处签名补 `convert_as_inline=False, parent_tags=None, **kwargs`（与已验证的本地副本一致）；标题走的是基类 `convert_hN`（大写 N，第 403 行 lambda），自定义的小写 `convert_hn` 是死代码，不动。无模型验证 `logs/test-web-mdconvert.py`：用 AST 从真实源文件抽出该类 exec 后转换含 `<a>`/`<img>`/`<h2>`/javascript 链接/`<blockquote>`/`<pre>` 的 HTML，8 项全 PASS（不再抛异常、http 链接转 Markdown、javascript 链接剥离、图片渲染、data URI 截断、标题渲染、下划线转义）。

验证汇总（均不调用模型、不花钱）：`logs/test-flow-fixes.py` 11 项仍全 PASS（回归无破坏）；`logs/test-tcp-framing-fix.py` 全 PASS（真实客户端端到端，12/12 轮 120 KB 多分块写入 md5 一致）；`research_agent/logs/probe-tcp-framing.py` 修复后 30/30；`logs/test-web-mdconvert.py` 8 项全 PASS。修复 14/15/16 涉及的文件都已 `py_compile` 通过，运行中容器服务端已确认带修复（`grep` 到 `buffer_size=65536` / `if not part` / `errors="replace"`）。

仍存的硬阻塞：DeepSeek 余额 HTTP 402，完整研究实验需充值后才能实跑；本次只做了无模型的链路缺陷修复与验证。

## 2026-10-28 状态复查

Docker 服务可连接，但 `tjbtech1/airesearcher:v1` 镜像仍不存在；此前拉取进程已不在运行，旧执行会话也已失效，无法从该会话确认退出原因。网页服务当前未运行，7039 端口不可访问。本地检索模型文件保留，DeepSeek 密钥配置保留，依赖一致性检查仍通过。昨日的编码、入口导入和实际工具调用成功记录保留；本次未重复付费模型调用。完整研究实验尚未启动。下文“仍在拉取”描述的是 2026-10-27 当时状态。

日期：2026-10-27。依据本项目 README.md 执行。当前完成环境安装、网页启动、本地检索模型及 DeepSeek 工具调用验证，尚未完成研究任务或论文实验复现。

## 环境与已通过的检查

- Windows / PowerShell；WSL2 当前只有 docker-desktop，无普通 Linux 发行版。因此本次在 Windows 原生独立虚拟环境验证，未声称完成 README 推荐的 Linux/WSL 环境验证。
- Python 3.11.16，uv 0.12.5，Docker Engine 29.1.3，Docker 服务端可连接。
- GPU：NVIDIA GeForce RTX 5060 Laptop GPU，8 GB；未验证实验容器内 CUDA。
- 项目 `.venv` 已按 `uv pip install -e .` 安装，255 个包；最终 `uv pip check` 通过。
- 主要版本：ai-researcher 0.2.0、LiteLLM 1.55.0、Gradio 5.23.1、Playwright 1.39.0、BrowserGym 0.13.0、PyTorch 2.14.1。
- `python -m playwright install` 成功安装 Chromium、Firefox、WebKit；Chromium 无头启动和本地页面内容检查通过。
- `python web_ai_researcher.py` 成功启动；`http://127.0.0.1:7039` 返回 HTTP 200。真实浏览器确认页面含 AI-Researcher，且 Conversation Record / Environment Variable Management 两个标签可见。仅验证渲染，未触发研究任务。

## 模型配置

已从 `.env.template` 新建仅本机使用的 `.env`，Git 忽略已确认。

根据用户选择和 DeepSeek 官方文档 <https://api-docs.deepseek.com/>，使用：

```dotenv
COMPLETION_MODEL=openai/deepseek-flash
CHEEP_MODEL=openai/deepseek-flash
API_BASE_URL=https://api.deepseek.com
OPENAI_API_KEY=
```

`openai/` 是给旧版 LiteLLM 使用的兼容路由前缀，服务端模型名为 `deepseek-flash`。用户已在本机填写 DeepSeek 密钥；后续验证 `/models` 返回 HTTP 200，包含 `deepseek-flash`，并通过项目安装的 LiteLLM 1.55.0 完成实际工具调用：`report_ready({"status":"ready"})`。调用使用 `thinking.type=disabled`。其余 OpenRouter 模板字段不用于此兼容路由。密钥未写入本记录。

## 继续下载后的验证

- Hugging Face 直连与代理仍不稳定，改从 ModelScope 的 `sentence-transformers/all-MiniLM-L6-v2` 同名仓库下载所需的 10 个文件。
- 权重文件为 90,868,376 字节，SHA-256 为 `53aa51172d142c89d9012cce15ae4d6cc0ca6895895114379cacb4fab128d9db`，与此前 Hugging Face 解析的 blob 摘要一致。
- 模型位于 `C:/Users/rain/.cache/ai-researcher/all-MiniLM-L6-v2`，已通过 `.env` 的 `LOCAL_EMBEDDING_MODEL_PATH` 指向该目录。未关闭 TLS 证书校验；下载进程使用 Windows 系统证书库。
- 离线加载及编码成功，输出形状 `(1, 384)`；`research_agent.run_infer_plan` 和 `research_agent.run_infer_idea` 导入均成功。此前工具注册失败的连带报错已消失。
- 再次执行原镜像 `docker pull`，本次已有更多层下载完成，结束本轮验证时仍在拉取，尚未得到完整镜像。注册表清单显示压缩总大小约 7.71 GiB，最大两个层约 3839.1 MiB 和 3454.5 MiB。不能将层下载完成视为整个镜像拉取成功。
- 新证据：`embedding-modelscope.log`、`research-import-verified.log`、`deepseek-smoke.log`；下载辅助脚本位于被 Git 忽略的 `logs/download_model.py`。

## 尚未通过的环节

1. `docker pull tjbtech1/airesearcher:v1` 之前两次遇到 `short read ... unexpected EOF`。备用站 `docker.m.daocloud.io` 返回 403。最新一次拉取仍在进行，镜像尚未确认安装成功，没有创建实验容器。
2. 尚未运行 `vq/one_layer_vq` 或任何其他研究任务。`examples/` 中产物仍是上游样例；模型工具调用与检索编码仅是连接和组件验证。
4. 原生 Windows 提示找不到 PATH 中的 ffmpeg；音视频转换尚未验证。

## 重启与继续步骤

在本目录 PowerShell 运行：

```powershell
.\start-local.ps1
```

如果本次服务仍在运行，可直接访问 <http://127.0.0.1:7039>。重复启动前先停止旧进程，避免端口冲突。

先等待 README 指定 Docker 镜像完整拉取成功，再验证容器通信、挂载与 GPU 支持，然后从界面运行一个受限示例。检索模型与 DeepSeek 配置已验证。README 的 `MAX_ITER_TIMES=0` 保留原语义，不视为禁用实验。

安装阶段 PyPI 通过本机代理曾超时；在单次 PowerShell 进程中设置 `$env:NO_PROXY='*'` 后，通过官方源完成安装。这是本次环境的排障记录，不是所有网络环境的通用配置。

本机证据：`install-direct.log`、`playwright-install.log`、`web-startup.log`、`research-import-offline.log`；完整依赖快照位于 `logs/requirements-reproduced.txt`。日志、环境和缓存均不提交。新增启动脚本与本记录未修改研究算法源码。
