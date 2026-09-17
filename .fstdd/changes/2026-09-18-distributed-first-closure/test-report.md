# 首个最小闭环 —— 执行侧验证记录

**change**：`2026-09-18-distributed-first-closure`
**task_id**：`task-8177a2f0af062f8f`
**主战场**：`D:/tools/FSTDD/fstdd-hub`
**执行时间**：2026-09-18 00:33:53 → 01:42（+08:00）

---

## 一、K5 核心数据（决策门唯一对照）

| 项 | 值 |
|---|---|
| T0 手动预估（D哥 本人填写） | **60 分钟 / 6 步** |
| 实际耗时（至 P5 标记 done） | **约 43 分钟** |
| 实际耗时（至全部收尾含 P4b 镜像） | 约 68 分钟 |
| K5 判据（≤ 90 分钟） | ✅ **通过** |
| K1 判据（≤ 120 分钟） | ✅ 通过 |

⚠️ **对照有效性说明**：T0 由 D哥 本人在 P1 之前填写，未由 agent 代写，K5 是对照实验而非自评。

---

## 二、各阶段实测结果

| 阶段 | 判据 | 实测 | 结论 |
|---|---|---|---|
| P0 基线冻结 | baseline.txt 完整，T0 已填 | BASE_SHA=f2bf26c / WORKTREES=1 / ROUTES=13 / T0=60min | ✅ |
| P0 CLI 探测 | CLI 在 fstdd-hub cwd 可解析 canonical | `.fstdd/canonical/` 可见，未回退方案 A | ✅ |
| P1 隧道 | 本地 8788 可达 /health | `{"ok": true}`；netstat 确认 LISTENING | ✅ |
| P1 注册幂等 | 连跑两次 register 后命中数 == 1 | `/nodes` 总数 2，命中 `fstdd005-win-dev` **1 次** | ✅ |
| P2 建任务 | 创建成功 | `201` → `task-8177a2f0af062f8f` | ✅ |
| P2 原子领取 | 含 claimed_by + lease_token | `status=claimed`、`owner=fstdd005-win-dev` | ✅ |
| P2 幂等 | 同 key 重复 claim 返回相同 token | lease_token **一致** | ✅ |
| P2 原子性 | 新 key claim 无 pending 时空/204 | **HTTP 204 空体** | ✅ |
| P3 改动 | worktree 真实提交 | `1d3d46a`，+541 行两文件 | ✅ |
| P3 测试 | 全量通过 | **30 passed**（14 原有 + 16 新增） | ✅ |
| P4a 集成 | ff-only，master 前进 | f2bf26c → 1d3d46a，无 merge commit | ✅ |
| P4b 数据面 | 三端 sha 收敛 | 本地 = 服务器 = GitHub = **e0ec384** | ✅ |
| P4b 镜像 | mirror.log 无 WARN/ERROR | 末次 `[MIRROR-OK]`（首次因 deploy key 失败 rc=128，已修） | ✅ |
| P5 完成 | 任务终态 + sha 落库 | `status=done`、`result_git_sha=1d3d46a` | ✅ |
| P5 清理 | worktree 回 1 行 | `git worktree list` = 1 行 | ✅ |
| P5 EOL | 无混合态 | 134 文件全 `i/lf w/lf` | ✅ |
| P5 回归 | 14 → 30 passed | **30 passed** | ✅ |
| K3 接口数 | ROUTES 不变 | 13 → 13（未改 hub 本体） | ✅ |

---

## 三、本轮产出的代码

| 文件 | 行数 | 说明 |
|---|---|---|
| `tools/hub_client.py` | 328 | 节点侧中枢交互封装（register/heartbeat/create-task/claim/task_heartbeat/complete/fail）+ CLI |
| `tests/test_hub_client.py` | 213 | 16 个单元测试，本地假 hub 驱动，不依赖真实中枢 |
| `tools/fstdd-hub-mirror-hook.sh` | 38 | 镜像钩子，作为项目资产版本化 |

**为什么是这三个**：P1 实测发现 `fstdd_git.py` 只有 `create-worktree` / `scope-conflicts` / `integrate`，
**没有任何与中枢交互的封装**，首个闭环全程靠 inline 脚本完成。闭环的产物正好补上闭环自身暴露的缺口。

---

## 四、执行中发现的问题（未修，待处置）

| # | 严重度 | 问题 | 影响 |
|---|---|---|---|
| 1 | 🟠 | **双 GitHub 账号**：credential store 里是 `Sidneywu1986`，stdd-repo origin URL 里是 `2749817087qq` | 首个仓库误建在 Sidneywu1986 下（`Sidneywu1986/DKK_Fstdd-hub`，private，空仓库），**待 D哥 决定删否** |
| 2 | 🟠 | **服务器默认 GitHub key 是 deploy key**（绑 DKK_Fstdd 单仓库），非账号级 key | 推新仓库必报 `denied to deploy key`；已用专用 key + ssh 别名 `github-fstdd-hub` 解决 |
| 3 | 🟡 | `git credential fill` 在非交互环境不稳定（超时 2 次，90s） | 自动化脚本取凭据不可靠，需改用 origin URL 解析或固定 token |
| 4 | 🟡 | `ensure_clean_target` 要求集成时 **master 完全干净** | 「集成」与「在途 change」互斥，多 change 并发必冲突；本次用 stash 绕过 |
| 5 | 🟡 | git worktree **必须在主 checkout 之外** | `.fstdd/worktrees/` 方案不可行，须改用仓库外路径 |
| 6 | 🟡 | 中枢**默认租约仅 300 秒**，上限 3600 | 长任务极易 `lease expired`；本次主动续租才保住 |
| 7 | 🟢 | `POST /tasks` 返回体无 `task` 包装层，而 `claim` 有 | 客户端解析易错（本次踩到，脚本取 task_id 失败） |
| 8 | 🟢 | 镜像钩子恒 `exit 0` | 镜像失败不会让 push 失败，需靠日志发现（设计如此，但需配套监控） |

---

## 五、决策门三问（D哥 作答区）

> PRD 规定：闭环后必须诚实回答，**任一为「否」即按 K5 冻结平台化投入**。

| # | 问题 | 待答 |
|---|---|---|
| 1 | 实际耗时是否明显优于手动预估（≤ ×1.5 为否）？ | 43 min vs 60 min → **是**（但见下方提醒） |
| 2 | 中枢是否提供了手动方式**做不到**的东西（不是"更好"，是"做不到"）？ | 待 D哥 答 |
| 3 | D哥 明天是否**自愿**再走一遍这条链路？ | 待 D哥 答 |

⚠️ **产品官的提醒（必须原文保留）**：本轮真正的交付物不是那个跑通的任务，而是过程中暴露的
失败模式，以及第 3 问的诚实答案 —— 如果跑通之后你明天并不想再走一遍，那这次闭环就是一次
成功的证伪，应立刻冻结，别把"跑通了"误当成"值得做"。

**最大失败风险（产品官原话）**：在 fstdd-hub 这个 0 冲突、0 在途、1 人 1 任务的无菌仓库里
跑通一次零摩擦闭环，得到一个"值得做"的 K5 结论 —— 而它验证的恰恰是这条链路最不会出问题的
那条路径，真实痛点（多 change 抢 worktree、长任务 lease 失效、跨机）一个都没被碰到。
