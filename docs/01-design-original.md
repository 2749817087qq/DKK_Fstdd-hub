# FSTDD 分布式任务协作方案（6 Agent / 3 开发机）

> 范围：**设计方案**（D哥 已选「先出方案」）。不含代码实现。
> 本机节点 id：`FSTDD005`（D哥 指定）。

---

## 0. 已拍板的四个方向

| # | 决策 | 含义 |
| --- | --- | --- |
| 1 | **服务器裸库 + 轻量控制面** | 数据面走服务器裸库（git over ssh，比 GitHub 稳）；另起 8788 服务管领取/心跳/消息。**真值源仍是 git**，控制面可丢可重建 |
| 2 | **SSH 为主** | 复用既有密钥，git 走 git-over-ssh，控制命令走 ssh。不新增公网端口、不做 TLS、不发明鉴权协议 |
| 3 | **对等 + 主动领取** | 6 agent 平权，任务池公开，谁空谁领，领取即上锁。Gate 确认仍由人把关 |
| 4 | **先出设计** | 本文件 |


---

## 1. ⚠️ 三个必须先解决的现状问题（实测确认，否则方案不成立）

这三条是我读代码 + 查仓库状态**实测**出来的，不是推测。**它们排在所有开发工作之前。**

### B1 · 批级派单机制当前被「遗留的顶层活跃 change」完全遮蔽 —— 最严重

`guard.py:203-251` 的 `_find_active_change()` 是两段逻辑：

1. **第一段**：扫 `.fstdd/changes/*`（**排除 `_batch`**），按 mtime 倒序取第一个满足
`status in (active|in_progress|pending)` **或** `phase in (understand|spec|build|deliver)` 的 change —— 命中即 `return`。
2. **第二段**（batch pipeline，V3.0.5 新增）：仅当第一段**没返回**时才会走到，用于放行批级子 change。

**实测当前仓库状态**：

```
.fstdd/changes/2026-09-16-inbox-review-sync/   status: active  current_phase: build  task_type: code
.fstdd/changes/2026-09-17-migrate-to-d-drive/  status: active  current_phase: build  task_type: code
```

两个顶层 change 都活跃且都在 build 相位 → **第一段必然命中并 return，第二段（batch 子 change 放行）永远不可达。**

后果：本方案的派单模型（`batch child` 作为可委派单元 + Guard 据批级 pipeline 放行）**现在跑不起来**。子 change 落在 worktree 里也不会被 Guard 认到，Guard 会一直认那两个遗留 change。

**必须先做**：把这两个 change 收尾——`2026-09-17-migrate-to-d-drive` 归档（迁移已完成）、`2026-09-16-inbox-review-sync` 归档或 `fstdd abort`（它停在 build 且无 `slices.md`/`test-report.md`，本身就是未完成的半成品，且 **2026-09-23 起会变成僵尸 change**）。

> 顺带修正一处设计假设：第一段的判定是 **`或`** 关系，所以 `status: archived` 但 `current_phase` 仍是四相位之一的 change **照样会命中**。归档时必须确认相位也被推进到终态，不能只改 status。

### B2 · 同机多 change 并发时 Guard 会认错 change（低概率、影响有限，但要知道）

第二段逻辑取的是**子 change 按 mtime 倒序的第一个**，不是「当前 worktree 正在做的那个」。若一个 worktree 里存在多个活跃子 change（都是从 master 检出的，**必然如此**），Guard 可能指向另一个 agent 的 change。

**实际影响有限**（诚实评估）：只要两个子 change 都在 `build` 相位，Guard 的放行/拦截结论**相同**，所以不会误拦。真正会出偏差的场景是「一个在 understand/spec、另一个在 build」——此时按 `task_type: code` 的规则，understand/spec 相位禁止编辑代码，认错就会误判。

**对策（二选一，建议都做）**：

- **轻量**：同机两个 agent **不要同时跑 build 相位任务**（机器级 concurrency=1，而非节点级）。零代码改动。
- **彻底**：给 `_find_active_change` 加「当前 change」提示——worktree 内放标记文件 `.fstdd/.active-change`（内容为 change_id，由 `fstdd hub task start` 写入，加入 `.gitignore`），Guard 优先读它；再支持 `FSTDD_ACTIVE_CHANGE` 环境变量覆盖。这是对 `upstream/` 的补丁，**升级上游时要重新应用**。

### B3 · 凭证与 Guard 的两处既有缺陷

| 问题 | 事实 | 处置 |
| --- | --- | --- |
| **`origin` URL 硬编码明文 PAT** | `git remote -v` 直接可读 `ghp_...`；`push_stdd_repo.sh:74-85` 每次运行还会重写它 | **撤销轮换该 token**，改用已配好的 `credential.helper=store`；origin 只留 `https://github.com/2749817087qq/DKK_Fstdd.git` |
| **仓库里的 Guard hook 很可能失效** | `.claude/settings.local.json` 里是裸命令 `stdd guard check ...`，而 `guard.py:785` 自己的注释写明「必须是绝对路径，裸命令不在 PATH，调用失败即放行」；且 `.codebuddy/` 目录**从未生成** | 在 P0 跑 `fstdd guard init` 修复；并把它纳入每节点 bootstrap 的验收项 |


> B3 的第二条与 B1 叠加意味着：**当前这台机器上 Guard 大概率既不认批级子 change、hook 本身也没生效** —— 宪法第 1/4 条在分布式下会整体失守。所以 P5 的服务端 `pre-receive` 兜底钩子（唯一不依赖客户端自觉的强制点）**优先级要提前**。

---

## 2. 总体架构

### 2.1 三层

```
L3 节点本地（3 台开发机 / 6 agent）
   ~/.fstdd/node.yaml            节点身份 + 连接参数（含密钥路径，绝不入库）
   D:/fstdd-work/<task_id>/      每任务一个 git worktree
   fstdd hub <cmd>               agent 的双手
L2 控制面（腾讯云 SG，8788，仅绑 127.0.0.1，经 ssh 访问）
   fstdd-hub.service / hub_server.py   （零依赖：http.server + sqlite3）
   SQLite hub.db                       节点在线态 / 任务租约 / 消息 / 幂等表
   语义：可丢。丢了由 git 重建
L1 数据面（真值源，腾讯云 SG，git over ssh）
   裸库 /home/ubuntu/fstdd-git/stdd-repo.git   ← 唯一真值源
   master（集成线，单写者）+ task/<task_id>（每任务一线）
   服务器 → GitHub DKK_Fstdd 单向镜像（备份/对外，失败不阻塞）
   8787 经验端点：外部贡献者通道，与本方案完全隔离
```

### 2.2 真值源裁定

| 数据 | 真值源 | 副本 | 丢了怎么办 |
| --- | --- | --- | --- |
| change/batch 相位、Gate 审计、产出物 | **git master** | 各机 worktree、GitHub | 从 GitHub/本地裸库恢复 |
| 任务声明（task_id/依赖/scope globs） | **git**（`.fstdd/coordination/tasks/*.yaml`） | 8788 `tasks` 表（索引） | 扫 git 重建 |
| 租约/心跳/在线态/消息/幂等 | **8788 SQLite** | 无（**允许丢**） | 租约过期 → 任务回 pending；消息按需 export 落 git |
| 节点注册表（非密） | **git**（`.fstdd/coordination/nodes.yaml`） | 8788 `nodes` 表 | 从 git 重建 |
| 私钥 / token / 本机路径 | **仅本机**（`~/.fstdd/`） | 无 | 重新分发 |


> 铁律落实：**只有 git master 一份被写入**；8788 与 GitHub 都是可重建派生物。控制面挂掉不阻塞任何 change 推进。

### 2.3 一个任务的完整路径

```
编排:  batch open → proposal → D哥 Gate1 → 批级 spec → D哥 Gate2
       batch child add <name> ×N     ← 拆成 N 个互不重叠的子 change
       git push hub master           ← 【前置条件】子 change 必须先上 master
       hub task declare <change_id> --allowed … --frozen … --cap … --depends …
                    ↓
worker: hub task next → claim（原子）→ start（建 worktree）
        按 slices.md 逐片 RED→GREEN→REFACTOR，逐片验证
        agent verify（宪法第 4 条）→ commit → push hub task/<id> → complete
                    ↓
集成:  integrator 逐个 git merge --no-ff → 重跑验证 → push hub master（串行）
       D哥 Gate3 → batch deliver → close → archive
```

---

## 3. 数据模型

### 3.1 任务粒度：**跨机单元 = 子 change**（不是 slice）

| 候选 | 支持 | 反对 | 结论 |
| --- | --- | --- | --- |
| **子 change**（`batch child`） | 已有 CLI/Gate/Guard 全套；`.fstdd.yaml` 是机器可读状态；继承批级 Gate1/2；一次 claim 只写一个 `.fstdd.yaml`，**天然单写者** | 并行度 ≤ change 数 | ✅ **采用** |
| slice | 理论并行度更高 | `slices_completed` 是 `.fstdd.yaml` 内一个 dict → 多机并发写同一文件**必然冲突**；且 `phase advance` 要求 per-slice 证据完整，多写者互相覆盖 | ❌ v0 不做 |
| batch 本身 | — | `batch open` 会先闭合已有 open batch（**全局只允许一个 open batch**，`batch.py:201-205`） | ❌ 只作程序容器 |


> **v0 明确不做 slice 级跨机**。理由：收益需 ≥6 个真正独立 slice 才显现，冲突成本立刻显现。若将来确需，走旁挂证据 `slices-evidence/<sid>.yaml`（单文件单写者）+ 新增 `fstdd slice merge-evidence` 汇总。

### 3.2 节点注册表（入 git，非密）

`.fstdd/coordination/nodes.yaml`

```yaml
version: "1.0"
updated_at: "2026-09-18T10:00:00+08:00"
nodes:
  - node_id: FSTDD005
    host_label: "阿枢-开发机"
    platform: windows
    capabilities: [python, git, ssh, fstdd-cli, windows]
    ssh_pubkey_fp: "SHA256:…"     # 只放指纹，绝不放密钥
    work_root: "D:/fstdd-work"
    concurrency: 1
    enabled: true
  # 其余 5 个由 D哥 填入；未填 = 该节点不存在。任何代码不得硬编码 FSTDD00X
```

### 3.3 节点本地配置（**绝不入库**）

`%USERPROFILE%\.fstdd\node.yaml`（Windows）/ `~/.config/fstdd/node.yaml`（Linux）

```yaml
node_id: FSTDD005
hub:
  ssh_host: ubuntu@43.134.236.80
  ssh_key: /d/id_ed25519          # 只放路径
  local_port: 18788               # ssh -L 18788:127.0.0.1:8788
  base_url: http://127.0.0.1:18788
git:
  remote: "ssh://ubuntu@43.134.236.80/home/ubuntu/fstdd-git/stdd-repo.git"
  ssh_command: "ssh -i /d/id_ed25519 -o StrictHostKeyChecking=no"
work_root: "D:/fstdd-work"
concurrency: 1
heartbeat_interval: 30            # 秒
lease_ttl: 900                    # 秒
```

> `fstdd hub doctor` 必须断言此文件**未被 git 跟踪**，否则 FAIL。

### 3.4 任务声明（入 git，慢变）

`.fstdd/coordination/tasks/<task_id>.yaml`

```yaml
task_id: T-20260918-001
kind: change                      # change | ops | debug
change_id: 2026-09-18-git-plane
parent_batch: <batch_id>
title: "搭建服务器裸库 + GitHub 镜像同步"
required_capabilities: [git, ssh]
allowed_globs:                    # 唯一可改范围（至少一个）
  - "tools/deploy_git_plane.sh"
  - ".fstdd/coordination/**"
frozen_globs:                     # 不可碰
  - "upstream/fstdd/cli/commands/guard.py"
depends_on: []                    # 未满足依赖不派发
declared_status: pending          # pending | done | cancelled
created_by: FSTDD005
created_at: "2026-09-18T10:00:00+08:00"
```

**运行时状态**（快变，**只存 8788 SQLite**）：
`pending → claimed → running → done | failed | blocked`

### 3.5 与 `.fstdd.yaml` 的共存规则（硬约束）

| 字段 | 归谁 | 说明 |
| --- | --- | --- |
| `current_phase` / `status` / `phases.*` | **FSTDD CLI 独占** | 只能 `fstdd phase advance`；协调面**永远不写**（宪法 F1：禁止手改） |
| `confirmed_by/actor/evidence/at` | **Gate CLI 独占（人触发）** | 协调面只可*请求*，不可代写 |
| `slices_completed` | 执行该 change 的**唯一节点** | 一 change 一租约 → 天然单写者 |
| `resume_context/active_slice/last_action` | 该节点用 `fstdd state --set` | 走 CLI 白名单字段 |
| owner / lease / state / messages | **8788** | 与 `.fstdd.yaml` 无交集 |


> **协调面 `done` ≠ FSTDD 推进**。它只表示 worker 完成 build 并推了证据；Gate3/advance/archive 仍由 integrator + D哥 走 FSTDD。`hub doctor` 增加一致性检查：task=done 但 change 未过 Gate3 → WARN。**不做自动推进**（违反宪法第 3 条）。

### 3.6 消息（8788 SQLite）

```sql
CREATE TABLE messages(
  msg_id TEXT PRIMARY KEY,       -- M-000001
  thread_id TEXT, task_id TEXT,
  from_node TEXT NOT NULL, to_node TEXT,          -- NULL = 广播
  kind TEXT NOT NULL,            -- question|answer|blocker|notify|handoff
  body TEXT NOT NULL,            -- 人读文本，≤4KB
  refs TEXT NOT NULL DEFAULT '[]',  -- JSON: ["commit:9e80240","file:…","msg:M-000007"]
  created_at TEXT NOT NULL,
  ack_by TEXT NOT NULL DEFAULT '[]'
);
```

投递语义：**拉取式 + at-least-once + 幂等键**。`GET /messages?to=<node>&since=<msg_id>`，**不做推送**（推送要长连接/重连/NAT 穿透，6 agent 规模收益为零）。**证据不内联**，`refs` 只放 commit sha / 仓库内路径 / msg_id；大对象走 git。

### 3.7 故障单（入 git，沿用 `experiences/EXP-*.md` 的 frontmatter 风格）

`.fstdd/coordination/faults/FLT-<YYYYMMDD>-<nnn>.md`，证据放 `faults/<fault_id>/evidence/*`（**禁止 scp**，一律 commit）。

```yaml
fault_id: FLT-20260918-001
status: repro_requested    # open|repro_requested|confirmed|fixed|wontfix
severity: high
reporter_node: FSTDD001
suspect_node: FSTDD003
change_id: 2026-09-18-xxx
env: {node_id: FSTDD001, platform: windows, python: "3.11.5", commit: 9e80240}
evidence_refs: ["commit:9e80240", "file:faults/FLT-20260918-001/evidence/stack.txt"]
```

运维记录用轻量 append-only 日志 `.fstdd/coordination/ops/OPLOG-<YYYYMM>.md`（时间/节点/动作/命令/端状态判据/结果），避免为每次巡检开 fault。

---

## 4. 控制面 8788（`fstdd-hub`）

### 4.1 路由表

所有 `/api/v1/*` 写操作**必须**带 `Idem-Key`，否则 400。

| 方法 | 路径 | 关键响应 |
| --- | --- | --- |
| GET | `/health` | `{ok, db, nodes_online, tasks{pending,claimed,running,blocked}, leases_expiring}` |
| POST | `/api/v1/nodes/register` | `{ok, node_id}` / 409（node_id 与 key_fp 不符） |
| POST | `/api/v1/nodes/{id}/heartbeat` | `{ok, next_beat_s}` |
| GET | `/api/v1/nodes` | 节点在线态 |
| GET | `/api/v1/tasks?state=&cap=&since=` | 可领任务列表 |
| POST | `/api/v1/tasks` | 声明任务 → 201 / 409 |
| POST | `/api/v1/tasks/{id}/claim` | `{ok, lease_id, expires_at, attempt}` / **409 已被占** / 412 依赖未满足 |
| POST | `/api/v1/tasks/{id}/renew` | 续租 → 200 / 409 lease 失效 |
| POST | `/api/v1/tasks/{id}/start` | → running |
| POST | `/api/v1/tasks/{id}/complete` | 需 `result_commit`，否则 422 |
| POST | `/api/v1/tasks/{id}/fail\ | block\ | release` | 终态/释放 |
| GET/POST | `/api/v1/messages`、`/{id}/ack` | 消息收发 |
| POST | `/api/v1/reap` | 回收过期租约 |


### 4.2 原子领取（现有 8787 无锁非原子，这里必须做对）

SQLite `BEGIN IMMEDIATE` 单语句 CAS，`rowcount==1` 才算领到：

```sql
BEGIN IMMEDIATE;
UPDATE tasks
   SET state='claimed', owner_node=:node, lease_id=:lease,
       lease_expires_at=:exp, attempt=attempt+1, updated_at=:now
 WHERE task_id=:task AND declared_status='pending'
   AND (state='pending' OR (state IN ('claimed','running') AND lease_expires_at < :now));
-- rowcount==1 → 200（返回 lease_id）；rowcount==0 → 409
COMMIT;
```

- `PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;`（`sqlite3` 是 stdlib，**仍满足零依赖**）
- 线程模型沿用 `ThreadingHTTPServer`；**写路径过一把进程内 `threading.Lock()`**，读路径不加锁
- `lease_id = uuid4().hex`，后续所有变更用 `(task_id, lease_id)` 做 CAS，防「过期租约持有者回来乱写」

### 4.3 租约回收（双保险）

`lease_ttl` 默认 900s；worker 每 30s `renew`（兼作心跳）。
① **懒回收**：每次 `claim` 前先跑 reap；② **定时回收**：systemd timer 每 60s 调 `/api/v1/reap`。

```sql
-- 未超尝试上限 → 回 pending
UPDATE tasks SET state='pending', owner_node=NULL, lease_id=NULL, lease_expires_at=NULL
 WHERE state IN ('claimed','running') AND lease_expires_at < :now AND attempt < :max_attempts;
-- 超上限（默认 3）→ blocked + 广播 blocker 消息
```

节点 `offline`（`now - last_seen > 90s`）时**不立即回收租约**，避免网络抖动误杀，等自然过期。

### 4.4 幂等键（应对 ssh 双执行 / 重试）

```sql
CREATE TABLE idempotency(idem_key TEXT PRIMARY KEY, node_id TEXT, endpoint TEXT,
  request_hash TEXT, response_json TEXT, created_at TEXT NOT NULL);
```

与业务写**同一事务**内：命中且 hash 相同 → 原样返回缓存响应；命中但 hash 不同 → 409 `idem_key_reused`；未命中 → 执行 + 写入。TTL 24h。

> 为什么够用：ssh 双执行是**同一命令同一 body** → hash 相同 → 第二次直接命中缓存。本设计**服务端没有任何 append-only 业务写**（这是刻意的）。

### 4.5 身份：SSH 密钥即身份

**推荐（需 D哥 决策 Q3）**：每节点一把独立 SSH 公钥，`authorized_keys` 用 **forced command** 注入身份：

```
command="/home/ubuntu/fstdd-hub-proxy --actor=<node_id>",no-port-forwarding,no-pty ssh-ed25519 AAAA… FSTDD005
```

`fstdd-hub-proxy` 是个约 10 行的脚本：读 stdin 的 `METHOD PATH BODY`，塞入 `X-Actor` 头，`curl 127.0.0.1:8788`，回吐响应。**node_id 由密钥决定，agent 无法冒充。**

**降级（若共用 `/d/id_ed25519`）**：服务端**无法区分调用者**，`node_id` 只能自报 → 租约归属不可追责，故障节点可冒充他人 complete。这是**诚实的弱点**，必须写进文档，不能假装有安全性；此时真审计只能靠 **git commit author + 任务分支名**。

### 4.6 与 8787 的关系：**新建独立服务，不改造**

| 维度 | 8787 `fstdd-inbox` | 8788 `fstdd-hub` |
| --- | --- | --- |
| 面向 | 外部无凭证贡献者 | 内部 6 agent |
| 鉴权 | **有意无鉴权** | 密钥/身份（**相反**） |
| 语义 | **有意只写不读**、无索引、非原子 | 事务、读多写、租约（**相反**） |
| 暴露 | `0.0.0.0` 公网 | `127.0.0.1` 仅本机 |
| 爆炸半径 | 外部使用者依赖 | 内部任务流转 |


合并 = 让「对外无鉴权端点」与「内部任务权威」共享进程与故障域；8787 被灌爆或重启会**全队停摆**。宪法第 6 条（不向第三方外发）也要求任务数据不出现在对外端点。**共享的只是部署范式，不是代码。**

### 4.7 部署（沿用 `deploy_inbox_server.sh` 的幂等范式）

`tools/deploy_hub_server.sh`，5 步对齐既有脚本：

```
HOST="${FSTDD_SSH_HOST:-ubuntu@43.134.236.80}"; KEY="${FSTDD_SSH_KEY:-/d/id_ed25519}"
PORT="${FSTDD_HUB_PORT:-8788}"
REMOTE_DIR=/home/ubuntu/fstdd-hub-server ; DATA_DIR=/home/ubuntu/fstdd-hub-data
# 0/5 探测远端 python3 + `python3 -c 'import sqlite3'` 自检
# 1/5 scp hub_server.py + md5 断言（照抄 deploy_inbox_server.sh:51-58）
# 2/5 sudo tee /etc/systemd/system/fstdd-hub.service（ExecStart 绑 127.0.0.1）
# 3/5 停旧进程：OLD=$(sudo lsof -t -i:$PORT) → kill   ← 绝不用 pkill -f（会杀掉 ssh 自身）
# 4/5 ss -lntp | grep :8788 + curl 127.0.0.1:8788/health + systemctl is-enabled
# 5/5 【端状态】断言 bind 地址是 127.0.0.1 而 不是 0.0.0.0
```

---

## 5. Git 数据面

| 项 | 值 |
| --- | --- |
| 裸库 | `/home/ubuntu/fstdd-git/stdd-repo.git`（`git init --bare`） |
| 访问 | `ssh://ubuntu@43.134.236.80/home/ubuntu/fstdd-git/stdd-repo.git` |
| 分支 | `master`（集成线，唯一真值，**单写者**）+ `task/<task_id>`（每任务，短命）+ `debug/<fault_id>`（可选） |
| 保护 | `hooks/pre-receive`：**拒绝删除 master、拒绝向 master 强推**；`task/*` 允许 force（工作者自己的线） |


**各机 push/pull 走直连 ssh，不走 SOCKS5 隧道**。隧道（`push_stdd_repo.sh:90-165`）是为「本地 → GitHub」设计的兜底；本方案里本地只与服务器说话，服务器与 GitHub 说话（实测 0.06s）。**既有 `push_stdd_repo.sh` 保留不动**，作为 GitHub 直推旁路。

**服务器 → GitHub 镜像**：`tools/git_mirror.sh` + `fstdd-git-mirror.timer`（每 5 分钟）。

```
git -C /home/ubuntu/fstdd-git/stdd-repo.git push "$REMOTE_URL" master:master   # ff-only，永不 force
git -C … push --tags "$REMOTE_URL"
```

非 ff 即失败 → 记 `mirror.log`，**不 force**，交 integrator 判断（通常是有人绕过 hub 直推了 GitHub）。token 存 `/etc/fstdd/github-token`（600，root），**绝不出现在仓库**。不做 post-receive hook（带 token 风险高、hook 失败会阻塞 push）。

**并发写冲突规则**：

| 场景 | 机制 | 裁决 |
| --- | --- | --- |
| 两 agent 抢同一 task | 8788 原子 claim（一 200 一 409） | 服务端 |
| 两 task 改同一文件 | **声明期拦截**：`allowed_globs` 有交集 → declare 返回 409 `scope_overlap`，强制串行 | 编排者 |
| 同 task 线被两人推 | `task/<id>` 非 ff 被拒 | 先持有租约者 |
| 集成冲突 | integrator **一次只合一个** `task/*` → master，合完**重跑验证** | integrator |


**避免 half-sync 的硬规则**（写进 skill + 宪法补充）：

1. 禁止 `cp`/`scp`/网盘在机器间传源码；跨机产物一律 `git commit + push`
2. 禁止「只同步某个文件」；要同步就 `git fetch` + 整树 merge
3. 故障证据也必须 commit，不得 scp
4. 补丁必须显式声明基线 commit（`git format-patch <base_sha>`）
5. `hub doctor` 增加启发式检查：`work_root` 外存在与 master 同名的旁路副本 → 告警并**阻断该节点 claim**

---

## 6. 节点运行时

**领取→执行→回传循环**：

```
0. hub node register --from-config                    （一次）
1. hub node heartbeat                                 （每 30s，后台）
2. hub task next --cap python,git
3. hub task claim <task_id>                           → 409 则回 2
4. hub task start <task_id> --lease <lease_id>
     git fetch hub master
     git worktree add D:/fstdd-work/<task_id> -b task/<task_id> hub/master
5. cd D:/fstdd-work/<task_id>
     fstdd guard status        ← 必须显示 active change
     fstdd state --compact     ← 期望 phase=build, freshness=FRESH
     按 slices.md 逐片 RED→GREEN→REFACTOR，逐片验证
     需澄清 → hub msg send --kind question --to <node>
     卡住   → hub msg send --kind blocker …  +  hub task block
6. fstdd agent verify --task <id>                     （宪法第 4 条）
7. git add -A && git commit && git push hub task/<task_id>
8. hub task complete <task_id> --lease <lease_id> --commit <sha>
9. 保留 worktree 供 integrator review；合完再 hub task cleanup
```

**工作隔离**：`git worktree`，位置 `D:/fstdd-work/<task_id>`（主 checkout 之外）。

- **不用 gitless 沙箱**：delegate-slice 的 gitless 沙箱是为「worker 无 git 权限」设计的；本方案 worker **有**任务分支写权限（这是回传的前提）。
- **诚实偏差**：worktree 元数据在 `<repo>/.git/worktrees/` 内，严格说不满足 delegate-slice 的「沙箱在任何 git 仓库之外」。我判断不构成风险——不存在两个 worker 共用一个目录，且文件系统路径不重叠。
- **机器级 concurrency=1**（见 B2）：一台机上 2 个 agent 不要同时跑 build 任务。

**与 Guard 的关系**：子 change 已由编排方 `batch child add` 落在 master，worktree 检出后即存在 → Guard 经 batch pipeline 放行。**所以「先提交子 change 到 master 再派单」是硬前置条件**（Guard 只认本地磁盘的 `.fstdd/changes/**`）。ops/debug 任务同样要挂子 change（`task_type: configuration`），否则 Guard 拦 Bash。

---

## 7. 新增文件清单

### 7.1 CLI 扩展（`upstream/fstdd/cli/`）

新增 `commands/hub.py`，在 `cli/__init__.py` 的 `COMMAND_GROUPS` **「管控」组**注册 `hub`。

| 命令 | 行为 |
| --- | --- |
| `hub node register\ | heartbeat\ | list` | 节点注册/心跳/列表 |
| `hub task declare\ | next\ | claim\ | start\ | renew\ | complete\ | fail\ | block\ | release\ | list\ | cleanup` | 任务全生命周期 |
| `hub msg send\ | inbox` | 消息收发 |
| `hub fault open\ | update\ | close\ | list` | 故障单 |
| `hub doctor` | 自检：node.yaml 是否入库 / hub 可达 / git remote 可达 / Guard 是否装 / work_root 是否在仓库内 / task.done 与 Gate3 一致性 |


**为什么进 CLI 而非 tools/**：`task declare` 要读当前 change、写 `.fstdd/coordination/`、与 `guard status` 联动；`task start` 要调 git worktree 并校验 active change；`doctor` 要复用 Guard 判定。放 tools/ 会重复实现一遍 `_find_active_change`。

### 7.2 tools/ 脚本（命名与既有 12 个一致）

| 文件 | 职责 | 对应范式 |
| --- | --- | --- |
| `tools/hub_server.py` | 8788 控制面（stdlib only） | `inbox_server.py` |
| `tools/deploy_hub_server.sh` | 幂等部署 fstdd-hub | `deploy_inbox_server.sh` |
| `tools/git_mirror.sh` | 裸库 → GitHub ff-only 镜像 | 新 |
| `tools/hub_reap.sh` | 定时调 `/api/v1/reap` | 新 |
| `tools/deploy_git_plane.sh` | 建裸库 + pre-receive + mirror timer | 新 |
| `tools/node_bootstrap.sh` / `.ps1` | 节点一键引导（装 Guard、配 remote、写 node.yaml 模板） | `setup_git_credential.py` / `install.ps1` |


**不新增** `hub_client.py` —— 客户端逻辑进 CLI，避免两套实现漂移。

### 7.3 Skill（4 个，放 `skills/<name>/SKILL.md`，登记进 `install_workbuddy_skills.py` 的 `LOCAL_SKILLS`）

frontmatter 四件套：`name` / `description` / `version` / `license`（`check_skill_metadata.py:27` 硬要求）。

| skill | 触发 | 职责 |
| --- | --- | --- |
| `fstdd-coordinate` | 拆片、派单、多机并行、编排 | 编排者：batch proposal→Gate1/2→按 slices 拆 N 个 `batch child add`→`hub task declare`（含 scope globs + 依赖）→监控→**串行集成**→`batch deliver` |
| `fstdd-hub` | 领取任务、节点、心跳、上报 | 工作者：注册/心跳/领取/worktree/执行/上报；**强制**「Gate 确认必须由人，AI 只能转发请求」 |
| `fstdd-debug-relay` | 跨机复现、故障单、证据传递 | 开 fault、传证据（commit 引用）、请求复现、结论回填 `experiences/EXP-*.md` |
| `fstdd-ops` | 运维、部署、健康检查、节点掉线 | 8787/8788 健康、`deploy_*`、镜像状态、磁盘水位 |


**不改** `fstdd-understand/spec/build/deliver/upgrade` 的阶段语义，只在编排/工作者 skill 中引用它们。

---

## 8. 三场景走查（要点）

**A · 开发（6 agent 并行一个 batch）**：编排者 `batch open`（**注意**：`batch open` 会拒绝中大型描述，用短描述 open、完整描述走 `batch proposal`）→ 批级 proposal/spec → **D哥 Gate1/Gate2** → `batch child add` ×6（两两 `allowed_globs` 不得相交）→ `git push hub master` → `hub task declare` ×6 → 各节点 claim/start/逐片 TDD/`agent verify`/push `task/*`/complete → integrator **串行** merge → **D哥 Gate3** ×6 → `batch deliver`/`close`/`archive` → `hub task cleanup`。

**B · 调试（A 机失败，B 机复现）**：A `hub fault open --env-commit <sha>` → 证据**在本机仓库内**收集并 commit（不是 scp）→ `git checkout -b debug/<fault_id>` + push → `fault update --status repro_requested` + `msg send --kind blocker --to <B> --ref commit:<sha>` → B `msg inbox` → `git worktree add` → 复现，**只做「发现+回传」不改代码**（唯一真值源铁律第 2 条）→ 追加证据 commit + `msg send --kind answer` → A 定位根因，**若需改代码必须开 change** → `fault close` + 结论回填 EXP。

**C · 运维**：`hub doctor` + `curl 8787/health` + `ssh … curl 127.0.0.1:8788/health`；部署用 `deploy_hub_server.sh`（判据：`is-active=active` **且** `ss` 显示 `127.0.0.1:8788` **且** `/health.ok=true`，三条同时为真）；镜像状态比对裸库 HEAD 与 `git ls-remote github master`；**磁盘水位**（有 97% 前科）`df -h /` < 85%，超 95% 时 hub **拒绝新 claim（507）——拒绝写比写坏好**。

---

## 9. 并发规则与失败模式

**可并行**：读 master / 读任务池 / heartbeat / 写各自 `task/*` 分支 / 同 change 内不同 slice 的 TDD / 不同子 change。
**必须串行**：`allowed_globs` 相交的 task（declare 期 409 强制）/ 写 `.fstdd.yaml` / **Gate 确认（只能人）** / 推 master / `task/*`→master 合并（一次一个，合完重跑验证）/ `batch deliver`+`archive` / 服务器部署 / **口径变更（先改文档再改代码）**。

| # | 失败模式 | 检测 | 处置 |
| --- | --- | --- | --- |
| F1 | 节点掉线 | `last_seen` > 90s；租约 > 900s | 租约自动回收→pending（`attempt+1`）；超 3 次→blocked + 广播 |
| F2 | 任务重复领取 | `BEGIN IMMEDIATE` 的 `rowcount` | 后者 409；ssh 双执行同 `Idem-Key` → 返回同一 `lease_id` |
| F3 | git 冲突 | 非 ff push 被拒 / merge 冲突 | 非 ff 后来者 rebase；master 冲突停在 integrator，一次解一个 |
| F4 | **服务器不可达** | `ssh -o ConnectTimeout=20` 失败 | **降级路径**：① 继续在本地 worktree 干活（不阻塞）② 回传改走本地裸库 `backups/stdd-repo.git` 稍后补推 ③ 领取改走 **git-ref 占位**（见 §11 R1）④ `doctor` 报 `hub=UNREACHABLE, git=LOCAL_ONLY` |
| F5 | GitHub 不可达 | `git_mirror.sh` push 失败 | 只记 `mirror.log`，**不影响任何流转**；恢复后 timer 自动补齐 |
| F6 | ssh 双执行 | `idempotency` 表命中 | 返回缓存；**禁止任何 append-only 服务端写**；部署全程幂等；验证用端状态 |
| F7 | 未过 Gate 想推进 | `phase advance` 检查 `confirmed_at`；BUILD→DELIVER 检查 per-slice 证据 + test-report；`gate approve` 缺 `--confirmed-by` exit 2 | 命令直接拒绝；integrator **审 diff 而非只读报告**，派**全新 reviewer**（只给 brief+diff+report） |
| F8 | 僵尸任务 | ① `.fstdd.yaml.last_modified` > 7 天（既有）② hub task `claimed/running` 且 `updated_at` > 2h ③ `pending` 超 3 天无人领 | ① `fstdd abort` ② 强制 release + 广播 ③ 重新拆片或 cancel |
| F9 | hub DB 丢失 | `/health` `db=error` | 扫 `.fstdd/coordination/tasks/*.yaml` 重建（全 pending）；消息丢失（已知并接受） |
| F10 | 磁盘打满 | `df -h /` ≥ 85% | 告警 + `VACUUM` + `git gc` + 轮转日志；≥95% 拒绝 claim |
| F11 | 密钥泄漏进仓库 | `doctor` 扫 `node.yaml`/`id_ed25519`/`ghp_` 是否被跟踪；pre-receive 扫 diff | `git rm --cached` + **轮换**密钥/token |
| F12 | 跨机拷贝式同步 | doctor 启发式扫 | 阻断该节点 claim 直到清理 |


---

## 10. 落地阶段（每阶段 = 一个 FSTDD change）

> **元要求**：本方案自身也走 FSTDD。建议 `fstdd batch open "分布式协作"`（短描述）→ `batch proposal`（写本方案）→ 批级 design → **D哥 Gate1/Gate2** → 按阶段 `batch child add` 逐个子 change 交付。
> 两条实测摩擦：① `batch open` 的 scope 分类器会拒绝中/大型描述（`batch.py:168-181`）→ 短描述 open；② **全局只允许一个 open batch** → 全部阶段挂同一 batch。

| 阶段 | 内容 | 主要文件 | 验收判据 |
| --- | --- | --- | --- |
| **P0 清障与决策**（0 代码） | **解决 B1**：归档 `2026-09-17-migrate-to-d-drive`、归档或 abort `2026-09-16-inbox-review-sync`（**必须同时确认相位推进到终态**）；**解决 B3**：轮换泄漏的 PAT、跑 `fstdd guard init`；D哥 拍板 §11 的 Q1–Q8；填 `nodes.yaml` | `.fstdd/coordination/nodes.yaml`（骨架） | `.fstdd/changes/` 下**无**顶层 active change；`git remote -v` 无明文 token；`.claude/` 与 `.codebuddy/` 的 hook 都是**绝对路径**；6 个 node_id 有值；每节点 `ssh -i <key> ubuntu@… 'echo ok'` 成功 |
| **P1 Git 数据面** | 服务器建裸库 + pre-receive 保护；本机加 `hub` remote；镜像脚本 + timer；**同时落地 git-ref 占位降级路径** | `tools/deploy_git_plane.sh`、`tools/git_mirror.sh`、`tools/fstdd-git-mirror.{service,timer}` | ① `git ls-remote hub master` 通 ② 从 2 台机各推一个 `task/*` 成功 ③ 向 master 强推被拒 ④ 5 分钟后 GitHub master sha == 裸库 master sha |
| **P2 控制面 8788（MVP）** | `hub_server.py`（health/nodes/heartbeat/tasks declare/claim/renew/complete/reap + idempotency）+ 部署脚本 + reap timer | `tools/hub_server.py`、`tools/deploy_hub_server.sh`、`tools/fstdd-hub.service`、`tools/hub_reap.sh`、`tools/fstdd-hub-reap.{service,timer}` | ① `/health` `ok:true` ② `ss` 显示 **127.0.0.1** ③ **并发 claim 压测：同 task 并发 20 次，恰好 1 个 200、19 个 409** ④ 同 `Idem-Key` 连发 2 次 → 同 `lease_id` ⑤ 杀进程后 900s 任务自动回 pending |
| **P3 CLI + 节点运行时** | `fstdd hub` 全套；node.yaml 模板；worktree 流程；`hub doctor`；**解决 B2**（`.active-change` 标记文件或机器级 concurrency=1） | `upstream/fstdd/cli/commands/hub.py`、`cli/__init__.py`、`tools/node_bootstrap.{sh,ps1}` | ① 一台机端到端 declare→claim→worktree→commit→push→complete ② `hub doctor` 全 PASS ③ worktree 内 `guard status` 显示**正确的** active change ④ 缺 `--lease` 或 lease 失效时 complete 被拒 409 |
| **P4 协调产物 + Skill** | tasks/faults/ops 落盘与导出；4 个新 skill；安装脚本登记 | `.fstdd/coordination/**`、`skills/fstdd-{coordinate,hub,debug-relay,ops}/SKILL.md`、`tools/install_workbuddy_skills.py` | ① `check_skill_metadata.py` 对 4 个 skill 报 0 缺失 ② `verify_workbuddy_skills.py` 通过 ③ 走通 §8B 调试全流程 ④ `hub msg export` 能把消息落 git |
| **P5 加固与演练**（**优先级高于 P2/P3 的便利功能**） | **服务端 `pre-receive` 兜底「无 change 不得改代码」**（唯一不依赖客户端自觉的强制点）；磁盘水位；chaos 演练 | 服务器 `hooks/pre-receive`、`tools/git_mirror.sh`、`skills/fstdd-ops` | ① 无 change 直接推代码被拒 ② 拔网线 15 分钟后任务自动回 pending 并被他人领走 ③ 磁盘 85% 告警、95% 拒绝 claim ④ 6 机同跑一个 batch，master 无脏合并 |


---

## 11. 风险与需 D哥 决策的开放问题

### 主要风险

**R1 · 最大风险：为 6 agent / 3 机的规模造了一个分布式系统。**
`fstdd-hub` 引入新单点、新状态存储、新协议，而它全部价值只有「原子领取 + 心跳 + 消息」。有一个**零新增基础设施**的替代：

> **备选方案 A：用 git ref 做锁。**
> `git push hub <sha>:refs/tasks/<task_id>/lease` 配合 `--force-with-lease=refs/tasks/<task_id>/lease:<expected>` —— 由裸库 `receive-pack` 提供**服务端原子性**（git 原生能力，无需自研锁）。领取 = push lease ref；心跳 = 定期重推更新；释放 = 删 ref。
> **优点**：真值源与锁在**同一处**，没有第二套一致性；不存在「git 活着但锁服务死了」的脑裂。
> **缺点**：心跳变成 git push（比 HTTP 重）、消息无法承载、`force-with-lease` 语义要全员都懂。
> >
> **建议**：P1 **先落地 git-ref 占位**作为 F4 降级路径；8788 保留但设明确的「不做」清单。**若 P2 完成后 3 周内没有一次「因为 8788 才解决」的真实事件，应退回方案 A。**

**R2 · 单点服务器 + 磁盘 97% 前科**：同一台 VM 承载 8787（对外）+ 8788（内部）+ 裸库 + 镜像，一次磁盘打满会**同时**打掉外部贡献通道与全队流转。对策：P5 水位守卫 + 裸库与 hub.db 分目录 + 明确「8787 优先保命」。**没有 HA 就是不成立**——取舍见 Q6。

**R3 · 身份不可证**（若共用一把 SSH key）：见 §4.5，必须二选一并写进文档。

**R4 · Guard 在异构机器上失守**：Guard 支持 Claude Code / OpenCode / Codex / WorkBuddy，其余 5 个 agent 若是别的形态，hook 未必装得上。→ P5 的 `pre-receive` 兜底是**唯一绕不过的强制点**，**优先级应高于任何便利功能**。

**R5 · 并行度被 change 粒度锁死**：一个 change 只有 2 个独立 slice 时，6 个 agent 里 4 个在等。→ 编排时**按 change 数凑并行**（把程序拆成 ≥6 个子 change），不要为此上 slice 级跨机。

**R6 · 双状态机漂移**：task 标 `done` 但 `.fstdd.yaml` 还停在 build。→ 文档反复强调 + `doctor` 加一致性 WARN + **不做自动推进**。

**R7 · Windows/Git-Bash 路径陷阱**（有前科）：`/c/Users/x` 传给 Windows Python 会变 `C:\c\Users\x` 且**静默不报错**。→ `hub task start` 必须输出 Windows 风格路径（`D:/fstdd-work/T-…`），`doctor` 断言 `work_root` 解析结果与预期一致。

### 需你决策（我无法代决）

| # | 问题 | 我的倾向 |
| --- | --- | --- |
| Q1 | 其余 5 个 agent 是什么形态（WorkBuddy / Claude Code / 其他）？跑在哪 3 台机的哪些 OS？ | 先按 Windows+Linux 混合设计 |
| Q2 | 3 台机能否直连 `43.134.236.80:22`？还是需要走代理？ | 假设可直连（与现有 push 脚本一致） |
| Q3 | 能否为 6 个节点各配一把**独立** SSH 密钥？ | **强烈建议各配一把**（否则 R3 成立） |
| Q4 | 8788 只绑 `127.0.0.1`（经 ssh 访问）可以吗？ | **坚持 loopback**；一旦暴露公网就必须发明鉴权/TLS，违背方向 2 |
| Q5 | integrator 固定为 `FSTDD005` 还是按 batch 轮换？ | 固定 FSTDD005，后续再轮换 |
| Q6 | 接受单台 VM 作 SPOF 吗？要不要定时冷备（裸库 `git clone --mirror` + `hub.db` 快照）到另一处？ | **至少加定时冷备** |
| Q7 | GitHub 镜像还需要吗？ | **保留但降级**：只镜像 master+tags，失败不告警到人 |
| Q8 | 先做 git-ref 占位最小版，还是直接上 8788？ | **先 git-ref**（P1 就落地），再 8788 |


### 明确不做（v0）

实时推送 / Web UI / mTLS / HA / 容器化 / 单节点多并发 / slice 级跨机分工 / 消息内联大对象 / 自动推进 Gate。
理由：6 agent、3 机规模下收益为零，而成本与故障面显著。

---

## 附：本方案依据的关键实测事实

| 事实 | 来源 |
| --- | --- |
| `_find_active_change` 第一段排除 `_batch` 且判定是**或**关系，命中即 return | `guard.py:203-241`（实读） |
| batch pipeline 分支在第二段，**当前不可达** | `guard.py:242-254` + 实测两个顶层 active change |
| `project_root = Path.cwd()` | `guard.py:591,723,814,909`（实读） |
| `batch open` 先闭合已有 open batch | `batch.py:201-205`（实读） |
| `.gitignore` 已忽略 `.claude/`、`.workbuddy-ai/`、`experiences/`、`inbox/` | `.gitignore:1-17`（实读） |
| skill 四件套 `name/description/version/license` | `check_skill_metadata.py:27`（实读） |
| `LOCAL_SKILLS = ["fstdd-fin"]` | `install_workbuddy_skills.py:32`（实读） |
| CLI 可用，子命令 31 个 | `python upstream/bin/fstdd` 实跑 |
| `origin` URL 内嵌明文 PAT | `git remote -v`（实跑） |
| 8787 有意无鉴权、只写不读、无锁非原子 | `inbox_server.py` + `changes/2026-09-16-inbox-review-sync/design.md` Decision 1 |
| Gate 审计四字段、`--confirmed-by` 必填 | `gate.py` |
| delegate-slice 的 scope/ledger/4 态/并行三前提 | `backups/skill-metadata-20260915-103230/.../stdd-delegate-slice/SKILL.md` |
| 唯一真值源五铁律、禁止单文件拷贝式同步 | `~/.workbuddy/skills/multi-copy-truth-source/SKILL.md` |
