# Spec: 任务留言线程（task-thread-bbs）

> Change: 2026-09-18-task-thread-bbs | Auto-generated Human View

## Requirements

### Requirement: 发留言：向指定任务发布一条公开留言

#### Scenario: SC-001

- **GIVEN** 中枢可达，且 from_node_id 已注册
- **WHEN** 调用 post_message(task_id, body, kind='question')
- **THEN** SHALL 向 POST /messages 提交 payload，其中 task_id、body、kind、idempotency_key 均正确透传，且 from_node_id 为调用方身份

### Requirement: BBS 语义：留言必须对所有人可见，不得退化成私信

#### Scenario: SC-002

- **GIVEN** 调用 post_message
- **WHEN** payload 被序列化
- **THEN** SHALL 不包含 to_node_id 键（或为 null）—— 因为服务端过滤条件是 to_node_id=? OR to_node_id IS NULL，一旦指定收件人则仅该节点可见

### Requirement: kind 白名单：只允许中枢定义的四种消息类型

#### Scenario: SC-003

- **GIVEN** 调用 post_message 传入非法 kind（如 'chat'）
- **WHEN** 客户端校验
- **THEN** SHALL 在发出请求前抛出 ValueError，错误信息列出合法取值 question/blocker/status/notice

### Requirement: 取留言：按任务过滤，只返回该任务的讨论

#### Scenario: SC-004

- **GIVEN** 任务 T 有 2 条留言，任务 U 有 1 条留言
- **WHEN** 调用 list_messages(node_id, task_id=T)
- **THEN** SHALL 请求 GET /messages?node_id=X&task_id=T，且返回结果仅含 T 的 2 条，不含 U 的留言

### Requirement: 看板渲染：每个任务下方跟随其留言线程

#### Scenario: SC-005

- **GIVEN** 看板已拉取任务列表与各任务的留言
- **WHEN** 渲染任务区
- **THEN** SHALL 在每个任务条目下方输出其留言线程（扁平、按 created_at 升序），逐条显示 发言者、kind、相对时间、正文；无留言时显示「（暂无留言）」占位

### Requirement: ack 禁忌：BBS 留言不得被 ack，否则会因服务端 acked_at IS NULL 过滤而对所有人消失

#### Scenario: SC-006

- **GIVEN** 任务下存在留言
- **WHEN** 任何 BBS 流程执行
- **THEN** SHALL NOT 调用 ack_message()；ack_message 的存在仅为告警类消息保留，其文档字符串 SHALL 明确写出此禁忌

### Requirement: 不回归：既有能力不受影响

#### Scenario: SC-007

- **GIVEN** 改动前 tests/ 为 31 passed
- **WHEN** 本 change 完成后运行 pytest tests/
- **THEN** SHALL 全部通过，且数量不少于 31（新增用例计入）
