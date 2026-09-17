---
name: fstdd-upgrade
description: "Fstdd 技能层升级 — 用本仓库的静态资源刷新项目 .fstdd/ 快照与全局技能，不依赖外部网络"
stdd_version: "3.0.5-fin.1"
---
# Fstdd Upgrade — 技能层版本同步

## 阶段目标

把项目的 `.fstdd/` 静态资源与已安装技能刷新到**本仓库当前版本**，解决版本漂移问题。

> **本项目已独立于上游发展。** 本 skill **不再从上游仓库拉取任何内容** ——
> 资源来源是本仓库自带的 `upstream/.fstdd/`。若需要跟随上游，那是另一套流程，
> 不属于 Fstdd 的升级语义。

## 前置条件

- 项目已初始化 Fstdd（存在 `.fstdd/` 目录）
- 本仓库可用（`upstream/.fstdd/` 下为权威静态资源）
- **不需要**网络访问

## 执行流程

### Step 1: 版本检查

1. 读取项目 `.fstdd/version.yaml`
2. 显示项目版本与本仓库版本
3. 项目版本 >= 仓库版本：提示"已是最新"，询问是否强制刷新
4. 项目版本 < 仓库版本：确认升级

### Step 2: 平台检测

检查以下目录/文件的存在性，确定当前平台：

| 平台 | 检测标志 |
|------|---------|
| Claude Code | `.claude/skills/` 目录存在 |
| OpenCode | `.opencode/skills/` 目录存在 |
| Cursor | `.cursor/rules/fstdd.md` 文件存在 |
| WorkBuddy | `~/.workbuddy-ai/skills/` 目录存在 |
| Trae | `.trae/skills/` 目录存在 |

提示用户检测到的平台列表。

### Step 3: 备份当前版本

1. 创建备份目录：`.fstdd/backup/<old_version>-<timestamp>/`
2. 复制当前 `.fstdd/skills/`、`.fstdd/templates/`、`.fstdd/config.d/`、`.fstdd/version.yaml` 到备份目录

### Step 4: 刷新静态资源

**从本仓库复制**（源：`<仓库>/upstream/.fstdd/`）到项目 `.fstdd/`：

**技能文件**（源 `.fstdd/skills/`）：
- `understand.md`、`spec.md`、`build.md`、`deliver.md`（slice/verify 已并入 build）
- `_shared/confirm-gate.md`、`_shared/version-check.md`、`_shared/mode-selection.md`、`_shared/long-range-auth.md`
- `upgrade.md`

**配置文件**（源 `.fstdd/config.d/`）：
- `gates.yaml`、`quality.yaml`、`long_range.yaml`、`lite.yaml`、`experience.yaml`
- `project.yaml`：**特殊处理** — 覆盖时保留 `project` 和 `paths` 字段的原有值

**模板文件**（源 `.fstdd/templates/` 与 `.fstdd/templates/canonical/`）

> 复制而非下载：资源随本仓库分发，升级不依赖网络，也不受上游变更影响。

### Step 5: 更新版本标记

更新 `.fstdd/version.yaml`：
```yaml
stdd_version: "<new_version>"
upgraded_at: "<current_iso_timestamp>"
```

### Step 6: 重装平台技能

对 Step 2 检测到的每个平台，重新生成技能文件：
1. 读取 `.fstdd/skills/` 下最新的技能文件
2. 为每个技能生成对应平台的 SKILL.md（含 name / description / stdd_version 的 YAML frontmatter）
3. 写入目标平台目录

**WorkBuddy 重装**（本项目主平台）：
- 执行 `python tools/install_workbuddy_skills.py`（在仓库内），
  它会生成 6 个 `fstdd-*` skill 到 `~/.workbuddy-ai/skills/` 并自动校验
- 若只需刷新，可先归档旧 skill 再重装

**Claude Code / OpenCode 重装**：
- `.claude/skills/fstdd-<phase>/SKILL.md` 或 `.opencode/skills/fstdd-<phase>/SKILL.md`
- 每个文件 = YAML frontmatter + `.fstdd/skills/<phase>.md` 内容

**Cursor 重装**：
- 重装 `.cursor/rules/fstdd.md`（若存在适配器 `.fstdd/platforms/cursor/`）

### Step 7: 输出升级摘要

```
✅ Fstdd 升级完成
  项目版本: <old_version> → <new_version>
  刷新文件: <N> 个
  重装平台: <platform_list>
  备份位置: .fstdd/backup/<old_version>-<timestamp>/
  资源来源: 本仓库 upstream/.fstdd/（非上游）
```

## 错误处理

| 场景 | 处理 |
|------|------|
| 本仓库不可用 | 提示先获取仓库；**不要**退化为从上游下载 |
| `.fstdd/` 目录不存在 | 提示"当前项目未初始化 Fstdd，请先运行 fstdd init" |
| 项目已锁定 | 提示"项目已锁定在版本 X.X.X，使用 fstdd upgrade --unlock 解锁后再升级" |

## 产出物

- 更新后的 `.fstdd/skills/`、`.fstdd/templates/`、`.fstdd/config.d/`
- `.fstdd/version.yaml`（版本号和时间戳更新）
- `.fstdd/backup/<old_version>-<timestamp>/`（升级前备份）
- 重装后的平台技能文件

## 质量检查

完成前确认：
- [ ] 所有刷新文件成功写入
- [ ] `.fstdd/version.yaml` 版本号正确更新
- [ ] 备份目录包含升级前的文件快照
- [ ] 平台技能文件 frontmatter 包含新版本号
- [ ] **全程未访问上游仓库**（独立发展的硬要求）
