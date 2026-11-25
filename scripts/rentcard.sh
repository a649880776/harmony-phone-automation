#!/bin/bash
# rentcard.sh —— 结算上报：把 email + card_id 标记为已用
#
# 用法：
#   ./rentcard.sh                 # 从 profile.json 取 email/card_ref，从 .env 取 token
#   ./rentcard.sh <email> <card_id>
#
# 只调一次。非 200 或 success:false 直接非零退出并提示人工处理，避免重复提交。
# 报告里不打印 .env 的 token。

set -uo pipefail
SELF_DIR=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$SELF_DIR/.." && pwd)
ENVF=${ENV_FILE:-$ROOT/.env}

[ -f "$ENVF" ] || { echo "缺少 $ENVF" >&2; exit 2; }

# 逐行取值，绝不 source（source 会执行内容、出错时还可能把密钥打到终端）
getenv() {
  awk -v k="$1" '
    /^[[:space:]]*#/ { next }
    {
      eq = index($0, "=")
      if (eq == 0) next
      key = substr($0, 1, eq-1); val = substr($0, eq+1)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", key)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", val)
      gsub(/^["\047]|["\047]$/, "", val)
      if (key == k) { print val; exit }
    }' "$ENVF"
}

TOKEN=$(getenv RENTCARD_TOKEN)
BASE=$(getenv RENTCARD_BASE); BASE=${BASE:-https://agent.hiaihk.com/prod/api}
[ -n "$TOKEN" ] || { echo "缺少 RENTCARD_TOKEN" >&2; exit 2; }

EMAIL=${1:-}; CARD_ID=${2:-}
if [ -z "$EMAIL" ] || [ -z "$CARD_ID" ]; then
  read -r E C < <(python3 - "$ROOT/profile.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1],encoding='utf-8'))
print(d.get('email',''), d.get('card_ref') or d.get('card_id') or '')
PY
)
  EMAIL=${EMAIL:-$E}; CARD_ID=${CARD_ID:-$C}
fi
[ -n "$EMAIL" ] && [ -n "$CARD_ID" ] || { echo "缺少 email / card_id" >&2; exit 2; }

BODY=$(python3 -c 'import json,sys;print(json.dumps({"email":sys.argv[1],"card_id":sys.argv[2]}))' "$EMAIL" "$CARD_ID")

RESP=$(curl -sS -X POST "${BASE%/}/rentCard?token=${TOKEN}" \
        -H 'Content-Type: application/json' -d "$BODY" \
        -w '\n%{http_code}' --max-time 25)
CODE=$(printf '%s' "$RESP" | tail -1)
BODYJSON=$(printf '%s' "$RESP" | sed '$d')

python3 - "$CODE" "$BODYJSON" <<'PY'
import json,sys
code, body = sys.argv[1], sys.argv[2]
try: d=json.loads(body)
except Exception: d={}
ok = code=="200" and d.get("success") is True
data = d.get("data") or {}
print(("[OK] " if ok else "[FAIL] ") + f"HTTP {code} {d.get('message','')}".strip())
if data:
    print(f"  use_status={data.get('use_status')} card_id={data.get('card_id')} "
          f"card_key={data.get('card_key')} channels={data.get('use_channels')}")
    if data.get("withdraw_amount") is not None:
        print(f"  withdraw={data.get('withdraw_amount')} ({data.get('withdraw_status')})")
sys.exit(0 if ok else 1)
PY
