# FSTDD 分布式任务规划与执行 - 技术设计

## Context

FSTDD 需要支持 6 个 AI agent 分布在 3 台开发机上，协作完成自身的开发、调试与运维。当前系统已有四阶段 Change、3 个 Gate、机器可读的 `.fstdd.yaml`、`batch`/子 change、切片证据链、经验回传端点和 Git 推送脚本，但不存在任务队列、owner、租约、节点心跳、消息读取或原子领取能力。

本设计必须遵守：

- FSTDD 是唯一流程真值源；代码和流程状态不能绕过 active Change。
- Gate 1/2/3 必须由 D哥明确确认，AI 不得代签。
- `.fstdd.yaml` 的 phase/status/Gate 原始审计字段不得手改，只能由受控 CLI 写入。
- 服务器经 SSH 执行的命令可能重复执行；所有远端动作必须幂等，并以端状态验证。
- 8787 经验端点是公网、无鉴权、只写端点，不改造成内部任务总线。

## Decisions

### 1. 三层架构与唯一真值源

**方案**：采用“服务器裸库 + 8788 轻量控制面 + 节点本地运行时”三层结构。

| 层 | 位置 | 内容 | 真值属性 |
|---|---|---|---|
| 数据面 | 服务器 `fstdd-git/stdd-repo.git` | Git 对象、分支、任务分支、Change 产物 | 唯一权威；`master` 为串行集成线 |
| 控制面 | 服务器 `127.0.0.1:8788` | SQLite 中的节点、任务租约、消息、幂等键 | 可重建缓存，不承载 Change 事实 |
| 节点面 | 每台机器本地 | 配置、SSH 凭据引用、每任务 worktree、运行日志 | 临时工作区，不是权威副本 |

服务器控制面丢失时，从 Git 的任务分支、Change `.fstdd.yaml`、slices/test-report 和审计记录重建；控制面恢复不应改写 Git 数据面。8787 保持独立，仅用于经验投稿。

### 2. 跨机粒度采用子 Change，不采用共享 Slice

跨机分派的最小对象是“一个子 Change”，而不是同一 Change 内的 Slice。原因是 `.fstdd.yaml` 的 `phases.build.slices_completed` 是共享机器状态；多个 agent 同时写同一文件会产生冲突。每个子 Change 由一个 agent 单写，父 Change/批次只在集成阶段串行合并。

现有 `batch` 仅适合微修复；分布式大任务使用标准 Change + 明确的子 Change 目录，不把 `batch` 的空壳范围判定当作授权。

### 3. 任务状态与 FSTDD phase 分离

控制面任务状态为：

```text
pending -> claimed -> running -> done
                         \-> failed
                         \-> blocked
```

它只描述“协调任务是否被领取/完成”，不推进 `.fstdd.yaml.current_phase`。只有 FSTDD CLI 和人工 Gate 流程可以推进 `understand/spec/build/deliver`。控制面 `done` 不等于 Gate 通过，也不等于 Change 可归档。

### 4. Gate 审计修正采用追加，不覆盖

历史上若 Gate 已存在且 `confirmed_actor: ai`，重复 `gate approve` 必须保持幂等，不能覆盖原字段。新增受控命令 `fstdd gate amend-audit`，将用户后续追认追加到 `.fstdd.yaml.audit_amendments[]`，保留原始记录。

每条修正至少包含：

```yaml
audit_amendments:
  - gate: 1
    original_confirmed_at: "2026-09-17T08:59:09"
    original_confirmed_by: dialog
    original_confirmed_actor: ai
    amended_actor: user
    amended_by: dialog
    amended_evidence: "D哥明确追认原 Gate 1 结论……"
    amended_at: "2026-09-17T12:00:00"
    amended_by_tool_version: "3.0"
    idempotency_key: "<change_id>:gate1:<evidence-hash>"
```

命令只允许显式 `amended_actor=user` 的追认，要求既有 Gate、确认通道和非空证据；重复同一幂等键返回成功但不追加；同一 Gate 使用不同证据不得静默覆盖，必须报冲突。该机制用于历史修正，不改变 Gate 顺序，也不自动产生新的 Gate 确认。

### 5. 控制面使用 SQLite，8788 独立于 8787

8788 使用 Python 标准库 `sqlite3` 和 `http.server`，仅监听 `127.0.0.1`，经 SSH 命令或本地端口转发访问。SQLite 事务负责原子领取、租约更新和幂等键约束。8787 保持现状，避免把无鉴权公网写入接口与内部任务权威混在同一故障域。

## Architecture

### 任务生命周期

```text
D哥/agent 创建 Change 与任务描述
        |
        v
Git master + task/<id> 分支（数据面真值）
        |
        +--> 8788 POST /tasks（控制面索引，可重建）
                       |
         agent 领取 + lease + heartbeat
                       |
         本机外部 worktree D:/fstdd-work/<task-id>
                       |
         FSTDD CLI / 测试 / test-report / slice evidence
                       |
         git push task/<id> -> 集成者串行 merge master
                       |
         D哥 Gate 1/2/3 -> phase advance -> archive
```

### 控制面数据模型

节点注册表：

```yaml
node_id: FSTDD005
machine_name: "<machine-name>"
platform: workbuddy
os: windows
capabilities: [python, fstdd-cli, desktop]
ssh_public_key_fingerprint: "SHA256:<fingerprint>"
status: online|offline|disabled
last_seen: "2026-09-17T12:00:00Z"
metadata: {}
```

任务表 `tasks`：

```text
task_id TEXT PRIMARY KEY
parent_change_id TEXT NOT NULL
child_change_id TEXT
kind TEXT NOT NULL                 # change | slice | debug | ops
summary TEXT NOT NULL
scope_json TEXT NOT NULL           # allowed/frozen paths
status TEXT NOT NULL               # pending/claimed/running/done/failed/blocked
owner_node_id TEXT
lease_token_hash TEXT
lease_expires_at TEXT
attempt INTEGER NOT NULL DEFAULT 0
idempotency_key TEXT UNIQUE
base_git_sha TEXT NOT NULL
result_git_sha TEXT
created_at TEXT NOT NULL
updated_at TEXT NOT NULL
```

消息表 `messages`：

```text
message_id TEXT PRIMARY KEY
conversation_id TEXT NOT NULL
task_id TEXT
from_node_id TEXT NOT NULL
to_node_id TEXT              # NULL 表示广播给任务参与者
kind TEXT NOT NULL           # question/blocker/status/notice
body TEXT NOT NULL
idempotency_key TEXT UNIQUE
created_at TEXT NOT NULL
acked_at TEXT
```

调试/运维记录优先落 Git Change 的 `test-report.md`、`design-adjustments.md` 或 `experiences/EXP-*.md`；8788 只保留消息与索引，不把服务器 SQLite 当长期事实库。

### 8788 路由

| 方法 | 路径 | 语义 | 成功 | 失败 |
|---|---|---|---|---|
| GET | `/health` | 服务与 SQLite 健康 | 200 | 503 |
| POST | `/nodes/register` | 注册/更新节点 | 200 | 400/409 |
| POST | `/nodes/heartbeat` | 更新心跳和租约 | 200 | 400/404 |
| GET | `/nodes` | 查看在线节点 | 200 | 503 |
| POST | `/tasks` | 创建任务，按幂等键去重 | 201/200 | 400/409 |
| GET | `/tasks?status=pending` | 查询任务池 | 200 | 400 |
| POST | `/tasks/claim` | 事务内领取一个可用任务 | 200 | 204/409 |
| POST | `/tasks/{id}/heartbeat` | 延长租约 | 200 | 404/409 |
| POST | `/tasks/{id}/complete` | 回传结果 SHA/报告引用 | 200 | 400/404/409 |
| POST | `/tasks/{id}/fail` | 回传失败与证据 | 200 | 400/404/409 |
| POST | `/messages` | 投递消息，按幂等键去重 | 201/200 | 400/409 |
| GET | `/messages?node_id=...` | 拉取未确认消息 | 200 | 400 |
| POST | `/messages/{id}/ack` | 确认消息 | 200 | 404 |

所有写请求携带 `idempotency_key`。领取事务使用 `BEGIN IMMEDIATE`，按 `pending` 和租约过期条件选择任务，更新 owner/status/lease；重复请求返回首次结果，不重复分配。

### Git 数据面

- 服务器裸库：`/srv/fstdd-git/stdd-repo.git`（最终部署路径以运维配置为准）。
- `master` 只有集成者写入；每个任务使用 `task/<task_id>`，每台机器每任务一个 worktree，路径必须位于任何 Git checkout 之外，例如 `D:/fstdd-work/<task_id>`。
- 节点通过 git-over-SSH 访问服务器裸库；GitHub 仅作镜像/备份，服务器到 GitHub 使用 ff-only 推送，失败不阻塞内部任务。
- 同一任务不得由多个 agent 同时修改；跨任务可以并行；集成、冲突裁决、master 写入严格串行。
- 禁止单文件拷贝式同步；所有同步必须是 Git commit/ref/merge，明确基线 SHA。

### 节点运行时

节点配置不进仓库，放在用户配置目录，例如 Windows `%USERPROFILE%\\.fstdd\\node.yaml`，含 `node_id`、服务器 SSH 目标、裸库路径、worktree 根目录、能力标签；私钥只引用系统 SSH agent/文件路径，不写入 YAML。

一次循环：

1. `register`/heartbeat，确认 node_id 与公钥指纹。
2. 查询 pending 任务并原子 claim，取得 lease token。
3. fetch 基线，创建外部 worktree 和 `task/<task_id>` 分支。
4. 在对应子 Change 内执行 FSTDD 流程、测试、per-slice 验证和报告。
5. commit/push 任务分支，向 8788 回传 result SHA 和报告路径。
6. 集成者串行审 diff；发生冲突则任务进入 blocked，不允许强推 master。
7. lease 到期由服务回收为 pending；原 owner 的回传必须携带 lease token，旧 token 被拒绝。

## Risks / Trade-offs

| 风险 | 缓解措施 |
|---|---|
| 8788 丢失或 SQLite 损坏 | 控制面可丢；从 Git 与 Change 状态重建；定时冷备数据库 |
| 节点掉线 | heartbeat + lease expiry 自动回收；任务尝试次数与人工告警 |
| 重复领取/SSH 双执行 | SQLite 唯一幂等键 + `BEGIN IMMEDIATE` + lease token |
| Git 冲突 | 每任务分支、外部 worktree；master 集成单写者；冲突进入 blocked |
| GitHub 不可达 | 不阻塞服务器裸库；沿用推送脚本重试/SSH 隧道 |
| Gate 被 AI 静默推进 | CLI 强制确认通道；审计修正追加，不覆盖；Gate 仍由 D哥确认 |
| 任务越界 | scope 白名单/冻结 glob；集成前审 diff；不接受仅有散文报告 |
| 服务器磁盘接近满 | 8787/8788/裸库分别监控，低于阈值告警，定期清理可重建缓存 |

## 落地顺序

1. 当前 SPEC：完成本文、canonical code/agent spec、test-plan，并由 D哥 Gate 2 确认。
2. BUILD Slice A：实现 `gate amend-audit` 和测试；先 red，再 green。
3. BUILD Slice B：实现控制面最小闭环（节点注册、任务创建/claim/heartbeat/complete），其余路由按证据推进。
4. BUILD Slice C：实现 Git worktree/分支客户端和集成检查。
5. BUILD Slice D：部署 8788、冷备、健康检查与运维脚本。
6. Gate 3：全量测试、失败模式检查、变异验证、test-report。
7. Deliver：只经 `phase advance` 后执行 `fstdd archive`。
