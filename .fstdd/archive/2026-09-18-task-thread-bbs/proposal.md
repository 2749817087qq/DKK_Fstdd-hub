# 任务留言板（BBS）：让 agent 能就具体任务留言讨论

<!-- source_hash: 6c3cd05583ffd845 -->
<!-- generated_at: 2026-09-17T17:48:04+00:00 -->
<!-- canonical: canonical/proposals/2026-09-18-task-thread-bbs.yaml -->

## Why

看板只能显示任务的**状态与归属**，看不到**讨论**。多 agent 协作时，
「为什么卡住」「谁在等谁」「这个任务的前提假设是什么」这类关键信息无处承载，
只能散落在各节点的本地上下文里 —— 中枢看不到，其他 agent 也看不到。

实测发现中枢的 `messages` 表天生就支持这件事：已有 `task_id` 字段、
已有 question/blocker/status/notice 四种 kind、`GET /messages` 已支持 task_id 过滤。
**缺的只是客户端封装与看板渲染，hub 本体无需一行改动。**

两个必须写进约定的约束，否则留言会「消失」：
  1. `GET /messages` 的 SQL 含 `WHERE acked_at IS NULL` —— 留言一旦被 ack
     就对所有人不显示。因此 **BBS 留言绝不 ack**（ack 只用于告警类）。
     这是最容易踩的坑：某个 AI 顺手 ack 一下，整个讨论串就空了。
  2. 发留言**必须不指定 `to_node_id`** —— 一旦指定收件人，就只有那个人可见，
     留言板退化成私信。


## What Changes

- hub_client.py 新增留言封装：post_message()（BBS 广播，强制不带 to_node_id）、list_messages()（支持按 task_id 过滤）、ack_message()（明确标注仅供告警类使用）
- hub_board.py 在任务区为每个任务渲染留言线程（扁平、按时间序），显示 发言者 / kind / 时间 / 正文
- 新增单元测试覆盖留言契约：task_id 与 kind 透传、to_node_id 恒为空、kind 白名单校验等
- 把两条使用禁忌（不 ack、不指定收件人）写入代码注释与 change 文档，避免后来者踩坑

### New Capabilities

- **任务留言线程**：agent 可就某个具体任务发起或参与讨论，留言对所有人可见，并在看板上随任务一并展示

### Modified Capabilities

- **任务看板**：从「只看状态」升级为「状态 + 讨论」，每个任务后跟随其留言线程
- **hub_client**：补齐消息类接口封装（此前只有任务类接口，消息类只能靠 inline 脚本）

## Success Criteria

- [ ] SC-001：调用 post_message(task_id=..., body=..., kind='question') 后，GET /messages?task_id= 能取到该留言
- [ ] SC-002：发出的留言 to_node_id 恒为空，任何节点都能看到（BBS 语义，非私信）
- [ ] SC-003：kind 限定在 question/blocker/status/notice 之内，非法值在客户端即被拒绝
- [ ] SC-004：list_messages(node_id, task_id) 只返回该任务的留言，不含其他任务的留言
- [ ] SC-005：看板输出中，每个任务下方跟随其留言线程，格式清晰可辨
- [ ] SC-006：新增单元测试覆盖上述契约并全部通过；既有 31 passed 不回归
