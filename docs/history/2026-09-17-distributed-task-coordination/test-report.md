# distributed-task-coordination 测试报告（Slice A/B）

> 变更：`2026-09-17-distributed-task-coordination`
> 执行日期：2026-09-17
> 执行环境：Windows，Python 3.11.9（`C:\Python311\python.exe`）
> 测试位置：`D:/tools/FSTDD/stdd-repo/upstream`

## 一、当前 Slice 执行结果

本轮只完成 Slice A：

- `fstdd gate amend-audit`：追加用户追认，不覆盖原始 Gate 审计；同一证据幂等；冲突证据拒绝。
- `fstdd phase record-slice`：受控写入 `phases.build.slices_completed`；缺参数、非 BUILD、冲突覆盖均拒绝。

执行命令：

```bash
cd D:/tools/FSTDD/stdd-repo
C:/Python311/python.exe -m pytest upstream/tests/commands/test_gate.py upstream/tests/commands/test_phase.py -q
```

结果：**40 passed / 0 failed**，退出码 0。

## 二、覆盖内容

| 能力 | 测试 | 结果 |
|---|---|---|
| Gate 基础确认、顺序和 file token | `test_gate.py` 原有用例 | 通过 |
| Gate 追认追加且不覆盖 | TC-GATE-110 | 通过 |
| Gate 追认幂等 | TC-GATE-111 | 通过 |
| Gate 冲突证据拒绝 | TC-GATE-112 | 通过 |
| 未确认 Gate 不可追认 | TC-GATE-113 | 通过 |
| Slice 证据受控写入 | `test_phase.py` 新增 | 通过 |
| Slice 冲突覆盖拒绝 | `test_phase.py` 新增 | 通过 |
| 非 BUILD 阶段拒绝记录 | `test_phase.py` 新增 | 通过 |

## 三、Slice B 执行结果

Slice B 已实现 `tools/fstdd_hub.py`，提供标准库 HTTP + SQLite 控制面：节点注册/心跳、任务创建/查询/原子领取、租约续期、完成/失败回传、消息投递/拉取/ack，以及写操作幂等键。

执行命令：

```bash
C:/Python311/python.exe -m pytest upstream/tests/test_fstdd_hub.py -q
```

结果：**8 passed / 0 failed**，退出码 0。

覆盖重点：并发安全的事务领取、重复创建/领取幂等、租约过期回收、旧 token 拒绝、失败/阻塞记录、消息 ack 和未注册节点拒绝。

## 四、Slice C 执行结果

Slice C 已实现 `tools/fstdd_git.py`：任务分支和仓库外 worktree、固定基线 SHA、保守 scope 冲突判定、干净目标分支上的 ff-only 串行集成。冲突、脏目标、非 `task/*` 分支和基线漂移均直接拒绝，不强推、不自动 reset。

执行命令：

```bash
C:/Python311/python.exe -m pytest upstream/tests/test_fstdd_git.py -q
```

结果：**6 passed / 0 failed**，退出码 0。

## 五、Slice D 执行结果

Slice D 已完成本地可验证的运维资产：

- `tools/deploy_hub.sh`：独立 `fstdd-hub.service`、仅监听 `127.0.0.1:8788`、上传 md5 校验、按端口停旧进程、`systemctl enable --now`、重启后检查 `is-active`/监听/health。
- `tools/hub_backup.sh`：SQLite WAL checkpoint + 在线 backup，保留 14 天。
- `tools/hub_healthcheck.py`：只读 `/health` 检查。

执行命令：

```bash
C:/Python311/python.exe -m pytest upstream/tests/test_hub_ops.py -q
```

结果：**3 passed / 0 failed**，退出码 0。

## 六、云端部署与备份实测

D哥已明确授权执行云端部署。通过 `tools/deploy_hub.sh` 部署到 `ubuntu@43.134.236.80`：

| 检查项 | 实测结果 |
|---|---|
| 上传 md5 | 本地与远端均为 `d4ea5eb31821cd41e371d4a947d4e635` |
| systemd | `fstdd-hub.service` = **active** |
| 监听 | **127.0.0.1:8788**，未对公网监听 |
| 健康检查 | `{"ok": true, "tasks": 0, "online_nodes": 0}` |
| SQLite 备份 | `/home/ubuntu/fstdd-hub/backups/fstdd-hub-20260917T041218Z.sqlite3`，48K，已生成 |
| 8787 隔离 | 本次部署未修改 `fstdd-inbox.service` 或 8787 数据目录 |

SSH 输出出现远端主机 ED25519 指纹变更警告（当前 `SHA256:Fj27h...`，本机 known_hosts 有旧 ECDSA 记录），但本次连接因部署脚本显式使用 `StrictHostKeyChecking=no` 仍成功。该主机密钥告警应在后续运维中核实并清理旧 known_hosts 记录，不能长期忽略。

## 七、验收边界

代码、脚本、本地测试以及云端 systemd/监听/health/备份验收均已完成；本报告可作为 BUILD 阶段质量报告。

## 八、全量回归

在提交 Slice A/B/C/D 及两个遗留 change 归档记录后，从法定源 `D:/tools/FSTDD/stdd-repo/upstream` 执行全量套件：

```bash
C:/Python311/python.exe -m pytest tests -q
```

结果：**586 passed / 0 failed**，退出码 0。

其中包含 Slice A/B/C/D 新增测试以及两个已归档 change 的相关测试。pytest 退出时偶发的批量临时目录清理守卫不影响用例汇总；本次汇总本身为 586 passed。
