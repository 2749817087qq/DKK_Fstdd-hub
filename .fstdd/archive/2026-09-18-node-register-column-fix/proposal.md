# 节点注册 SQL 列错位：指纹被写成 online，新节点不计入在线数

<!-- source_hash: f0e848fdb8d6acbe -->
<!-- generated_at: 2026-09-17T18:45:48+00:00 -->
<!-- canonical: canonical/proposals/2026-09-18-node-register-column-fix.yaml -->

## Why

_register 的 INSERT 语句列与占位符错位一格：列顺序是
(..., capabilities_json, ssh_fingerprint, status, last_seen, ...)，
而 VALUES 第 6 个写的是字面量 'online'。净效果是两个字段的值互换：
  * ssh_fingerprint 列 <- 字面量 'online'（真实指纹永久丢失）
  * status 列 <- 指纹串（首次插入时），直到第二次注册才被 ON CONFLICT 兜回 'online'
/health 的 online_nodes 按 status='online' 计数，因此**首次注册的节点会被漏算**。

发现路径：补验 QA 遗留盲区「hub_client.register() 从未对真实中枢跑过」时，
发现返回的 machine_name/platform 为 null，顺藤摸瓜查到落库值不对。


## What Changes

- 修正 INSERT 的 VALUES 占位符：ssh_fingerprint 由字面量 'online' 改为绑定参数，status 恢复为字面量 'online'
- 补守卫型测试：断言注册后 status=='online'、ssh_fingerprint 保留传入值、/health 的 online_nodes 计入首次注册的节点
- 生产数据修复：清理验证探针节点，用真实 SSH 公钥指纹重新注册两个节点

### Modified Capabilities

- **节点注册**：字段不再错位，指纹真实落库，首次注册即计入在线数

## Success Criteria

- [ ] 新注册节点 status=='online' 且 ssh_fingerprint 等于传入值
- [ ] /health 的 online_nodes 计入首次注册的节点
- [ ] 注入原缺陷（改回错位 VALUES）后测试转红
- [ ] 既有 45 条用例不回归
