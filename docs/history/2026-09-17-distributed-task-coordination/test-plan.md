# v3.0 测试方案与详细案例

> 版本：3.0
> 创建日期：2026-09-17
> 对应 Phase 2 Spec：`canonical/specs/code/2026-09-17-distributed-task-coordination.yaml`、`canonical/specs/agent/2026-09-17-distributed-task-coordination.yaml`

## 一、测试策略

### 1.1 测试金字塔

1. **单元测试**：状态机、幂等键、租约过期、scope 判定、Gate 审计追加。
2. **SQLite 集成测试**：并发 claim、唯一约束、消息 ack、节点心跳和重启恢复。
3. **协议测试**：8788 HTTP 路由、状态码、请求/响应 schema、重复请求行为。
4. **Git 集成测试**：裸库、task 分支、外部 worktree、ff-only 集成和冲突阻断。
5. **运维验收**：systemd 幂等部署、127.0.0.1 监听、8787 隔离、健康检查、磁盘阈值。
6. **变异验证**：至少注入“释放 lease 时跳过 owner 校验”“幂等键唯一约束失效”“Gate 审计覆盖原字段”“8788 监听 0.0.0.0”四类缺陷，确认目标测试能捕获。

### 1.2 测试原则

- 先写测试，再实现；每个 Slice 单独 red-green。
- 测试断言行为和不变量，不绑定易变日期、日志措辞或机器路径。
- 并发测试必须验证最终数据库状态，而不仅是 HTTP 回显。
- Gate 测试必须区分“用户确认”“AI 调用 CLI”“历史用户追认”。
- Git 测试必须验证 commit/ref/merge，不用单文件复制模拟同步。

### 1.3 已有测试资产

| 测试文件 | 用例数 | 类型 | 覆盖范围 |
|---|---:|---|---|
| `upstream/tests/test_inbox_endpoint.py` | 44 | 端点集成 | 8787 经验端点限流、批量、响应 |
| `upstream/tests/test_inbox_pull.py` | 23 | 客户端集成 | 经验池拉取、脱敏、发布 |
| `upstream/tests/test_migrate_to_d_drive.py` | 19 | 迁移验收 | D 盘路径、树对象、校验、非误伤 |
| `upstream/tests/commands/test_gate.py` | 现有 | CLI 单元 | Gate 通道、顺序、actor 基础行为 |
| `upstream/tests/commands/test_phase.py` | 现有 | CLI 单元 | 四阶段、Gate 前置、build→deliver 证据 |
| `upstream/tests/commands/test_archive.py` | 现有 | CLI 单元 | 归档、dry-run、spec 合并 |

## 二、详细测试案例

### 功能 1：任务状态与原子领取

#### 案例 1.1 — 并发领取只产生一个 owner

| 字段 | 内容 |
|---|---|
| **ID** | TC-DTC-001 |
| **对应 Spec** | distributed-task-coordination → SC-007 |
| **优先级** | P0 |
| **预置条件** | SQLite 有一个 pending 任务，两个节点均在线 |
| **输入** | 两个线程同时 POST `/tasks/claim` |
| **预期结果** | 恰有一个 200；另一个得到 204/409；任务只有一个 owner 和一个有效 lease |
| **当前状态** | ❌ 待 BUILD 实现 |

#### 案例 1.2 — lease 过期后回收

| 字段 | 内容 |
|---|---|
| **ID** | TC-DTC-002 |
| **对应 Spec** | distributed-task-coordination → SC-006 |
| **优先级** | P0 |
| **预置条件** | running 任务的 lease_expires_at 已过期 |
| **输入** | 新节点查询并领取；旧节点携旧 token complete |
| **预期结果** | 新节点可领取；旧 token 被拒绝；attempt 递增 |
| **当前状态** | ❌ 待 BUILD 实现 |

### 功能 2：幂等与消息

#### 案例 2.1 — 重复写请求不产生重复对象

| 字段 | 内容 |
|---|---|
| **ID** | TC-DTC-003 |
| **对应 Spec** | distributed-task-coordination → SC-008/SC-009 |
| **优先级** | P0 |
| **预置条件** | 空任务池或空消息表 |
| **输入** | 相同 idempotency_key 重复提交相同 payload，再提交不同 payload |
| **预期结果** | 相同 payload 返回首次结果；不同 payload 返回冲突；数据库只保留一条 |
| **当前状态** | ❌ 待 BUILD 实现 |

#### 案例 2.2 — 消息 ack 语义

| 字段 | 内容 |
|---|---|
| **ID** | TC-DTC-004 |
| **对应 Spec** | distributed-task-coordination → SC-014/SC-015 |
| **优先级** | P1 |
| **预置条件** | 有一条未确认的 blocker 消息 |
| **输入** | GET 消息、POST ack、再次 GET |
| **预期结果** | 首次 GET 返回消息；ack 成功；再次 GET 不返回已确认消息 |
| **当前状态** | ❌ 待 BUILD 实现 |

### 功能 3：Gate 审计修正

#### 案例 3.1 — 用户追认追加且不覆盖原始字段

| 字段 | 内容 |
|---|---|
| **ID** | TC-DTC-005 |
| **对应 Spec** | distributed-task-coordination → SC-013 |
| **优先级** | P0 |
| **预置条件** | Gate 已确认，原 confirmed_actor=ai |
| **输入** | `gate amend-audit <change> --gate 1 --confirmed-by dialog --evidence "..."` |
| **预期结果** | `audit_amendments[]` 新增一项；原 confirmed_at/by/actor/evidence 完全不变 |
| **当前状态** | ❌ 待 BUILD 实现 |

#### 案例 3.2 — 审计修正拒绝无证据与冲突重复

| 字段 | 内容 |
|---|---|
| **ID** | TC-DTC-006 |
| **对应 Spec** | distributed-task-coordination → SC-013 |
| **优先级** | P0 |
| **预置条件** | Gate 已确认；已存在一个 amendment |
| **输入** | 空 evidence；无原 Gate；相同 key；相同 Gate 的不同 evidence |
| **预期结果** | 空 evidence/无 Gate 拒绝；相同 key 幂等成功；不同 evidence 冲突失败 |
| **当前状态** | ❌ 待 BUILD 实现 |

### 功能 4：Git 隔离与串行集成

#### 案例 4.1 — 外部 worktree 与任务分支

| 字段 | 内容 |
|---|---|
| **ID** | TC-DTC-007 |
| **对应 Spec** | distributed-task-coordination → SC-003 |
| **优先级** | P0 |
| **预置条件** | 服务器裸库可访问，任务有 base_git_sha |
| **输入** | 创建两个 task 分支和两个外部 worktree |
| **预期结果** | worktree 不嵌套任何 checkout；两个任务可独立提交；分支均可追溯到 base SHA |
| **当前状态** | ❌ 待 BUILD 实现 |

#### 案例 4.2 — master 集成保持串行

| 字段 | 内容 |
|---|---|
| **ID** | TC-DTC-008 |
| **对应 Spec** | distributed-task-coordination → SC-016 |
| **优先级** | P0 |
| **预置条件** | 两个任务分支均有产出，其中一个与 master 冲突 |
| **输入** | 集成者逐一审 diff 并合并 |
| **预期结果** | 无冲突任务可 ff/merge；冲突任务进入 blocked；不得强推 master；结果 SHA 回写控制面 |
| **当前状态** | ❌ 待 BUILD 实现 |

### 功能 5：部署隔离与恢复

#### 案例 5.1 — 8788 只监听本地且不改 8787

| 字段 | 内容 |
|---|---|
| **ID** | TC-DTC-009 |
| **对应 Spec** | distributed-task-coordination → SC-010 |
| **优先级** | P0 |
| **预置条件** | 8787 已由 `fstdd-inbox.service` 使用 |
| **输入** | 幂等部署 8788 两次，检查 `ss -lntp`、systemd、health |
| **预期结果** | 8788 仅绑定 127.0.0.1；独立 unit/data；8787 路由和数据不变；重复部署无残留进程 |
| **当前状态** | ❌ 待 BUILD 实现 |

#### 案例 5.2 — 控制面重建不改变 Git 事实

| 字段 | 内容 |
|---|---|
| **ID** | TC-DTC-010 |
| **对应 Spec** | distributed-task-coordination → SC-001/SC-002 |
| **优先级** | P1 |
| **预置条件** | Git 有任务分支、Change 产物和 test-report；SQLite 可删除 |
| **输入** | 删除/重建控制面数据库并执行重建脚本 |
| **预期结果** | 任务索引恢复；Git SHA、Change phase/status、Gate 原始审计不变 |
| **当前状态** | ❌ 待 BUILD 实现 |

#### 案例 5.3 — 节点注册幂等更新在线状态

| 字段 | 内容 |
|---|---|
| **ID** | TC-DTC-011 |
| **对应 Spec** | distributed-task-coordination → SC-005 |
| **优先级** | P0 |
| **预置条件** | 节点 `FSTDD005` 尚未注册或已有旧 last_seen |
| **输入** | 重复 POST `/nodes/register`，携相同 node_id 和公钥指纹 |
| **预期结果** | 只保留一个节点记录；能力标签、status、last_seen 按最新请求幂等更新 |
| **当前状态** | ❌ 待 BUILD 实现 |

#### 案例 5.4 — 节点心跳延长任务租约

| 字段 | 内容 |
|---|---|
| **ID** | TC-DTC-012 |
| **对应 Spec** | distributed-task-coordination → SC-005/SC-006 |
| **优先级** | P0 |
| **预置条件** | 节点拥有有效 lease token 的 running 任务 |
| **输入** | POST `/nodes/heartbeat` 与 `/tasks/{id}/heartbeat` |
| **预期结果** | last_seen 更新；租约延长；错误 node_id 或 token 被拒绝 |
| **当前状态** | ❌ 待 BUILD 实现 |

#### 案例 5.5 — 任务完成回传不推进 FSTDD phase

| 字段 | 内容 |
|---|---|
| **ID** | TC-DTC-013 |
| **对应 Spec** | distributed-task-coordination → SC-012 |
| **优先级** | P0 |
| **预置条件** | 任务处于 running，Change 仍在 build 或其他未完成 phase |
| **输入** | POST `/tasks/{id}/complete`，提交 result_git_sha 和报告引用 |
| **预期结果** | 控制面任务变为 done；Change `.fstdd.yaml` 的 current_phase/status/Gate 字段完全不自动变化 |
| **当前状态** | ❌ 待 BUILD 实现 |

#### 案例 5.6 — 消息按 node/task 过滤

| 字段 | 内容 |
|---|---|
| **ID** | TC-DTC-014 |
| **对应 Spec** | distributed-task-coordination → SC-014 |
| **优先级** | P1 |
| **预置条件** | 两个 node_id 和两个 task_id 各有消息 |
| **输入** | GET `/messages?node_id=FSTDD005&task_id=<id>` |
| **预期结果** | 只返回目标节点/任务可见的未确认消息，不泄漏其他任务载荷 |
| **当前状态** | ❌ 待 BUILD 实现 |

#### 案例 5.7 — 任务失败与阻塞证据可追踪

| 字段 | 内容 |
|---|---|
| **ID** | TC-DTC-015 |
| **对应 Spec** | distributed-task-coordination → SC-006/SC-016 |
| **优先级** | P1 |
| **预置条件** | running 任务遇到 Git 冲突或环境故障 |
| **输入** | POST `/tasks/{id}/fail`，提交 kind、错误摘要、证据引用 |
| **预期结果** | 任务变为 failed 或 blocked；保留 result/证据引用；不得释放为可重复领取而丢失失败原因 |
| **当前状态** | ❌ 待 BUILD 实现 |

#### 案例 5.8 — GitHub 镜像失败不阻塞内部流转

| 字段 | 内容 |
|---|---|
| **ID** | TC-DTC-016 |
| **对应 Spec** | distributed-task-coordination → SC-016 |
| **优先级** | P1 |
| **预置条件** | 服务器裸库可用，GitHub 暂时不可达 |
| **输入** | 完成本地 task 分支合并并触发镜像同步 |
| **预期结果** | 内部 master/任务状态成功；镜像失败进入重试/告警记录，不回滚已验证的内部产出 |
| **当前状态** | ❌ 待 BUILD 实现 |

## 三、测试执行矩阵

| 功能模块 | 单元测试 | 集成测试 | E2E | 状态 |
|---|---|---|---|---|
| 原子领取/租约 | 待补 | 待补 | 待补 | 🔴 |
| 幂等/消息 | 待补 | 待补 | 待补 | 🔴 |
| Gate 审计修正 | 待补 | N/A | 待补 | 🔴 |
| Git worktree/集成 | 待补 | 待补 | 待补 | 🔴 |
| 8788 部署/恢复 | 待补 | 待补 | 待补 | 🔴 |
| 8787 经验端点回归 | 67 passed | 已有 | N/A | 🟢 |

## 四、回归风险矩阵

| 风险区域 | V3.0 改动 | 已有回归保护 | 风险等级 |
|---|---|---|---|
| `.fstdd.yaml` 审计链 | 新增追加字段，不覆盖旧字段 | test_gate + 新增 amendment 测试 | 高 |
| 8787 经验端点 | 明确不修改 | 23 + 44 个相关测试 | 低 |
| Git 集成与路径 | 引入服务器裸库和外部 worktree | Git 集成测试 + diff 审查 | 高 |
| 节点/租约状态 | 新增 SQLite 控制面 | 并发、重启、过期测试 | 高 |
| 凭证安全 | 新增 SSH 访问配置 | 脱敏、仓库扫描、配置不入库 | 高 |

## 五、建议补充顺序

1. **第一优先（部署前必补）**：TC-DTC-001/002/003/005/006/007/008/009。
2. **第二优先（最小闭环后补）**：TC-DTC-004/010。
3. **第三优先（运维增强）**：控制面指标、冷备恢复演练和长期容量报告。
