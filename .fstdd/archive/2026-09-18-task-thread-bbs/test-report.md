# 任务留言板（BBS）—— 验证记录

**change**：`2026-09-18-task-thread-bbs`
**基线**：`eace612`
**结论**：🟢 全部判据通过，hub 本体**零改动**

---

## 一、核心结论

中枢的 `messages` 表天生就支持任务留言（已有 `task_id` / `kind` / `task_id` 过滤），
本 change 只补了**客户端封装 + 看板渲染**，`tools/fstdd_hub.py` **一行未动**
（ROUTES 仍为 13）。

---

## 二、判据验证结果

| 判据 | 验证方式 | 结果 |
|---|---|---|
| SC-001 发留言并可按任务取回 | 对**真实中枢**发 3 条（question/status/blocker），`list_messages(task_id=)` 取回 3 条 | ✅ |
| SC-002 `to_node_id` 恒为空（BBS 非私信） | 3 条留言 `to_node_id` 均为 `None`；换 `fstdd-hub-infra` 身份查询，同样看到 3 条 | ✅ |
| SC-003 kind 白名单 | 传 `chat` → 客户端抛 `ValueError`，错误信息列出四种合法取值 | ✅ |
| SC-004 按任务过滤 | `list_messages(node_id, task_id=T)` 只返回 T 的留言，不含其他任务 | ✅ |
| SC-005 看板渲染留言线程 | `python tools/hub_board.py` 输出中，每个任务下方跟随其留言（含 发言者/kind/时间/正文），无留言显示「（暂无留言）」 | ✅ |
| SC-006 测试不回归 | `pytest tests/ -q` → **37 passed**（原 31 + 新增 6） | ✅ |
| REQ-006 未 ack 留言 | 验证全程未调用 `ack_message`；重查仍能取到全部 3 条 | ✅ |

---

## 三、真实中枢实测记录

```
1) [question] -> message_id=msg-e5525e66e4aa9d19
2) [status]   -> message_id=msg-d366d7b3f29271f0
3) [blocker]  -> message_id=msg-4c6a03eb2430681b

按任务取留言（node_id=fstdd005-win-dev）: 共 3 条
  - 2026-09-17T17:51:35 fstdd005-win-dev [question] to=None
  - 2026-09-17T17:51:36 fstdd005-win-dev [status]   to=None
  - 2026-09-17T17:51:37 fstdd005-win-dev [blocker]  to=None

换节点身份（fstdd-hub-infra）看同一任务: 3 条   ← 广播可见性成立
非法 kind: 正确拒绝 invalid message kind 'chat', must be one of ('question','blocker','status','notice')
```

看板输出片段：

```
 已完成 (2)
   task-8177a2f0af062f8f
      新增 tools/hub_client.py：节点侧与中枢交互的客户端封装...
      类型 change  领取者 fstdd005-win-dev  尝试 1  更新 09-18 00:59:15
      基线 f2bf26c -> 结果 1d3d46a   租约剩余 -
      留言 (3)
         - 09-18 01:51:35 fstdd005-win-dev [question]
           hub_client.register() 的写路径还没对真实中枢验证过...
         - 09-18 01:51:36 fstdd005-win-dev [status]
           F5 已补验：create/claim/heartbeat/complete 对真实中枢全通...
         - 09-18 01:51:37 fstdd005-win-dev [blocker]
           发现 complete 成功后幂等键失效（租约校验先于幂等缓存）...
   task-93737c23d2fbb818
      ...
      留言 (0)   （暂无留言）
```

---

## 四、两条使用禁忌（已写入代码注释，务必遵守）

1. **绝不可 ack 留言**。服务端 `GET /messages` 的 SQL 含 `WHERE acked_at IS NULL`
   —— 一旦被 ack，该留言会对**所有人**消失，整个讨论串凭空不见。
   `ack_message()` 仅为告警类消息（如镜像失败 notice）保留，其 docstring 已写明此禁忌。
2. **发留言不得指定 `to_node_id`**。服务端过滤条件是
   `to_node_id=? OR to_node_id IS NULL`；指定收件人后只有那人可见，留言板退化成私信。
   为此 `post_message()` **刻意不提供 `to_node_id` 参数**，从 API 层面杜绝误用。

---

## 五、测试防护（针对上述禁忌）

新增 6 条用例，其中两条是**守卫型**：

- `test_post_message_is_broadcast_not_dm` —— 断言 payload 中**不存在** `to_node_id` 键
- `test_post_message_rejects_unknown_kind` —— 非法 kind 必须在客户端被拦

（上一个 change 的教训：7 个存活变异体全是「字段透传类零覆盖」，故本轮一开始就补上。）

---

## 六、改动清单

| 文件 | 改动 |
|---|---|
| `tools/hub_client.py` | 新增 `list_messages()` / `post_message()` / `ack_message()` + `MESSAGE_KINDS` 常量 |
| `tools/hub_board.py` | 消息收集提前 + 构建 `thread` 索引 + 每个任务下渲染留言线程 |
| `tests/test_hub_client.py` | fake hub 支持 `POST /messages`；新增 6 条用例 |

**未改动**：`tools/fstdd_hub.py`（服务端）、`tools/fstdd_git.py`、任何 systemd unit。
