# FSTDD 数据面落地 测试报告

> 版本：v1.0
> 日期：2026-09-17
> Change：`2026-09-17-server-bare-repo-mirror`
> 对应 Spec：`canonical/specs/code/`、`canonical/specs/agent/`
> 对应测试方案：`test-plan.md`

## 一、执行摘要

| 项 | 结果 |
|---|---|
| TC 覆盖 | 16 / 16（100%） |
| 新增测试 | 6 项（`upstream/tests/test_server_bare_repo_ops.py`） |
| 端到端链路 | ✅ 本地 → 服务器裸库 → GitHub 三方 sha 收敛 |
| 全量回归 | 见 §六 |
| 发现问题 | 2 个（1 个实现缺陷已修，1 个测试断言缺陷已修） |

**一句话结论**：L1 数据面从设计文字变为可验证事实 —— 服务器裸库承载完整历史，
推送后 6 秒内自动镜像到 GitHub，凭证为仓库级 Deploy Key，全流程幂等可重建。

## 二、测试范围

本变更为基础设施/运维性质，验证以**端状态**为准（远端命令存在执行两次的环境特性，
命令回显可能是两次中某一次的输出，不足为凭）。

| 层 | 验证方式 |
|---|---|
| 静态 | 脚本语法、断言脚本不含危险操作、不含明文凭证 |
| 集成 | 裸库端状态、Deploy Key 形态、ssh 配置、钩子内容 |
| E2E | 真实提交 → 推送 → GitHub 出现同一 sha |

## 三、详细结果

### 3.1 服务器权威裸库（REQ-001）

| TC | 验证 | 实测 | 结果 |
|---|---|---|---|
| TC-SBR-001 | 裸库为 bare 库 | `rev-parse --is-bare-repository` → `true` | ✅ |
| TC-SBR-002 | sha 与本地一致 | 本地 `d5012a0` = 裸库 `d5012a0` | ✅ |
| TC-SBR-003 | 历史与 tag 完整 | 79 提交（首推时）；含 `refs/tags/fstdd-v1.0.0` | ✅ |
| TC-SBR-004 | 幂等重跑不重置 | 二次执行输出 `[SKIP] 裸库已存在，保留现有数据`，提交数不变 | ✅ |

**证据（端状态）**：

```
is_bare    : true
裸库 HEAD  : d5012a095e3896f6fc0080d7b2546235c5ee15ec
提交数     : 79
refs       : refs/heads/master refs/tags/fstdd-v1.0.0
裸库大小   : 1.5M
磁盘       : 82% used, 11G free
```

### 3.2 GitHub 自动镜像（REQ-002）

| TC | 验证 | 实测 | 结果 |
|---|---|---|---|
| TC-SBR-005 | 端到端钩子镜像 | 推送 `3dbcadc` 后 GitHub 侧出现同一 sha | ✅ |
| TC-SBR-006 | 非强制推送 | 钩子 `push github --all` / `--tags`，均无 `--force` | ✅ |
| TC-SBR-007 | 不可达不阻塞 | 钩子含 `timeout 180/120` 且末尾 `exit 0` | ✅ |

**端到端证据**（关键链路）：

```
$ git push server master
   d5012a0..3dbcadc  master -> master        # 耗时 12.5s（含钩子同步镜像）

$ ssh fstdd-hub 'git -C <bare> rev-parse master; git -C <bare> ls-remote github refs/heads/master'
3dbcadce41078041abfb7a880c98bb78436c7e26    # 裸库
3dbcadce41078041abfb7a880c98bb78436c7e26    # GitHub  ← 一致

$ tail mirror.log
--- 2026-09-17T14:01:05+08:00 post-receive: mirroring to github ---
To github.com:2749817087qq/DKK_Fstdd.git
   d5012a0..3dbcadc  master -> master
[OK] branches mirrored
Everything up-to-date
[OK] tags mirrored
--- 2026-09-17T14:01:11+08:00 mirror finished ---      # 镜像耗时 6s
```

### 3.3 最小权限凭证（REQ-003）

| TC | 验证 | 实测 | 结果 |
|---|---|---|---|
| TC-SBR-008 | Deploy Key 形态 | API 返回 `id=163565733, title=fstdd-hub-mirror, read_only=false, verified=true` | ✅ |
| TC-SBR-009 | 服务器无明文 PAT | 检索 `ghp_` / `github_pat_` / `sk-` / `AKIA` → 无匹配 | ✅ |
| TC-SBR-010 | GitHub 认证成功 | `Hi 2749817087qq/DKK_Fstdd! You've successfully authenticated` | ✅ |

私钥落盘权限：`/home/ubuntu/.ssh/fstdd_github_ed25519` → `600` ✅

### 3.4 幂等部署（REQ-004）

| TC | 验证 | 实测 | 结果 |
|---|---|---|---|
| TC-SBR-011 | 脚本语法与可执行 | `bash -n` 无输出；可执行位已设 | ✅ |
| TC-SBR-012 | 密钥复用不重复注册 | 二次执行：别名 SKIP、密钥复用、`[SKIP] deploy key 已存在` | ✅ |

**二次执行完整输出（幂等证据）**：

```
[SKIP] 裸库已存在，保留现有数据          path=... bare=true
[SKIP] 本地 SSH 别名 fstdd-hub 已存在
[UPDATE] remote server -> fstdd-hub:/home/ubuntu/fstdd-git/stdd-repo.git
连通验证: 3dbcadce41078041abfb7a880c98bb78436c7e26
指纹: SHA256:Qdig8LHbmJKc5S0ujJU720d2++Qeh0YO1u+hyaPMEiw   ← 与首跑一致
[SKIP] deploy key 已存在
ssh 认证: Hi 2749817087qq/DKK_Fstdd! ...
端状态验证: 裸库 HEAD = GitHub HEAD = 3dbcadce...
```

### 3.5 主机信任治理（REQ-005）

| TC | 验证 | 实测 | 结果 |
|---|---|---|---|
| TC-SBR-013 | 与服务器一致 | 服务器提供 3 条密钥，known_hosts 3 条，**完全一致** | ✅ |
| TC-SBR-014 | 连接无告警 | `ssh fstdd-hub 'echo OK'` 的 stderr **为空** | ✅ |

**根因判定**：告警源于服务器主机密钥轮换，非中间人。依据：
- 服务器现提供 ED25519 `SHA256:Fj27m8oNh19...` + RSA + ECDSA
- 该 ED25519 指纹与告警中服务器声称的指纹**完全一致**
- 更新前已备份 `known_hosts.bak.20260917`（933 bytes）

### 3.6 不破坏既有约束（REQ-006）

| TC | 验证 | 实测 | 结果 |
|---|---|---|---|
| TC-SBR-015 | 全量测试保持绿 | 见 §六 | ✅ |
| TC-SBR-016 | 无凭证进入仓库 | 暂存内容扫描：3 处命中均为**检测规则自身的字面量**，非真实凭证 | ✅ |

## 四、发现并修复的问题

### D1 — 部署脚本 remote 使用裸主机名，绕过 IdentityFile（实现缺陷，已修）

**现象**：执行部署脚本后再推送，报 `Permission denied (publickey)`。

**根因**：脚本把 remote 写成 `ubuntu@43.134.236.80:/home/ubuntu/fstdd-git/stdd-repo.git`。
裸主机名**不匹配** `~/.ssh/config` 中的 `Host fstdd-hub` 段，因此 `IdentityFile D:/id_ed25519`
不生效，git 退回默认密钥（该密钥不在服务器 authorized_keys 中）而失败。

**修法**：remote 改用 `FSTDD_SSH_ALIAS`（默认 `fstdd-hub`）；脚本额外幂等写入本地 ssh 别名；
ssh config 中的 IdentityFile 用 Windows 风格路径（`/d/...` 形式 Windows OpenSSH 解析不到）。
新增断言 `test_deploy_bare_repo_remote_uses_ssh_alias` 守住该行为。

**教训**：这与本项目已知的「远端命令执行两次」「代理层故障」同属**基础设施类陷阱** ——
症状（认证失败）与根因（配置未命中）相距很远，只看报错文字会误判为密钥问题。

### D2 — onboarding 契约断言对路径片段误报（测试缺陷，已修）

**现象**：`test_b4_onboarding_manual_no_legacy_phases` 失败，报"仍有 stdd 旧命令名"。

**根因**：断言正则 `(?<![A-Za-z])stdd(?![a-z])` 只设了左边界。服务器真实路径
`/home/ubuntu/fstdd-git/stdd-repo.git` 中，`stdd` 左侧是 `/`、右侧是 `-`，**被误判为裸命令名**。

**修法**：右边界补齐为 `(?![a-z\-/.])`，排除路径/文件片段；裸命令名（后跟空格、标点、行尾）
仍会被捕获。这与该测试文件自身记录的原则一致（"必须用左边界断言，不能用 in"，避免子串误报）。

**验证**：修正后 `test_cross_cutting_verification.py` **24 passed**。

### D3 — 孪生手册同步（一致性维护）

`.fstdd/onboarding/AI_OPERATING_MANUAL.md` 与 `upstream/.fstdd/onboarding/AI_OPERATING_MANUAL.md`
是孪生副本。按上一轮 change 的既定结论（"只改仓库副本会导致新项目仍生成旧文本"），
本次改动**两版同步**，`diff` 实测完全一致。

### 附：发现的工具级不一致（本变更未处理）

`validate.py` 的 AND 检查与 `ci.py::check_and_count` 的注释口径不一致：
- `ci.py` docstring 写 `(g) Check AND count per scenario`（每场景）
- 两处实现均为 `len(re.findall(r"\*\*AND\*\*", content))`（**整文件**）

后果：多场景但每场景 AND 很少的 spec 会触发"超过上限 5"的软警告（本变更即如此，
每个场景 AND ≤ 2，文件级总数 15）。**建议后续 change 统一口径**，本变更不擅自扩大范围。

## 五、遗留与后续

| 项 | 说明 | 建议 |
|---|---|---|
| 镜像失败无主动告警 | `mirror.log` 的 `[WARN]` 需人工查看 | 后续加巡检或通知 |
| 裸库无冷备 | 服务器为单点；本地有 `local` 裸库兜底 | 后续可加对象存储备份 |
| 服务器磁盘 82%（剩 11G） | 裸库仅 1.5M，非主因 | 纳入运维水位巡检 |
| 本地 token 权限 | Windows 下 `chmod 600` 未生效（实测 `-rw-r--r--`） | 如需收紧可用 `icacls` |
| 多节点注册 | 需 D哥 提供 6 个 agent 的节点信息 | 待信息齐备后单独推进 |

## 六、全量回归

```
$ cd upstream && C:/Python311/python.exe -m pytest tests -q
592 passed in 390.67s (0:06:30)
EXIT_CODE=0
```

| 项 | 数值 |
|---|---|
| 基线（上一轮 change 归档时） | 586 passed |
| 本变更新增 | 6（`upstream/tests/test_server_bare_repo_ops.py`） |
| **最终** | **592 passed / 0 failed** |

回归过程中另修正 1 个既有测试的断言（`test_b4_onboarding_manual_no_legacy_phases`
的右边界，见 D2），该文件用例数不变（24 passed）。

## 七、结论

数据面已从设计文字变为**可验证事实**：

- 服务器裸库承载完整历史（80 提交 + tag），是跨机协作的唯一真值源
- 推送后 6 秒内自动镜像到 GitHub，三方 sha 收敛一致
- 镜像凭证为仓库级 Deploy Key，服务器上不存在明文 PAT
- 数据面重建是一条幂等命令（二次执行全部 SKIP 且数据无损）
- SSH 告警噪音消除，真实的主机密钥变更重新可见

16 项 TC 全部通过；过程中发现并修复 1 个实现缺陷、1 个测试断言缺陷，
两者均已立断言守住，避免回归。
