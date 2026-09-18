# 集成只检查本地状态：本地干净不等于可以集成，推送时才炸且已分叉

<!-- source_hash: a57affcc5808db79 -->
<!-- generated_at: 2026-09-18T03:01:05+00:00 -->
<!-- canonical: canonical/proposals/2026-09-18-integration-remote-check.yaml -->

## Why

`integrate_task_branch` 的准入检查 `ensure_clean_target` 只看**本地**：
在 target 分支上 + 无未提交改动，就放行。多机协作下这个假设不成立 ——
别的机器可能已经推送了新提交，而本地工作区依然是干净的。

后果链条（实测）：
  本地看着干净 -> ensure_clean_target 放行 -> `merge --ff-only` 本地成功
  -> **推送被拒**（fetch first）-> **本地 master 已与远端分叉**，
     必须手动 rebase 才能重来。

问题不在于「最终会失败」，而在于**失败得太晚且状态已脏**：
集成阶段报成功，把人骗过去；等推送时才炸，此时本地分支已经脏了，
恢复成本远高于集成前拦一下。

根因：准入检查缺少「远端视角」。本地干净是必要条件，不是充分条件。


## What Changes

- 新增 ensure_target_up_to_date()：集成前 fetch 并比对本地 target 与上游，落后则拦下并给出明确指引
- ensure_clean_target / integrate_task_branch 增加 check_remote 开关（默认开启）
- 无 upstream 的纯本地仓库直接放行 —— 不破坏离线与单机使用
- fetch 失败时拒绝集成（远端状态未知），并提示可显式绕过；fetch 带 GIT_TERMINAL_PROMPT=0 防止卡在交互式凭据输入
- CLI 增加 --no-check-remote 开关
- 补 3 条测试，含 make_remote_pair 双机夹具（裸库 + 两个 clone）

### Modified Capabilities

- **任务分支集成**：集成前校验远端一致性，避免本地假绿灯导致推送分叉

## Success Criteria

- [ ] 本地落后远端时集成被拦，错误信息含 'behind' 与 'origin/master'
- [ ] 被拦时本地 target 分支未被改动（不产生需要 rebase 的分叉）
- [ ] check_remote=False 可显式绕过
- [ ] 无 upstream 的纯本地仓库集成不受影响
- [ ] 既有 51 条用例不回归，新增 3 条（共 54 passed）
