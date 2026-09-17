# Spec: 独立项目骨架与可独立运行的测试基线

> Change: 2026-09-17-project-bootstrap | Auto-generated Human View

## Requirements

### Requirement: 新建独立项目并以 FSTDD 初始化，使其拥有自洽的流程骨架

#### Scenario: SC-001

- **GIVEN** D:/tools/FSTDD/ 下尚无协作程序的独立项目
- **WHEN** 在项目根执行 fstdd init
- **THEN** 系统 SHALL 生成 .fstdd/ 骨架（含 config.d、skills、templates、canonical、changes、archive、specs）以及 AGENTS.md 与 FSTDD_CONSTITUTION.md，并安装 PreToolUse Guard 钩子

### Requirement: 控制面与节点本地代码迁移至新项目并保持可执行属性

#### Scenario: SC-002

- **GIVEN** stdd-repo/tools/ 下存在五个协作程序文件
- **WHEN** 将其复制到新项目 tools/ 目录
- **THEN** 新项目 SHALL 包含 fstdd_hub.py、fstdd_git.py、deploy_hub.sh、hub_backup.sh、hub_healthcheck.py，且三个 .sh 文件保留可执行位

### Requirement: 测试迁移后须能在新项目内独立运行，不依赖原仓库目录层级

#### Scenario: SC-003

- **GIVEN** 测试的仓库根定位原写死为 parents[2] 以适配 stdd-repo/upstream/tests/ 层级
- **WHEN** 测试位于新项目的 tests/ 目录下
- **THEN** 定位 SHALL 调整为 parents[1]，且不改动任何断言与业务逻辑；在项目根执行 pytest tests/ 输出 14 passed、0 failed

### Requirement: 文档体系随代码一并迁移，保留设计溯源

#### Scenario: SC-004

- **GIVEN** 接入文档、原始设计方案及本轮产出的核查、PRD、数据库设计分散存放
- **WHEN** 执行迁移
- **THEN** 新项目 docs/ SHALL 包含 DISTRIBUTED_ACCESS.md 与编号化的四份文档，并将两个历史归档 change 存入 docs/history/ 作为溯源

### Requirement: 迁移不得破坏原仓库，原文件保持可用

#### Scenario: SC-005

- **GIVEN** stdd-repo 侧仍在进行其他变更
- **WHEN** 以复制方式迁移代码与文档
- **THEN** 原仓库 SHALL 不因本次迁移删除或改名任何文件；是否移除原文件由 D哥 另行决定
