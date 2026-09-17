# FSTDD 分布式任务协作 · 首次真实接入 PRD + 开发计划

**日期**：2026-09-17
**场景**：全流程交付（PRD → 开发计划 → 验证与回滚预案）
**参与成员**：产品官（product-reviewer，PRD）+ 质量门神（qa-lead，开发计划/验证/回滚）
**性质**：**本文件是计划，不是执行结果。** 除「§0 事实基线」外，文中所有内容均未发生、尚未执行。
**约束**：本轮只产出文档，**不写任何代码**。

---

## 📌 TL;DR（执行摘要）

- 整体结论：🟡 **条件 Go** —— 工程缺口已闭合，本轮唯一目的是**用 1 台机 + 1 个 agent + 1 个真实子 change 跑完一次闭环**，产出「留或杀」的证据
- 时间盒：**2 小时**；代码增量上限：**30 行 / 1 个接口**
- 阻塞项：**2 个**（agent 注册 0、任务 0）；前置冲突：**1 个**（已有 2 个活跃 change，新立 change 前须先收口）
- 下一步：D哥 过 Gate 1（批准本计划 + 记录手动预估耗时），然后才能开工

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| Go / No-Go | 🟡 **条件 Go** —— 限时验证，不达标即冻结（Kill Criteria K1–K5） |
| 范围 | 1 机 / 1 agent / 1 真实子 change。**不做** 3 机 6 AI |
| 时间盒 | 120 min（P3/P4 风险最高，Buffer 只给这两段） |
| 代码增量上限 | ≤30 行、≤1 个新增接口（超即触发 K3 停止） |
| 唯一可能的新增能力 | `PATCH /tasks/{id}`（任务终态写入口，约 20–30 行）—— 现有路由**没有**终态入口 |
| 前置必须先做 | 将 `mirror-failure-alert` 过 Gate 3 并归档，否则新立 change 会触发 `guard.py` 歧义 |

---

## 0. 事实基线（已发生）vs 计划声明（未发生）

| 项 | 性质 | 内容 |
|---|---|---|
| 三端 sha 收敛 `c11789a`、mirror.log 全 `[OK]`、hub `/health` ok、控制面 14 passed | **已发生** | 本计划 T0 起点 |
| `/nodes` 仅 `fstdd-hub-infra`、`/tasks=[]`、无 task worktree/分支 | **已发生** | 需验证后进入 P1 |
| 隧道打通、节点注册、任务领取、worktree、push、集成、done | **计划** | 一次都没跑过 |
| `PATCH /tasks/{id}` 是否落地 | **计划** | 尚未决策，受 K3 约束 |

> 已建成接口（`tools/fstdd_hub.py` 551 行）：GET `/health` `/nodes` `/tasks` `/messages`；
> POST `/nodes/register` `/nodes/heartbeat` `/tasks` `/tasks/claim` `/messages`。
> `tools/fstdd_git.py`（222 行）：`create-worktree` / `scope-conflicts` / `integrate`。

---

# Part 1 · PRD（产品官）

## 1. 问题定义

**一句话**：中枢能跑但零接入，今天没有任何证据表明「服务器分发任务」优于「D哥 开三个终端手动分工」——本轮用 1 台机 + 1 个 agent + 1 个**真实**子 change 跑完一次闭环，产出这个证据，或当场杀死这个方案。

**解决**：让一个真实开发任务完整走完 `register → claim → worktree → push → integrate → done`，并在主干留下可见改动。

**不解决**：多人效率、并行、冲突、"平台长什么样"。本轮结束时不要求系统更好，只要求**判断有依据**。

## 2. 目标与非目标

### 目标（4 条）

| # | 目标 | 判据 |
|---|---|---|
| G1 | 出现第一个真实 agent 节点 | `GET /nodes` 含 ≥1 条 agent 记录，`node_id` ≠ `fstdd-hub-infra` |
| G2 | 出现第一个真实任务并进入终态 | `GET /tasks` 含 ≥1 条 `status=done` |
| G3 | 真实改动经数据面流回主干 | 本地 / 裸库 / GitHub 三端 sha 相同且 ≠ `c11789a` |
| G4 | 不破坏既有资产 | hub 14 passed 仍全绿；`mirror.log` 无新增 WARN |

### 非目标（比目标更硬，违反即视为本轮失败）

- **不验证** 3 机 6 AI 的任何假设。本轮是单机单 agent，结论不向多机外推。
- **不提升** 吞吐、自动化率、失败恢复优雅度。卡住就人工介入并如实记录。
- **不新增** 除 REQ-M3 标注的 1 个最小接口外的任何能力；不改 `fstdd_hub.py` 既有逻辑。
- **不写** 新文档（闭环记录除外）、不做 README 美化、不整理目录。
- **不迁移** D哥 现有工作方式；本轮结束后默认仍用手动方式，除非 G1–G4 全绿且对照结论明确有利。
- **不追求** 零故障。**暴露故障是本轮的正产出。**

## 3. 用户与场景

| # | 场景 | 流程 | 如果不做会怎样 |
|---|---|---|---|
| S1（主） | 单机单 agent 交付 1 个真实子 change | 本地起 SSH 端口转发 → 注册 `dev-<机器>-<agent>` → 建任务 → 领取 → `create-worktree` 执行 → push → `integrate` → done | 中枢继续以"已建成、零接入"状态挂着，成为持续吸走注意力的沉没成本；下次评审仍在原地打转 |
| S2（对照） | 同一任务用"手动开终端"方式的耗时/心智负担基线 | **T+0 先写下手动完成的预估耗时与步骤数**，闭环后回填实际值 | 无对照就无法回答"值得吗"，项目会靠惯性滑向平台化；K5 失去判据 |
| S3（降级） | 控制面不可用时，仅用数据面完成任务 | 跳过 register/claim，直接 worktree + push 裸库 | 若控制面卡死，无法区分"控制面无用"与"数据面也不通"，会误判整个架构价值 |

## 4. 功能需求（MoSCoW）

| ID | 级别 | 需求 | 依赖接口 | 验收判据 |
|---|---|---|---|---|
| REQ-M1 | **Must** | 注册 1 个真实 agent 节点 | `POST /nodes/register` | `GET /nodes` 返回该节点，`node_id` 符合 `docs/DISTRIBUTED_ACCESS.md` 标识约定 |
| REQ-M2 | **Must** | 创建并原子领取 1 个真实任务 | `POST /tasks` → `POST /tasks/claim` | 首次 claim 返回 `lease_token`；**立即重复 claim 同一 task 失败**（验证原子性） |
| REQ-M3 | **Must** | task worktree 内完成真实改动 → push → ff-only 集成 → 标记 done | `fstdd_git.py create-worktree / integrate` + `git push server` + **新增** `PATCH /tasks/{id}` | 改动在主干可见；三端 sha 相同且 ≠ `c11789a`；`GET /tasks` 该条 done |
| REQ-S1 | Should | 执行期间发 ≥1 次心跳 | `POST /nodes/heartbeat` | `/nodes` 中 `last_seen` 晚于注册时间 |
| REQ-S2 | Should | 领取/完成各留 1 条消息 | `POST /messages` + `GET /messages` | 2 条消息可回读，含 task 关联 |
| REQ-C1 | Could | 集成前跑一次 scope 预检 | `fstdd_git.py scope-conflicts` | 输出无冲突或明确列出冲突 |
| REQ-C2 | Could | 用幂等键重复建任务验证不重复 | `POST /tasks` idempotency_key | 第二次创建返回同一 task_id |

### 唯一新增能力标注（REQ-M3）

| 项 | 说明 |
|---|---|
| 新增内容 | `PATCH /tasks/{id}`，仅支持 `status ∈ {done, abandoned}`，约 20–30 行 + 1 个测试 |
| 理由 | 现有路由**没有任务终态写入口**。若无 done，闭环判定只能靠人脑记忆，直接违反"成功指标可观测"原则 |
| 代价 | ≈30 分钟，占时间盒 25%；会使既有 14 passed 变成 15+ |
| 降级路径 | T+60min 仍未完成 → **放弃新增**，改用 `POST /messages` 发一条 `type=done` 事件作为终态留痕，REQ-M3 判据相应改为"消息可读回" |

## 5. 关键流程（首个任务时序）

| 步 | 动作 | 命令层示意 | 出口判据 |
|---|---|---|---|
| T0 | 前置核验 | `git ls-remote server HEAD` / `curl localhost:8788/health` | 三端 `c11789a`；health ok |
| T1 | SSH 端口转发 | `ssh -L 8788:127.0.0.1:8788 -N fstdd-hub` | `curl localhost:8788/health` 通 |
| T2 | 注册节点 | `curl -X POST localhost:8788/nodes/register -d '{node_id, host, agent}'` | `/nodes` 出现该条 |
| T3 | 建任务 | `curl -X POST localhost:8788/tasks -d '{title, scope, idempotency_key}'` | 返回 `task_id` |
| T4 | 领取 | `curl -X POST localhost:8788/tasks/claim -d '{node_id, task_id}'` | 返回 `lease_token`；重复调用失败 |
| T5 | 建 worktree | `python tools/fstdd_git.py create-worktree --task <id> --baseline c11789a` | worktree 路径存在，`git -C <wt> status` 干净 |
| T6 | 执行真实改动 | 见下表候选，commit 到 task 分支 | `git -C <wt> log -1` 为真实改动，非占位 |
| T7 | 推送 task 分支 | `git -C <wt> push server HEAD:<task-branch>` | `git ls-remote server <task-branch>` 有值 |
| T8 | 串行集成 | `python tools/fstdd_git.py integrate --ff-only` | 主干前进，无 merge commit |
| T9 | 回推数据面 | `git push server master` → 等 `post-receive` | GitHub 收到；`mirror.log` 新增 `[OK]` |
| T10 | 标记 done | `curl -X PATCH localhost:8788/tasks/<id> -d '{"status":"done"}'` | `GET /tasks` 显示 done |
| T11 | 收尾复核 | `hub_healthcheck.py` + 三端 sha 对比 + 测试 | 全绿；sha 一致 |

### T6 真实改动候选（选 1，判据：真实、可合入、非占位）

| 候选 | 说明 |
|---|---|
| A | 修正 `docs/DISTRIBUTED_ACCESS.md` 中接入步骤经实测发现的一处不准确（**推荐**：顺带验证文档可执行性） |
| B | 给 `tools/hub_healthcheck.py` 补一个断言（如校验 `/nodes` 至少 1 条 agent） |
| C | 任意一个当前顺位最高的真实小改动 |

## 6. 成功指标（2 小时内可观测）

| 指标 | 阈值 | 观测方式 |
|---|---|---|
| 闭环完成 | T+120min 内走完 T1–T11 | 人工计时 |
| agent 注册 | 0 → **≥1** | `GET /nodes` |
| 任务终态 | 0 → **≥1 条 done** | `GET /tasks` |
| 数据面真实流量 | 三端 sha 一致且 **≠ `c11789a`** | `git rev-parse` + `git ls-remote` |
| GitHub 镜像 | `mirror.log` 新增 ≥1 条 `[OK]`，**0 WARN** | 服务器日志 |
| 回归 | 控制面测试 **14 passed → 全绿**（新增接口则 15+） | 测试命令 |
| 代码增量 | **≤30 行** | `git diff --stat` |
| 对照数据 | 手动预估耗时 vs 实际耗时（**记录，不设阈值**） | S2 表 |
| 卡点记录 | 每个阻塞点 + 排查耗时 | 复盘表 |

## 7. Kill Criteria（触发即执行，不容讨论）

| ID | 触发条件 | 立即动作 |
|---|---|---|
| **K1** | T+120min 未达成 REQ-M3 | **冻结**。删除 worktree 与 task 分支，主干回到 `c11789a`，写 10 行复盘，change 标 `parked` |
| **K2** | 任一单点（端口转发 / 路由 / 领取）排查累计 **>30min** | **降级**：放弃控制面，走 S3 仅用数据面完成任务；复盘中记"控制面本次未通过" |
| **K3** | 需要新增 **>1 个接口** 或代码增量 **>30 行** | **停止**。判定为过度工程信号，回到评审，不继续施工 |
| **K4** | 数据面受损：裸库 HEAD 回退 / `mirror.log` 出现 WARN / 三端 sha 不收敛 | **立即停止一切操作**，先恢复到 `c11789a` 基线再复盘；本轮直接判负 |
| **K5** | 闭环完成后：**手动预估耗时 ≤ 实际耗时 × 1.5** | 判定中枢本轮**无收益**；保留已建成能力，**冻结一切平台化投入**至少到第 2 个真实节点出现 |

## 8. 风险与开放问题

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| SSH 端口转发不通，卡在 T1 | 中 | 中 | 动手前先单独验证 `curl localhost:8788/health`，不通就走 S3 降级，不排查超过 30min（K2） |
| 仓库外 worktree 与 FSTDD CLI 假设不符 | 中 | 高 | T5 后立刻 `git -C <wt> status/log` 确认；异常即刻 K2 |
| SQLite 单点、无备份触发点 | 低 | 中 | T0 前跑一次 `tools/hub_backup.sh` |
| **本轮根本不验证并发**：单 agent 下原子性价值有限 | 高 | 中 | **承认并接受**。原子性验证明确推到第 2 个节点（见 Won't） |
| hub 在"长任务 + 网络抖动 + worktree 长期占用"下的行为 | 未知 | 高 | 这正是本轮主要价值，见下 |

### 未知未知（诚实说明）

我们对 hub **在真实链路下的行为是完全零观测的**。`14 passed` 测的是接口与单元语义，不是"一个真人 + 一个真任务 + 一次真 push"的端到端行为。首个真实任务极可能暴露我们**完全没有预料到**的失败模式——数据面与控制面状态不一致、worktree 生命周期与任务生命周期不同步、lease 语义在长任务下失效等。**如果 2 小时内冒出一个我们没列进上表的失败模式，那不是意外，那是本轮最大的收获**，请优先记录它而不是修复它。

### 开放问题

| # | 问题 | 处理 |
|---|---|---|
| Q1 | `node_id` 是否需在文档中固化格式？ | 本轮按现有约定执行，不顺带改文档结构 |
| Q2 | done 之后任务如何归档 / 是否清理？ | **不处理**。留到第 2 个节点 |
| Q3 | hub 在无真实使用时是否继续保留服务？ | 由 K5 结论决定 |
| Q4 | 任务与 worktree 的生命周期谁管？ | 本轮人工管，明确不自动化 |

## 9. 明确不做清单（Won't）

| # | 不做项 | 原因 | 重开条件 |
|---|---|---|---|
| W1 | 6 AI 并行调度 | 需求是"未来可能有"，不是"今天在痛苦地绕" | 出现 ≥2 个真实节点且 K5 判定有收益 |
| W2 | 节点对等与选举 | 单节点下无意义，属纯平台建设 | 出现 ≥3 个真实节点 |
| W3 | 消息系统（agent 间通信） | 只有一个 agent 时是空转 | 出现 ≥2 个真实节点 |
| W4 | 跨机 worktree 编排 | 无跨机事实，纯推测性设计 | 出现跨机协作的真实任务 |
| W5 | 租约过期回收 | 无并发竞争即无过期场景 | 第 2 个真实节点上线时 |
| W6 | 心跳机制打磨 | 单节点心跳是自证存在，无信息量 | 第 2 个真实节点上线时 |
| W7 | 幂等键验证与强化 | 单创建者无幂等压力 | 出现重复提交的真实事故 |
| W8 | 鉴权 / 权限模型 | 单一用户，控制面仅听 127.0.0.1 | 出现非 D哥 的使用者 |
| W9 | Web UI / 看板 / 可视化 | 一人使用，curl 足够 | 使用者 ≥2 且需要共享视图 |
| W10 | 自动重试 / 任务依赖图 / 优先级队列 | 单任务下全是过度工程 | 任务数稳定 ≥10/周 |
| W11 | 性能与压测 | 无并发，无基线 | 出现真实并发 |
| W12 | 新文档、目录整理、README 美化 | 本轮产出只有证据 | 闭环完成后另行评估 |

## 10. 决策门

闭环完成后立即回答三个问题，任一为"否"则执行 K5：

| # | 问题 |
|---|---|
| 1 | 实际耗时是否明显优于手动预估（≤ ×1.5 为否）？ |
| 2 | 中枢是否提供了手动方式**做不到**的东西（不是"更好"，是"做不到"）？ |
| 3 | D哥 明天是否**自愿**再走一遍这条链路？ |

> **产品官提醒**：本轮真正的交付物不是那个跑通的任务，而是**§8 里你现在还看不见的那个失败模式**，以及第 3 问的诚实答案——如果跑通之后你明天并不想再走一遍，那这次闭环就是一次成功的证伪，请立刻按 K5 冻结，别把"跑通了"误当成"值得做"。

---

# Part 2 · 开发计划（质量门神）

## P. 阶段划分（P0–P5）

| 阶段 | 目标 | 进入条件 | 产出物 | 退出判据（可执行/可证伪） |
|---|---|---|---|---|
| **P0 基线冻结** | 拿到可对比快照，隔离在途改动 | 三端可连通 | `baseline.txt`（sha / nodes / tasks / worktree 数 / 活跃 change 列表）+ **T0 手动预估耗时（分钟）** | `git ls-remote server master` 首 7 位 == `c11789a`；`curl -s localhost:8788/health` 含 `"ok"`；`git worktree list` 行数 == 1；`C:/Python311/python.exe -m pytest upstream/tests -q` 末行 == `14 passed`；预估耗时已写入文件 |
| **P1 隧道 + 注册** | L2 控制面对本机可见 | P0 退出 | 隧道进程、新 node 条目 | `curl -s localhost:8788/nodes \| grep -c <node_id>` == 1；**同一 register 命令连跑两次后该计数仍 == 1**（SSH 双执行坑的端状态验证） |
| **P2 建任务 + 原子领取** | 拿到 `lease_token` | P1 退出 | 1 条 task + lease | `/tasks` 中该 task 同时含 `claimed_by=<node_id>` 与 `lease_token`；重复 claim 第二次返回空/非 200 且不改 `claimed_by` |
| **P3 worktree + 真实改动 + push 分支** | 产生一个真子 change | P2 退出 | task worktree、task 分支、远端分支 | `git -C <wt> status --porcelain` 非空后转空（commit 后）；`git ls-remote server task/<id>` 存在且 sha == `git -C <wt> rev-parse HEAD` |
| **P4 ff-only 集成 + push 裸库 + 镜像** | 数据面推进一次 | P3 退出 | master 前进 1 commit、mirror 成功 | `git ls-remote server master` == P4 前 `git rev-parse HEAD`；`grep -c '\[OK\]' mirror.log` 增量 ≥1 且**新增行无 WARN/ERROR**；`git ls-remote origin master` == server 值（三端收敛） |
| **P5 标记 done + 收尾** | 闭环收口 | P4 退出 | task 终态、清理、复盘 | `/tasks` 中该 task 状态为终态；`git worktree list` 回 1 行；`git status --porcelain` 与 P0 快照**仅差本 change 预期文件**；`verify_eol.py --fix` 退出码 0；14 passed 仍绿 |

## V. 各阶段验证清单（命令 → 期望输出）

```bash
# P0
git ls-remote server master          # 期望：c11789a... 开头（禁止 git remote -v，会回显 token）
curl -s localhost:8788/health        # 期望：含 "ok"
curl -s localhost:8788/nodes         # 期望：仅 1 条 fstdd-hub-infra
curl -s localhost:8788/tasks         # 期望：[]（ASCII 方括号）
git -C D:/tools/FSTDD/stdd-repo worktree list   # 期望：1 行
ls .fstdd/changes/                   # 期望：mirror-failure-alert / time-baseline 两个活跃项
C:/Python311/python.exe -m pytest upstream/tests -q   # 期望：14 passed

# P1（隧道，另开窗口常驻）
ssh -L 8788:127.0.0.1:8788 -N fstdd-hub
curl -s localhost:8788/nodes | grep <node_id>    # 期望：命中 1 次

# P3（CLI：cwd 必须是 D:/tools/FSTDD/stdd-repo）
cd D:/tools/FSTDD/stdd-repo
C:/Python311/python.exe upstream/bin/fstdd ...   # 期望：不出现 "canonical not found"
git -C <wt> status --porcelain                   # commit 后期望：空
git ls-remote server task/<id>                   # 期望：非空

# P4
git ls-remote server master ; git ls-remote origin master   # 期望：两者相同
tail -n 20 mirror.log                            # 期望：全 [OK]，无 WARN/ERROR

# P5
C:/Python311/python.exe upstream/scripts/verify_eol.py --fix  # 期望：exit 0
git worktree list                                # 期望：1 行
```

**关键规则**：远端每一步都用**端状态**验证（上面每条 `ls-remote` / `curl`），命令回显一律不作为通过依据（SSH 双执行坑）。

## T. 测试策略

| 层 | 本轮做什么 | 判据 |
|---|---|---|
| 静态 | `python -m py_compile` 于 `tools/fstdd_hub.py`；`grep -c 'self.path ==' tools/fstdd_hub.py` 记**接口数基线**；`git diff --stat c11789a` 记**代码增量基线** | 退出码 0；两个基线写入 P0 快照（K3 要用） |
| 单元 | 现状 14 passed 作为**回归基线**，P0 与 P5 各跑一次；若新增 `PATCH /tasks/{id}`，补 5 例：未知 id→404、重复 done→200 且 body 同、缺 lease→409、终态回退→409、双 claim→第二个失败 | 两次均为 `14 passed`（新增时为 19） |
| 集成 | 走**真实 hub + 真实隧道**，不 mock：register→task→claim→done 串行 | 与单元断言一致 |
| E2E | 只有本轮这一次闭环，不自动化、不重跑 | P5 退出判据全绿 |
| **变异测试** | **不做**（2h 盒内收益为负）。若 P5 提前完成且余量 >20min，仅对新增 PATCH 做 2 个手工变异（去掉 404 分支 / 去掉幂等键判断），确认测试转红；10min 未完成即放弃 | 变异后至少 1 条用例 fail |

## R. 回滚预案（分场景）

| 场景 | 恢复动作 | 恢复后校验 |
|---|---|---|
| **worktree 残留** | `git worktree remove --force <wt>` → `git worktree prune` | `git worktree list` == 1 行；`ls .fstdd/worktrees` 无残留目录 |
| **task 分支残留** | 先 `git branch --contains task/<id>` 确认未被 master 引用 → `git branch -D task/<id>`；`git push server --delete task/<id>` | `git ls-remote server task/<id>` 为空；`git branch --list task/*` 为空 |
| **裸库 HEAD 被误推进** | **只用** `git push --force-with-lease server c11789a:master`（禁用 `--force`） | `git ls-remote server master` == `c11789a`；**随后必须**确认 mirror 已把该 reset 镜像到 origin，`git ls-remote origin master` 同值 |
| **SQLite 脏数据** | `systemctl stop fstdd-hub` → 用 `hub_backup.sh` 最近备份整库回滚（优先）→ 启服务；仅当无备份时按 `task_id` 定点删行 | `/nodes` 仅 `fstdd-hub-infra`；`/tasks` == `[]`；`/health` ok |
| **镜像失败** | 立即停止一切写操作（K4）；比对 server vs origin sha；重跑一次到裸库的 push 触发 post-receive；仍失败则只恢复数据面，本轮判停 | `tail mirror.log` 全 `[OK]` 且两端 sha 相同，才允许继续 |
| **K1 全量冻结** | 按上表 1+2 清理 → 裸库回 `c11789a`（force-with-lease）→ change 标 parked → 写复盘 | 与 P0 基线快照逐项比对一致 |

## F. FSTDD 流程如何套用

**结论：新立 change**（建议 `2026-09-17-distributed-first-closure`）。理由：已归档的 `2026-09-17-distributed-task-coordination` 不可再推进相位；本轮是新交付单元（"首个闭环"），复用会污染归档历史。

**⚠️ 前置冲突（必须 P0 处理）**：当前已有 2 个活跃 change（`mirror-failure-alert` 待 Gate 3、`time-baseline` 在 understand）。再立第三个可能触发 `guard.py:_find_active_change()` 的歧义/误命中。处置顺序：**先把 `mirror-failure-alert` 过 Gate 3 并归档（相位显式 advance 到终态再 archive），再立本 change**；否则本轮直接判停或降级。

| Gate | 本轮判据（可证伪） |
|---|---|
| Gate 1 UNDERSTAND→SPEC | 本计划获批；K1–K5 写入 change 文档；T0 预估耗时已记录 |
| Gate 2 SPEC→BUILD | P0 快照完成（接口数 / 代码增量基线已存）；`PATCH /tasks/{id}` 是否需要已决策，且预估 ≤30 行、≤1 接口 |
| Gate 3 BUILD→DELIVER | P5 全绿 + 14 passed + 三端 sha 收敛 + mirror 全 `[OK]` |
| DELIVER→归档 | **必须显式 `phase advance` 到终态后再 archive**；归档后 `ls` 确认目录不在活跃区（防 `_find_active_change()` 命中） |

## K. 已实测环境坑（计划必须规避）

| 坑 | 缓解 |
|---|---|
| **SSH 命令被执行两次** | 所有远端写操作幂等（固定 `node_id`、幂等键、`lease_token`）；**只用端状态验证** |
| remote 写成裸主机名 → Permission denied | 统一 `fstdd-hub:/home/ubuntu/fstdd-git/stdd-repo.git`；`IdentityFile` 用 `D:/id_ed25519` |
| CLI cwd 错误 → canonical not found | 所有 CLI 调用前 `cd D:/tools/FSTDD/stdd-repo`；禁止在 `upstream/` 下跑 |
| 托管 python 无 pytest | 固定 `C:/Python311/python.exe -m pytest` |
| CLI 写 CRLF | P3/P5 结束各跑一次 `verify_eol.py --fix` |
| `fstdd status` 文件列不可信 | 进度判断一律 `ls` change 目录，不信 status |
| origin URL 内嵌 token | 全程禁用 `git remote -v`；只用 `git ls-remote <name>`；日志不落 URL |
| 在途未提交改动（另一 change） | P0 快照 `git status --porcelain`，P5 比对差异集，避免夹带提交 |
| 归档漏推相位 | 归档后立刻 `ls` 活跃 change 目录复核 |
| force push 破坏镜像 | 只用 `--force-with-lease`，且 reset 后必须复核 origin |

## S. 排期（120 min）

| 阶段 | 分钟 | 说明 |
|---|---|---|
| P0 基线冻结 | 10 | 含记录 T0 预估耗时（K5 唯一数据来源） |
| P1 隧道 + 注册 | 15 | |
| P2 建任务 + 领取 | 15 | |
| P3 worktree + 改动 + push | 35 | 最大不确定性段 |
| P4 集成 + push + 镜像 | 25 | 数据面操作，慢一点 |
| P5 done + 收尾复核 | 15 | 含 verify_eol、14 passed 复跑 |
| **Buffer** | **5** | 只给 P3/P4 追加 |

**Kill Criteria 触发方式**：T+60 / 90 / 120 三次硬检查点。K1 看墙钟；K2 每次进入排查即记开始时刻、累计 >30min 降级为纯数据面；K3 用 `git diff --stat c11789a` 行数与接口 grep 计数对比 P0 基线；K4 在 P4 每步前后比对 sha 与 mirror.log；K5 用 P0 记录的预估 vs P5 实际耗时。

**分工建议**：D哥 一人执行，建议至少留一名"只读观察员"角色负责在 T+60/90 朗读判据——单人执行最容易漏掉 K1 的墙钟。

---

# Part 3 · 主理人汇编

## ✅ 行动清单

| # | 行动 | 负责方 | 紧急度 | 期望完成 |
|---|------|--------|--------|---------|
| 1 | **过 Gate 1**：批准本计划；在 P0 前**先写下手动完成的预估耗时**（K5 唯一数据来源，漏了整个实验失效） | D哥 | P0 | 开工前 |
| 2 | **先收口 `mirror-failure-alert`**（过 Gate 3 → 显式 phase advance → 归档），解除活跃 change 冲突 | D哥 + 执行 agent | P0 | 立新 change 前 |
| 3 | 决策 `PATCH /tasks/{id}` 是否需要（无它则 done 无终态留痕；有它则占 25% 时间盒） | D哥 | P0 | Gate 2 前 |
| 4 | 新立 change `2026-09-17-distributed-first-closure`，按 P0–P5 执行，严守 120min 时间盒 | 执行 agent | P1 | Gate 1 后 |
| 5 | 闭环后回答「决策门三问」，任一为"否"即按 K5 冻结平台化投入 | D哥 | P1 | 闭环当天 |

## ⚠️ 待完善 / 已知局限

- **本文件全是计划，零执行**。所有判据未经实测，首次执行大概率会暴露 §8「未知未知」里的失败模式。
- `time-baseline` change 的 `.fstdd.yaml` 显示 `current_phase: understand` 且四个相位全 `pending`，但历史记录称其已过 Gate 2 进入 Phase 3 —— **存在相位状态不一致**，需在处理前置冲突时一并核实，否则同样会干扰 `guard.py` 判定。
- 本轮**不验证并发**，原子性/租约/心跳在单 agent 下价值有限，结论不可外推到 3 机 6 AI。
- 服务器 SQLite 为单点，回滚依赖 `hub_backup.sh` 备份有效性，**执行前需确认备份存在**。
- `origin` remote URL 内嵌明文 token，本文档及所有日志均不得回显。

## 📚 成员产出索引

- gstack-product-reviewer（产品官）：PRD 全文 —— 问题定义 / 目标与非目标 / 3 场景 / MoSCoW 7 条 REQ / 成功指标 / K1–K5 / Won't 12 条 / 决策门三问
- gstack-qa-lead（质量门神）：开发计划 —— P0–P5 阶段划分 / 命令级验证清单 / 四层测试策略 / 6 场景回滚预案 / FSTDD Gate 套用 / 10 条环境坑 / 120min 排期

---

> 本报告由软件工坊 AI 协作生成，关键决策请由工程负责人复核。
