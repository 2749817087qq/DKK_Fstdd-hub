# Spec: 节点注册字段正确性

> Change: 2026-09-18-node-register-column-fix | Auto-generated Human View

## Requirements

### Requirement: 节点注册必须把各字段写入正确的列

#### Scenario: SC-001

- **GIVEN** 一个全新 node_id
- **WHEN** POST /nodes/register 携带 ssh_fingerprint=SHA256:xxx
- **THEN** 库中 status SHALL 为 'online'，ssh_fingerprint SHALL 为 'SHA256:xxx'

#### Scenario: SC-002

- **GIVEN** 一个全新 node_id 刚完成首次注册
- **WHEN** GET /health
- **THEN** online_nodes SHALL 包含该节点（不得因 status 异常而漏算）

### Requirement: 该缺陷必须有测试守卫，防止复发

#### Scenario: SC-003

- **GIVEN** 守卫测试已存在
- **WHEN** 把 VALUES 改回错位版本
- **THEN** 测试 SHALL 转红
