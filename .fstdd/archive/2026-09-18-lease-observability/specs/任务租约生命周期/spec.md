# Spec: 任务租约生命周期

> Change: 2026-09-18-lease-observability | Auto-generated Human View

## Requirements

### Requirement: 租约过期不等于原 owner 失效

#### Scenario: SC-001

- **GIVEN** 节点 A 持有任务租约，租约已过期，且任务未被回收、未被接管
- **WHEN** A 调用 heartbeat 或 complete
- **THEN** SHALL 返回 200，租约得以续期 / 任务得以完成

#### Scenario: SC-002

- **GIVEN** 节点 A 持有任务租约且已过期，任务已被回收回 pending 池且无人接管
- **WHEN** A 调用 heartbeat
- **THEN** SHALL 返回 400 且 error_code == lease_reaped，语义为「可以重新 claim 继续干」

### Requirement: 语义相反的失败必须给出不同的错误码

#### Scenario: SC-003

- **GIVEN** 任务已被节点 B 接管
- **WHEN** 原 owner A 调用 heartbeat
- **THEN** SHALL 返回 400 且 error_code == task_taken_over，错误文本 SHALL 含接管者 node_id

#### Scenario: SC-004

- **GIVEN** A 已对本任务 complete 成功
- **WHEN** A 再次调用 heartbeat
- **THEN** SHALL 返回 400 且 error_code == lease_released（而非暗示 A 不是 owner）

#### Scenario: SC-005

- **GIVEN** lease_reaped 与 task_taken_over 两种响应
- **WHEN** 比较两者的 error_code
- **THEN** SHALL 不相同

### Requirement: 接管必须通知原 owner，不能静默

#### Scenario: SC-006

- **GIVEN** A 的租约过期，任务被回收
- **WHEN** B claim 该任务
- **THEN** SHALL 给 A 发一条私信（kind=notice，from=B，to=A，带 task_id）

#### Scenario: SC-007

- **GIVEN** 回收由 GET /tasks 触发（而非由接管者的 claim 触发）
- **WHEN** B 随后 claim 该任务
- **THEN** 仍 SHALL 通知 A —— 上一任 owner 必须来自持久化字段，不能依赖同一次调用的 reap 返回值

### Requirement: 真并发 claim 只能有一个赢家

#### Scenario: SC-008

- **GIVEN** 池中有 1 个 pending 任务
- **WHEN** 8 个线程（两个节点）同时 claim
- **THEN** SHALL 恰有 1 个返回 200，其余全部 204

### Requirement: 老库结构必须能自动迁移

#### Scenario: SC-009

- **GIVEN** 一个缺少 last_owner_node_id 列的既有数据库
- **WHEN** init_db 运行
- **THEN** SHALL 补上该列（CREATE TABLE IF NOT EXISTS 不补列，必须显式迁移）
