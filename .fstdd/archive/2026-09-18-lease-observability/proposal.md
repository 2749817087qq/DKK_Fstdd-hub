# 租约过期后原 owner 被静默没收，且失败原因不可区分

<!-- source_hash: 60af4e184e00f74e -->
<!-- generated_at: 2026-09-18T01:27:48+00:00 -->
<!-- canonical: canonical/proposals/2026-09-18-lease-observability.yaml -->

## Why

多 agent 协作下，租约（lease）是「谁在干这个活」的唯一依据。当前实现在租约
过期时把原 owner 一脚踢开，且不给任何可区分的反馈：

  1. 原 owner 被永久锁死。_lease_update 无条件 `raise "lease expired"`，
     长任务心跳晚一秒，后续的 heartbeat / complete 全部 400，已做的工作
     全部作废 —— 哪怕池子里根本没人接手这个任务。

  2. 失败原因不可区分。租约过期后 owner_node_id 与 token 被清空，原 owner
     收到的是 "invalid task owner or lease token"。而「任务已回池、还没人接」
     与「已被别的节点接管」这两种语义完全相反的处境，返回的**是同一句话**：
     前者应该重新 claim 继续干，后者应该立刻停手。原 owner 只能猜，猜错就是
     重复劳动或白扔已完成的活。

  3. 接管全程静默。租约过期 -> 被回收 -> 被别的节点 claim，原 owner 要等到
     下一次心跳被拒才知道，而那时它已经干完了。

根因不是「租约过期要回收」（这是对的），而是**回收语义把「可回收」错当成了
「原 owner 失效」**，并且整个链路没有给原 owner 任何可观测性。


## What Changes

- 区分租约失败的四种语义，返回机器可读的 error_code：lease_reaped（已回池，可重新 claim）/ task_taken_over（已被接管，应停手）/ lease_released（自己已完成，别重试）/ invalid_lease（真非法）
- 允许原 owner 在任务未被回收、未被接管的前提下续租与完成 —— 「过期」的语义是别人可以来抢，不是自己不能续
- 新增 last_owner_node_id 列持久化上一任 owner，接管时主动给原 owner 发私信通知（必须从持久化字段读，不能用 reap 返回值：触发回收的往往不是接管者）
- init_db 增加轻量迁移 _ensure_columns —— CREATE TABLE IF NOT EXISTS 对已存在的表不补列，生产库是旧表结构，不迁移则接管通知静默不发
- 补 5 条守卫测试：真并发 claim、owner 可续租、失败原因可区分、接管通知、completed 后报 lease_released
- hub_client 文档同步：租约语义与 error_code 分支处理表

### Modified Capabilities

- **任务租约**：过期后可恢复、失败原因可区分、被接管有通知

## Success Criteria

- [ ] 租约过期但无人接管时，原 owner 续租与完成均返回 200
- [ ] lease_reaped 与 task_taken_over 的 error_code 不同
- [ ] 被接管时原 owner 能收到一条来自接管者的私信
- [ ] 8 线程并发 claim 只有一个节点拿到任务
- [ ] 既有 45 条用例不回归，新增 5 条（共 50 passed）
- [ ] 变异测试：注入 6 个已知缺陷，除天然不可观测者外全部被杀死
