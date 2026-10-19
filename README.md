# FocusFlow

## SYA design journeys (2026-10-19)

The default SYA React workspace uses `plans.preview` then explicit `plans.confirm`. Preview calls the configured model and returns an editable four-level tree without changing the plan store; absent/failed model requests fail visibly. Manual drafts use the same confirm contract. The older `/api/v1` and `/decompose` workflows remain available in the advanced planner and retain their legacy no-key sample behavior.

Electron supplies `SYA_SCHEDULER_DATA_DIR` under its user-data scheduler directory. Confirmed projects, task dates/content/order/status, and configuration are atomically persisted to `planner.json`; mutations roll back in memory on write failure. A source server started without this environment variable uses an ephemeral store. Only one runtime may own a given data directory. Work hours (`workStart`, `workEnd`) and daily granularity (`fine`, `standard`, `coarse`) control renderer calendar blocks. Parent React views schedule unassigned tasks from today; these are provisional placements until an explicit date is saved. Focus completion never marks a scheduler task complete.

Implementation choice: reuse the existing Pydantic task tree and FastAPI nested bodies (MIT), Python `json`/atomic `os.replace` (PSF), and the existing async lock. SQLite (public domain) and a new ORM were considered; one bounded project snapshot under a single runtime owner does not require another persistence layer. No dependency was added. References: [FastAPI nested models](https://fastapi.tiangolo.com/tutorial/body-nested-models/), [Python atomic replace](https://docs.python.org/3.11/library/os.html#os.replace).

Source tests: `uv run --with-requirements requirements.txt python -m unittest discover -s tests -t . -p 'test*.py' -v`. Tests use temporary storage and a fixed model result; they do not validate live model quality or a packaged binary.


FocusFlow 把一个很长的目标拆成阶段，以及你现在就能做的下一步。

## Agent SDLC 开发框架

本仓库已经内置一套面向 FocusFlow 的项目级 Agent SDLC。它不是另一个应用或运行时服务，而是由 BMAD、TEA、OpenCode Skills、项目知识和 GitHub 质量门禁组成的 AI 辅助软件工程流程。它用于让产品分析、需求、UX、架构、实现、审查、测试和交付证据遵循同一套项目规则。

当前固定版本为 BMAD/BMM `6.12.0`、TEA `v1.24.0` 和 OpenCode `1.18.29`，共提供 39 个仓库级 Skills。版本与完整性记录位于 `agent-sdlc.lock.json`。

### 它的作用

- 让 AI 先识别产品意图、已接受规格、接口契约和当前实现证据，再修改代码。
- 为产品、UX、架构、开发和测试提供职责明确的 Agent 与工作流。
- 将需求、实现、测试和 PR 证据串联起来，减少只改代码、不更新契约或测试的情况。
- 区分静态检查、服务/源码契约、打包 Electron 和真实 OS 等证据等级，避免用低等级测试声称高等级能力已经完成。
- 在 PR 中执行只读质量门禁；该门禁不会打包发布，也不会读取模型密钥。

该框架不会替代 `docs/research-timeline-ui/` 里已接受的规格、根目录的 OpenAPI / manifest，或实际测试结果。它也不能代替 SYA-UI 父仓库里的产品需求文档。父应用要带上这里的改动时，还需要更新父仓库的 submodule 指针。

### 第一次启动

在仓库根目录准备 Git、uv、Python `>=3.11`、Node `>=22.12.0` 和 npm `>=10.9.0`，然后验证项目级框架：

```bash
scripts/verify-agent-sdlc.sh
```

启动固定版本的 OpenCode：

```bash
npx --yes opencode-ai@1.18.29 .
```

OpenCode 会从 `.agents/skills/` 发现 39 个 Skills，并读取仓库根目录的 `AGENTS.md`。安装或更新 Skills 后需要退出并重新启动 OpenCode。

日常开发不需要重复安装框架。只有安装损坏或经过审核的版本升级才运行：

```bash
scripts/install-agent-sdlc.sh
```

不要手工修改 `_bmad/config.toml`、`_bmad/*/config.yaml` 或 `.agents/skills/`；团队级定制应写入 `_bmad/custom/`。

### 如何让 AI 使用框架

进入 OpenCode 后，最稳妥的入口是：

```text
/bmad-help
```

随后说明目标、需求 ID 或用户请求、验收标准和期望证据。`bmad-help` 会根据任务规模推荐最短的工作流；小型修复不需要机械地执行完整产品流程。

如果使用的 AI 客户端不支持 slash command，可以直接发送：

```text
请先读取 AGENTS.md 和 docs/bmad-project-knowledge/README.md，
然后使用 bmad-help 为当前任务选择最短且充分的 BMAD/TEA 工作流。
```

建议每次给 AI 的任务至少包含以下信息：

```text
使用 /bmad-help 处理以下 FocusFlow 任务。

目标：<用户最终要得到什么>
需求 ID：<例如 SCH-003；没有现有 ID 时写“明确用户请求”>
验收标准：<可观察、可测试的完成条件>
范围：<允许修改的界面、Voyage 层、接口或文档>
证据：<需要静态检查、Python 测试、开发态桌面或打包安装包中的哪一级>
排除项：<本次明确不做的内容>

开始实现前先核对权威来源和现有代码。发现冲突时列出文件与证据，不要自行合并
矛盾结论。完成后运行对应验证，并报告修改文件、结果和未验证边界。不要执行发布。
```

### 常用调用

| 任务 | 调用 |
| --- | --- |
| 不确定下一步或不知道选哪个 Agent | `/bmad-help` |
| 把想法整理为产品方向 | `/bmad-product-brief` |
| 把一个明确改动整理为精简规格 | `/bmad-spec` |
| 创建、更新或检查 PRD | `/bmad-prd` |
| 设计或检查交互体验 | `/bmad-ux` |
| 明确跨模块架构约束 | `/bmad-architecture` |
| 拆分 Epic、Story 和 Sprint | `/bmad-create-epics-and-stories`、`/bmad-sprint-planning` |
| 根据已确认需求实现功能或修复 | `/bmad-build` |
| 设计验收测试或先写失败测试 | `/bmad-testarch-test-design`、`/bmad-testarch-atdd` |
| 扩展自动化测试 | `/bmad-testarch-automate` |
| 审查代码和测试质量 | `/bmad-code-review`、`/bmad-testarch-test-review` |
| 建立需求到测试的追踪和门禁结论 | `/bmad-testarch-trace` |
| 引导人工验收或完成复盘 | `/bmad-walkthrough`、`/bmad-retrospective` |

需要直接与角色讨论时，可以调用 `/bmad-agent-analyst`、`/bmad-agent-pm`、`/bmad-agent-ux-designer`、`/bmad-agent-architect`、`/bmad-agent-dev` 或 `/bmad-tea`。角色会加载 FocusFlow 项目知识并显示自己的可用菜单。

典型的新功能链路是：

```text
product brief/spec -> PRD -> UX -> architecture -> epics/stories
-> test design/ATDD -> build -> automate -> code/test review
-> trace -> walkthrough -> retrospective
```

### 调用示例

实现一个已有需求：

```text
/bmad-build
实现 SCH-003 的时间轴阶段展示。先读取 docs/research-timeline-ui/SPEC.md、
现有静态界面、OpenAPI 和 tests/test_smoke.py，列出验收标准后再修改。
至少运行 npm test；不要把浏览器里的静态检查说成已打包安装验证。
```

处理一个缺陷：

```text
/bmad-spec
问题：勾选叶子清单后，时间轴进度没有跟着变化。请先复现并建立精简规格，
保留现有 PATCH 状态契约，定义回归测试和不在本次处理的范围；规格确认后再交给 bmad-build。
```

审查当前改动：

```text
/bmad-code-review
审查当前未提交改动。重点检查需求与实现是否一致、静态界面是否绕过了同源接口、
OpenAPI 契约、回归风险和缺失测试。先列出有文件和行号的发现，不要执行发布操作。
```

### 框架文件和产物

| 路径 | 用途 |
| --- | --- |
| `AGENTS.md` | 所有 AI 在本仓库必须遵守的工程规则和已知风险 |
| `docs/bmad-project-knowledge/README.md` | FocusFlow 权威来源、架构边界、需求和证据规则 |
| `.agents/skills/` | OpenCode 自动发现的 39 个 BMAD/TEA Skills |
| `_bmad/custom/` | 可提交的 FocusFlow 团队级 Agent 和工作流定制 |
| `_bmad-output/` | 被 Git 忽略的规划、实现和测试工作产物 |
| `agent-sdlc.lock.json` | 工具版本、完整性、Skills 和已接受范围锁 |
| `scripts/verify-agent-sdlc.sh` | 本地完整框架与 OpenCode 验证入口 |
| `.github/workflows/pr-quality.yml` | 无发布副作用的 PR 质量门禁 |

AI 生成的草稿默认放在 `_bmad-output/`。确认后的长期规格应回写 `docs/research-timeline-ui/` 或新的 `docs/` 文档，架构知识应回写 `docs/`，可执行验收应进入 `tests/`。不要把 `_bmad-output/` 当成第二份长期 PRD。

详细的版本、GitHub 门禁和更新规则见 [`docs/agent-sdlc.md`](docs/agent-sdlc.md)。

## 快速开始（给使用者）

用已经打好的 Mac 安装包，不需要安装 Python 或 Node。

1. 打开 `release/` 里的 `FocusFlow-*.dmg`（或从发布页下载同名安装包）
2. 把 **FocusFlow** 拖进「应用程序」
3. 打开 FocusFlow
4. 点「新建规划」，写下长程目标，选择规划粒度，生成研究时间轴
5. 在时间轴里选择阶段，把眼前的清单标成进行中或完成

没有配置模型 Key 时，应用会用内置示例计划，界面和操作流程是完整的。

## 从源码运行（给开发者）

改代码、看最新界面时用这条路径，不要用旧的 `.dmg`。

准备：macOS、[Python 3.11+](https://www.python.org/downloads/)、[Node.js](https://nodejs.org/)。

```bash
git clone https://github.com/Yiounah/SYA-function-scheduler.git
cd SYA-function-scheduler

python3.13 -m venv .venv   # 必须是 3.11+；macOS 自带 python3 通常是 3.9，不能用
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
npm install
npm run app:dev
```

- `npm run app:dev`：开发模式，直接打开当前源码。改完再运行一次即可看到更新。
- `npm run app:dist:mac`：打包模式，生成别人也能安装的 `release/*.dmg`。包里是打包那一刻的代码，之后改源码要重新打包。

要用 GLM-5.3 做真正拆解时：到 [智谱开放平台](https://bigmodel.cn/usercenter/proj-mgmt/apikeys) 创建 Key，填进 `.env` 的 `SYA_OPENAI_API_KEY=`，然后重新运行 `npm run app:dev`。

## 仓库结构

FocusFlow 是独立桌面 App，也是 SYA-UI 的 `function/scheduler` 子模块。规划核心按 Polaris Voyage 分层，但入口仍在仓库根目录，父应用契约不变。

```text
manifest.json / api.openapi.json / module.json / package.json
bin/scheduler-server     # SYA local-http 与桌面 App 共用入口
app/                     # FastAPI（uvicorn app.main:app）
  api/                   # 薄路由
  services/              # 业务编排
  models/                # 任务树与 Voyage
  agents/voyage/         # Navigator 规划 / Helm 记录人的执行 / Sextant 校验再规划
  core/                  # 配置、事件总线、内存状态、唯一 LLM 边界
  static/                # 桌面 UI
desktop/                 # Electron 壳
tests/ docs/
```

