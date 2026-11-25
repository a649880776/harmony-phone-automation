#!/bin/bash
# vault.sh —— 密文取用封装：Keychain 存取 + 脱敏显示 + 直接注入手机
#
# 设计原则：密文只允许存在于「Keychain → 本进程变量 → hdc 参数」这条链上，
# 任何时候都不打印明文；对外只输出脱敏值，并往 audit.log 记一条（值为 ***）。
#
# 用法：
#   ./vault.sh put  devin.card CK0IS           # 从 stdin 读 JSON 存进钥匙串（不回显）
#   ./vault.sh get  devin.card CK0IS           # 取整块 JSON（只在管道里用，别直接看）
#   ./vault.sh field devin.card CK0IS pan      # 取单个字段（仍不建议直接打印）
#   ./vault.sh mask devin.card CK0IS pan       # 取出来只显示脱敏值
#   ./vault.sh type devin.card CK0IS pan 609 2184   # 取 → 点 → 输，回显脱敏
#   ./vault.sh token put <email>               # 从 stdin 存 PAT
#   ./vault.sh token mask <email>
#   ./vault.sh ls                              # 列出本技能包用到的服务名

set -uo pipefail
SELF_DIR=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$SELF_DIR/.." && pwd)
PH=${PH:-$SELF_DIR/ph.sh}
AUDIT=${AUDIT:-$ROOT/audit.log}
export SHOTS_DIR=${SHOTS_DIR:-$ROOT/shots}

log() { printf '%s\t%s\n' "$(date -Is)" "$*" >> "$AUDIT" 2>/dev/null || true; }

mask_generic() { sed -E 's/^(.{4}).*(.{4})$/\1****\2/'; }

cmd=${1:-}

case "$cmd" in
  put)
    svc=${2:?service}; acct=${3:-default}
    secret=$(cat)                      # 从 stdin 读，绝不作为参数传
    security add-generic-password -U -s "$svc" -a "$acct" -w "$secret" >/dev/null && \
      { echo "已写入钥匙串：$svc/$acct（${#secret} 字节）"; log "put $svc/$acct ***"; }
    ;;
  get)
    svc=${2:?service}; acct=${3:-default}
    security find-generic-password -s "$svc" -a "$acct" -w
    ;;
  field)
    svc=${2:?service}; acct=${3:-default}; f=${4:?field}
    security find-generic-password -s "$svc" -a "$acct" -w \
      | python3 -c "import sys,json;print(json.load(sys.stdin)['$f'])"
    ;;
  mask)
    svc=${2:?service}; acct=${3:-default}; f=${4:-}
    if [ -n "$f" ]; then
      v=$(security find-generic-password -s "$svc" -a "$acct" -w | python3 -c "import sys,json;print(json.load(sys.stdin)['$f'])")
    else
      v=$(security find-generic-password -s "$svc" -a "$acct" -w)
    fi
    case "$f" in
      cvc|cvv) printf '***\n' ;;
      exp)     printf '**/**\n' ;;
      *)       printf '%s\n' "$v" | mask_generic ;;
    esac
    ;;
  type)
    svc=${2:?service}; acct=${3:?account}; f=${4:?field}; x=${5:?x}; y=${6:?y}
    v=$(security find-generic-password -s "$svc" -a "$acct" -w | python3 -c "import sys,json;print(json.load(sys.stdin)['$f'])")
    [ -z "$v" ] && { echo "取值为空" >&2; exit 1; }
    "$PH" tap "$x" "$y" >/dev/null 2>&1
    sleep 0.6
    "$PH" text "$v" >/dev/null 2>&1
    log "type $svc/$acct.$f at $x,$y ***"
    case "$f" in
      cvc|cvv) echo "***" ;;
      exp)     echo "**/**" ;;
      *)       printf '%s\n' "$v" | mask_generic ;;
    esac
    ;;
  token)
    sub=${2:-mask}; acct=${3:-default}
    case "$sub" in
      put)  t=$(cat); security add-generic-password -U -s devin.pat -a "$acct" -w "$t" >/dev/null
            echo "PAT 已存入钥匙串（${#t} 字节，尾四位 ${t: -4}）"; log "put devin.pat/$acct ***" ;;
      mask) t=$(security find-generic-password -s devin.pat -a "$acct" -w); printf '%s\n' "$t" | sed -E 's/^(....).*(....)$/\1****\2/' ;;
      *)    echo "用法: vault.sh token put|mask <email>" >&2; exit 1 ;;
    esac
    ;;
  ls)
    cat <<'EOT'
本技能包使用的钥匙串条目：
  devin.card   <card_id>   {"pan":"...","exp":"MM/YY","cvc":"..."}
  devin.pat    <email>     cog_... 形式的 PAT
  devin.card.key <card_id> RSA 私钥 PEM（可选：用 CARD_RSA_KEY_PATH 指向文件也可以）
EOT
    ;;
  *)
    sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'
    ;;
esac
