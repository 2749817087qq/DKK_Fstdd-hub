#!/bin/bash
# fstdd-hub 镜像钩子 —— 服务器裸库 → GitHub
#
# 安装位置：/home/ubuntu/fstdd-git/fstdd-hub.git/hooks/post-receive
# 安装命令：scp 本文件到服务器 → cp 到 hooks/post-receive → chmod +x
#
# 设计要点（与 stdd-repo 的镜像钩子保持一致，勿随意删减）：
#
#   1. 恒以 exit 0 退出。真值源（服务器裸库）已更新，push 在语义上已经成功；
#      若此处返回非零，会制造「报失败但其实成功」的混乱，并诱导使用者改用
#      --force 重推。失败只通过日志 + 故障标记 + 控制面告警表达。
#   2. 失败时三件套缺一不可（2026-09-18 QA 发现初版只有 echo，导致一次真实
#      镜像失败全程无人知晓）：
#        a. 写故障标记文件 mirror-failed.flag（供 check_mirror.sh 与监控消费）
#        b. 上报控制面 /messages（短超时 3s + 失败容忍，不得阻塞推送）
#        c. timeout 守卫，防镜像卡死拖住 push
#   3. 失败标记统一写 [MIRROR-FAILED]（不带 D），与 stdd-repo 钩子一致，
#      否则共享监控脚本会漏判。
#   4. ⚠️ 必须用 ssh 别名 github-fstdd-hub，不能写 git@github.com。
#      服务器 ~/.ssh/config 里 Host github.com 绑定的是 DKK_Fstdd 的**专属 deploy key**
#      （GitHub 限制一个 deploy key 只能绑一个仓库），用它推本仓库会得到
#      "Permission to ... denied to deploy key"。
#      github-fstdd-hub 指向专用 key fstdd_hub_mirror_ed25519，其公钥已作为
#      本仓库的 deploy key（id 163628534, read_only=false）注册。
#      判定法：`ssh -T git@github.com` 返回 "Hi <owner>/<repo>!" 是 deploy key，
#      返回 "Hi <username>!" 才是账号级 key。

set -uo pipefail
export HOME=/home/ubuntu
unset GIT_DIR GIT_WORK_TREE

# 兜底：无论从哪条路径结束，本钩子恒以 exit 0 退出。
trap 'exit 0' EXIT

LOG=/home/ubuntu/fstdd-git/fstdd-hub-mirror.log
FLAG=/home/ubuntu/fstdd-git/fstdd-hub-mirror-failed.flag
BARE=/home/ubuntu/fstdd-git/fstdd-hub.git
TARGET=git@github-fstdd-hub:2749817087qq/DKK_Fstdd-hub.git
HUB_URL=http://127.0.0.1:8788
HUB_NODE_ID=fstdd-hub-infra

ts() { date -Iseconds; }

{
  echo "============================================================"
  echo "--- $(ts) post-receive: mirroring to github ---"

  FAILED_ITEMS=""
  FAILED_DETAIL=""

  # 分支（超时守卫 180s，防卡死拖住 push）
  if timeout 180 git -C "$BARE" push "$TARGET" --all 2>&1; then
    echo "[MIRROR-STEP] branches mirrored"
  else
    rc=$?
    FAILED_ITEMS="${FAILED_ITEMS}branches "
    FAILED_DETAIL="${FAILED_DETAIL}branches=rc${rc};"
    echo "[MIRROR-FAILED] branches push failed rc=$rc"
  fi

  # 标签（超时守卫 120s）
  if timeout 120 git -C "$BARE" push "$TARGET" --tags 2>&1; then
    echo "[MIRROR-STEP] tags mirrored"
  else
    rc=$?
    FAILED_ITEMS="${FAILED_ITEMS}tags "
    FAILED_DETAIL="${FAILED_DETAIL}tags=rc${rc};"
    echo "[MIRROR-FAILED] tags push failed rc=$rc"
  fi

  bare_head="$(git -C "$BARE" rev-parse --short HEAD 2>/dev/null || echo unknown)"

  if [ -z "$FAILED_ITEMS" ]; then
    # 成功：清除故障标记
    rm -f "$FLAG" 2>/dev/null || true
    echo "[MIRROR-OK] mirror complete: branches + tags up to date"
  else
    # (a) 故障标记
    if {
      echo "time=$(ts)"
      echo "failed_items=${FAILED_ITEMS% }"
      echo "detail=${FAILED_DETAIL%;}"
      echo "bare_head=$bare_head"
    } > "${FLAG}.tmp" 2>/dev/null; then
      mv -f "${FLAG}.tmp" "$FLAG" 2>/dev/null || true
    else
      rm -f "${FLAG}.tmp" 2>/dev/null || true
      echo "[MIRROR-ALERT] 故障标记写入失败（$FLAG）—— 退出码不受影响"
    fi

    # (b) 上报控制面：尽力而为，短超时 + 失败容忍（不得阻塞推送）
    hub_body="镜像未完成：失败项=${FAILED_ITEMS% }；原因=${FAILED_DETAIL%;}；时间=$(ts)；裸库=$bare_head"
    hub_json="$(printf '{"idempotency_key":"%s","from_node_id":"%s","kind":"notice","body":"%s"}' \
      "fstdd-hub-mirror-fail:${bare_head}:${FAILED_ITEMS% }" "$HUB_NODE_ID" "$hub_body")"
    if command -v curl >/dev/null 2>&1; then
      curl -s --max-time 3 -X POST "$HUB_URL/messages" \
        -H 'Content-Type: application/json' -d "$hub_json" >/dev/null 2>&1 || true
    else
      echo "[MIRROR-ALERT] 控制面告警未上报：服务器缺少 curl"
    fi

    echo "[MIRROR-FAILED] 镜像未完成 —— 服务器裸库已更新，GitHub 未同步"
    echo "  失败项  : ${FAILED_ITEMS% }"
    echo "  失败原因: ${FAILED_DETAIL%;}"
    echo "  故障标记: $FLAG"
  fi

  echo "--- $(ts) mirror finished ---"
} >> "$LOG" 2>&1

exit 0
