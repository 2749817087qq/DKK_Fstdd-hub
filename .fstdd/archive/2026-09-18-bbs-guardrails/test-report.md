# 验证报告 — 2026-09-18-bbs-guardrails

**日期**：2026-09-18
**来源**：QA 独立验证（change `2026-09-18-task-thread-bbs`）给出的两个 🟠 级放行条件
**结论**：✅ 通过（44 passed，6/6 变异体杀死，真实服务端四道护栏全绿）

---

## 1. 修复清单

| # | QA 编号 | 问题 | 修复 | 位置 |
|---|---|---|---|---|
| F1 | QA F1 | `ack_message` 零测试覆盖；任何注册节点都能 ack 任何 BBS 留言（等同删帖） | 服务端收窄为「仅作者可 ack」+ 客户端补 3 条测试 | `fstdd_hub.py:_ack` / `test_hub_client.py` |
| F2 | QA F2 | `messages.task_id` 外键声明了但从未生效，孤儿留言静默丢失 | `connect_db` 加 `PRAGMA foreign_keys=ON` + `_message` 显式校验 | `fstdd_hub.py:125 / :519` |
| F3 | QA F3 | 看板只替换 LF，残留 CR 破坏版面 | 同时归一 CRLF/CR/LF（用 `chr()` 构造，避开跨层转义） | `hub_board.py:244` |
| F4 | QA F4 | 长留言硬截无提示 | 超 68 字符补 `...` | `hub_board.py:250` |
| F5 | QA F5 | `from_node_id` 缺失渲染成字面 `None` | 统一用 `-`（与 `_iso()` 风格一致） | `hub_board.py:243` |

---

## 2. 一个关键的设计冲突（差点改错）

**第一版方案**：完全禁止 ack 带 `task_id` 的留言。
**结果**：`tests/test_fstdd_hub.py::test_failure_and_blocker_message_ack` 转红。

原因：既有的 **blocker 工作流**正是「任务失败 → 发 blocker 留言 → 发起者自己 ack 表示已处理」，
其数据形态（task_id 非空 + to_node_id 空）与 BBS 讨论串**完全相同，无法区分**。
一刀切禁止会摧毁这条合法工作流。

**最终方案**：收窄为「仅作者可 ack」——
- 非作者 ack 任务讨论串 → 400（解决 QA 的核心担忧：删帖权限不再对所有人开放）
- 作者 ack 自己的留言 → 200（保留既有 blocker 工作流）
- 无 task_id 的广播 notice → 200（告警通道不受影响）

---

## 3. 变异测试（6 个，全杀）

| 编号 | 注入的缺陷 | 结果 |
|---|---|---|
| B7 | `ack_message` 路径 `/ack` → `/ack2` | ✅ 杀死 |
| B8 | `ack_message` payload 漏传 `node_id` | ✅ 杀死 |
| B9 | 去掉 `ack_message` 的 node_id 必填校验 | ✅ 杀死 |
| M-TASK | 去掉 `_message` 的 task_id 存在性校验 | ✅ 杀死 |
| M-AUTHOR | 放行非作者 ack | ✅ 杀死 |
| M-FK | 去掉 `PRAGMA foreign_keys=ON` | ✅ 杀死（补断言后） |

**M-FK 的插曲**：首次注入时**存活**——因为上层显式校验先拦住了孤儿留言，
FK 根本没机会触发，**开没开在行为上不可观测**。补了一条直接断言
`PRAGMA foreign_keys == 1` 的测试让它可观测，复验转红。

> 值得记住：defense in depth 的**第二层防线天然不可观测**，
> 除非直接断言它本身的状态，否则变异测试会误报为「存活」。

---

## 4. 真实服务端验证（部署后，非 mock）

| # | 操作 | 期望 | 实际 |
|---|---|---|---|
| 1 | 既有数据 | 不受影响 | tasks 2 条全 done；3 条演示留言 `acked_at` 全为空 ✅ |
| 2 | `POST /messages` 带不存在的 task_id | 400 | `400 task not found` ✅ |
| 3 | 他人身份 ack 讨论串 | 400 且留言仍在 | `400 only the author can ack...`，留言仍 3 条 ✅ |
| 4 | 无 task_id 的广播 notice ack | 200 | 200 ✅（告警通道正常） |
| 5 | 收尾 | 数据无污染 | tasks 仍 2 条，演示留言仍 3 条未 ack ✅ |

**部署动作**：备份 `fstdd_hub.py.pre-guardrails` → 替换 → `py_compile` → `systemctl restart` → `active`。

---

## 5. 测试与回归

- 测试数：37 → **44 passed**（+3 服务端护栏 +3 客户端 ack +1 FK 可观测性 +1 孤儿留言）
- 路由数：仍 **13**（本 change 只加校验、不加端点）
- 看板：实跑正常

## 6. 顺带完成（D哥 指示）

- **备份提频**：daily → **hourly**（下次触发 03:00 整点），保留期 14 天 → 3 天（避免 336 份堆积），
  并补清孤儿 `-wal`/`-shm` 残留。已立即触发一次 —— **最新备份终于含 2 条 done 任务**
  （此前 4 份备份全 `tasks=0`，闭环状态无保护）。
- **备份脚本资产化**：`hub_backup.sh` 此前只在服务器上，已拉回 `tools/` 版本化。

## 7. 未完成

- 误建仓库 `Sidneywu1986/DKK_Fstdd-hub` **删除失败**：HTTP 403 `Must have admin rights`。
  本机该账号的凭据无 `delete_repo` 权限，且无 `~/.git-credentials` 可换凭据。需 D哥 网页端手删。
