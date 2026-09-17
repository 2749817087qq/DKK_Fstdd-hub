#!/bin/bash
# fstdd-hub 镜像钩子 —— 服务器裸库 → GitHub
#
# 安装位置：/home/ubuntu/fstdd-git/fstdd-hub.git/hooks/post-receive
# 安装命令：scp 本文件到服务器 → cp 到 hooks/post-receive → chmod +x
#
# 设计要点（与 stdd-repo 的镜像钩子一致）：
#   * 恒以 exit 0 退出。真值源（服务器裸库）已更新，push 在语义上已经成功；
#     若此处返回非零，会制造「报失败但其实成功」的混乱，并诱导使用者改用
#     --force 重推。失败只通过日志与 [MIRROR-FAIL] 标记表达。
#   * 用 --mirror 同步所有 refs（分支 + tag），不用逐个 refspec。
#   * ⚠️ 必须用 ssh 别名 github-fstdd-hub，不能写 git@github.com。
#     服务器 ~/.ssh/config 里 Host github.com 绑定的是 DKK_Fstdd 的**专属 deploy key**
#     （GitHub 限制一个 deploy key 只能绑一个仓库），用它推本仓库会得到
#     "Permission to ... denied to deploy key"。
#     github-fstdd-hub 指向专用 key fstdd_hub_mirror_ed25519，其公钥已作为
#     本仓库的 deploy key（id 163628534, read_only=false）注册。
#     判断依据：`ssh -T git@github.com` 返回 "Hi <owner>/<repo>!" 是 deploy key 格式，
#     返回 "Hi <username>!" 才是账号级 key。

set -uo pipefail
export HOME=/home/ubuntu
unset GIT_DIR GIT_WORK_TREE

LOG=/home/ubuntu/fstdd-git/fstdd-hub-mirror.log
TARGET=git@github-fstdd-hub:2749817087qq/DKK_Fstdd-hub.git

{
  echo "--- $(date -Iseconds) post-receive: mirroring to github ---"
  if git push --mirror "$TARGET"; then
    echo "[MIRROR-OK] mirror complete"
  else
    echo "[MIRROR-FAIL] rc=$?"
  fi
  echo "--- $(date -Iseconds) mirror finished ---"
} >> "$LOG" 2>&1

exit 0
