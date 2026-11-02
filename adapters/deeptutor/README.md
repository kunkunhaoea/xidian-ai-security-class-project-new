# DeepTutor课题适配

`files.py`提供轻量文件交接、当前正文读取、来源模板和非覆盖归档；`http.py`只在04承载状态和交接接口，不导入03依赖。03的`services/workbench.py`从完整仓库加载适配层，无需临时PYTHONPATH，`api/routers/workbench.py`复用03工作区、会话、管理员鉴权和导出服务。不迁移全局根目录或删除旧数据。

## 连接与读取

04校验固定shared课题和明确文件清单，保存隐藏交接记录，返回03前端`/workbench-connect?topic=...&handoff=...`。03在自己的登录态下调用`POST /api/workbench-bridge/connect`，注册/复用同目录工作区，在其作用域建立`wb_<交接ID>`会话，再进入原生`/chat/<id>?dt_workspace=<workspace>`。同一交接刷新复用会话，再次点击去聊天则新建，不自动发问或调用模型。不传密钥、Cookie或绝对文件路径，不使用postMessage，不复制临时资料库。

WebSocket沿用原协议，无新字段。共享文件复用03原生SelectedResources附件区域，保持shared原文件引用，不调用上传接口制作副本。发送时最多提供新/修改文件的前2000字符预览；未修改文件只保留身份。原生`workspace_read`按需读取最新正文（共享PDF复用pypdf），记录工具实际读取范围和指纹；不把全部正文每轮重复加入UnifiedContext，不新增共享资料模型预分析。保留手动上传附件的原生source manifest和处理链路。

`GET /api/workbench-bridge/sessions/{session}?workspace_id=...`返回清单、预览URL和最近读取状态；`PATCH /sessions/{session}/references`更新后续选定清单；`GET /sessions/{session}/file`提供选定原文件的实时预览/下载。移除不删文件，刷新交接页面不恢复已移除的附件。附件显示“文件引用/已附预览/已读取”，悬停查看范围与限制；每个原文件使用完整路径身份避免同名预览混淆。读取和引用记录仍在课题`.workbench/deeptutor/`，原生运行数据在`.deeptutor/`，不展示为研究输入。

沿用共享解析：Markdown/UTF-8文本及现有代码配置后缀，pypdf可提取文本的PDF；单文件15MB，解析预览沿用最多60000字符的上限，截断/解析失败记录警告，扫描件不提供OCR。最多30个文件，每份实际附加的短预览最多2000字符，不套用04全文分析的180000字符总输入限制。03环境需pypdf。全部所选文件无法读取时该轮失败，部分失败不冒充完整处理。

共享会话限普通chat，关闭自动问题推荐和下一句提示；文件工具限定当前课题，保留受限文本保存、明确导出和可用的联网搜索。exec/MCP/技能/全局记忆及知识库不在此会话自动开放，03独立会话保留原行为。这是课题边界；同课题其他公开文件仍可经工具读取，不能宣称严格文件级沙箱。扩大参考范围应先说明、征求同意。

`workspace_save`仅向可信运行时指定的`outputs/chat/<会话>/<轮次>/`保存非空UTF-8 .md/.txt（最多1MB），不覆盖已有文件，复用workspace_present生成原生文件卡片。不执行代码、不替代原聊天生成器。

## 主动归档

文件预览“归档”明确选择notes/plans/writing和标题，确认后调用`POST /api/workbench-bridge/archive`。后端校验workspace_item_id、课题、outputs会话/轮次与原件指纹；复用原生账户导出策略和`export_once`，确认只授权本次明确复制，不授予通用写权限，不重复要求聊天卡片确认。

新建`<目标>/<短标题>-<随机ID>/归档.md`和`原始文件.<后缀>`，outputs原件保留。归档Markdown是人工编辑和复用入口，原件只作生成记录。文本填入已有正文，非文本保留附件并配来源说明。模板区分该生成轮次的选定资料、短预览、实际工具读取和回答引用；未知生成时间/引用明确注明，不编造证据。归档成功使用原生短暂通知，不再显示常驻保存路径，按钮保持“归档”。

归档无LLM调用，04离线也可运行；失败可重试，不需重新生成。复制完成但说明保存失败时保留已复制原件、明确报错，重试另存版本。长标题保留正文，目录/文件名限制UTF-8字节，拒绝非法源、符号链接、隐私目录和任意目标。

配置、构建和验收见[工作台说明](../../workbench/README.md)，共同语义见[shared README](../../shared/README.md)。
