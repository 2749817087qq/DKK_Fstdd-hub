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
#   * 服务器默认 SSH key 属 2749817087qq 账号级 key，对该账号下仓库天然有写权限，
#     因此不需要额外配 deploy key。

set -uo pipefail
export HOME=/home/ubuntu
unset GIT_DIR GIT_WORK_TREE

LOG=/home/ubuntu/fstdd-git/fstdd-hub-mirror.log
TARGET=git@github.com:2749817087qq/DKK_Fstdd-hub.git

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
