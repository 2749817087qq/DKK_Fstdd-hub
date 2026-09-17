# Spec: server-bare-repo-mirror

> Change: 2026-09-17-server-bare-repo-mirror | Auto-generated Human View

## Requirements

### Requirement: 服务器裸库必须作为跨机协作的唯一数据面真值源，且创建过程幂等

#### Scenario: SC-001

- **GIVEN** 服务器可达且具备 git
- **WHEN** 执行数据面部署
- **THEN** 系统 SHALL 在约定路径创建 bare 仓库
- **AND** 裸库 SHALL 承载完整 master 历史与全部 tag
- **AND** 裸库 master sha SHALL 与部署发起方 master sha 一致

#### Scenario: SC-002

- **GIVEN** 裸库已存在且含有提交
- **WHEN** 再次执行数据面部署
- **THEN** 系统 SHALL 保留已有数据且不重置仓库
- **AND** 重复执行 SHALL NOT 产生非幂等副作用

### Requirement: 服务器裸库必须以非强制方式自动镜像到 GitHub

#### Scenario: SC-003

- **GIVEN** 服务器裸库配置了 GitHub 镜像 remote 与有效凭证
- **WHEN** 一次推送落到服务器裸库
- **THEN** 服务端钩子 SHALL 自动将分支与 tag 镜像到 GitHub
- **AND** 镜像 SHALL 在推送返回前完成或已完成尝试

#### Scenario: SC-004

- **GIVEN** GitHub 侧存在服务器裸库所没有的提交
- **WHEN** 钩子执行镜像
- **THEN** 镜像 SHALL 失败并被记录到日志
- **AND** 系统 SHALL NOT 静默强制覆盖远端提交

#### Scenario: SC-005

- **GIVEN** 镜像目标不可达
- **WHEN** 推送落到服务器裸库
- **THEN** 推送 SHALL 成功返回
- **AND** 镜像失败 SHALL NOT 影响裸库自身状态

### Requirement: 镜像凭证必须遵循最小权限，且不得以明文形式散落

#### Scenario: SC-006

- **GIVEN** 需要为服务器授予 GitHub 写权限
- **WHEN** 配置镜像凭证
- **THEN** 凭证 SHALL 为限定到单一仓库的 Deploy Key
- **AND** Deploy Key SHALL 具备写权限（read_only=false）
- **AND** 私钥 SHALL 仅存在于服务器且权限为 600

#### Scenario: SC-007

- **GIVEN** 凭证配置完成
- **WHEN** 服务器执行 GitHub 认证测试
- **THEN** 认证 SHALL 成功并识别出目标仓库
- **AND** 服务器上 SHALL NOT 存在明文 PAT

### Requirement: 数据面部署必须固化为可重复执行的脚本，并以端状态验证结果

#### Scenario: SC-008

- **GIVEN** 存在数据面部署脚本
- **WHEN** 执行部署脚本
- **THEN** 脚本 SHALL 完成建库、配 remote、配凭证、装钩子与验证
- **AND** 验证 SHALL 读取端状态（rev-parse / ls-remote）而非命令回显

#### Scenario: SC-009

- **GIVEN** 镜像密钥与 Deploy Key 已存在
- **WHEN** 再次执行部署脚本
- **THEN** 系统 SHALL 复用既有密钥
- **AND** 系统 SHALL NOT 重复注册 Deploy Key

### Requirement: SSH 主机信任必须与服务器实际密钥一致，不得残留过期记录

#### Scenario: SC-010

- **GIVEN** 本地 known_hosts 存有该服务器的过期主机密钥记录
- **WHEN** 更新主机信任记录
- **THEN** 记录 SHALL 与服务器当前提供的主机密钥一致
- **AND** 更新前 SHALL 备份原文件
- **AND** 指纹 SHALL 经核对确认（排除中间人）

#### Scenario: SC-011

- **GIVEN** 主机信任记录已更新
- **WHEN** 连接该服务器
- **THEN** 连接 SHALL 不产生主机密钥告警
- **AND** 连接的 stderr SHALL 为空

### Requirement: 数据面不得破坏既有工程约束

#### Scenario: SC-012

- **GIVEN** 数据面变更已完成
- **WHEN** 运行既有工程测试套件
- **THEN** 测试 SHALL 保持全绿
- **AND** 基线为 586 passed
