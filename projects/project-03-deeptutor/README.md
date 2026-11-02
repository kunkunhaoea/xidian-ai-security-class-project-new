# 项目 03：DeepTutor 使用指南

DeepTutor 提供网页与命令行学习助手，支持对话、知识库、学习材料和多阶段学习任务。课程开发应安装当前目录的源码，以便修改后验证。

[课程任务](COURSE.md) · [课程总说明](../../README.md) · [上游原始 README](README.original.md) · [许可证](LICENSE)

## 1. 环境准备

源码开发版建议 Python 3.11–3.14、Node.js 22 LTS 和 npm。初学者可使用 Python 3.11；不同可选功能有各自的版本和系统依赖。先确认 `python --version`、`node --version`、`npm --version`。

下列步骤从课程仓库根目录开始，不需要再次克隆上游仓库。

## 2. 安装源码版

### Windows PowerShell

```powershell
cd projects/project-03-deeptutor
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
cd web
npm ci --legacy-peer-deps
cd ..
.\.venv\Scripts\deeptutor.exe init
.\.venv\Scripts\deeptutor.exe start --dev
```

直接调用虚拟环境中的程序，无需更改 PowerShell 执行策略。如果没有 `py` 命令，可用确认版本后的 `python` 替换第一条建环境命令中的 `py -3.11`。

### Linux / macOS Bash

```bash
cd projects/project-03-deeptutor
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
(cd web && npm ci --legacy-peer-deps)
deeptutor init
deeptutor start --dev
```

`init` 是首次初始化向导；后续启动不必重复初始化。`--dev` 用于源码开发，前端改动可热更新。生产模式使用 `deeptutor start`，会构建并复用本地前端。

## 3. 配置模型

打开终端显示的网页地址，默认 **http://127.0.0.1:3782**。

1. 按初始化向导或网页提示完成首次设置。
2. 在 **Settings → Providers** 配置自己的模型服务地址和密钥。
3. 在 **Language models** 中选择可用模型，并保存配置。
4. 先发起简单对话确认模型可用，再尝试知识库等功能。

本版本配置保存在运行主目录下的 `data/user/settings/`，**不会读取项目根目录的 `.env`**。每次从同一目录启动可避免误以为配置丢失；如使用 `DEEPTUTOR_HOME` 或 `start --home`，保持运行目录一致。

## 4. 完成一次最小演示

1. 在对话中询问一个熟悉的问题，例如“解释什么是对抗样本”，核验答案。
2. 在界面知识库功能中导入一份自己有权使用的小型文档，等待处理完成。
3. 针对文档提问，检查回答是否引用正确、是否遗漏上下文。
4. 记录输入、配置、回答及人工判断，再尝试课程要求的改进。

复杂 RAG 引擎可能还需要嵌入模型、解析服务或额外依赖。先跑通基础对话，不必一次安装所有扩展。

## 5. 命令行与常用操作

在已激活的环境中运行（Windows 未激活时将 `deeptutor` 换成 `.\.venv\Scripts\deeptutor.exe`）：

```bash
deeptutor --help
deeptutor run chat "解释什么是对抗样本"
deeptutor chat
deeptutor kb list
```

保持网页启动终端开启，按 Ctrl+C 停止前后端。源码安装后，Python 改动由可编辑安装使用；前端在开发模式中更新。

## 6. 数据位置与排错

课程新增[共享课题适配](../../adapters/deeptutor/README.md)：从workbench“去聊天”进入课题工作区。选定shared原文件显示在输入框原生附件区域，支持预览和移除；发送时提供短预览，正文按需读取最新内容，不每轮重复注入全文。生成文本通过workspace_save进入课题outputs，文件预览“归档”确认保存到notes/plans/writing，不调用AI；成功只短暂提示，不显示常驻路径。共享会话限普通对话及课题文件工具，独立会话保留原能力。Python修改需重启03，前端开发模式热更新、生产模式需重新构建。工作台使用非默认8080端口时，启动03前设置WORKBENCH_PUBLIC_URL，详见[工作台说明](../../workbench/README.md)。

运行主目录的 `data/` 保存本地配置和运行数据；不要提交个人密钥、会话、上传资料和知识库。课程中的代码目录与个人运行数据应区分。

| 问题 | 检查方式 |
| --- | --- |
| 找不到 deeptutor 命令 | 使用虚拟环境内完整路径，确认 `pip install -e .` 成功 |
| npm 安装依赖冲突 | 使用源码文档指定的 `npm ci --legacy-peer-deps`，检查 Node 版本 |
| 网页打不开 | 查看终端实际 URL、前端构建错误与端口冲突 |
| 修改 .env 没有效果 | 当前版本应使用初始化向导、网页设置和运行主目录配置 |
| 配置突然为空 | 检查本次启动目录与 `DEEPTUTOR_HOME` 是否变化 |
| 文档入库失败 | 检查解析和嵌入模型配置，先用小型纯文本资料排查 |

更多安装方案和可选扩展见原始 README。作业提交要求见 COURSE.md；本课程源码整理时的上游版本为 `f07029cfcf2c8dfccdb671cdfc343db8334f5741`。本次文档依据当前源码编写，未安装或启动服务验证。
