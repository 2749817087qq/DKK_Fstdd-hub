# 多机多 agent 协作：首个最小闭环（1 机 1 agent 1 任务，120min 时间盒）

<!-- source_hash: 69b236462a6e5750 -->
<!-- generated_at: 2026-09-18T00:35:11.561857 -->
<!-- canonical: canonical/proposals/2026-09-18-distributed-first-closure.yaml -->

## Why

多机多 agent 协作系统的三层架构（L1 数据面 / L2 控制面 / L3 节点本地）已全部建成，
服务器部署昨夜也已对齐（四文件 md5 一致、备份 timer 已挂、服务 enabled+active），
但 **tasks 表至今为 0 —— 6 个 agent 从未真正通过中枢领过一次任务**。
也就是说：这套系统目前只有"地基和工具"，一次完整的任务流转都没跑通过。

本轮的目的是**证伪式验证**：用 120 分钟时间盒跑通一次最小闭环
（1 台机器、1 个 agent、1 个任务），拿到真实数据来回答「决策门三问」，
从而判断这条协作链路**是否值得继续投入**。

⚠️ 本轮真正的交付物不是那个跑通的任务，而是过程中暴露的失败模式，
以及第 3 问的诚实答案 —— 如果跑通之后并不想再走一遍，那这次闭环就是一次
成功的证伪，应立刻按 K5 冻结平台化投入，**不能把"跑通了"误当成"值得做"**。


## What Changes

- P0 基线冻结：拿到可对比快照（sha / worktree / 路由数 / 活跃 change / 服务端状态），并记录 T0 手动预估耗时（K5 唯一数据源）
- P1 建 SSH 隧道让控制面对本机可见，并注册本节点；验证 register 幂等（SSH 双执行坑）
- P2 经中枢创建 1 条真实任务并原子领取，拿到 lease_token；验证重复 claim 不会改 claimed_by
- P3 在 task worktree 中做一次真实改动，提交并推送 task 分支
- P4a 本地 ff-only 集成，master 前进 1 commit
- P4b（D哥 拍板做完整版）新建 GitHub 仓库 + 服务器裸库 + 镜像钩子，完成数据面推送与三端 sha 收敛
- P5 标记任务 done（带 result_git_sha）+ 收尾复核 + 回答决策门三问

### New Capabilities

- **中枢任务流转闭环**：首次验证 register → task → claim → complete 全链路在真实环境可用（不 mock）
- **fstdd-hub 数据面**：为独立项目 fstdd-hub 建立远端仓库与镜像链路（此前零 remote）

### Modified Capabilities

- **无生产代码修改**：本轮为验证性闭环，hub 代码增量目标 0 行（PATCH /tasks/{id} 经评审确认不需要）

## Success Criteria

- [ ] P0：baseline.txt 已生成，含 BASE_SHA=f2bf26c / WORKTREES=1 / ROUTES=13 / T0=60min
- [ ] P1：同一 register 命令连跑两次后 /nodes 中该 node_id 计数仍 == 1（SSH 双执行的端状态验证）
- [ ] P2：/tasks 中该 task 同时含 claimed_by 与 lease_token；重复 claim 第二次返回空/非 200 且不改 claimed_by
- [ ] P3：task worktree 提交后 status --porcelain 为空；git ls-remote 远端 task/<id> 存在且 sha == 本地 HEAD
- [ ] P4a：本地 master 前进 1 commit
- [ ] P4b：远端与 GitHub 三端 sha 收敛，mirror.log 新增行全 [OK] 无 WARN/ERROR
- [ ] P5：/tasks 中该 task 为终态；git worktree list 回 1 行；tests/ 仍 14 passed
- [ ] 【K1】墙钟 <= 120 min；超时即冻结
- [ ] 【K5】实际耗时 <= 90 min（60 x 1.5）才算明显优于手动
- [ ] 【决策门三问】闭环后必须诚实回答，任一为「否」即冻结平台化投入
