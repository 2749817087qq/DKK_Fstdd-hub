# 独立项目建项（fstdd-hub） 测试报告

> 版本：v1.0
> 日期：2026-09-17
> Change：`2026-09-17-project-bootstrap`（task_type: code）
> 对应 Spec：`canonical/specs/code/`（5 REQ / 5 SC）、`canonical/specs/agent/`（6 步验证规格）

## 一、执行摘要

| 项 | 结果 |
|---|---|
| 需求覆盖 | **5 / 5 REQ**（SC-001 ~ SC-005） |
| 验证步骤 | **6 / 6** |
| 测试基线 | ✅ `pytest tests/` → **14 passed，0 failed** |
| 可执行位 | ✅ 三个 `.sh` 文件保留 `rwxr-xr-x` |
| 文档完整性 | ✅ docs/ 五份文档 + history/ 两个归档 change |
| 原仓库 | ✅ **零改动**，所有原文件仍在 |
| canonical 校验 | ✅ `canon verify` 2/2 通过 |

**一句话结论**：多机多 agent 协作程序已从框架仓库中剥离，成为可独立运行、可独立走 FSTDD 流程的项目；
测试摆脱了对 `stdd-repo` 全量回归（639 用例）的依赖，且迁移全程未破坏原仓库。

## 二、逐条需求验证

| REQ | 场景 | 结果 | 证据 |
|---|---|---|---|
| REQ-001 项目初始化 | SC-001 | ✅ | `.fstdd/` 骨架齐备（config.d、skills、templates、canonical、changes、archive、specs）；`AGENTS.md`、`FSTDD_CONSTITUTION.md` 已生成；Guard 钩子写入 `.claude/` 与 `.codebuddy/` 的 `settings.local.json` |
| REQ-002 代码迁移 | SC-002 | ✅ | `tools/` 下五文件齐全：`fstdd_hub.py`(24345B)、`fstdd_git.py`(9043B)、`deploy_hub.sh`(950B)、`hub_backup.sh`(950B)、`hub_healthcheck.py`(998B)，可执行位均为 `rwxr-xr-x` |
| REQ-003 测试独立运行 | SC-003 | ✅ | 两测试文件各含 1 处 `parents[1]`（原为 `parents[2]`）；`14 passed in 10.88s` |
| REQ-004 文档体系 | SC-004 | ✅ | `docs/` 含 `DISTRIBUTED_ACCESS.md` 与 `01-design-original`、`02-status-review`、`03-prd-and-devplan`、`04-db-design`；`docs/history/` 含两个归档 change |
| REQ-005 非破坏性迁移 | SC-005 | ✅ | `../stdd-repo/tools/fstdd_hub.py` 与 `../stdd-repo/upstream/tests/test_fstdd_hub.py` 均仍存在、大小未变；全程仅用 `cp -p`，未执行任何删除或改名 |

## 三、关键改动明细

迁移中唯一的代码改动是仓库根定位，属**迁移适配**，不涉及任何断言与业务逻辑：

| 文件 | 改动 |
|---|---|
| `tests/test_fstdd_hub.py:14` | `parents[2]` → `parents[1]` |
| `tests/test_fstdd_git.py:10` | `parents[2]` → `parents[1]` |

> `parents[2]` 适配的是 `stdd-repo/upstream/tests/` 层级（往上跳两级到仓库根）；
> 新项目为 `fstdd-hub/tests/`，只需跳一级。若不改，会定位到 `D:/tools/FSTDD` 而找不到 `tools/`，
> 收集阶段即报 `FileNotFoundError`。

## 四、附带产出

建项过程中另有两个 systemd unit 作为项目资产落入 `tools/`（由后续 change `server-deploy-align` 使用）：

| 文件 | 用途 |
|---|---|
| `tools/fstdd-hub-backup.service` | oneshot 备份单元 |
| `tools/fstdd-hub-backup.timer` | 每日定时备份 |

## 五、已知局限

- 迁移以**复制**完成，`stdd-repo` 与 `fstdd-hub` 目前两边并存，长期会重复维护；
  是否从原仓库移除由 D哥 决定。
- 新项目在本次建项时尚未配置 git 版本管理（由后续步骤补上）。
- `fstdd status` 显示「Guard 未安装」，但 `guard init` 确实写入了 PreToolUse 钩子 ——
  属 CLI 检测口径与实际安装位置不一致，不阻塞流程。
- **tasks 仍为 0**，6 个 agent 尚未接入；本 change 只解决项目归属与可独立运行，不改变这一事实。
