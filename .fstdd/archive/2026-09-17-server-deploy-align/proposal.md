# 服务器部署与新项目对齐（补齐文件 + 备份定时）

<!-- source_hash: 372bf6789c3682e2 -->
<!-- generated_at: 2026-09-17T23:19:55.361911 -->
<!-- canonical: canonical/proposals/2026-09-17-server-deploy-align.yaml -->

## Why

盘点发现服务器上的 L2 控制面只装了一半：只有 fstdd_hub.py 与 hub_backup.sh
两个文件落地（md5 与本地完全一致，零 drift），而 fstdd_git.py、hub_healthcheck.py、
deploy_hub.sh 三个文件从未上传。后果有两层：一是 L3 侧能力缺失——服务器上没有
git 封装与巡检入口，任何涉及 worktree 创建或 ff-only 集成的线上操作都无从执行；
二是验证盲区——本地 14 项测试覆盖不到服务器实际运行环境，因为服务器上根本没有
同样的文件，测的是本地副本而非线上真实部署。
此外，备份只有部署当时（09-17 12:12）生成的那一份，且没有任何定时调度，
hub_backup.sh 自带的 14 天轮转从未生效，此后 10 小时产生的数据（含 2 条镜像告警）
处于零备份状态。本 change 将服务器部署与新建的独立项目 fstdd-hub 对齐：
补齐缺失文件、以幂等方式重跑部署、数据库原地接管、给备份挂上定时调度。


## What Changes

- 向服务器 /home/ubuntu/fstdd-hub-server/ 补齐两个缺失文件：fstdd_git.py、hub_healthcheck.py（此前全盘 find 无命中）
- 以新项目 fstdd-hub/tools/ 为来源重跑 deploy_hub.sh；因 md5 与服务器现有版本一致，属幂等操作，不改变运行时行为
- 数据库原地接管：沿用 /home/ubuntu/fstdd-hub/fstdd-hub.sqlite3，不做数据迁移（tasks 为 0，无真实业务数据）
- 为 hub_backup.sh 挂 systemd timer，使其 14 天轮转逻辑真正生效，消除零备份隐患
- 部署后端到端验证：服务状态、监听地址、/health、四文件 md5 一致性、timer 排定、备份数增加、数据库行数不变

### New Capabilities

- **服务器文件完整性**：服务器 fstdd-hub-server/ 下具备与本地 tools/ 一致的完整文件集（fstdd_hub.py、fstdd_git.py、hub_healthcheck.py、hub_backup.sh），消除 L3 侧能力缺失与线上验证盲区
- **备份定时调度**：hub_backup.sh 由 systemd timer 定期调用，14 天轮转真正生效，不再依赖部署时那一份手工备份

### Modified Capabilities

- **部署权威来源**：服务器代码的权威来源由 stdd-repo/tools/ 切换为独立项目 fstdd-hub/tools/；两者 md5 一致，切换本身不改变运行时行为，仅为确立归属

## Success Criteria

- [ ] 服务器 /home/ubuntu/fstdd-hub-server/ 下 fstdd_hub.py、fstdd_git.py、hub_healthcheck.py、hub_backup.sh 四个文件的 md5 与本地 fstdd-hub/tools/ 完全一致
- [ ] systemctl is-active fstdd-hub 返回 active，且监听仅为 127.0.0.1:8788，不得出现 0.0.0.0 绑定
- [ ] 经 SSH 端口转发 curl /health 返回 ok
- [ ] systemctl list-timers 中出现 fstdd 备份 timer，且 NEXT 触发时间已排定
- [ ] 手动触发一次备份后，/home/ubuntu/fstdd-hub/backups/ 新增至少 1 个 sqlite3 备份文件
- [ ] 数据库行数保持不变：nodes=1、tasks=0、idempotency=2、messages=2，不丢失任何数据
- [ ] 不执行破坏性操作；如需重启服务，须确认 Restart=always 能自动拉起后再执行
