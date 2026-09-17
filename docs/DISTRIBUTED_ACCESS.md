# FSTDD 多节点接入指引 / Distributed Access Guide

> 适用：6 个 AI agent / 3 台开发机共同推进同一代码库
> 建立日期：2026-09-17
> 相关 change：`2026-09-17-server-bare-repo-mirror`（数据面）、`2026-09-17-distributed-task-coordination`（控制面）、`2026-09-17-mirror-failure-alert`（镜像失败告警）

## 一、架构速览

```
  开发机 A / B / C 上的 6 个 agent
        │
        │  git-over-ssh（唯一数据面入口）
        ▼
  云服务器 43.134.236.80
  /home/ubuntu/fstdd-git/stdd-repo.git   ← L1 数据面：唯一真值源
        │
        │  post-receive 钩子（Deploy Key，非强制）
        ▼
  GitHub 2749817087qq/DKK_Fstdd          ← 镜像 / 对外通道
```

**三条硬规则**：

1. **服务器裸库是唯一真值源**。任何机器的本地仓库都只是副本，不得作为"事实"依据。
2. **禁止跨机文件拷贝式同步**。所有产出必须走 `git push` —— 本项目已因手工 cp 发生过两次漂移。
3. **GitHub 是镜像，不是源**。GitHub 不可达不阻塞任何流转。

## 二、新节点接入（3 步）

### 步骤 1 — 配置 SSH 别名

在 `~/.ssh/config` 中加入（把 `IdentityFile` 换成该机器的私钥路径）：

```sshconfig
Host fstdd-hub
    HostName 43.134.236.80
    User ubuntu
    IdentityFile <该机器的私钥路径>
    IdentitiesOnly yes
    StrictHostKeyChecking accept-new
    ServerAliveInterval 30
    ServerAliveCountMax 4
```

接入前需把该机器的**公钥**加入服务器 `/home/ubuntu/.ssh/authorized_keys`（由运维执行）。

验证：

```bash
ssh fstdd-hub 'echo OK; hostname'
# 期望输出 OK 与 VM-0-17-ubuntu，且 stderr 为空（无主机密钥告警）
```

### 步骤 2 — 克隆或挂载 remote

新机器首次：

```bash
git clone fstdd-hub:/home/ubuntu/fstdd-git/stdd-repo.git
```

已有本地仓库（如法定源 `D:/tools/FSTDD/stdd-repo`）：

```bash
git remote add server fstdd-hub:/home/ubuntu/fstdd-git/stdd-repo.git
# 若已存在则改用：
# git remote set-url server fstdd-hub:/home/ubuntu/fstdd-git/stdd-repo.git
```

### 步骤 3 — 验证接入

```bash
git fetch server
git rev-parse server/master        # 应与服务器裸库 HEAD 一致
```

## 三、日常操作

| 动作 | 命令 |
|---|---|
| 拉取最新 | `git fetch server && git rebase server/master` |
| 推送产出 | `git push server <branch>` |
| 推送并自动镜像 | 同上（钩子自动完成，无需额外操作） |
| 查镜像状态 | `./tools/check_mirror.sh` —— 退出码 **0**=已收敛 / **2**=滞后 / **3**=无法测量 |
| 核对三方一致 | 对比本地 `git rev-parse HEAD`、`server/master`、GitHub HEAD |

**推送后如何确认镜像成功**：推送命令返回后，钩子已同步执行完毕，
**镜像结果会直接回显在你的 `git push` 输出里**，无需任何额外命令：

```
remote: [MIRROR-STEP] branches mirrored
remote: [MIRROR-STEP] tags mirrored
remote: [MIRROR-OK] mirror complete: branches + tags up to date
```

三个标识**语义互斥**，判断成败只看标识本身，不要只看字面：

| 标识 | 含义 |
|---|---|
| `[MIRROR-STEP]` | 单项进度（branches / tags 各自的镜像结果）。**单项成功不等于整体成功** |
| `[MIRROR-OK]` | **整体**成功（branches 与 tags 均已同步） |
| `[MIRROR-FAILED]` | 有任一项失败，附结构化告警块 |
| `[MIRROR-ALERT]` | **辅助通道**失效（控制面告警未上报：服务器缺 `curl`；或故障标记写入失败）。**回显、退出码与日志不受影响** |

> 之所以把「逐项」与「整体」拆成两个标识：若逐项成功也打 `MIRROR-OK`，
> 则「branches 失败、tags 成功」时会同时出现成功与失败标识，
> 用 `grep MIRROR-OK` 就会把**整体失败**误读为成功 —— 而分叉（GitHub 领先）
> 恰恰是最需要告警的场景。

镜像失败时同一位置会出现结构化告警块（`[MIRROR-FAILED]`），
明确写出「裸库已更新 / GitHub 未同步 / 对外通道滞后」以及失败项与失败原因。

需要**事后**核对（例如推送的人不是你）时，一条命令给出判定：

```bash
./tools/check_mirror.sh      # 0=已收敛  2=滞后  3=无法测量（前置条件缺失或远端不可达）
```

该命令只读、幂等，不写任何状态；依赖仅 `bash` / `git` / `ssh` 与 coreutils
（`echo` / `sed` / `grep` / `head` / `cut` / `timeout`），**不依赖 `jq`** ——
任何节点 `git pull` 后可直接执行。

**镜像目标**（`FSTDD_MIRROR_URL`）：默认是裸库里的 remote 名 `github`，
可覆盖为任意 URL 或本地路径 —— 钩子与巡检命令都支持：

```bash
FSTDD_MIRROR_URL=/tmp/other.git ./tools/check_mirror.sh   # 巡检另一个目标
```

该变量**仅供自动化验证**（端到端脚本用它把镜像指向临时库，从而不碰真实配置）；
生产环境不设置，一律走 remote 名 `github`。

> 为什么是「直传目标」而不是「用环境变量覆盖 remote URL」：
> `remote.<name>.url` 是 git 的**多值**键 —— 第一个值用于 fetch，全部值用于 push。
> 环境注入只往列表末尾**追加**一项，于是注入会「看起来生效」：
> `git config --get remote.github.url` 返回注入值，而 `git ls-remote` 仍走
> 第一个 URL。实测注入一个必然被拒的地址（`http://127.0.0.1:9/`）后，
> `ls-remote` 依旧返回真实 GitHub 的 sha ——「无法测量 / 滞后」两条判定
> 因此永远测不出来；更糟的是真实 GitHub 仍留在 push 目标里，
> 验证脚本会**真的推线上**。直传目标则完全绕过 remote 配置解析。

> 判定顺序：**先看故障标记，再看 sha 是否一致**。故障标记是上一次失败留下的
> 确定事实；即使此刻 GitHub 不可达（本可判「无法测量」），只要标记还在，
> 仍返回 `2`（滞后）并打印标记内容 —— 否则会把已知的失败项与发生时间丢掉。

## 四、节点标识约定

| node_id | 说明 |
|---|---|
| `FSTDD001` … `FSTDD006` | 6 个 agent 的稳定标识 |
| `FSTDD005` | 法定源所在开发机（D 盘工作区）上的 agent |
| `fstdd-hub-infra` | **基础设施节点**（服务器侧），用于投递镜像告警等运维通知；不是 agent |

- node_id 是**协作层**标识，与控制面（8788）注册表一致
- 节点 id **不得硬编码**在共享代码里，应由本地配置提供
- 控制面地址 `http://127.0.0.1:8788`（仅回环，经 SSH 访问）

## 五、故障处置

| 现象 | 处置 |
|---|---|
| 推送被拒（非快进） | 先 `git fetch server && git rebase server/master`，不要强推 |
| 推送输出出现 `[MIRROR-FAILED]` | 镜像未完成（**真值源已更新，数据安全**）。处置：① 执行 `./tools/check_mirror.sh` 判断是「滞后」（2）还是「无法测量」（3）；② 若是 GitHub 侧分叉，**不要**在服务器上手动 `--force`，先确认那几个提交的来源；③ 恢复镜像目标后**再次 push** 即自动重试，故障标记 `mirror-failed.flag` 会被自动清除 |
| GitHub 不可达 | 无需处置。裸库与各机流转不受影响，镜像会在下次推送时重试 |
| 主机密钥告警 | **不要**用 `StrictHostKeyChecking=no` 绕过。先 `ssh-keyscan <host> \| ssh-keygen -lf -` 取指纹，与告警中服务器声称的指纹核对，一致才更新 `known_hosts` |
| 服务器磁盘水位高 | `df -h /`；裸库本身仅 MB 级，水位主要来自其他服务 |

## 六、重建数据面

服务器裸库可一条命令重建（幂等，已存在则保留数据）：

```bash
./tools/deploy_server_bare_repo.sh
```

覆盖参数见脚本头部注释。`FSTDD_SKIP_MIRROR=1` 可只建裸库不碰 GitHub。
