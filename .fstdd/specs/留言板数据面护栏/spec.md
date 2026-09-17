# Spec: 留言板数据面护栏

> Change: 2026-09-18-bbs-guardrails | Auto-generated Human View

## Requirements

### Requirement: messages.task_id 外键必须真正生效，禁止生成孤儿留言

#### Scenario: SC-001

- **GIVEN** 中枢运行中，connect_db 建立的连接
- **WHEN** POST /messages 携带不存在的 task_id
- **THEN** 服务端 SHALL 返回 400 task not found，且不写入任何行

#### Scenario: SC-002

- **GIVEN** 中枢运行中
- **WHEN** POST /messages 携带真实存在的 task_id
- **THEN** 服务端 SHALL 返回 201，行为与改动前一致

### Requirement: BBS 讨论串留言不可被 ack（防止任一节点删除公共讨论）

#### Scenario: SC-003

- **GIVEN** 存在一条 task_id 非空且 to_node_id 为空的留言
- **WHEN** 任一已注册节点 POST /messages/{id}/ack
- **THEN** 服务端 SHALL 返回 400，且该留言 acked_at 保持为 NULL

#### Scenario: SC-004

- **GIVEN** 存在一条 task_id 为空的告警类 notice
- **WHEN** 节点 POST /messages/{id}/ack
- **THEN** 服务端 SHALL 返回 200，行为与改动前一致（告警通道不受影响）

### Requirement: ack_message 客户端契约必须有测试覆盖

#### Scenario: SC-005

- **GIVEN** HubClient 已配置 node_id
- **WHEN** 调用 ack_message(message_id)
- **THEN** SHALL POST 到 /messages/{message_id}/ack，payload 只含 node_id

### Requirement: 看板留言渲染须正确处理 Windows 换行与超长文本

#### Scenario: SC-006

- **GIVEN** 一条 body 含 CRLF 换行的留言
- **WHEN** 看板渲染该留言
- **THEN** 输出 SHALL 为单行，不出现残留 CR 导致的版面错位

#### Scenario: SC-007

- **GIVEN** 一条 body 长度超过 68 字符的留言
- **WHEN** 看板渲染该留言
- **THEN** 输出 SHALL 带省略号提示被截断

### Requirement: 不回归：既有能力与接口数量不变

#### Scenario: SC-008

- **GIVEN** 改动完成
- **WHEN** 统计 fstdd_hub.py 的路由分支数
- **THEN** SHALL 仍为 13（本 change 只加校验、不加端点）

#### Scenario: SC-009

- **GIVEN** 改动完成
- **WHEN** 运行 pytest tests/
- **THEN** 既有 37 条用例 SHALL 全部通过，新增用例亦通过
