# 留言板护栏：封住 ack 删帖与孤儿留言两个数据面缺口

<!-- source_hash: ae8ec7c09587aa8a -->
<!-- generated_at: 2026-09-17T18:20:35+00:00 -->
<!-- canonical: canonical/proposals/2026-09-18-bbs-guardrails.yaml -->

## Why

任务留言板（BBS）功能已交付并归档，但 QA 独立验证给出「有条件 Go」，两个
🟠 级问题未修：

1. ack_message 是唯一能让整条讨论串从所有人眼前消失的操作，却零测试覆盖，
   且服务端对 to_node_id IS NULL 的留言不做任何 ack 权限校验 —— 等于把
   「删帖」权限默认开放给了每一个注册节点。目前唯一的防线是 docstring 里
   一句话，属于「靠人不犯错」的设计。
2. messages.task_id 的外键声明了但从未生效：PRAGMA foreign_keys=ON 只写在
   SCHEMA 里，而 connect_db 未设置。SQLite 的 foreign_keys 是连接级 pragma、
   默认 OFF，所以所有请求连接都没开。task_id 打错一个字会静默生成孤儿留言，
   在任何任务下都看不到，且无任何报错。

两者都不影响「BBS 能用」，但都属于护栏缺失：一旦有多个 agent 自动接入，
误用概率会显著上升。


## What Changes

- connect_db 增加 PRAGMA foreign_keys=ON，让 SCHEMA 已声明的外键真正生效（契约与代码一致）
- _message 增加 task_id 显式存在性校验，返回明确的 400 task not found 而非 201 静默接受
- _ack 拦截 task_id 非空且 to_node_id 为空的留言 —— 这类即 BBS 讨论串，不可被 ack
- 补 ack_message 的 3 条单元测试（路径 / payload / node_id 必填），杀掉 QA 的 B7/B8/B9 变异体
- 看板渲染修复：body 同时归一 CRLF/CR/LF（Windows 多行留言会破坏版面）；截断超长留言补省略号

### Modified Capabilities

- **messages 数据完整性**：外键生效 + task_id 显式校验 + BBS 留言禁止 ack，讨论串不再可被误删或静默丢失
- **看板留言渲染**：Windows 换行不破坏版面，截断有视觉提示

## Success Criteria

- [ ] POST /messages 指向不存在的 task_id 返回 400 task not found，不再生成孤儿留言
- [ ] ack 一条 BBS 留言（task_id 非空、to_node_id 为空）返回 400，讨论串对所有人保持可见
- [ ] 告警类 notice（task_id 为空）仍可正常 ack，行为不变
- [ ] QA 的 B7/B8/B9 三个变异体被杀死（注入后测试转红）
- [ ] 现有测试不回归（37 passed 起，新增用例后仍全绿），ROUTES 仍 13
