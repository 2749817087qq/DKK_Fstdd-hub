# 服务器部署对齐 测试报告

> 版本：v1.0
> 日期：2026-09-17
> Change：`2026-09-17-server-deploy-align`（task_type: configuration）
> 对应 Spec：`canonical/specs/agent/`（8 步操作 + 5 条预期结果）
> 执行方式：全程经 SSH 只读/幂等写操作，以**端状态**为唯一判据

## 一、执行摘要

| 项 | 结果 |
|---|---|
| 步骤覆盖 | **8 / 8** |
| 文件 md5 一致性 | ✅ 四文件全部与本地 `fstdd-hub/tools/` 一致 |
| 服务状态 | ✅ `active` + `enabled`，重启后 PID 4190317 |
| 监听地址 | ✅ 仅 `127.0.0.1:8788`，**未出现 0.0.0.0** |
| 数据完整性 | ✅ nodes=1 / tasks=0 / idempotency=2 / messages=2，**与基线完全一致** |
| 备份定时 | ✅ timer 已排定（2026-09-18 00:04:40 CST） |
| 备份链路 | ✅ 手动触发成功，新增 2 份备份文件 |
| 本地回归 | ✅ `pytest tests/` → **14 passed** |

**一句话结论**：服务器部署已与新项目 `fstdd-hub` 对齐——三个从未上传的文件已补齐、
部署流程经新项目路径幂等重跑通过、备份从"只有一份且无调度"变为"每日定时 + 已验证可用"，
全程数据库零丢失。

## 二、基线 vs 部署后

| 项 | 部署前 | 部署后 | 结论 |
|---|---|---|---|
| 服务状态 | active / enabled | active / enabled | ✅ 不变 |
| 监听 | 127.0.0.1:8788 (pid 3576726) | 127.0.0.1:8788 (pid 4190317) | ✅ 仅回环，PID 因重启变化 |
| 服务器文件 | 仅 `fstdd_hub.py` + `hub_backup.sh` | 四文件齐全 | ✅ 补齐 `fstdd_git.py`、`hub_healthcheck.py` |
| 备份文件数 | 1（09-17 12:12，部署当时） | 3（新增 23:26、23:27 两份） | ✅ 链路打通 |
| 备份调度 | **无 timer、无 cron** | timer 已排定 00:04:40 | ✅ 消除零备份隐患 |
| DB 行数 | 1 / 0 / 2 / 2 | 1 / 0 / 2 / 2 | ✅ 零丢失 |

## 三、四文件 md5 三方一致

| 文件 | 本地 `fstdd-hub/tools/` | 服务器 | 一致 |
|---|---|---|---|
| `fstdd_hub.py` | `d4ea5eb31821cd41e371d4a947d4e635` | 同 | ✅ |
| `fstdd_git.py` | `1577efdbf94098cae7e8a6d0de15f8ee` | 同 | ✅ |
| `hub_healthcheck.py` | `feb8522402827a8572ac42cbcac3d61d` | 同 | ✅ |
| `hub_backup.sh` | `4b74d3d70a4da1e27c735092b6f36e19` | 同 | ✅ |

> `fstdd_git.py` 与 `hub_healthcheck.py` 为本次**首次上传**（此前服务器全盘 find 无命中）；
> 另两个文件原本即一致，本次属幂等覆盖。

## 四、逐步执行记录

| # | 步骤 | 结果 | 证据 |
|---|---|---|---|
| 1 | 记录部署前基线 | ✅ | active/enabled、ss 监听、DB 行数、backups=1 |
| 2 | scp 补齐两个缺失文件 | ✅ | 服务器 md5 与本地逐一比对一致，`chmod +x` 已执行 |
| 3 | 以新项目为来源重跑 `deploy_hub.sh` | ✅ | local md5 = remote md5；`service=active`；`listen=127.0.0.1:8788`；`health={"ok": true...}`；`[OK] fstdd-hub deployed` |
| 4 | 数据库原地接管 | ✅ | 只读查询四表行数与基线完全一致 |
| 5 | 安装并启用备份 timer | ✅ | `Created symlink ... timers.target.wants/fstdd-hub-backup.timer`；`list-timers` 显示 NEXT=2026-09-18 00:04:40 CST |
| 6 | 手动触发备份验证链路 | ✅ | backups 目录新增 `fstdd-hub-20260917T152638Z.sqlite3`、`fstdd-hub-20260917T152707Z.sqlite3`（各 49152B） |
| 7 | 端到端复验 /health | ✅ | `{"ok": true, "tasks": 0, "online_nodes": 1}` |
| 8 | 本地回归复跑 | ✅ | `14 passed in 11.27s` |

## 五、新项目新增资产

本次将两个 systemd unit 作为**项目资产**落到 `fstdd-hub/tools/`，使其可版本化、可重复部署：

| 文件 | 用途 |
|---|---|
| `tools/fstdd-hub-backup.service` | oneshot 备份单元，`ExecStart=bash hub_backup.sh`，日志追加到 `backup.log` |
| `tools/fstdd-hub-backup.timer` | `OnCalendar=daily`、`Persistent=true`、`RandomizedDelaySec=300` |

部署方式为 scp 到 `/tmp` 后 `sudo cp` 覆盖（幂等），再 `daemon-reload` + `enable --now`。

## 六、observed 环境特性（非缺陷）

- **远端命令双执行**：手动触发备份时产生了 **2 份**备份文件（23:26 与 23:27），
  即同一命令被实际执行两次。备份本身幂等，故无害；但**今后凡非幂等的远端操作都需注意**。
  本次全部采用幂等方式（覆盖式 cp、`enable --now`、固定文件名 md5 比对）规避。
- `deploy_hub.sh` 默认 `HOST=ubuntu@43.134.236.80`（裸主机名）且 `KEY=/d/id_ed25519`（Git Bash 风格路径）。
  为避开「裸主机名绕过 ssh config 的 IdentityFile」与「Windows OpenSSH 解析不到 `/d/` 路径」两个已知坑，
  本次显式以环境变量覆盖：`FSTDD_SSH_HOST=fstdd-hub`、`FSTDD_SSH_KEY=D:/id_ed25519`。
  **后续如直接在 Git Bash 裸跑该脚本，可能命中这两个坑**，建议固化到脚本默认值或写入 README。

## 七、未做 / 遗留

- **未**从 `stdd-repo` 删除原文件（本次为复制迁移，两边并存），是否移除由 D哥 决定。
- **未**为新项目执行 `git init`，无版本管理。
- `deploy_hub.sh` 本身仍**未上传**到服务器（与 `deploy_server_bare_repo.sh` 同属"本地远程执行、不留服务器"的既有模式）。
- 首个真实任务仍未产生：**tasks 依旧为 0**，6 个 agent 尚未接入，本 change 不改变这一事实。
