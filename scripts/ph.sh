#!/bin/bash
# ph.sh —— 鸿蒙 (HarmonyOS) 手机控制脚本（基于 hdc）
#
# 与 HoKit 投屏共用同一条 hdc 通道：投屏期间严禁 `hdc kill / start / kill-server`，
# 会把投屏连接一起打断。
#
# 用法：
#   ./ph.sh targets                  # 列出设备
#   ./ph.sh shot [名字]              # 截图 -> shots/<名字>.jpeg，打印路径
#   ./ph.sh ui   [名字]              # 导出控件树 -> shots/<名字>.json，打印路径
#   ./ph.sh find <文本/ID> [json]    # 在控件树里找元素中心坐标（默认用最新的 json）
#   ./ph.sh tap <x> <y>              # 单击
#   ./ph.sh lpress <x> <y>           # 长按
#   ./ph.sh dclick <x> <y>           # 双击
#   ./ph.sh swipe <x1> <y1> <x2> <y2> [速度]
#   ./ph.sh drag  <x1> <y1> <x2> <y2> [速度]
#   ./ph.sh key <Back|Home|Power>    # 按键
#   ./ph.sh text <文本>              # 给已聚焦输入框输入
#   ./ph.sh inputtext <x> <y> <文本> # 在坐标处输入（会替换原有内容）
#   ./ph.sh open <url>               # 用系统浏览器打开 URL（新窗口，非无痕）
#   ./ph.sh app <bundle> <ability>   # 启动应用
#   ./ph.sh kill <bundle>            # 强制停止应用
#   ./ph.sh ps <关键字>              # 查进程
#   ./ph.sh sh "<命令>"              # 在手机上执行 shell 命令
#
# 环境变量：
#   PHONE_ID    设备号（多设备时必填，单设备自动取第一个）
#   HDC_BIN     hdc 可执行文件路径
#   SHOTS_DIR   截图/控件树输出目录（默认 ./shots）

set -o pipefail

HDC=${HDC_BIN:-/Applications/DevEco-Studio.app/Contents/sdk/default/openharmony/toolchains/hdc}
if [ ! -x "$HDC" ]; then
  HDC=/Applications/HoKit.app/Contents/Resources/assets/tools/hdc/hdc
fi
if [ ! -x "$HDC" ]; then
  echo "找不到 hdc，可用 HDC_BIN 指定路径" >&2; exit 1
fi

DEV=${PHONE_ID:-$("$HDC" list targets 2>/dev/null | grep -v '^\[Empty\]' | head -1)}
if [ -z "$DEV" ]; then echo "没有连接的设备：hdc list targets 为空" >&2; exit 1; fi

SHOTS=${SHOTS_DIR:-./shots}
if ! mkdir -p "$SHOTS" 2>/dev/null || [ ! -w "$SHOTS" ]; then
  SHOTS=/tmp/phshots
  mkdir -p "$SHOTS" 2>/dev/null
fi
D=("$HDC" -t "$DEV")
SELF_DIR=$(cd "$(dirname "$0")" && pwd)

case "$1" in
  targets)  "$HDC" list targets ;;
  shot)     n=${2:-latest}
            "${D[@]}" shell snapshot_display -f /data/local/tmp/_s.jpeg >/dev/null 2>&1
            "${D[@]}" file recv /data/local/tmp/_s.jpeg "$SHOTS/$n.jpeg" >/dev/null 2>&1
            echo "$SHOTS/$n.jpeg" ;;
  ui)       n=${2:-latest}
            "${D[@]}" shell uitest dumpLayout -p /data/local/tmp/_l.json >/dev/null 2>&1
            "${D[@]}" file recv /data/local/tmp/_l.json "$SHOTS/$n.json" >/dev/null 2>&1
            echo "$SHOTS/$n.json" ;;
  find)     q=$2; f=${3:-$(ls -t "$SHOTS"/*.json 2>/dev/null | head -1)}
            [ -z "$f" ] && { echo "没有控件树 json，先跑 ./ph.sh ui" >&2; exit 1; }
            python3 "$SELF_DIR/find.py" "$f" "$q" ;;
  tap)      "${D[@]}" shell uitest uiInput click "$2" "$3" ;;
  lpress)   "${D[@]}" shell uitest uiInput longClick "$2" "$3" ;;
  dclick)   "${D[@]}" shell uitest uiInput doubleClick "$2" "$3" ;;
  swipe)    "${D[@]}" shell uitest uiInput swipe "$2" "$3" "$4" "$5" "${6:-600}" ;;
  drag)     "${D[@]}" shell uitest uiInput drag "$2" "$3" "$4" "$5" "${6:-600}" ;;
  fling)    "${D[@]}" shell uitest uiInput fling "$2" "$3" "$4" "$5" "${6:-4000}" "${7:-300}" ;;
  key)      "${D[@]}" shell uitest uiInput keyEvent "$2" ;;
  text)     "${D[@]}" shell uitest uiInput text "$2" ;;
  inputtext)"${D[@]}" shell uitest uiInput inputText "$2" "$3" "$4" ;;
  open)     "${D[@]}" shell aa start -a MainAbility -b com.huawei.hmos.browser -U "$2" ;;
  app)      "${D[@]}" shell aa start -b "$2" -a "$3" ;;
  kill)     "${D[@]}" shell aa force-stop "$2" ;;
  ps)       "${D[@]}" shell "ps -ef" 2>/dev/null | grep -i "$2" | grep -v grep ;;
  sh)       shift; "${D[@]}" shell "$@" ;;
  *)        sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//' ;;
esac
