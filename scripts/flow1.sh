#!/bin/bash
# flow1.sh —— 流程一：关流量 → 切换数据卡 → 清理并重开自带浏览器 →
#             关闭全部标签 → 进无痕组新建无痕窗口 → 打开 devin.ai → 点 Get started
#
# 用法：./flow1.sh [目标网址]
# 依赖：同目录 ph.sh、find.py（同一设备、同一 hdc 通道）
#
# 设计原则：
#   * 每一步都先 dump 控件树再按文本找坐标，不硬写易漂移的坐标（少数固定入口除外）；
#   * 每步截图留档到 shots/flow1_*.jpeg，失败可回看哪一步偏了；
#   * 只做「本轮该做的事」，不重复点击已经处于目标状态的开关。

set -uo pipefail
cd "$(dirname "$0")" || exit 1
PH=./ph.sh
URL=${1:-https://devin.ai}
S=${SHOTS_DIR:-./shots}
STAMP=$(date +%H%M%S)

log() { printf '[flow1] %s\n' "$*"; }
shot() { $PH shot "flow1_${STAMP}_$1" >/dev/null; }
# 取控件树 -> 输出元素中心坐标 "x,y"
pos_of() {
  local q=$1 json
  json=$($PH ui "_t_${STAMP}" 2>/dev/null)
  python3 ./find.py "$json" "$q" 2>/dev/null | head -1 | awk -F'\t' '{print $3}'
}
tap_at() { # tap_at "x,y"
  local p=$1; [ -z "$p" ] && return 1
  $PH tap "${p%,*}" "${p#*,}" >/dev/null
}
wait_for() { # wait_for <文本> <最多秒>
  local q=$1 n=${2:-10} i=0 p
  while [ $i -lt "$n" ]; do
    p=$(pos_of "$q")
    if [ -n "$p" ]; then echo "$p"; return 0; fi
    sleep 1; i=$((i+1))
  done
  return 1
}

# ---------- 1. 下拉控制中心 ----------
log "下拉控制中心"
$PH swipe 1150 20 1150 1500 3000 >/dev/null; sleep 1.2
shot 01_control_center

# ---------- 2. 关闭移动数据 ----------
# 开启时卡片文字是「卡 1 数据 / 卡 2 数据」，关闭后变成「移动数据」
DATA_TILE=$(pos_of "数据")
if [ -z "$DATA_TILE" ]; then log "✗ 控制中心里没找到数据开关"; exit 1; fi
TILE_TEXT=$(python3 - "${S}/_t_${STAMP}.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
res=[]
def w(n):
    a=n.get('attributes',{}) or {}
    t=a.get('text') or ''
    if '数据' in t: res.append(t)
    for c in n.get('children') or []: w(c)
w(d); print(res[0] if res else '')
PY
)
if [ "$TILE_TEXT" = "移动数据" ]; then
  log "流量已是关闭状态（$TILE_TEXT），跳过"
else
  log "关闭流量（当前 $TILE_TEXT）"
  tap_at "$DATA_TILE"; sleep 1.2
  shot 02_data_off
fi

# ---------- 3. 长按切换数据卡 ----------
log "长按数据卡片，打开卡切换面板"
$PH lpress "${DATA_TILE%,*}" "${DATA_TILE#*,}" >/dev/null; sleep 1.5
shot 03_sim_panel

SIM1=$(pos_of "卡 1"); SIM2=$(pos_of "卡 2")
DEFAULT_LABEL=$(python3 ./find.py "${S}/_t_${STAMP}.json" "默认" 2>/dev/null | head -1 | awk -F'\t' '{print $2}')
if echo "$DEFAULT_LABEL" | grep -q "卡 1"; then
  TARGET=$SIM2; log "当前默认是卡 1 → 切到卡 2"
elif echo "$DEFAULT_LABEL" | grep -q "卡 2"; then
  TARGET=$SIM1; log "当前默认是卡 2 → 切到卡 1"
else
  TARGET=$SIM2; log "没读到默认标记，按切到卡 2 处理"
fi
tap_at "$TARGET"; sleep 1.8
shot 04_sim_switched

# ---------- 4. 收起面板，检查并关闭遗留浏览器进程 ----------
$PH key Back >/dev/null; sleep 0.8
$PH key Back >/dev/null; sleep 0.8
if $PH ps "hmos.browser" | grep -q browser; then
  log "发现遗留浏览器进程，强制关闭"
  $PH kill com.huawei.hmos.browser >/dev/null; sleep 2
else
  log "没有遗留浏览器进程"
fi

# ---------- 5. 重新打开自带浏览器 ----------
log "重新打开自带浏览器"
$PH app com.huawei.hmos.browser MainAbility >/dev/null; sleep 3.5
shot 05_browser_open

# ---------- 6. 打开标签管理 → 全部关闭 ----------
log "打开标签管理"
$PH tap "${TAB_BTN_X:-943}" "${TAB_BTN_Y:-309}" >/dev/null; sleep 1.5
shot 06_tab_manager
CLOSE_ALL=$(pos_of "全部关闭")
tap_at "$CLOSE_ALL"; sleep 1.2
CONFIRM=$(python3 ./find.py "${S}/_t_${STAMP}.json" "全部关闭" 2>/dev/null | tail -1 | awk -F'\t' '{print $3}')
shot 07_confirm_close
# 确认弹窗里的「全部关闭」是第二个同名按钮（第一个是底部工具按钮）
p1=$(python3 ./find.py "${S}/_t_${STAMP}.json" "全部关闭" 2>/dev/null | sed -n '1p' | awk -F'\t' '{print $3}')
p2=$(python3 ./find.py "${S}/_t_${STAMP}.json" "全部关闭" 2>/dev/null | sed -n '2p' | awk -F'\t' '{print $3}')
if [ -n "$p2" ] && [ "$p2" != "$p1" ]; then
  log "确认关闭全部标签页"
  tap_at "$p2"; sleep 1.5
fi
shot 08_tabs_closed

# ---------- 7. 选择右侧无痕标签组 ----------
log "切到右侧无痕标签组"
$PH tap "${INC_GROUP_X:-883}" "${INC_GROUP_Y:-198}" >/dev/null; sleep 1.2
shot 09_incognito_group

# ---------- 8. 新建无痕窗口 ----------
log "新建无痕窗口"
NEW_INC=$(wait_for "新建无痕窗口" 8)
tap_at "$NEW_INC"; sleep 2.5
shot 10_incognito_window

# ---------- 9. 打开目标网址 ----------
log "在无痕窗口打开 $URL"
$PH tap "${URL_BAR_X:-420}" "${URL_BAR_Y:-248}" >/dev/null; sleep 1.5
$PH inputtext "${URL_BAR_X:-420}" "${URL_BAR_Y:-165}" "$URL" >/dev/null; sleep 1
GO=$(pos_of "搜索")
tap_at "$GO"
sleep 12
shot 11_page_loaded

# ---------- 10. 点击 Get started（若目标页有） ----------
GS=$(wait_for "Get started" 15)
if [ -n "$GS" ]; then
  log "点击 Get started"
  tap_at "$GS"; sleep 6
  shot 12_get_started
else
  log "本页没有 Get started 按钮，流程到「打开网址」为止"
fi

log "完成，截图见 $S/flow1_${STAMP}_*.jpeg"
