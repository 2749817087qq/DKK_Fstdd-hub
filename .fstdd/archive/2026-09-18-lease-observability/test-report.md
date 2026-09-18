# 验证报告 — 2026-09-18-lease-observability

**变更**：租约过期后原 owner 被静默没收，且失败原因不可区分
**日期**：2026-09-18
**基线**：`b77ca1d`
**结论**：通过（4 项语义全部在真实生产数据副本上验证，生产库迁移成功且数据零改动）

---

## 1. 发现路径

主动排查多机多 agent 的并发与租约生命周期（第一轮闭环未覆盖的场景）。
先写探针实测再决定修什么 —— 不凭代码推断下结论。

探针结果（本地中枢实例，2 节点）：

| 场景 | 结果 |
|---|---|
| 16 线程同时 claim 同一任务 | ✅ 只有 1 个拿到 —— 真并发安全 |
| 租约过期后原 owner 续租 | ❌ `400 invalid task owner or lease token` |
| 租约过期后原 owner 完成 | ❌ `400 invalid task owner or lease token` |
| 被 B 接管后 A 续租 | ❌ 与"未被接管"**返回同一句话** |

附带发现：`GET /tasks` 也会触发 `reap_expired` —— 任何节点列一次表就没收所有过期租约，
因此**触发回收的人通常不是接管的人**。

---

## 2. 修复内容

| # | 修复 | 位置 |
|---|---|---|
| 1 | 新增 `LeaseError`，错误响应带机器可读 `error_code` | `fstdd_hub.py` |
| 2 | 四种失败语义可区分：`lease_reaped` / `task_taken_over` / `lease_released` / `invalid_lease` | `_lease_update` |
| 3 | 原 owner 在未被回收、未被接管时**可以续租与完成** | `_lease_update` |
| 4 | `last_owner_node_id` 持久化上一任 owner；接管时主动私信通知原 owner | `_claim` / `reap_expired` |
| 5 | `init_db` 增加轻量迁移 `_ensure_columns` | `init_db` |
| 6 | `claim` 的 UPDATE 补 rowcount 校验（defense in depth） | `_claim` |
| 7 | `hub_client` 文档同步：租约语义 + error_code 分支处理表 | `hub_client.py` |

**设计说明**：「租约过期」的语义是**别的节点可以来抢**，不是**原 owner 不能续**。
只要 owner 仍是自己，就说明期间无人接手，续租是安全的。

---

## 3. 测试

**50 → 51 passed**（新增 6 条，既有 45 条无回归）

| 测试 | 守卫内容 |
|---|---|
| `test_true_concurrent_claim_grants_task_to_exactly_one_node` | 8 线程真并发，恰 1 个赢家 |
| `test_owner_can_renew_after_lease_expiry_if_nobody_took_over` | 过期未接管 → 续租 200 |
| `test_lease_failure_reasons_are_distinguishable` | `lease_reaped` ≠ `task_taken_over` |
| `test_takeover_notifies_the_previous_owner` | 接管私信可达 |
| `test_completed_task_reports_lease_released_not_invalid_owner` | 已完成 → `lease_released` |
| `test_init_db_migrates_legacy_schema_without_last_owner` | 老库自动补列 |

既有 `test_claim_is_atomic_and_returns_lease` 名为 atomic，实际是**串行**断言
（第二次 204 只是因为池子空了），从没真正并发过 —— 新测试补上这个洞。

---

## 4. 变异测试（6 个变异体，4 杀 2 存活）

| 变异体 | 结果 | 性质判定 |
|---|---|---|
| M2 恢复「过期一律拒绝」旧行为 | ✅ 杀死 | — |
| M3 两种失败用同一错误码 | ✅ 杀死 | — |
| M4 去掉 `BEGIN IMMEDIATE` | ✅ 杀死 | — |
| M6 去掉 `_ensure_columns` 迁移 | ✅ 杀死（补测试后） | 补了迁移测试才转红 |
| M1 reap 不保留上一任 owner | ❌ 存活 | **冗余路径**：`claim` 每次都写同一字段，新流程看不出差别。但**保留它对老库迁移有意义**（迁移前就已 claimed 的旧行，只有 reap 能补上） |
| M5 去掉 claim 的 rowcount 守卫 | ❌ 存活 | **天然不可观测**：`BEGIN IMMEDIATE` 已挡住，与上次 `PRAGMA foreign_keys` 同类。**不得因变异报告删除** |

> 存活 ≠ 冗余。M5 是第二层防线，行为上不可观测；M1 是覆盖迁移场景的冗余写入。
> 两者都不是「测试没测到」。

---

## 5. 真实环境验证

**生产库**（部署后）：

- 迁移前确认**无** `last_owner_node_id` 列 → 迁移是刚需，不是防御性代码
- 部署后：**已补列 ✅**，数据零改动（tasks=2, nodes=2, messages=9）
- `/health` 正常，服务 `active`

**行为验证**：在**迁移前的真实生产数据副本**上进行（不在生产上跑破坏性场景），
使用真实节点 `fstdd005-win-dev` / `fstdd-hub-infra`：

| 场景 | 结果 |
|---|---|
| 迁移：补列 + 数据保真 | ✅ 列已补，tasks/nodes/messages 计数不变 |
| 过期未接管 → 续租 | ✅ `200 running`（旧实现此处是 400） |
| 已回收 → 续租 | ✅ `400 lease_reaped` |
| 被接管 → 续租 | ✅ `400 task_taken_over`，文本含接管者 node_id |
| 接管通知 | ✅ A 收到来自 B 的私信，含 task_id 与 attempt |

---

## 6. 遗留与风险

- **M5 守卫不可观测**：依赖 `BEGIN IMMEDIATE` 兜底，若将来改成共享连接或去掉
  事务，该守卫是最后一道防线，删了就没人拦得住。已在代码注释中标注。
- **接管通知的触发面**：只在 `claim` 时发。若任务被回收后长期无人认领，原 owner
  不会收到任何提示（它会在下一次心跳时才知道）。可接受：无人接管时也不必停手。
- **回滚版本**：`/home/ubuntu/fstdd-hub-server/fstdd_hub.py.pre-lease-observability`
- **数据库备份**：`/home/ubuntu/fstdd-hub/backups/fstdd-hub-20260918T013224Z.sqlite3`（迁移前）
