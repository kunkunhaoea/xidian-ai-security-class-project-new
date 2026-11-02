# 项目04共享资料适配

`engine.py`提供后台任务、采集归档和成果恢复；`http.py`提供工作台HTTP边界。公共文件操作位于 `adapters/shared_files.py`。原项目04继续承担采集、规则分析、模型总结和研究想法生成。

共享内容遵循 [shared/README.md](../../shared/README.md)。模型输出进入notes，原始采集记录及运行状态留在04的data/workbench，不导入其他项目的数据库，也不调用旧知识库的清理同步逻辑。

## 接口

基础前缀 `/api/workbench`，挂载于04已有FastAPI服务；交互式参数文档见启动后的 `/docs`。

| 方法与路径 | 用途 |
| --- | --- |
| GET /health | 服务、领域、PDF与共享目录状态 |
| GET、POST /topics | 列出、创建课题 |
| PUT /topics/{topic} | 修改显示名称，保留标识和README正文 |
| GET /topics/{topic}/files | 从实际文件列出资料及成果，含手动补充附件 |
| GET /topics/{topic}/file?path=… | 预览最新文件；download=true下载 |
| POST /topics/{topic}/import | JSON base64上传，补充附件或创建资料，不覆盖同名文件 |
| POST /topics/{topic}/prepare | 明确选定路径与选项，读取最新正文，冻结输入，返回完整预览及prepared_id |
| POST /topics/{topic}/tasks | 提交预览过的analysis/ideas任务，只消费冻结输入 |
| POST /topics/{topic}/crawl | 采集，固定JSONL与任务独立输出目录 |
| GET /topics/{topic}/tasks | 列出本课题任务 |
| GET /topics/{topic}/tasks/{id} | 状态、输入、日志、成果和可恢复情况 |
| GET /topics/{topic}/tasks/{id}/raw?path=… | 下载本次原始批次 |
| POST /topics/{topic}/tasks/{id}/cancel | 取消当前服务支持的活动任务 |
| POST /topics/{topic}/tasks/{id}/retry | 原输入重试；archive_only=true仅恢复归档 |
| GET、PUT /settings | 读取不含密钥的配置状态；用户明确保存时更新指定配置 |

所有文件接口校验课题、路径和符号链接，任务查询也校验归属。HTTP接口不能指定共享根目录。每份资料用稳定编号、规范化来源标识去重；新观察单独保留，不覆盖人工正文。

原04代码的小范围扩展：

- `api/main.py`：加载仓库适配包、注册工作台接口和静态入口、恢复中断任务。
- `pipeline/analysis_service.py`：新增显式记录分析入口，复用原规则与主题归纳；严格报告AI失败，不写全局研究数据库或旧知识库。
- `api/routers/scientist.py`：生成函数增加可选selected_context，仅在新路径使用冻结正文与外部检索结果；旧调用默认行为保持。
- 模型任务用ContextVar隔离运行时配置，密钥不持久化。

原功能对照、安装、前端构建、输入限制及手工验收见 [workbench/README.md](../../workbench/README.md)。
