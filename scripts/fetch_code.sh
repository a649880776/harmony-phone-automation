#!/bin/bash
# fetch_code.sh —— 从 devin 注册后台取邮箱验证码
#
# 用法：./fetch_code.sh <email> [初始等待秒数]
#
# 规则（业务方明确要求，改动前先确认）：
#   * 提交注册后等 N 秒（默认 5）再取；
#   * 验证码接口**只能成功取一次**，取到后不要重复调用，重复调用会让旧码失效；
#   * 返回为空时等 5 秒重试，最多 3 次；3 次都空 = 判定失败，输出失败报告并以 1 退出。
#
# 成功输出：CODE=<6位码>（写进 stdout，便于调用方 grep）
# 失败输出：FAILED=<原因>

set -uo pipefail
EMAIL=${1:?用法: ./fetch_code.sh <email> [初始等待秒数]}
INIT_WAIT=${2:-5}
API="https://agent.hiaihk.com/prod/api/confirmationCode?email=${EMAIL}"
MAX_TRY=3

sleep "$INIT_WAIT"

for i in $(seq 1 $MAX_TRY); do
  RESP=$(curl -sS --max-time 20 "$API" 2>&1)
  CODE=$(printf '%s' "$RESP" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
    print((d.get("data") or {}).get("confirmation_code") or "")
except Exception:
    print("")
')
  echo "[尝试 $i/$MAX_TRY] code='${CODE}'" >&2
  echo "$RESP" >&2
  if [ -n "$CODE" ]; then
    echo "CODE=$CODE"
    exit 0
  fi
  [ "$i" -lt "$MAX_TRY" ] && sleep 5
done

echo "FAILED=验证码连续 ${MAX_TRY} 次为空（邮箱 ${EMAIL}）"
exit 1
