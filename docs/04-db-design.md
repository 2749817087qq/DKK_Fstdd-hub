# FSTDD 分布式任务协作 · 数据库设计（对齐现状版）

**日期**：2026-09-17
**场景**：数据库设计核验与对齐（只读，未改动任何代码）
**参与成员**：排障手（investigator，schema 提取与差距分析）
**性质**：本文件记录**当前真实实现的数据库**，以及与原始设计稿的偏差。不是从零开始的设计稿。
**数据来源**：`tools/fstdd_hub.py`（551 行）实测；设计稿 `~/.workbuddy-ai/plans/blazing-aurora-einstein-fvQ3VO0Y.md` §3

---

## 📌 TL;DR

- SQLite 共 **4 张表**（`nodes` / `tasks` / `messages` / `idempotency`）+ 1 个显式索引 + 2 个隐式唯一索引
- **数据模型层面无硬阻塞** —— `tasks` 字段齐备、claim 有 `BEGIN IMMEDIATE` + CAS、租约/幂等/失败三路径闭合，跑第一个真实任务够用
- **真正的缺口在实现层**：设计稿 §3.2/3.3/3.4/3.7 定义的 **git 侧持久化层一行代码都没落地**（`nodes.yaml`、`node.yaml`、`tasks/<id>.yaml`、故障单全部不存在）
- **最危险的一条**：`scope_json` 无结构校验，且 `fstdd_git.py` 的 `scope_conflicts()` **从未被 hub 调用** → 并发写冲突在控制面完全不设防

---

## 1. 真实 Schema

建表位置：`fstdd_hub.py:33-96` 的 `SCHEMA` 常量，`init_db()`（:124-129）用 `executescript` 一次性执行。

### 1.1 表 `nodes`（:36-47）

| 字段 | 类型 | 主键 | NULL | 默认 | 约束 |
|---|---|---|---|---|---|
| node_id | TEXT | ✅ PK | ⚠️ 未声明 NOT NULL | — | 应用层正则 `[A-Za-z0-9][A-Za-z0-9_.-]{0,79}`（:152） |
| machine_name | TEXT | | NOT NULL | — | ≤200 字符（:156） |
| platform | TEXT | | NOT NULL | — | ≤100（:157） |
| os | TEXT | | NOT NULL | — | ≤100（:158） |
| capabilities_json | TEXT | | NOT NULL | — | JSON 数组（:159） |
| ssh_fingerprint | TEXT | | NOT NULL | — | ≤200（:160） |
| status | TEXT | | NOT NULL | `'online'` | **代码中只写入过 `'online'`** |
| last_seen | REAL | | NOT NULL | — | `time.time()` epoch 秒 |
| metadata_json | TEXT | | NOT NULL | `'{}'` | JSON 对象（:161） |
| updated_at | TEXT | | NOT NULL | — | ISO8601 UTC |

### 1.2 表 `tasks`（:48-70）

| 字段 | 类型 | 主键 | NULL | 默认 | 约束 |
|---|---|---|---|---|---|
| task_id | TEXT | ✅ PK | ⚠️ 未声明 | — | `task-<hex16>`（:112, :389） |
| parent_change_id | TEXT | | NOT NULL | — | ≤200（:173） |
| child_change_id | TEXT | | ✅ 允许 | — | 无校验（:174） |
| kind | TEXT | | NOT NULL | — | ∈ change/slice/debug/ops（:168） |
| summary | TEXT | | NOT NULL | — | ≤2000（:176） |
| scope_json | TEXT | | NOT NULL | — | 仅校验「是 dict 或 list」（:135），**不做 allowed/frozen 结构校验** |
| status | TEXT | | NOT NULL | `'pending'` | ∈ pending/claimed/running/done/failed/blocked（:30，仅常量，无 CHECK） |
| owner_node_id | TEXT | | ✅ 允许 | — | FK→nodes(node_id)（:69） |
| lease_token_hash | TEXT | | ✅ 允许 | — | sha256(明文 token)（:109, :426） |
| lease_expires_at | REAL | | ✅ 允许 | — | epoch 秒 |
| attempt | INTEGER | | NOT NULL | `0` | claim 时 +1，**无上限判定** |
| idempotency_key | TEXT | | NOT NULL | — | **UNIQUE**（:60） |
| request_hash | TEXT | | NOT NULL | — | sha256(规范化 payload)（:104） |
| base_git_sha | TEXT | | NOT NULL | — | ≤200（:178） |
| result_git_sha | TEXT | | ✅ 允许 | — | complete 必填（:467） |
| result_ref | TEXT | | ✅ 允许 | — | 无校验 |
| result_json | TEXT | | ✅ 允许 | — | JSON |
| failure_json | TEXT | | ✅ 允许 | — | `{kind,summary,evidence}`（:485） |
| created_at / updated_at | TEXT | | NOT NULL | — | ISO8601 UTC |

### 1.3 表 `messages`（:72-87）

| 字段 | 类型 | 主键 | NULL | 默认 | 约束 |
|---|---|---|---|---|---|
| message_id | TEXT | ✅ PK | ⚠️ 未声明 | — | `msg-<hex16>`（:509） |
| conversation_id | TEXT | | NOT NULL | — | 未传则 `conv-<hex16>` |
| task_id | TEXT | | ✅ 允许 | — | FK→tasks(task_id)（:84） |
| from_node_id | TEXT | | NOT NULL | — | FK→nodes，**服务端校验已注册**（:504） |
| to_node_id | TEXT | | ✅ 允许 | — | FK→nodes；NULL = 广播（:286） |
| kind | TEXT | | NOT NULL | — | ∈ question/blocker/status/notice（:31, :501） |
| body | TEXT | | NOT NULL | — | ≤10000（:511） |
| idempotency_key | TEXT | | NOT NULL | — | **UNIQUE**（:80） |
| request_hash | TEXT | | NOT NULL | — | — |
| created_at | TEXT | | NOT NULL | — | ISO8601 UTC |
| acked_at | TEXT | | ✅ 允许 | — | NULL 未确认 |

### 1.4 表 `idempotency`（:88-95）

| 字段 | 类型 | 主键 | 备注 |
|---|---|---|---|
| idempotency_key | TEXT | ✅ PK | 全局唯一，跨 operation 共享命名空间 |
| operation | TEXT | NOT NULL | task.create / task.claim / task.complete / task.fail / message.create |
| request_hash / response_json | TEXT / TEXT | NOT NULL | 命中且 hash 同 → 回放；hash 异 → 400（:347） |
| response_code | INTEGER | NOT NULL | — |
| created_at | TEXT | NOT NULL | **无 TTL** |

### 1.5 索引

| 名称 | 表 | 字段 | 唯一 |
|---|---|---|---|
| `idx_tasks_pool`（:71） | tasks | (status, lease_expires_at, created_at) | 否 |
| `sqlite_autoindex_tasks_1` | tasks | idempotency_key | 是（UNIQUE 隐式） |
| `sqlite_autoindex_messages_1` | messages | idempotency_key | 是（UNIQUE 隐式） |

> 无 `messages(to_node_id, acked_at)` 索引 → `GET /messages` 全表扫描。

### 1.6 实体关系

```
nodes.node_id ←── tasks.owner_node_id        (FK :69)
nodes.node_id ←── messages.from_node_id      (FK :85)
nodes.node_id ←── messages.to_node_id        (FK :86)
tasks.task_id ←── messages.task_id           (FK :84)
idempotency   ←── 无 FK，纯 KV
```

**⚠️ 外键运行期不强制**：`PRAGMA foreign_keys=ON`（:35）只在 `init_db` 那一条连接上生效；运行时每条请求的 `connect_db()`（:116-121）**只设 `busy_timeout`，从未设 `foreign_keys`** → 运行期外键不生效。（`journal_mode=WAL` 持久化在库文件里，仍生效。）

### 1.7 写入路径 / 事务 / CAS

| 表 | 写入路由 | 事务 | CAS / 乐观锁 |
|---|---|---|---|
| nodes | `POST /nodes/register`（:356，UPSERT）、`POST /nodes/heartbeat`（:373） | ❌ 无 | ❌ |
| tasks | `POST /tasks`（:382）、`POST /tasks/claim`（:401）、`POST /tasks/<id>/heartbeat`（:450）、`.../complete`（:460）、`.../fail`（:475） | **仅 claim 有** `BEGIN IMMEDIATE … COMMIT/ROLLBACK`（:411-434） | claim 有 `WHERE task_id=? AND status='pending'`（:426）；heartbeat/complete/fail 靠 `_lease_update` 比对 `owner_node_id + lease_token_hash + 未过期`（:444-447） |
| messages | `POST /messages`（:494）、`POST /messages/<id>/ack`（:519） | ❌ 无 | ❌ |
| idempotency | 各写路由内 `_save_idempotent`（:351） | 随调用方 | 主键冲突即 409 |

### 1.8 生命周期

- **写入**：register（可重复 UPSERT）、create_task、claim、renew、complete/fail、message、ack
- **更新**：`pending → claimed → running → done|failed|blocked`；租约到期由 `reap_expired`（:204-212）回退到 `pending`
- **删除 / 归档**：**代码中没有任何 `DELETE` 或 `VACUUM`**（全文件 grep 零命中）。`reap_expired` 只改状态不删行；`idempotency` 无 TTL；已 ack 消息永久保留。唯一清理是库外 `hub_backup.sh` 的备份轮转（14 天 `-delete`）

> **结论：无清理机制，四张表均只增不减。**

---

## 2. Git 侧数据 —— 设计稿有，实现全无

| 设计稿路径 | 是否存在 | 代码是否有读/写逻辑 |
|---|---|---|
| `.fstdd/coordination/nodes.yaml` | ❌ 不存在 | ❌ 无。全仓 `grep -rn "coordination"` 仅命中 docstring 措辞 |
| `%USERPROFILE%\.fstdd\node.yaml` / `~/.config/fstdd/node.yaml` | ❌ 不存在 | ❌ 无 |
| `.fstdd/coordination/tasks/<task_id>.yaml` | ❌ 不存在 | ❌ 无 |
| `.fstdd/coordination/faults/FLT-*.md`、`ops/OPLOG-*.md` | ❌ 不存在 | ❌ 无 |

**后果**：设计稿 §3.2/3.3/3.4/3.7 定义的 git 侧持久化层**一行代码都没落地**。

- 节点身份实际由 HTTP 请求体自报（`validate_node` 只读 payload）
- 任务声明实际由 `POST /tasks` 的 JSON body 直接进 SQLite —— `scope_json` 是自由 JSON，**没有** `allowed_globs` / `frozen_globs` / `required_capabilities` / `depends_on` 的结构校验
- `fstdd_git.py:139` 有 `scope_conflicts()` 能读 `scope.allowed/frozen`，但 **hub 从不调用它**，两条链路未接通

---

## 3. 设计稿 §3 vs 实现 · 差距分析

### ✅ 一致

| 项 | 证据 |
|---|---|
| 任务粒度 = 子 change（非 slice） | `tasks` 有 `parent_change_id` + `child_change_id`，与 §3.1 一致 |
| 状态机 `pending→claimed→running→done/failed/blocked` | :30 与 §3.4 完全一致 |
| 租约 + 心跳 + 过期回收三件套 | :425、:456、:204 |
| 幂等键表 + request_hash 比对 | :88-95、:342-349，与 §4.4 语义一致 |
| 消息表 from/to/kind/body + 拉取式 + NULL=广播 | :286、:512 |
| 只存 lease/owner/messages，不碰 `.fstdd.yaml` | 代码中无任何 `.fstdd.yaml` 写入，符合 §3.5 |
| 零第三方依赖（stdlib sqlite3 + WAL） | :20、:34 |

### ⚠️ 不一致

| 项 | 设计稿 | 真实实现 | 证据 |
|---|---|---|---|
| 消息表字段 | `msg_id`/`thread_id`/`from_node`/`to_node`/`refs`/`ack_by` | `message_id`/`conversation_id`/`from_node_id`/`to_node_id`；**无 refs、无 ack_by**，改为单行 `acked_at` | :73-83 |
| 消息 kind | `question\|answer\|blocker\|notify\|handoff` | `question\|blocker\|status\|notice`（新增 status/notice，砍 answer/notify/handoff） | :31 |
| 消息拉取游标 | `?to=<node>&since=<msg_id>` | `?node_id=&task_id=`，用 `acked_at IS NULL` 过滤，**无 since 增量** | :282-291 |
| 幂等表字段 | `idem_key/node_id/endpoint/...`，TTL 24h | `idempotency_key/operation/...`，**无 node_id、无 TTL** | :88-95 |
| **claim 语义** | `POST /api/v1/tasks/{id}/claim`，客户端指定任务，rowcount==0 → 409 | `POST /tasks/claim`，**服务端 `ORDER BY created_at LIMIT 1` 自选最早 pending**，无任务 → **204** | :321、:414-418 |
| 路由前缀 / 幂等键位置 | `/api/v1/*` + `Idem-Key` 请求头 | **无 `/api/v1` 前缀**；幂等键放 **JSON body** | :315-332、:166 |
| 幂等冲突状态码 | 409 | **400** | :347 |
| lease TTL | 默认 900s | **默认 300s**，上限 3600s | :28-29 |
| 租约标识 | `lease_id = uuid4().hex` | `lease_token`（urlsafe 32B），**库里只存 sha256 哈希** | :422、:57 |
| CAS 条件 | 单语句 `WHERE task_id=? AND (state='pending' OR 租约过期)` | 先全局 `reap_expired` 再 `WHERE status='pending'`，**两步而非单语句** | :413、:426 |
| 定时回收 | systemd timer 每 60s 调 `/api/v1/reap` | **无 `/reap` 路由**；仅 `GET /tasks` 与 claim 触发懒回收 | :272、:413 |
| 尝试上限 | 超 `max_attempts`(3) → blocked + 广播 blocker | `attempt` 只 +1，**无上限判定、无自动 blocked、无广播** | :426 |
| 时间表示 | 未区分 | 同一行内混用：`last_seen`/`lease_expires_at` 为 REAL epoch，其余为 ISO TEXT | :44/:58 vs :67-68 |
| 节点字段命名 | `host_label`/`ssh_pubkey_fp`/`concurrency`/`work_root`/`enabled` | `machine_name`/`ssh_fingerprint`；**无 concurrency、work_root、enabled** | :38-46 |
| 节点离线 | `now-last_seen>90s` → offline | **从未写入 'offline'**，`/health` 的 `online_nodes` 恒等于已注册总数 | :376、:260 |

### ❌ 设计有、实现没有

| 项 | 证据 |
|---|---|
| §3.2 `nodes.yaml` 入 git | 文件不存在，代码无读写 |
| §3.3 节点本地 `node.yaml` + `fstdd hub doctor` 断言未入库 | 文件不存在；`tools/` 下**无 `hub doctor` 子命令** |
| §3.4 `tasks/<task_id>.yaml` 及 `required_capabilities`/`allowed_globs`/`frozen_globs`/`depends_on`/`declared_status` | 文件不存在；`scope_json` 仅做「dict or list」校验 |
| §3.5 `hub doctor` 一致性检查（task=done 但 change 未过 Gate3 → WARN） | 无此逻辑 |
| §3.7 故障单 `faults/FLT-*.md` + `ops/OPLOG-*.md` | 无表、无路由 |
| §4.1 `POST /renew`、`POST /start` 独立路由 | 心跳路由一步置 running 并续租（:456），**renew 与 start 合并** |
| §4.1 `POST /release` | **无** —— 领取后不能主动放弃 |
| §4.1 `/health` 返回 `leases_expiring` / tasks 分状态计数 | 只返回 `tasks` 总数 + `online_nodes`（:259-261） |
| §4.2 写路径进程内 `threading.Lock()` | 代码中**无 Lock**，靠 `BEGIN IMMEDIATE` + `busy_timeout` 兜底 |
| §4.5 SSH forced-command 身份注入 | 无；`node_id` 纯自报，命中设计稿自己承认的「降级弱点」 |

### ➕ 实现有、设计没提

| 项 | 证据 |
|---|---|
| `idx_tasks_pool` 复合索引 | :71 |
| `nodes.metadata_json`、`os` 字段 | :40、:45 |
| `tasks.request_hash` 列（幂等表外再存一份） | :61 |
| `tasks.result_ref`、`failure_json.evidence` | :64、:486 |
| `messages.conversation_id` 会话概念（替代 thread_id） | :74 |
| `PRAGMA foreign_keys=ON` + 三处显式 FK | :35、:69、:84-86（运行期未生效） |
| 请求体 1MB 上限、message body ≤10000 | :27、:511 |
| `hub_backup.sh`（WAL checkpoint + `conn.backup` + 14 天轮转） | 文件实测存在 |
| `fstdd_git.py` 的 worktree/branch 隔离、`scope_conflicts`、`--ff-only` 串行合并 | :66-104、:139-158、:172-188 |

---

## 4. 阻塞判定

**数据模型层面：无硬阻塞。** `tasks` 表字段齐备、claim 有 `BEGIN IMMEDIATE` + `status='pending'` CAS、租约/幂等/失败三路径闭合，跑第一个真实任务在 schema 上够用。

**实现层：三类真阻塞。**

| # | 阻塞 | 影响 | 建议 |
|---|---|---|---|
| 1 | `.fstdd/coordination/tasks/<id>.yaml` 与 `node.yaml` 零落地 | 「任务谁写的、能改哪些文件」**无声明载体** | 最小闭环可先不补（用 `scope_json` 自由 JSON + 人工约定），但第 2 个节点前必须补 |
| 2 | `scope_json` 无结构校验 + `scope_conflicts()` 从未被调用 | **并发写冲突在控制面完全不设防** | 单 agent 下不痛；多 agent 前必须接通 |
| 3 | `POST /tasks/claim` 服务端自选最早 pending，**不支持指定 task_id** | 无法按能力/依赖**定向派单** | 若首个闭环需要"指定任务"，这是直接阻塞 |

---

## ⚠️ 待完善 / 已知局限

- 本文件基于**只读代码审查**，未连接生产数据库；`/tasks` 当前为 `[]`、agent 注册数为 0，所有表**无真实业务数据**，字段语义未经真实流量验证。
- 运行期外键未强制这一点，在无真实并发时不会暴露，多 agent 后可能成为脏数据来源。
- 四张表均无清理机制，长期运行会单调增长；`idempotency` 无 TTL 尤其需要关注。

---

## 📚 成员产出索引

- gstack-investigator（排障手）：4 表 schema 全字段提取 + 索引/关系/事务/CAS 分析 + git 侧三份 YAML 存在性核验 + 设计稿 §3 四项差距分类（✅/⚠️/❌/➕）

---

> 本报告由软件工坊 AI 协作生成，关键决策请由工程负责人复核。
