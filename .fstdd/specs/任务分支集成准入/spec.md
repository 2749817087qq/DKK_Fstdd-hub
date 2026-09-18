# Spec: 任务分支集成准入

> Change: 2026-09-18-integration-remote-check | Auto-generated Human View

## Requirements

### Requirement: 集成准入必须包含远端视角

#### Scenario: SC-001

- **GIVEN** 本地 target 在正确的分支上且工作区干净，但已落后上游（别的机器推送过）
- **WHEN** 调用 integrate_task_branch
- **THEN** SHALL 抛 GitIsolationError，消息 SHALL 含 'behind' 与上游引用名

#### Scenario: SC-002

- **GIVEN** 集成因落后上游被拦
- **WHEN** 检查本地 target 分支的 sha
- **THEN** SHALL 与调用前一致 —— 不得产生需要 rebase 才能收拾的分叉

### Requirement: 不得破坏离线与单机使用

#### Scenario: SC-003

- **GIVEN** 仓库没有配置上游（纯本地仓库）
- **WHEN** 调用 integrate_task_branch
- **THEN** SHALL 正常集成，不得因遥测不到远端而拒绝

#### Scenario: SC-004

- **GIVEN** 调用方显式传入 check_remote=False（或 CLI --no-check-remote）
- **WHEN** 本地落后上游
- **THEN** SHALL 放行并完成集成

### Requirement: 远端状态未知时不得假装已知

#### Scenario: SC-005

- **GIVEN** fetch 失败（网络/代理故障、凭据不可用）
- **WHEN** 调用 integrate_task_branch
- **THEN** SHALL 抛 GitIsolationError 说明远端状态未知并提示可绕过，而非静默放行

#### Scenario: SC-006

- **GIVEN** fetch 过程中远端要求交互式凭据
- **WHEN** 调用 integrate_task_branch
- **THEN** SHALL 立即失败而非挂起（GIT_TERMINAL_PROMPT=0）
