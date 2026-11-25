#!/bin/bash
# sync.sh —— 把技能目录同步到本仓库并推送
#
# 用法：
#   ./sync.sh "feat: 支持双卡切换"      # 同步 + 提交 + 推送
#   ./sync.sh --dry "看看会改什么"      # 只同步，不提交
#
# 源目录默认 ~/.dsh/skills/harmony-phone-automation，可用 SKILL_DIR 覆盖。
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
SRC="${SKILL_DIR:-$HOME/.dsh/skills/harmony-phone-automation}"
DRY=0
MSG=""
for a in "$@"; do
  case "$a" in
    --dry|-n) DRY=1 ;;
    *) MSG="$a" ;;
  esac
done

[ -f "$SRC/SKILL.md" ] || { echo "✗ 源目录不像技能包：$SRC" >&2; exit 2; }

# 仓库自有文件（源目录里没有），同步时保留
rsync -a --delete \
  --exclude '.git/' \
  --exclude '.env' --exclude '.env.*' \
  --exclude 'profile.json' --exclude 'devin_token.json' \
  --exclude '.pickcache.json' --exclude 'audit.log' --exclude '*.log' \
  --exclude 'shots/' --exclude '__pycache__/' --exclude '*.pyc' \
  --exclude '.DS_Store' \
  --exclude 'README.md' --exclude 'sync.sh' --exclude 'design/' \
  --exclude 'sanitize.py' \
  --exclude '.gitignore' --exclude 'profile.example.json' \
  "$SRC"/ "$REPO"/

cd "$REPO"

# 脱敏是同步流程的一部分：源目录是未脱敏原件，公开仓库不能原样接收
python3 "$REPO/sanitize.py"
python3 "$REPO/sanitize.py" --check || {
  echo "✗ 仍有敏感残留，已中止（未提交）" >&2
  git checkout -- . 2>/dev/null || true
  exit 3
}
git add -A
if git diff --cached --quiet; then
  echo "= 无变化，跳过提交"
else
  git diff --cached --stat
  [ "$DRY" = 1 ] && { echo "(dry-run：已暂存，未提交)"; exit 0; }
  git commit -q -m "${MSG:-sync: 从技能目录同步 $(date '+%Y-%m-%d %H:%M')}"
  echo "✓ 已提交 $(git rev-parse --short HEAD)"
fi

[ "$DRY" = 1 ] && exit 0
git push -q origin HEAD
echo "✓ 已推送 $(git rev-parse --abbrev-ref HEAD) -> origin"
