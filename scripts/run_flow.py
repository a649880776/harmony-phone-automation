#!/usr/bin/env python3
"""run_flow.py —— 全流程编排（带检查点与异常中断）

设计要点：
  * 每一步都有「期望文本」检查点，做不到就停，不硬走；
  * 每步之后跑 monitor 异常检测；命中需要人工的异常（拒卡/3DS/AVS/2FA）立即退出并输出 JSON；
  * 能自动处理的异常（白屏、网络抖动、验证码过期）自动重试指定次数；
  * 退出码约定：
      0  完成
      2  参数/环境问题
      3  取卡失败
      9  需要人工决策（看看 JSON 里的 anomalies / stopped_at）
  * 本脚本不打印任何卡号/验证码/token 明文，只输出掩码与阶段名。

用法：
  ./run_flow.py --email-auto                # 从 profile.json 读邮箱
  ./run_flow.py --skip-browser-reset        # 浏览器已经是干净无痕窗口时
  ./run_flow.py --only checkout             # 只跑某一段：login|plan|checkout|onboard|pat|report
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_ui as U

ROOT = U.ROOT
PROFILE = os.path.join(ROOT, "profile.json")
STATE = {"stages": [], "ok": False}


# ---------------- 通用 ----------------
_T0 = time.time()


def log(stage, ok=True, **kw):
    rec = {"stage": stage, "ok": ok, "t": round(time.time() - _T0, 1)}
    rec.update(kw)
    STATE["stages"].append(rec)
    # 带累计耗时，方便一眼看出哪一步慢（用户要求评估流程是否流畅）
    print(f"[+{rec['t']:>6.1f}s] [{ 'OK ' if ok else 'FAIL' }] {stage}" + (f"  {kw}" if kw else ""),
          file=sys.stderr)
    return rec


def record_outcome(status, stage=None, anomalies=None, error=None, **extra):
    """把本轮结果写回 devin_token.json —— **成功、拒卡、失败都要留痕**。

    用户明确要求：遇到拒卡任务结束，结果必须落盘（否则事后不知道哪个账号/卡废了）。
    同一进程只写一次，避免 need_human 被多层调用时重复追加。
    """
    if STATE.get("_recorded"):
        return
    STATE["_recorded"] = True
    try:
        prof = profile()
    except Exception:
        prof = {}
    rec = {
        "email": prof.get("email"),
        "card_id": prof.get("card_id"),
        "status": status,
        "stage": stage or STATE.get("stopped_at"),
        "token": extra.pop("token", None),
        "token_name": extra.pop("token_name", None),
        "card_masked": STATE.get("card_masked"),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if error:
        rec["error"] = error
    if anomalies:
        rec["anomalies"] = [{"code": a.get("code"), "hint": a.get("hint")}
                            for a in anomalies if isinstance(a, dict)][:4]
    rec.update({k: v for k, v in extra.items() if v is not None})
    try:
        from token_store import append_event
        n = append_event(rec)
        STATE["recorded"] = True
        print(f"[record] 结果已写入 devin_token.json（{status}，共 {n} 条）", file=sys.stderr)
    except Exception as e:
        print(f"[record] 写 devin_token.json 失败：{type(e).__name__}", file=sys.stderr)


def need_human(stage, anomalies, extra=None, status=None, error=None):
    STATE["ok"] = False
    STATE["stopped_at"] = stage
    STATE["anomalies"] = anomalies
    if extra:
        STATE["detail"] = extra
    codes = {a.get("code") for a in anomalies if isinstance(a, dict)}
    st = status or ("declined" if "CARD_DECLINED" in codes else "blocked")
    record_outcome(st, stage, anomalies, error=error)
    print(json.dumps(STATE, ensure_ascii=False, indent=2))
    sys.exit(9)


def monitor(stage, auto_reload=1):
    """返回 True 表示健康；自动处理的异常会重试，无法处理的抛给人工"""
    for attempt in range(auto_reload + 1):
        r = U.check(tag="mon_" + re.sub(r"\W+", "_", stage))
        if r["ok"]:
            return True
        acts = {a["action"] for a in r["anomalies"]}
        if acts <= {"reload", "refetch_code", "recheck_country_zip"} and attempt < auto_reload:
            if "reload" in acts:
                log(f"{stage}: 白屏/网络异常，刷新重试")
                U.tap(1105, 184, wait=6)          # 浏览器刷新按钮
                continue
        need_human(stage, r["anomalies"])
    return True


def run(cmd, timeout=120):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def profile():
    return json.load(open(PROFILE, encoding="utf-8"))


# ---------------- 各阶段 ----------------
CELL_IFACE_PREFIX = ("ancormnet", "rmnet", "ccmni", "pdp", "eth0")
WIFI_IFACE_PREFIX = ("wlan", "ap0", "p2p")


def _sh_phone(cmd, timeout=10):
    try:
        r = subprocess.run([os.path.join(ROOT, "scripts", "ph.sh"), "sh", cmd],
                           capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "") + (r.stderr or "")
    except Exception:
        return ""


def _net_ok(timeout=6):
    """手机侧 ping，判断当前有没有网"""
    out = _sh_phone("ping -c 1 -W 2 223.5.5.5", timeout=timeout + 4)
    return ("1 received" in out) or ("1 packets received" in out) or ("ttl=" in out.lower())


def _route_iface():
    """默认路由走哪个网卡（移动数据=ancormnet0/rmnet*，WiFi=wlan*）"""
    out = _sh_phone("cat /proc/net/route")
    for line in out.splitlines()[1:]:
        f = line.split()
        if len(f) < 3:
            continue
        # Destination=00000000 且 Gateway 非 0 → 默认路由；这里取 gateway 非零的第一行
        if len(f) >= 3 and f[2] not in ("00000000", "") and f[1] == "00000000":
            return f[0]
    for line in out.splitlines()[1:]:
        f = line.split()
        if f and f[0] not in ("Iface", ""):
            return f[0]
    return None


def _iface_kind(name):
    if not name:
        return "unknown"
    n = name.lower()
    if n.startswith(WIFI_IFACE_PREFIX):
        return "wifi"
    if n.startswith(CELL_IFACE_PREFIX):
        return "cell"
    return "other"


def _wifi_enabled_by_if():
    out = _sh_phone("ifconfig 2>/dev/null | grep -A1 -E '^wlan[0-9]' ")
    return "inet addr" in out


def _control_center(tag="cc"):
    """下拉出控制中心；已经开着就直接复用"""
    d = U.dump(tag + "_pre")
    if U.has_text(d, "飞行模式"):
        return d
    U.ph("swipe", "1150", "20", "1150", "1500", "3000", wait=1.2)
    return U.dump(tag)


def _wlan_tile_state(d):
    """从控制中心控件树读 WLAN 开关状态：返回 (坐标, 是否开启) 或 (None, None)"""
    for n in U.nodes(d):
        a = n.get("attributes", {})
        t = U.text_of(n)
        if t == "WLAN" or t.startswith("WLAN"):
            xy = U.center(a.get("bounds", ""))
            val = a.get("checked")
            if val in ("true", "false"):
                return xy, (val == "true")
            return xy, None
    return None, None


def _ensure_wifi_off():
    """确保 WiFi 关闭（只能走移动数据）。返回 (ok, detail)"""
    d = _control_center()
    pos, on = _wlan_tile_state(d)
    if not pos:
        return False, "控制中心没找到 WLAN 开关"
    if on is None:
        on = _wifi_enabled_by_if()
    for i in range(3):
        if on is False:
            return True, f"WiFi 已关（第{i}次确认）"
        U.tap_xy(pos, wait=2.0)
        d = U.dump(f"wlan_off{i}")
        pos2, on = _wlan_tile_state(d)
        pos = pos2 or pos
        if on is None:
            on = _wifi_enabled_by_if()
        log(f"关闭 WiFi 第 {i+1} 次", on=on)
    return on is False, f"WiFi 状态={on}"


def _net_summary():
    iface = _route_iface()
    return {"iface": iface, "kind": _iface_kind(iface), "ping": _net_ok()}


def stage_airplane(hold=10):
    """① 检测网络（必须走移动数据、WiFi 必须关）② 开飞行模式 ③ 等 10 秒 ④ 关飞行模式 ⑤ 复核仍是移动数据。

    用户 2026-11-25 明确要求：
      * 触发飞行模式之前必须先检测网络；
      * 必须关闭 WiFi，只能使用移动网络；
      * 关飞行模式后也要确认没有偷偷恢复 WiFi。
    """
    # ── 1) 关 WiFi（只留移动数据）
    wifi_ok, wifi_detail = _ensure_wifi_off()
    s0 = _net_summary()
    log("网络自检（飞行模式前）", wifi=wifi_detail, iface=s0["iface"], kind=s0["kind"], ping=s0["ping"])
    if not wifi_ok:
        need_human("关闭 WiFi", [{"code": "WIFI_STILL_ON",
                                  "hint": f"WiFi 关不掉（{wifi_detail}），按要求只能用移动网络"}])
    if s0["kind"] == "wifi":
        need_human("网络自检", [{"code": "NET_ON_WIFI",
                                 "hint": f"默认路由仍是 WiFi（{s0['iface']}），不能继续"}])
    if not s0["ping"]:
        need_human("网络自检", [{"code": "NET_DEAD_BEFORE",
                                 "hint": "飞行模式前就没网，先查移动数据/信号"}])

    # ── 2) 开飞行模式
    d = _control_center()
    pos = U.find_text(d, "飞行模式")
    if not pos:
        need_human("下拉控制中心", [{"code": "NO_AIRPLANE_TILE",
                                     "hint": "控制中心里没找到「飞行模式」开关"}])
    U.tap_xy(pos, wait=2.0)
    log("打开飞行模式", net_before=s0["ping"])

    # ── 3) 等待期间必须断网（否则就是没点中）
    time.sleep(min(3, hold))
    during = _net_ok()
    if during:
        log("警告：飞行模式期间仍有网络，开关可能没点中", ok=False, net_during=during)
    else:
        log("飞行模式已生效（期间无网络）", net_during=during)
    time.sleep(max(0, hold - 3))

    # ── 4) 关飞行模式
    d2 = U.dump("air_off")
    pos2 = U.find_text(d2, "飞行模式") or pos
    U.tap_xy(pos2, wait=2.5)
    log("关闭飞行模式", hold=f"{hold}s")
    U.back(wait=0.6)
    U.back(wait=0.6)

    # ── 5) 复核：网络恢复 + WiFi 仍然关着 + 走移动数据
    t0 = time.time()
    s1 = _net_summary()
    while time.time() - t0 < 30 and not s1["ping"]:
        time.sleep(3)
        s1 = _net_summary()
    # 系统可能在关飞行模式时把 WiFi 一起恢复 → 再关一次
    if _iface_kind(s1["iface"]) == "wifi" or _wifi_enabled_by_if():
        log("关飞行模式后 WiFi 被恢复，再次关闭", iface=s1["iface"])
        _ensure_wifi_off()
        s1 = _net_summary()
    log("网络复核（飞行模式后）", iface=s1["iface"], kind=s1["kind"], ping=s1["ping"])
    if not s1["ping"]:
        need_human("飞行模式恢复网络", [{"code": "NET_DEAD",
                                         "hint": "关掉飞行模式后仍无网络，后面步骤都会失败"}],
                   status="blocked", error="airplane-off-but-no-network")
    if s1["kind"] == "wifi":
        need_human("网络复核", [{"code": "NET_ON_WIFI",
                                 "hint": f"关飞行模式后又走 WiFi 了（{s1['iface']}）"}])
    log("第一步完成：移动数据可用，WiFi 关闭", iface=s1["iface"])


def stage_network(sim_to=None):
    """（旧版第一步，保留做对照）关流量 → 长按切卡；新流程用 stage_airplane"""
    U.ph("swipe", "1150", "20", "1150", "1500", "3000", wait=1.2)
    d = U.dump("net0")
    pos = U.find_text(d, "数据")
    if not pos:
        need_human("下拉控制中心", [{"code": "NO_CONTROL_CENTER", "hint": "没看到数据开关"}])
    tile = None
    for n in U.nodes(d):
        t = U.text_of(n)
        if "数据" in t and "卡" in t or t == "移动数据":
            tile = t
            break
    if tile != "移动数据":
        U.tap_xy(pos, wait=1.3)
        log("关闭流量", True, was=tile)

    U.ph("lpress", str(pos[0]), str(pos[1]), wait=1.6)
    d = U.dump("net1")
    sims = [t for t in (U.text_of(n) for n in U.nodes(d)) if re.match(r"卡 \d", t or "")]
    if not sims:
        need_human("切卡面板", [{"code": "NO_SIM_PANEL", "hint": "长按没有弹出移动数据面板"}])
    default = next((s for s in sims if "默认" in s), "")
    if sim_to:
        target = next((s for s in sims if s.startswith(f"卡 {sim_to}")), None)
    else:
        # 默认行为：切到「另一张卡」（1<->2 互换），这是业务要求
        others = [x for x in sims if x is not default and "默认" not in x]
        target = others[0] if others else None
    if target and target != default:
        p = U.find_text(U.dump("net2"), target)
        if p:
            U.tap_xy(p, wait=1.8)
            log("切换数据卡", to=target)
    U.back(wait=0.6); U.back(wait=0.6)


def tap_text(text, timeout=15, tag="tap_text", y_min=None, y_max=None):
    """等某个文本出现再点它（可限定屏幕纵向范围，用于区分同名按钮）"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = U.dump(tag)
        cands = []
        for n in U.nodes(d):
            a = n.get("attributes", {})
            if text in U.text_of(n):
                xy = U.center(a.get("bounds", ""))
                b = U._bounds(a.get("bounds", ""))
                if xy and (y_min is None or b[1] >= y_min) and (y_max is None or b[1] <= y_max):
                    cands.append((b[1], xy))
        if cands:
            xy = min(cands)[1]
            U.tap_xy(xy, wait=1.2)
            return True
        time.sleep(0.7)
    return False


def stage_browser_reset():
    """杀浏览器 → 重开 → 关全部标签 → 选无痕组 → 新建无痕窗口

    优化点（2026-11-25，用户要求）：去掉「固定 sleep + 先等首页文案」的两段空等。
    原来：start → sleep(4) → wait_text("首页") 最多 20s → 再轮询徽标 10×1.5s，实测关标签要 21s。
    现在：start → **直接轮询徽标（0.7s 一次），一出现立刻点**；首页校验用同一份 dump 顺带做。
    """
    _, out, _ = run(f'"{os.path.join(ROOT,"scripts","ph.sh")}" ps hmos.browser')
    if "browser" in out:
        run(f'"{os.path.join(ROOT,"scripts","ph.sh")}" kill com.huawei.hmos.browser')
        # 等进程真的退掉（最多 3s），而不是干等 2s
        for _ in range(6):
            _, o2, _ = run(f'"{os.path.join(ROOT,"scripts","ph.sh")}" ps hmos.browser')
            if "browser" not in o2:
                break
            time.sleep(0.5)
        log("清理遗留浏览器进程")
    run(f'"{os.path.join(ROOT,"scripts","ph.sh")}" app com.huawei.hmos.browser MainAbility')

    def badge_of(d):
        for n in U.nodes(d):
            t = U.text_of(n)
            a = n.get("attributes", {})
            b = U._bounds(a.get("bounds", ""))
            if t.isdigit() and len(t) <= 2 and b and b[1] < 520 and b[0] > 800:
                return U.center(a.get("bounds", ""))
        return None

    def on_home(d):
        """确认还在浏览器首页（资讯流）。跑到文章页/应用市场就说明点歪了"""
        txt = U.screen_text(d)
        return ("首页" in txt) and ("安装" not in txt) and ("分享至" not in txt)

    # 一出现徽标就点，不再先等「首页」文案（首页文案出现 ≠ 顶栏就绪）
    opened = False
    t0 = time.time()
    badge = None
    attempt = 0
    while time.time() - t0 < 25:
        d = U.dump(f"br_poll{attempt}")
        badge = badge_of(d)
        if badge and on_home(d):
            U.tap_xy(badge, wait=1.4)
            if U.wait_any(["全部关闭", "新建无痕窗口"], timeout=6):
                opened = True
                log("打开标签管理", after=f"{time.time()-t0:.1f}s")
                break
            # 点空了：退回来再等
            U.back(wait=0.8)
        elif not badge and d is not None and ("安装" in U.screen_text(d)) or (d is not None and "分享至" in U.screen_text(d)):
            U.back(wait=0.8)          # 跑到应用市场/分享面板了，退回来
        attempt += 1
        time.sleep(0.7)
    if not opened:
        need_human("打开标签管理",
                   [{"code": "NO_TAB_MANAGER" if badge else "NO_TAB_BADGE",
                     "hint": "25s 内没能打开标签管理（徽标%s）" % ("点了没反应" if badge else "没出现")}])

    # 全部关闭 → 等确认弹窗 → 点弹窗内的「全部关闭」（弹窗按钮 y>1200）
    if not tap_text("全部关闭", timeout=12, tag="br1"):
        need_human("打开标签管理", [{"code": "NO_TAB_MANAGER", "hint": "没看到全部关闭按钮"}])
    if not U.wait_text("是否关闭全部标签页", timeout=10):
        need_human("关闭标签确认", [{"code": "NO_CONFIRM_DIALOG", "hint": "没有出现确认弹窗"}])
    if not tap_text("全部关闭", timeout=8, tag="br2", y_min=1200, y_max=1600):
        need_human("关闭标签确认", [{"code": "NO_CONFIRM_BUTTON", "hint": "弹窗里点不到全部关闭"}])
    if not U.wait_text("新建无痕窗口", timeout=12):
        need_human("标签管理", [{"code": "TAB_MANAGER_GONE", "hint": "关完标签后没回到标签管理"}])
    log("关闭全部标签")

    # 右侧无痕组：x>500 且 y<400 的可点 Row；已选中就不重复点
    d = U.dump("br3")
    inc, inc_selected = None, False
    for n in U.nodes(d):
        a = n.get("attributes", {})
        b = U._bounds(a.get("bounds", ""))
        if (a.get("type") == "Row" and a.get("clickable") == "true"
                and b and b[0] > 500 and b[1] < 400):
            inc = U.center(a.get("bounds", ""))
            inc_selected = (a.get("selected") == "true")
            break
    if inc and not inc_selected:
        U.tap_xy(inc, wait=1.4)
    if not tap_text("新建无痕窗口", timeout=10, tag="br4"):
        need_human("无痕窗口", [{"code": "NO_INCOGNITO", "hint": "没找到新建无痕窗口"}])
    # 地址栏在页面滚动后会隐藏，非致命：等到首页/搜索栏任一出现即可
    U.wait_any(["首页", "搜索、提问或输入网址", "搜索"], timeout=12)
    log("新建无痕窗口")


def entry_url():
    env = {}
    try:
        for line in open(os.path.join(ROOT, ".env"), encoding="utf-8"):
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.strip().split("=", 1)
                env[k.strip()] = v.strip().strip('"')
    except FileNotFoundError:
        pass
    return os.environ.get("DEVIN_ENTRY_URL") or env.get("DEVIN_ENTRY_URL") or "https://app.devin.ai"


def ensure_signup_mode(max_try=2):
    """确保停在「注册表单」而不是「登录表单」。

    判定依据（Devin 登录页）：
      登录态：Logo 下方是 `Don't have an account?  Sign up`
      注册态：Logo 下方是 `Already have an account?  Log in`
    做法：发现 `have an account?`（且是 Don't 那句）就点同一行的 Sign up 链接，
          点完重新 dump 校验 `Already have an account?` 出现；最多重试 max_try 次。
    """
    for attempt in range(1, max_try + 1):
        d = U.dump(f"su_mode{attempt}")
        txt = U.screen_text(d)
        if "Verify your identity" in txt or "Verification code" in txt:
            return True
        if "Already have an account" in txt and "Sign up" in txt:
            log("已是注册表单", attempt=attempt)
            return True
        # 登录表单：Logo 下方 “Don't have an account?  Sign up”
        if "have an account" in txt:
            # 优先取表格行里那个 link 类型的 Sign up（排除按钮/标题）
            cands = []
            for n in U.nodes(d):
                a = n.get("attributes", {})
                t = U.text_of(n)
                b = U._bounds(a.get("bounds", ""))
                if t == "Sign up" and a.get("type") in ("link", "staticText") and b:
                    cands.append((b[1], U.center(a.get("bounds", ""))))
            if not cands:
                for n in U.nodes(d):
                    t = U.text_of(n)
                    b = U._bounds(n.get("attributes", {}).get("bounds", ""))
                    if t == "Sign up" and b:
                        cands.append((b[1], U.center(n.get("attributes", {}).get("bounds", ""))))
            if cands:
                xy = sorted(cands)[0][1]      # 取最靠上的那个（标题行下面那句）
                log("检测到登录页，点击 Sign up 切换", attempt=attempt, xy=xy)
                U.tap_xy(xy, wait=3)
                continue
        time.sleep(1)
    d = U.dump("su_mode_final")
    return ("Already have an account" in U.screen_text(d)) or U.has_text(d, "Email address")


def stage_open_devin(url=None):
    url = url or entry_url()
    U.tap(420, 248, wait=1.5)               # 地址栏
    U.type_text(url, wait=1.0)
    # 先尝试把页面滚回顶部（地址栏会被隐藏），再找「搜索」；找不到就用兜底坐标
    if not tap_text("搜索", timeout=6, tag="url1"):
        U.ph("swipe", "600", "1600", "600", "2400", "2000", wait=0.6)
        if not tap_text("搜索", timeout=6, tag="url1b"):
            U.tap(420, 248, wait=1.5)
    time.sleep(10)
    # 打开后可能是：营销站(Get started) / 注册登录页(Email address) / 已登录(Settings/Bots/Choose your plan)
    landing_markers = ["Get started", "Email address", "Sign up", "Settings", "Bots",
                         "Choose your plan", "Expand sidebar", "Log in", "Sign in",
                         "Sign up", "Don't have an account?"]
    landed = U.wait_any(landing_markers, timeout=45)
    if landed is None:                       # 可能还停在启动 logo，再耐心等一轮
        log("入口页仍无文本，继续等 45s（SPA 启动慢）")
        landed = U.wait_any(landing_markers, timeout=45)
    if landed is None:                       # 仍白屏：刷新一次
        log("入口页白屏，刷新一次重试")
        U.tap(1105, 184, wait=6)
        landed = U.wait_any(landing_markers, timeout=45)
    if landed is None:
        r = U.check(tag="devin_chk")
        if r["anomalies"]:
            need_human("打开入口地址", r["anomalies"])
        need_human("打开入口地址", [{"code": "NO_LANDING", "hint": f"页面没有可识别内容（{url}）"}])
    if landed == "Get started":
        p = U.find_text(U.dump("devin2"), "Get started")
        U.tap_xy(p, wait=12)
        U.wait_any(["Email address", "Sign up"], timeout=30)

    # 落在登录页时要先切到注册（Don't have an account? → Sign up）
    if not ensure_signup_mode():
        need_human("入口页", [{"code": "NO_SIGNUP_FORM", "hint": "没切到注册表单"}])
    if not U.wait_any(["Email address"], timeout=20):
        need_human("入口页", [{"code": "NO_EMAIL_FIELD", "hint": "注册表单没有邮箱框"}])
    log("打开入口地址", url=url, landed=landed)


def stage_signup(email):
    d = U.dump("su0")
    xy = None
    for n in U.nodes(d):
        a = n.get("attributes", {})
        if a.get("type") == "textField" and not (a.get("text") or "").strip():
            xy = U.center(a.get("bounds", ""))
    if not xy:
        need_human("注册页", [{"code": "NO_EMAIL_FIELD", "hint": "没有邮箱输入框"}])
    U.fill_field(xy, email, hide_keyboard=False)
    # 键盘弹出后 Sign up 会整体上移，必须重新 dump 取坐标（本项目真实踩坑）
    d = U.dump("su1")
    sign = None
    for n in U.nodes(d):
        a = n.get("attributes", {})
        if a.get("type") == "button" and U.text_of(n) == "Sign up":
            sign = U.center(a.get("bounds", ""))
    if not sign:
        need_human("注册页", [{"code": "NO_SIGNUP_BTN", "hint": "没找到 Sign up 按钮"}])
    U.tap_xy(sign, wait=2)
    if not U.wait_text("Verify your identity", timeout=40) and not U.wait_text("Verification code", timeout=5):
        need_human("提交注册", [{"code": "NO_VERIFY_PAGE", "hint": "没有进入验证码页"}])
    log("提交邮箱并进入验证码页")


def stage_code(email):
    rc, out, err = run(f'cd "{ROOT}" && ./scripts/fetch_code.sh "{email}" 0', timeout=120)
    m = re.search(r"CODE=(\d{6})", out)
    if not m:
        state = {"action": "refetch_code/ask_human", "stdout": out[-200:], "stderr": err[-300:]}
        STATE["ok"] = False
        STATE["stopped_at"] = "取验证码"
        STATE["detail"] = state
        print(json.dumps(STATE, ensure_ascii=False, indent=2))
        sys.exit(9)
    code = m.group(1)
    U.type_text(code, wait=1.5)             # 6 位输满会自动提交
    if not U.wait_any(["Choose your plan", "Get Max", "Get Pro"], timeout=60):
        need_human("验证码登录", [{"code": "NO_PLAN_PAGE", "hint": "验证码后没进套餐页"}])
    log("取码并登录", code_len=len(code))


def scan_for(needles, steps=8, distance=850, tag="scan", top_first=False):
    """逐屏向下找文本（每屏都 dump 一次）。

    不能用「盲滚 N 屏再找」：Get Max 在 Max 卡片底部，一次 1200px×3 会直接越过它，
    控件树里就没有这个按钮了（真实踩坑，2026-11-25）。
    """
    if top_first:
        U.scroll_top(markers=tuple(needles))
    for i in range(steps + 1):
        d = U.dump(f"{tag}{i}")
        for t in needles:
            p = U.find_text(d, t)
            if p:
                return p, t
        if i < steps:
            U.scroll("down", distance=distance)
    return None, None



CHECKOUT_MARKS = ("订阅 Max", "US$", "¥", "￥", "CNY", "USD", "银行卡信息", "支付方式", "添加促销码")


def on_stripe_checkout():
    """是否已经在 Stripe 结账页：CDP 看 URL 最准，游标树文本作兜底"""
    try:
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import cdp as C
        ok, _ = C.ensure_forward()
        if ok:
            for t in C.targets():
                if t.get("type") == "page" and "checkout.stripe.com" in (t.get("url") or ""):
                    return True, "cdp-url"
    except Exception:
        pass
    d = U.dump("ck_probe")
    txt = U.screen_text(d)
    hit = next((k for k in CHECKOUT_MARKS if k in txt), None)
    return bool(hit), hit


def switch_currency_usd(timeout=25):
    """把币种切到 USD；优先 CDP 点 currency-selector 里的 usd radio，兜底点页面上的 USD 文本。"""
    def read_amount():
        try:
            sys.path.insert(0, os.path.join(ROOT, "scripts"))
            import cdp as C
            page = C.pick_page(("checkout.stripe.com",))
            if not page:
                return None
            r = json.loads(C.evaluate(
                "(() => JSON.stringify([...new Set((document.body.innerText||'')"
                ".match(/(US\\$|￥|¥)\\s?[\\d,]+\\.\\d{2}/g)||[])]))()", page))
            return r
        except Exception:
            return None

    t0 = time.time()
    while time.time() - t0 < timeout:
        amt = read_amount()
        if amt and any("US$200.00" in a for a in amt):
            return True, amt
        # 1) CDP：点 currency-selector iframe 里的 usd radio
        try:
            sys.path.insert(0, os.path.join(ROOT, "scripts"))
            import cdp as C
            for t in C.targets():
                if "currency-selector" in (t.get("url") or ""):
                    C.evaluate(r"""(() => {
                      const r=[...document.querySelectorAll('input[type=radio]')].find(x=>x.value==='usd');
                      if(!r) return 1;
                      const set=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'checked').set;
                      set.call(r,true);
                      r.dispatchEvent(new Event('input',{bubbles:true}));
                      r.dispatchEvent(new Event('change',{bubbles:true}));
                      r.click();
                      const els=[...document.querySelectorAll('label,div,span')].filter(e=>(e.innerText||'').trim()==='USD');
                      if(els.length) els[els.length-1].click();
                      return 1; })()""", t)
                    break
        except Exception:
            pass
        # 2) 手势兜底：点页面上的 USD 文本
        d = U.dump("usd_tap")
        p = None
        for n in U.nodes(d):
            if U.text_of(n) == "USD":
                p = U.center(n.get("attributes", {}).get("bounds", ""))
                break
        if p:
            U.tap_xy(p, wait=2.5)
        time.sleep(2)
    amt = read_amount()
    return (bool(amt) and any("US$200.00" in a for a in amt)), amt


def stage_plan():
    """选 Max → 进 Stripe → 切 USD → 确认 US$200.00（落地判定与币种都用 CDP 优先）"""
    on_checkout, why = on_stripe_checkout()
    if on_checkout:
        log("已在结账页，跳过套餐选择", by=why)
    if not on_checkout:
        p, hit = scan_for(["Get Max"], steps=8, distance=850, tag="plan_scan")
        if not p:
            p, hit = scan_for(["Get Max"], steps=10, distance=800, tag="plan_scan2",
                              top_first=True)
        if not p:
            need_human("套餐页", [{"code": "NO_GET_MAX", "hint": "没找到 Get Max"}])
        log("定位套餐按钮", hit=hit)
        U.tap_xy(p, wait=8)
        # 落地检测：先 CDP 看 URL（最准），再文本兜底，最多等 40s
        t0 = time.time()
        while time.time() - t0 < 40:
            ok, why = on_stripe_checkout()
            if ok:
                log("已进入结账页", by=why, after=f"{time.time()-t0:.0f}s")
                break
            time.sleep(2)
        else:
            need_human("进入结账页", [{"code": "NO_CHECKOUT", "hint": "点了 Get Max 40s 内没进 Stripe"}])

    # 币种切 USD（CDP 优先，手势兜底），只读文本确认金额
    ok_usd, amt = switch_currency_usd()
    log("切换币种", usd_ok=ok_usd, amounts=amt)
    if not ok_usd:
        need_human("确认金额", [{"code": "AMOUNT_NOT_200",
                                 "hint": f"金额不是 US$200.00（当前 {amt}）"}])
    log("选择 Max 并确认 US$200.00", amounts=amt)


def stage_checkout(card_id, userid):
    """填卡 + 账单地址：**优先 CDP 直接赋值**，CDP 不通才退回手势方案。

    CDP 的好处（2026-11-25 实测）：一次调用 243ms、零滚动、不会触发下拉刷新把表单清空；
    手势方案要 12–18 秒且踩过两次「刷新即清表」的事故。
    """
    use_cdp, why = False, ""
    try:
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import cdp as C
        ok, why = C.ensure_forward()
        use_cdp = bool(ok)
    except Exception as e:
        why = type(e).__name__
    log("CDP 通道", ok=use_cdp, detail=why)

    if use_cdp:
        rc, out, err = run(f'cd "{ROOT}" && python3 scripts/cdp_fill.py --card-id {card_id} '
                           f'--userid {userid}', timeout=180)
        engine = "cdp"
    else:
        rc, out, err = run(f'cd "{ROOT}" && python3 scripts/fill_checkout2.py --card-id {card_id} '
                           f'--userid {userid}', timeout=300)
        engine = "gesture"

    if rc != 0:
        STATE["ok"] = False
        STATE["stopped_at"] = "填写结账页"
        STATE["detail"] = {"rc": rc, "engine": engine, "stdout": out[-800:], "stderr": err[-400:]}
        # 只记「哪个字段失败 / 为什么」，不要把整段 JSON 塞进 error（噪音大且含卡号掩码）
        reason = None
        try:
            j = json.loads(out[out.index("{"):])
            bad = [x.get("field") for x in (j.get("steps") or []) if not x.get("ok")]
            missing = j.get("missing") or []
            parts = []
            if j.get("why"):
                parts.append(j["why"])
            if bad:
                parts.append("未通过: " + ", ".join([b for b in bad if b]))
            if missing:
                parts.append("缺字段: " + ", ".join(missing))
            reason = "；".join(parts) or None
        except Exception:
            reason = (out or err)[-200:]
        record_outcome("failed", "填写结账页", error=reason)
        print(json.dumps(STATE, ensure_ascii=False, indent=2))
        sys.exit(9)
    # 记下卡的掩码（两种引擎的输出里都有），失败落盘时用
    try:
        m = re.search(r'"pan":\s*"([^"]+)"', out)
        if m:
            STATE["card_masked"] = m.group(1)
    except Exception:
        pass
    # CDP/手势填完，焦点通常还停在卡号框 → 立刻收键盘，否则下一步点「订阅」会点到键盘
    try:
        kb_ok = U.dismiss_keyboard()
    except Exception as e:
        kb_ok = None
        log("收键盘辅助函数异常（不影响继续）", ok=False, why=type(e).__name__)
    try:
        j = json.loads(out[out.index("{"):])
        STATE["fill_missing"] = j.get("missing") or []
    except Exception:
        STATE["fill_missing"] = None
    log("填写卡与账单地址", engine=engine, card=STATE.get("card_masked"),
        keyboard_closed=kb_ok, missing=STATE.get("fill_missing"))


def find_exact(d, text):
    """只认完全相等的文本节点（避免「订阅」命中「订阅 Max」/「订阅即表示…」）"""
    for n in U.nodes(d):
        a = n.get("attributes", {})
        if U.text_of(n) == text:
            xy = U.center(a.get("bounds", ""))
            if xy:
                return xy
    return None


ONBOARD_MARKS = ["Connect your Git provider", "Where do you want to start",
                "Connect your teams", "Devin Bots", "Expand sidebar"]
DECLINE_MARKS = ["您的银行卡被拒绝了", "银行卡被拒绝", "被拒绝", "declined",
                 "Your card was declined"]
OTHER_STOP_MARKS = ["验证您的身份", "3D Secure", "地址不匹配", "AVS"]

# 拒卡重试策略（用户 2026-11-25 指定）：重试 4 次、每次间隔 10 秒，
# 4 次全失败才判定「真失败」。
DECLINE_RETRIES = 4
DECLINE_INTERVAL = 10


def _read_result(tag="sub_wait", timeout=150, ignore_stale_decline=False):
    """轮询结果：返回 ("ok"|"declined"|"other"|"timeout", 命中的文本)

    ignore_stale_decline=True 时，先等旧的拒卡文案消失（最多 15s）。
    因为上一次拒卡的报错会留在 DOM 里，不去等就会被误当成这一次的结果。
    """
    if ignore_stale_decline:
        t1 = time.time()
        while time.time() - t1 < 15 and _decline_visible():
            time.sleep(1)
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = U.dump(tag)
        txt = U.screen_text(d)
        if any(k in txt for k in ONBOARD_MARKS):
            return "ok", f"{time.time()-t0:.0f}s"
        hit = next((k for k in DECLINE_MARKS if k in txt), None)
        if hit:
            return "declined", hit
        hit2 = next((k for k in OTHER_STOP_MARKS if k in txt), None)
        if hit2:
            return "other", hit2
        # 结账页被顶掉（退回套餐页等）——通常是被 Back 键误触发了页面后退
        if "Choose your plan" in txt or "Get Max" in txt:
            return "session_lost", "退回套餐页"
        time.sleep(3)
    return "timeout", None


def _decline_visible():
    try:
        d = U.dump("decl_chk")
        txt = U.screen_text(d)
        return any(k in txt for k in DECLINE_MARKS)
    except Exception:
        return False


def _click_subscribe_by_ui():
    """在页面里找到「订阅」按钮并点它（只向下滚，绝不上滑；先收键盘）"""
    U.dismiss_keyboard()
    d = U.dump("sub_find")
    sub = find_exact(d, "订阅")
    steps = 0
    while not sub and steps < 6:
        U.scroll("down", distance=800)
        d = U.dump(f"sub_find{steps}")
        sub = find_exact(d, "订阅")
        steps += 1
    if not sub:
        return False
    U.tap_xy(sub, wait=8)
    return True


def _refill_and_submit():
    """拒卡重试：**只重填卡信息**再提交，绝不碰账单地址。

    实测（2026-11-25）：拒卡后 Stripe 会锁住账单地址，重填地址会回一句
    「如需更改您的账单地址，请离开此页面并重新结账。」——那不是本次提交的失败原因，
    但会让整步判失败、白烧一次重试。所以重试只用 `--card-only`：
    卡号/有效期/CVC（CVC 常被清空）+ 勾协议 + 点订阅。
    """
    card_id = profile().get("card_id")
    userid = os.environ.get("CARD_API_USER_ID", "8")
    try:
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import cdp as C
        ok, why = C.ensure_forward()
    except Exception as e:
        ok, why = False, type(e).__name__
    if ok:
        r = subprocess.run(f'cd "{ROOT}" && python3 scripts/cdp_fill.py --card-id {card_id} '
                           f'--userid {userid} --card-only --submit', shell=True,
                           capture_output=True, text=True, timeout=240)
        if r.returncode == 0:
            return True, "cdp-card-only"
        log("CDP card-only 重填未成功", ok=False, detail=(r.stdout or r.stderr)[-160:])
    # 兜底：直接点一次「订阅」重提交（数据还在，只是可能 CVC 被清）
    return _click_subscribe_by_ui(), "click-only"



CLICK_SUB_JS = r"""
(() => {
  const out = {ok: false};
  const cb = document.querySelector('#paymentForm-termsOfServiceConsentInput');
  if (cb && !cb.checked) { cb.click(); out.consentClicked = true; }
  out.consent = cb ? cb.checked : null;
  const bs = [...document.querySelectorAll('button')]
      .filter(b => (b.innerText || '').trim() === '订阅' && (b.offsetWidth || b.offsetHeight));
  if (!bs.length) return JSON.stringify({...out, why: 'no 订阅 button'});
  const b = bs[bs.length - 1];
  b.scrollIntoView({block: 'center'});
  b.click();
  return JSON.stringify({...out, ok: true});
})()
"""


def _cdp_submit():
    """用 CDP 完成「勾协议 + 点订阅」。返回 (ok, detail)。

    比手势稳的原因：按钮不用靠滚动找 —— JS 直接 scrollIntoView + click，
    既不会被软键盘挡住，也不会滚过头把按钮滚出控件树（2026-11-25 两次踩坑）。
    """
    try:
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import cdp as C
        ok, why = C.ensure_forward()
        if not ok:
            return False, f"cdp:{why}"
        t = C.find_select_target("country")
        if not t:
            return False, "cdp:no-iframe"
        r = json.loads(C.evaluate(CLICK_SUB_JS, t))
        time.sleep(1.0)
        return bool(r.get("ok")), r
    except Exception as e:
        return False, f"cdp:{type(e).__name__}"


def stage_subscribe():
    """提交订阅。拒卡会**自动重试 4 次（间隔 10 秒）**，全失败才算真失败。

    【硬约束】这一步**绝不向上滚动**：Stripe 结账页在顶部做「手指向下」的手势会触发
    Chrome 下拉刷新，整张表单（含已填卡号）被清空，而卡是一次性的，等于整单报废
    （2026-11-25 真实事故）。金额确认改为纯读控件树文本。

    【拒卡后的界面真相】点提交被拒后，Stripe 会把焦点**跳回卡号输入框并弹出软键盘**，
    页面同时被顶上去一点。此时：
      * 直接再点「订阅」会点到键盘/空白处 → 必须先 `key Back` 收键盘再重新 dump；
      * 拒卡文案（您的银行卡被拒绝了）会留在页面上，重试前必须确认它消失或已被新一次提交覆盖，
        否则轮询会立刻把「上一次的旧报错」当成这一次的结果；
      * CVC 常被清空，所以重试要**整张表重填**，而不是只点一次提交。
    """
    d = U.dump("sub_amt")
    amount_seen = U.has_text(d, "US$200.00")
    if amount_seen:
        STATE["amount"] = "US$200.00"
        log("提交前金额复核通过（读控件树）", amount="US$200.00")
    else:
        # 滚到表单下部时金额文本不在树里，这是正常的；此时只做「币种没被换回」的旁证检查。
        cny = any(U.has_text(d, k) for k in ("CNY", "¥", "CN ¥"))
        if cny:
            need_human("提交前复核金额", [{"code": "AMOUNT_NOT_200",
                                           "hint": "页面出现 CNY/¥ 价格，币种可能被重置"}])
        STATE["amount"] = "US$200.00(进结账页时已确认，当前不在可视区)"
        log("金额不在可视区，按进结账页时的确认为准", amount=STATE["amount"])

    # 优先用 CDP 提交：勾协议 + scrollIntoView + click，一步到位
    sub_clicked, detail = _cdp_submit()
    if sub_clicked:
        log("已用 CDP 勾协议并点击订阅", detail=detail.get("consent"))
        kind, hit = _read_result()
        attempt = 0
        while kind == "declined" and attempt < DECLINE_RETRIES:
            attempt += 1
            log(f"拒卡，第 {attempt}/{DECLINE_RETRIES} 次重试", sample=hit, wait=f"{DECLINE_INTERVAL}s")
            U.dismiss_keyboard()
            time.sleep(DECLINE_INTERVAL)
            ok_refill, engine = _refill_and_submit()
            if not ok_refill:
                log(f"第 {attempt} 次重试的重填/提交没成功", ok=False, engine=engine)
                kind, hit = "declined", "refill_failed"
                continue
            kind, hit = _read_result(tag=f"sub_retry{attempt}", ignore_stale_decline=True)
            if kind == "ok":
                log("重试后提交成功", attempt=attempt, engine=engine)
                return
        if kind == "ok":
            log("提交成功，进入引导页", after=hit)
            return
        if kind == "session_lost":
            need_human("提交订阅-结账页丢失",
                       [{"code": "SESSION_EXPIRED",
                         "hint": "结账页被退回套餐页（多为误按 Back），需重新选套餐", "action": "ask_human"}],
                       status="blocked", error="session_lost")
        need_human("提交订阅-拒卡",
                   [{"code": "CARD_DECLINED",
                     "hint": f"连续 {attempt + 1} 次提交均被拒（首次 + {attempt} 次重试），账号作废，需换账号",
                     "action": "ask_human", "sample": hit}],
                   status="declined", error=f"{hit}（含 {attempt} 次重试）")
    log("CDP 提交未成功，退回手势方案", ok=False, detail=detail)

    # 手势兜底：找订阅按钮（只向下滚，且小步滚动避免滚过头）—— 先收键盘！
    # CDP 填完表焦点还停在卡号框，数字键盘挡着，控件树的「订阅」坐标会落到键盘上（实测反复点到「2」）
    try:
        kb_ok = U.dismiss_keyboard()
    except Exception as e:
        kb_ok = None
        log("收键盘辅助函数异常（不影响继续）", ok=False, why=type(e).__name__)
    log("收软键盘", ok=kb_ok)
    d = U.dump("sub_after_kb")
    sub = find_exact(d, "订阅")
    steps = 0
    # 小步滚动（450px）：一次 800px×6 会把「订阅」滚过头，之后只能向上找，
    # 而向上手势又有下拉刷新风险 —— 2026-11-25 真实卡点
    while not sub and steps < 8:
        U.scroll("down", distance=450)
        d = U.dump(f"sub_s{steps}")
        sub = find_exact(d, "订阅")
        steps += 1
    if not sub:
        need_human("定位订阅按钮", [{"code": "NO_SUBSCRIBE_BTN", "hint": "滚动后仍找不到订阅按钮"}])

    # 勾选协议（CDP 填表时可能已勾上，无条件点击会「取消勾选」）
    cb, already = None, False
    for n in U.nodes(d):
        if n.get("attributes", {}).get("type") == "checkBox":
            cb = U.center(n.get("attributes", {}).get("bounds", ""))
            already = n.get("attributes", {}).get("checked") == "true"
    if not cb:
        need_human("勾选协议", [{"code": "NO_CHECKBOX", "hint": "没看到协议复选框"}])
    if already:
        log("协议已勾选，跳过点击")
    else:
        U.tap_xy(cb, wait=1.2)
        d = U.dump("sub1")
        checked = any(n.get("attributes", {}).get("checked") == "true"
                      for n in U.nodes(d) if n.get("attributes", {}).get("type") == "checkBox")
        if not checked:
            need_human("勾选协议", [{"code": "CHECKBOX_FAIL", "hint": "协议没勾上"}])
    sub = find_exact(d, "订阅") or sub
    U.tap_xy(sub, wait=8)

    kind, hit = _read_result()
    attempt = 0
    while kind == "declined" and attempt < DECLINE_RETRIES:
        attempt += 1
        log(f"拒卡，准备第 {attempt}/{DECLINE_RETRIES} 次重试",
            sample=hit, wait=f"{DECLINE_INTERVAL}s")
        # 拒卡后焦点会跳到卡号框并弹键盘 → 先收键盘，免得后续点击全落到键盘上
        try:
            U.dismiss_keyboard()
        except Exception:
            pass
        time.sleep(DECLINE_INTERVAL)
        ok_refill, engine = _refill_and_submit()
        if not ok_refill:
            log(f"第 {attempt} 次重试的重填/提交没成功", ok=False, engine=engine)
            kind, hit = "declined", "refill_failed"
            continue
        kind, hit = _read_result(tag=f"sub_retry{attempt}", ignore_stale_decline=True)
        if kind == "ok":
            log("重试后提交成功", attempt=attempt, engine=engine)
            return
        if kind == "session_lost":
            need_human("提交订阅-结账页丢失",
                       [{"code": "SESSION_EXPIRED",
                         "hint": "结账页被退回套餐页（多为误按 Back），本单会话作废，需重新选套餐",
                         "action": "ask_human"}],
                       status="blocked", error="session_lost")
        if kind == "other":
            need_human("提交订阅-需人工验证",
                       [{"code": "3DS_REQUIRED" if "验证" in (hit or "") or "3D" in (hit or "")
                         else "ADDRESS_MISMATCH",
                         "hint": f"重试中出现「{hit}」", "action": "ask_human", "sample": hit}],
                       status="blocked", error=hit)

    if kind == "ok":
        log("提交成功，进入引导页", after=hit)
        return
    if kind == "declined":
        need_human("提交订阅-拒卡",
                   [{"code": "CARD_DECLINED",
                     "hint": f"连续 {attempt + 1} 次提交均被拒（首次 + {attempt} 次重试），账号作废，需换账号",
                     "action": "ask_human", "sample": hit}],
                   status="declined", error=f"{hit}（含 {attempt} 次重试）")
    r = U.check(tag="sub_chk")
    need_human("提交订阅", r["anomalies"] or [{"code": "NO_ONBOARDING",
                                               "hint": "提交后没进入引导页（可能支付失败）"}],
               status="blocked", error="NO_ONBOARDING")


def on_onboarding_page():
    """判断当前是否真的停在引导页。

    订阅成功后**不一定**落在引导页：老账号/已建过组织的账号会直接进应用或设置页。
    这时候再去找 Skip / Continue with web 就是空转 40 秒以上（本轮实测）。
    判定顺序：URL（CDP，最准）→ 控件树文本（兜底）。
    """
    try:
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import cdp as C
        ok, _ = C.ensure_forward()
        if ok:
            page = C.pick_page(("app.devin.ai",))
            if page:
                url = page.get("url") or ""
                if "/onboarding" in url:
                    return True, url
                # URL 不在 onboarding，再确认页面里有没有引导标记
                r = json.loads(C.evaluate(r"""(() => {
                  const t = document.body.innerText || '';
                  const m = ['Connect your Git provider','Where do you want to start',
                             'Connect your teams','Devin Bots'];
                  return JSON.stringify({hit: m.filter(x => t.includes(x)), url: location.href});
                })()""", page))
                if not r.get("hit"):
                    return False, r.get("url") or url
                return True, r.get("url") or url
    except Exception:
        pass
    d = U.dump("ob_probe")
    txt = U.screen_text(d)
    markers = ("Connect your Git provider", "Where do you want to start",
               "Connect your teams", "Devin Bots")
    return any(m in txt for m in markers), "(控件树判定)"


def stage_onboard():
    """引导页按 Skip → Continue with web → Skip → Continue 跳过。

    不在引导页时**直接跳过**（用户要求）：订阅已成功就该直奔「设置 → Devin API → 建 PAT」。
    """
    onb, where = on_onboarding_page()
    if not onb:
        log("不在引导页，跳过引导步骤（直接去建 PAT）", url=str(where)[:80])
        return
    log("检测到引导页", url=str(where)[:80])

    if not U.wait_text("Connect your Git provider", timeout=20):
        pass
    p = U.find_text(U.dump("ob0"), "Skip")
    if p:
        U.tap_xy(p, wait=6)
    if U.wait_text("Where do you want to start", timeout=20) or U.has_text(U.dump("ob1"), "Devin CLI"):
        U.scroll("down", distance=2000)
        p = U.find_text(U.dump("ob2"), "Continue with web")
        if p:
            U.tap_xy(p, wait=6)
    if U.wait_text("Connect your teams", timeout=20):
        p = U.find_text(U.dump("ob3"), "Skip")
        if p:
            U.tap_xy(p, wait=6)
    if U.wait_text("Devin Bots", timeout=20):
        p = U.find_text(U.dump("ob4"), "Continue")
        if p:
            U.tap_xy(p, wait=4)
    log("完成引导")


def stage_pat():
    """设置 → Devin API → PATs → 创建 token → 写入 devin_token.json

    优先 CDP：直接导航到 /org/<slug>/settings/devin-api，再用 DOM 点 PATs / Create token。
    （点侧栏齿轮找 Settings 的路线在已登录态下经常点空，本轮就是卡在「打开设置」）
    失败再退回手势方案。
    """
    env = dict(os.environ)
    if STATE.get("card_masked"):
        env["CARD_MASKED"] = STATE["card_masked"]
    try:
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import cdp as C
        ok, why = C.ensure_forward()
    except Exception as e:
        ok, why = False, type(e).__name__
    if ok:
        r = subprocess.run(f'cd "{ROOT}" && python3 scripts/cdp_pat.py', shell=True,
                           capture_output=True, text=True, timeout=240, env=env)
        if r.returncode == 0:
            STATE["_recorded"] = True          # cdp_pat 已经写了 devin_token.json
            try:
                j = json.loads(r.stdout[r.stdout.index("{"):])
                STATE["token_tail"] = j.get("token_tail")
            except Exception:
                pass
            log("创建 PAT（CDP）并落盘", token_tail=STATE.get("token_tail"))
            return
        log("CDP 建 PAT 未成功，退回手势方案", ok=False, detail=(r.stdout or r.stderr)[-160:])
    else:
        log("CDP 不可用，退回手势方案", ok=False, detail=why)

    U.tap(71, 345, wait=2.5)                 # 侧边栏
    p = U.find_text(U.dump("pat0"), "Settings")
    if not p:
        need_human("打开设置", [{"code": "NO_SETTINGS", "hint": "侧边栏没有齿轮"}])
    U.tap_xy(p, wait=4)
    for _ in range(3):
        U.scroll("down", distance=1000)
    p = U.find_text(U.dump("pat1"), "Devin API")
    if not p:
        need_human("设置菜单", [{"code": "NO_DEVIN_API", "hint": "没找到 Devin API"}])
    U.tap_xy(p, wait=5)
    # 这个页面经常白屏，刷新一次
    if not U.wait_text("PATs", timeout=8):
        U.tap(1105, 184, wait=9)
    p = U.find_text(U.dump("pat2"), "PATs")
    if not p:
        need_human("Devin API 页", [{"code": "NO_PATS_TAB", "hint": "没有 PATs 页签（白屏？）"}])
    U.tap_xy(p, wait=3)
    p = U.find_text(U.dump("pat3"), "Create token")
    if not p:
        need_human("PATs 页", [{"code": "NO_CREATE_TOKEN", "hint": "没有 Create token"}])
    U.tap_xy(p, wait=2.5)

    name = "pat-" + time.strftime("%m%d") + "-" + os.urandom(2).hex()
    d = U.dump("pat4")
    nm = None
    for n in U.nodes(d):
        if n.get("attributes", {}).get("type") == "textField":
            nm = U.center(n.get("attributes", {}).get("bounds", ""))
    U.fill_field(nm, name, hide_keyboard=True)
    p = U.find_text(U.dump("pat5"), "Create token")
    U.tap_xy(p, wait=4)

    d = U.dump("pat6")
    tok = None
    for n in U.nodes(d):
        a = n.get("attributes", {})
        t = (a.get("text") or "").strip()
        if a.get("type") == "textField" and re.match(r"^[a-z]{3}_[A-Za-z0-9]{20,}$", t):
            tok = t
    if not tok:
        need_human("创建 PAT", [{"code": "NO_TOKEN_TEXT", "hint": "没抓到 token 文本"}])
    prof = profile()
    rec = {"email": prof["email"], "card_id": prof.get("card_id"), "token": tok,
           "token_name": name, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "status": "ok", "stage": "创建 PAT",
           "card_masked": STATE.get("card_masked")}
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    from token_store import append_event
    n = append_event(rec)          # 追加，不覆盖历史（带 status=ok）
    STATE["_recorded"] = True      # 已经落盘，主流程结束时不要再写一条
    STATE["token_tail"] = tok[-4:]
    out = os.path.join(ROOT, "devin_token.json")
    U.tap(893, 1758, wait=2)                 # I saved the token
    log("创建 PAT 并落盘（追加）", token_tail=tok[-4:], total=int(n), file=out)


def stage_report():
    rc, out, err = run(f'cd "{ROOT}" && ./scripts/rentcard.sh', timeout=60)
    print(out or err, file=sys.stderr)
    if rc != 0:
        need_human("结算上报", [{"code": "REPORT_FAIL", "hint": (out or err)[-200:]}])
    log("结算上报")


# ---------------- main ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["open", "login", "plan", "checkout", "onboard", "pat",
                                       "report", "all"], default="all")
    ap.add_argument("--skip-browser-reset", action="store_true")
    ap.add_argument("--sim", help="[旧模式] 切换数据卡到指定卡号")
    ap.add_argument("--net-mode", choices=["airplane", "sim", "none"], default="airplane",
                    help="第一步网络重置方式：airplane=开飞行模式10秒再关（默认）；sim=旧版关流量+切卡；none=跳过")
    ap.add_argument("--card-id")
    ap.add_argument("--userid", default=os.environ.get("CARD_API_USER_ID", "8"))
    a = ap.parse_args()

    if not U.device_online():
        STATE["ok"] = False
        STATE["stopped_at"] = "设备检查"
        STATE["anomalies"] = [{"code": "DEVICE_OFFLINE",
                               "hint": "hdc 列不到设备：USB 断了 / 手机锁屏 / 授权掉了",
                               "action": "ask_human"}]
        print(json.dumps(STATE, ensure_ascii=False, indent=2))
        sys.exit(9)

    prof = profile()
    email = prof["email"]
    card_id = a.card_id or prof.get("card_id")
    STATE["email"] = email

    if a.only in ("all", "open", "login"):
        if a.net_mode == "airplane":
            stage_airplane(10)
        elif a.net_mode == "sim":
            stage_network(a.sim)
        else:
            log("跳过网络重置（--net-mode none）")
        if not a.skip_browser_reset:
            stage_browser_reset()
        stage_open_devin()
        if a.only == "open":
            STATE["ok"] = True
            STATE["stopped_at"] = "注册表单（--only open 到此为止）"
            print(json.dumps(STATE, ensure_ascii=False, indent=2))
            return 0
        stage_signup(email)
        stage_code(email)
    if a.only in ("all", "plan"):
        stage_plan()
    if a.only in ("all", "checkout"):
        stage_checkout(card_id, a.userid)
        stage_subscribe()
    if a.only in ("all", "onboard"):
        stage_onboard()
    if a.only in ("all", "pat"):
        stage_pat()
    if a.only in ("all", "report"):
        stage_report()

    STATE["ok"] = True
    print(json.dumps(STATE, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
