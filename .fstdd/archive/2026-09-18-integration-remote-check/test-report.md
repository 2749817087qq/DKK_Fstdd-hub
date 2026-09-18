# 验证报告 — 2026-09-18-integration-remote-check

**变更**：集成准入只检查本地状态 —— 本地干净不等于可以集成，推送时才炸且已分叉
**日期**：2026-09-18
**基线**：`93f9dac`
**结论**：通过（双机夹具全流程验证，54 passed）

---

## 1. 发现路径

第三轮聚焦多机集成。先写探针实测，不凭代码推断。

探针用**裸库 + 两个 clone** 模拟两台机器（不走真实远端，因此不受本机 GitHub
代理时段性故障影响）：

```
A: task/a 提交 -> 集成 -> 推送成功，远端 master 前进到 28b995c
B: 本地 master c6d4c34，本地 origin/master 也还是 c6d4c34（未 fetch）
   远端真实 master 已是 28b995c，但 B 不 fetch 就看不到
B 集成 -> ✅ 本地成功 {before: c6d4c34, after: 68b7ca78}
   （ensure_clean_target 给了绿灯）
B 推送 -> rc=1
   ! [rejected] master -> master (fetch first)
```

**B 的本地 master 已与远端分叉，必须手动 rebase 才能重来。**

对照：B 若在集成前 fetch，可立刻发现自己落后 1 个提交 —— 集成前就能拦住。

补充事实：`tools/fstdd_git.py` 全文**没有任何** fetch / pull / ls-remote，
整个模块对远端状态完全无感知。

---

## 2. 问题定性

不在于「最终会失败」，而在于**失败得太晚且状态已脏**：

| | 旧行为 | 期望 |
|---|---|---|
| 失败时机 | 推送时 | 集成前 |
| 失败时本地状态 | master 已前进、与远端分叉 | 未改动 |
| 恢复成本 | 手动 rebase | pull 后重跑 |

集成阶段报"成功"把人骗过去，等推送时才炸。

**根因**：`ensure_clean_target` 的准入检查缺少远端视角 —— 本地干净是**必要条件**，
不是**充分条件**。

---

## 3. 修复

| # | 修复 | 位置 |
|---|---|---|
| 1 | 新增 `ensure_target_up_to_date()`：集成前 fetch，落后则拦下并给出明确指引 | `fstdd_git.py` |
| 2 | `ensure_clean_target` / `integrate_task_branch` 增加 `check_remote`（默认 True） | `fstdd_git.py` |
| 3 | 无 upstream 的纯本地仓库直接放行 —— 不破坏离线与单机使用 | `fstdd_git.py` |
| 4 | fetch 失败时**拒绝**集成（远端状态未知，不假装已知），提示可显式绕过 | `fstdd_git.py` |
| 5 | fetch 带 `GIT_TERMINAL_PROMPT=0`，防止卡在交互式凭据输入 | `fstdd_git.py` |
| 6 | CLI 增加 `--no-check-remote` | `fstdd_git.py` |

**设计取舍**：fetch 失败时选择 fail-closed（拒绝）而非 warn-and-continue。
理由：集成后再失败代价更高（本地已分叉）。但提供显式开关，离线场景可绕过。

---

## 4. 测试

**51 → 54 passed**（新增 3 条，既有 51 条无回归）

| 测试 | 守卫内容 |
|---|---|
| `test_integration_is_rejected_when_local_target_is_behind_remote` | 落后远端被拦 + **本地 master 未被改动** |
| `test_check_remote_false_allows_offline_integration` | 显式绕过可用 |
| `test_repo_without_upstream_is_not_blocked` | 纯本地仓库不受影响 |

新增 `make_remote_pair` 双机夹具（裸库 + 两个 clone），后续多机场景可直接复用。

> 踩到的坑：第一条测试最初断言「master == origin/master」，但**检查过程本身会 fetch**，
> fetch 后 origin/master 已经前进，两者本就不相等。正确断言是「master 与调用前一致」。

---

## 5. 变异测试（3 个变异体，2 杀 1 存活）

| 变异体 | 结果 | 性质判定 |
|---|---|---|
| M1 去掉落后远端的拦截 | ✅ 杀死 | — |
| M2 `check_remote` 恒为 True（绕过失效） | ✅ 杀死 | — |
| M3 无 upstream 也拒绝（破坏离线） | ❌ 存活 | **等价变异体** |

**M3 为什么存活**（实测诊断，不是猜测）：去掉早退分支后 `remote_ref` 为空 →
`git fetch --quiet ""` 被 git 当作**自拉取**、rc=0 → `rev-list --count master..` = 0
→ 不抛错 → 行为与原实现**完全一致**。注入的改动在这条输入下不改变任何行为。

> 这是本项目遇到的**第四类**存活变异体。前三类是：① 没测到 ② 冗余路径
> ③ 天然不可观测（第二层防线）。**新增 ④ 等价变异体** —— 注入了改动但语义未变，
> 补测试也无济于事，只能识别并标注，不必强求杀死。

---

## 6. 真实环境验证

本 change 的验证**不需要真实远端**：双机夹具用本地裸库模拟，因此不受代理故障影响。

一并确认既有环境未受影响：

- 中枢服务与前两轮修复保持 active
- 生产库数据无改动
- 本轮只改节点侧工具 `fstdd_git.py`，不涉及服务端

---

## 7. 遗留

- **未接线项：`scope_conflicts` 仍未被 hub 调用**（`docs/04-db-design.md` 已记录为
  「并发写冲突在控制面完全不设防」）。本轮未处理，因为它涉及一个需要 D哥 拍板的
  设计取舍：**在 claim 阶段拦截**会阻塞队首任务（可能与僵尸租约互相死锁）；
  **只做查询能力不接线**则重蹈"零接线"覆辙。建议方案：做成 claim 时的显式错误
  （409 + 明确原因），由调用方决定跳过还是等待 —— 待确认。
- **fetch 失败的频率**：本机经代理访问 GitHub 有时段性故障，fail-closed 可能让
  离线/弱网时的集成被拒。已提供 `--no-check-remote`，但真实使用频率需观察。
