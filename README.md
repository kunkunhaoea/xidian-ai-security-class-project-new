# 个人科研工作台

本仓库基于四个科研辅助项目，建设一个由用户分阶段推进、通过共享课题资料协作的个人论文工作流。

**当前仅建立目录与设计约定。一键启动、统一网页和跨项目适配尚未实现。** 四个项目仍按各自 README 配置和运行。

## 团队入口

- [课程说明与 Fork、分支、提交和 PR 流程](COURSE.md)：原根 README 已保留为 COURSE.md，提交作业前请阅读。
- [总体框架与协作分工](docs/workbench-design.md)：说明建设目标、整体框架和各模块协作关系。
- [开发日志](docs/devlog/README.md)：记录修改、验证及待解决问题。
- [每周组会材料](docs/meetings/README.md)：保存个人研究进展文档和 PPT。

## 目录结构

```text
仓库根目录/
├── COURSE.md                 # 原课程说明与协作提交规范
├── projects/                 # 四个现有项目，保留原位置
├── workbench/                # 统一入口与服务管理（待实现）
├── adapters/                 # 各项目输入输出与调用适配（待实现）
├── shared/                   # 个人课题资料与实验产物，Git忽略
│   └── topics/
└── docs/
    ├── workbench-design.md   # 总体框架与协作分工
    ├── devlog/               # 开发日志
    └── meetings/             # 周组会文档与PPT
```

`shared/` 固定放在仓库根目录，当前本机已建立。Git 不同步该目录，新克隆仓库后手动执行 `mkdir -p shared/topics`；后续启动器应自动创建。

## 四个项目

| 项目 | 工作流中的主要用途 | 使用说明 |
| --- | --- | --- |
| 01 AI-Researcher | 基于想法、论文与参考代码开展研究的可选引擎 | [项目01](projects/project-01-ai-researcher/README.md) |
| 02 AI-Scientist-v2 | 想法生成、实验迭代与论文写作的可选引擎 | [项目02](projects/project-02-ai-scientist-v2/README.md) |
| 03 DeepTutor | 选定文献问答、讨论与研究笔记 | [项目03](projects/project-03-deeptutor/README.md) |
| 04 Intelligence Gathering | 公开资料检索、采集与归档 | [项目04](projects/project-04-Intelligence%20Gathering/README.md) |

01和02能力重叠，不要求按顺序执行。网页负责调度，阅读编辑使用 Obsidian 或 VS Code。团队通过 Git 同步代码和文档，每个人独立维护自己的 `shared/`；密钥与 Codex 本地文件不提交。
