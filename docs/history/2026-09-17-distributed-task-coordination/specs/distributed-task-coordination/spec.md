# Spec: distributed-task-coordination

> Change: 2026-09-17-distributed-task-coordination | Auto-generated Human View

## Requirements

### Requirement: 分布式任务必须以服务器 Git 裸库作为唯一数据面真值源，控制面不得取代 Change 事实

#### Scenario: SC-001

- **GIVEN** 服务器裸库可通过 git-over-SSH 访问
- **WHEN** 节点创建或更新一个分布式任务
- **THEN** 任务 SHALL 关联 parent_change_id、base_git_sha 和可审计的 task 分支
- **AND** 控制面 SHALL NOT 直接修改 Change 的 current_phase/status
- **AND** Git commit/ref SHALL 是产出的权威记录

#### Scenario: SC-002

- **GIVEN** 控制面 SQLite 与 Git 数据均可读取
- **WHEN** 控制面数据库被删除后重建
- **THEN** 系统 SHALL 能从 Git 分支、Change .fstdd.yaml 和产物重建任务索引

### Requirement: 任务必须以子 Change 为跨机分派边界，并拥有单一 owner 与隔离工作区

#### Scenario: SC-003

- **GIVEN** 一个父 Change 被拆成多个可独立执行的工作项
- **WHEN** 系统生成任务
- **THEN** 每个跨机任务 SHALL 关联一个 child_change_id 或明确的子 Change 目录
- **AND** 同一任务 SHALL 同时只有一个有效 owner
- **AND** 任务 worktree SHALL 位于任何 Git checkout 之外

#### Scenario: SC-004

- **GIVEN** 两个任务声明了 scope
- **WHEN** 系统准备允许并行执行
- **THEN** 只有 scope 不重叠、接口不需要串行交接且 worktree 隔离时 SHALL 允许并行
- **AND** scope 重叠 SHALL 强制串行

### Requirement: 控制面必须提供可恢复的任务状态、节点注册、心跳与租约

#### Scenario: SC-005

- **GIVEN** 节点携带唯一 node_id、能力标签和 SSH 公钥指纹
- **WHEN** 节点调用注册接口
- **THEN** 服务 SHALL 幂等创建或更新节点，并记录 last_seen/status

#### Scenario: SC-006

- **GIVEN** 任务处于 claimed/running 且 lease 已过期
- **WHEN** 其他节点查询任务池
- **THEN** 服务 SHALL 将该任务回收为 pending 或标记为需人工处理
- **AND** 旧 owner 使用旧 lease token 回传 SHALL 被拒绝

#### Scenario: SC-007

- **GIVEN** 两个节点同时领取可用任务
- **WHEN** 两个 claim 请求并发到达
- **THEN** 同一任务 SHALL 只被一个节点领取
- **AND** 另一请求 SHALL 得到无任务或冲突响应
- **AND** 数据库 SHALL 保留一次 claim 的 owner/lease/attempt

### Requirement: 所有控制面写操作必须具备幂等键，能够抵抗网络重试和 SSH 双执行

#### Scenario: SC-008

- **GIVEN** 同一 idempotency_key 的创建或回传请求重复提交
- **WHEN** 服务处理第二次请求
- **THEN** 服务 SHALL 返回首次处理结果且 SHALL NOT 创建重复任务、消息或完成记录

#### Scenario: SC-009

- **GIVEN** 两个不同 payload 使用相同幂等键
- **WHEN** 第二个请求到达
- **THEN** 服务 SHALL 返回冲突并保留首次 payload

### Requirement: 8788 必须是内部控制面，与 8787 公网经验回传端点隔离

#### Scenario: SC-010

- **GIVEN** 服务部署在同一台服务器
- **WHEN** 检查监听地址、systemd unit 和数据目录
- **THEN** 8788 SHALL 只监听 127.0.0.1，使用独立 unit、端口和 SQLite 数据目录
- **AND** 8787 的现有路由和数据目录 SHALL 不被改造或复用为任务总线

#### Scenario: SC-011

- **GIVEN** 节点没有新的公网 token
- **WHEN** 节点访问 8788
- **THEN** 节点 SHALL 通过 SSH 命令或 SSH 端口转发访问控制面
- **AND** 私钥 SHALL NOT 写入仓库或任务载荷

### Requirement: FSTDD phase、Gate 与分布式协调状态必须隔离且可审计

#### Scenario: SC-012

- **GIVEN** 任务状态变为 done
- **WHEN** 节点回传完成
- **THEN** 系统 SHALL NOT 自动确认 Gate、推进 phase 或归档 Change
- **AND** 只有受控 FSTDD CLI 和 D哥明确确认才能完成 Gate

#### Scenario: SC-013

- **GIVEN** Gate 已存在 confirmed_at 且原 confirmed_actor 为 ai
- **WHEN** 用户要对历史 Gate 作追认
- **THEN** `gate amend-audit` SHALL 追加 audit_amendments[]，不得覆盖原始审计字段
- **AND** 命令 SHALL 要求非空 evidence、确认通道和 amended_actor=user
- **AND** 相同幂等键 SHALL 幂等；不同证据 SHALL 报冲突

### Requirement: 任务间消息必须支持提问、阻塞上报、状态通知与确认回执

#### Scenario: SC-014

- **GIVEN** 节点需要请求上下文或报告阻塞
- **WHEN** 节点提交带 task_id 的消息
- **THEN** 消息 SHALL 记录 message_id、conversation_id、from_node_id、kind、body 和 created_at
- **AND** 消息 SHALL 可按 node_id/task_id 拉取
- **AND** 重复投递 SHALL 由幂等键去重

#### Scenario: SC-015

- **GIVEN** 接收节点已读取一条消息
- **WHEN** 节点提交 ack
- **THEN** 服务 SHALL 记录 ack 时间且不得再次返回已确认消息

### Requirement: 所有并行产出必须经过串行集成和可验证的失败处理

#### Scenario: SC-016

- **GIVEN** 多个任务分支均已回传 result_git_sha
- **WHEN** 集成者准备更新 master
- **THEN** 集成者 SHALL 逐任务审 diff、运行验证并串行合并
- **AND** 冲突 SHALL 将任务置为 blocked，不得强推 master
- **AND** GitHub 镜像失败 SHALL NOT 阻塞服务器裸库内部流转
