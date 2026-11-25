#!/usr/bin/env python3
"""lib_ui.py —— 手机 UI 自动化的共享底层库（2026-11-20 实战沉淀）

设计原则（每条都踩过坑）：
1. 任何坐标都必须来自「当场 dump 的控件树」，绝不复用上一次的坐标（键盘弹出会让按钮上移几百像素）；
2. 滚动只用 swipe（velocity ≥ 1800），从左右边缘空白处起手：
   - 慢速 drag 会被系统识别成长按 → 弹放大镜/文本选择；
   - 在页面顶部（scrollTop=0）做下拉手势 → 触发浏览器下拉刷新，表单全丢；
3. 下拉列表用 fling 大惯性 + 位置缓存（.pickcache.json），避免几十次小拖拽；
4. 每步之后跑一次 anomaly()，发现异常立刻返回，由调用方决定「问人」还是「自动重试」；
5. 密文/卡号/验证码等敏感值只允许在本进程内传递，只输出掩码。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PH = os.path.join(ROOT, "scripts", "ph.sh")
def _pick_shots_dir():
    cand = os.environ.get("SHOTS_DIR") or os.path.join(ROOT, "shots")
    try:
        os.makedirs(cand, exist_ok=True)
        probe = os.path.join(cand, ".w")
        open(probe, "w").close(); os.unlink(probe)
        return cand
    except Exception:
        fb = "/tmp/phshots"
        os.makedirs(fb, exist_ok=True)
        return fb


SHOTS = _pick_shots_dir()
CACHE_FILE = os.path.join(ROOT, ".pickcache.json")


# ---------------- 设备 ----------------
def device_online():
    """hdc 是否还能列到设备（掉线时所有操作都会静默失败，必须先查这个）"""
    try:
        r = subprocess.run([PH, "targets"], capture_output=True, text=True, timeout=15)
        out = (r.stdout or "").strip()
        return bool(out) and "[Empty]" not in out and r.returncode == 0
    except Exception:
        return False


# ---------------- 基础动作 ----------------
def ph(*args, wait: float = 0.0, quiet: bool = True):
    subprocess.run([PH, *args], env=dict(os.environ), check=False,
                   stdout=subprocess.DEVNULL if quiet else None,
                   stderr=subprocess.DEVNULL if quiet else None)
    if wait:
        time.sleep(wait)


def shot(name="screen"):
    path = os.path.join(SHOTS, f"{name}.jpeg")
    ph("shot", name)
    return path


def dump(tag="ui"):
    path = os.path.join(SHOTS, f"{tag}.json")
    for _ in range(3):
        ph("ui", tag)
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            time.sleep(0.2)
    return None


# ---------------- 控件树解析 ----------------
def nodes(d):
    out = []

    def walk(n):
        out.append(n)
        for c in n.get("children") or []:
            walk(c)

    if d:
        walk(d)
    return out


def _bounds(b):
    try:
        q = b.replace("][", ",").strip("[]").split(",")
        x1, y1, x2, y2 = (int(float(v)) for v in q)
        return x1, y1, x2, y2
    except Exception:
        return None


def center(b):
    r = _bounds(b)
    if not r:
        return None
    x1, y1, x2, y2 = r
    return (x1 + x2) // 2, (y1 + y2) // 2


def text_of(n):
    a = n.get("attributes", {})
    return (a.get("text") or "").strip()


def find_nodes(d, pred):
    return [n for n in nodes(d) if pred(n)]


def find_text(d, needle):
    """精确匹配 → 包含匹配，返回第一个命中节点的中心坐标"""
    if not d:
        return None
    exact = [n for n in nodes(d) if text_of(n) == needle]
    fuzzy = [n for n in nodes(d) if needle in text_of(n)]
    for n in (exact or fuzzy):
        c = center(n.get("attributes", {}).get("bounds", ""))
        if c:
            return c
    return None


def has_text(d, needle):
    return find_text(d, needle) is not None


def screen_text(d):
    return " | ".join(t for t in (text_of(n) for n in nodes(d)) if t)


# ---------------- 输入 ----------------
def tap(x, y, wait=0.6):
    ph("tap", str(x), str(y), wait=wait)


def tap_xy(xy, wait=0.6):
    if not xy:
        return False
    tap(xy[0], xy[1], wait)
    return True


def type_text(value, wait=0.6):
    ph("text", str(value), wait=wait)


def back(wait=0.5):
    ph("key", "Back", wait=wait)


def delete_chars(n=12, wait_each=0.12):
    for _ in range(n):
        ph("key", "2055", wait=wait_each)


def fill_field(xy, value, settle=0.7, hide_keyboard=True):
    """点一下 → 输入 → 收键盘。收键盘是为了让布局回到可预测状态"""
    if not xy:
        return False
    tap(xy[0], xy[1], wait=settle)
    type_text(value, wait=0.5)
    if hide_keyboard:
        back(wait=0.45)
    return True


# ---------------- 安全滚动（避免放大镜/下拉刷新） ----------------
OPT_PX = 156          # Stripe 下拉列表单个选项高度（本机实测，2026-11-25）
LIST_TOP, LIST_BOTTOM = 498, 2490
PAGE_PX = 1560        # 一次精确 swipe 覆盖的距离（10 个选项，无惯性）


def swipe_list(pages=1, page_px=PAGE_PX, x=580, velocity=700):
    """精确翻列表：低速 swipe（无惯性），位移≈手指位移。

    为什么不用 fling：fling 的惯性不可控，velocity 6000 能一次从「中国」飞到「阿尔巴尼亚」，
    要么越过目标、要么几十次小拖（2026-11-25 实测）。
    """
    for _ in range(pages):
        y2 = max(LIST_TOP + 120, LIST_BOTTOM - 120 - page_px)
        ph("swipe", str(x), str(LIST_BOTTOM - 60), str(x), str(y2), str(velocity), wait=0.32)


def list_view(d):
    """返回当前列表可视区的 (首项, 尾项) 名称"""
    names = []
    for n in nodes(d):
        a = n.get("attributes", {})
        if a.get("type") != "Option":
            continue
        b = _bounds(a.get("bounds", ""))
        t = text_of(n)
        if t and b and (b[2] - b[0]) > 0 and b[3] > b[1]:
            names.append((b[1], t))
    names.sort()
    return (names[0][1] if names else None, names[-1][1] if names else None)


def pick_option_scroll(name, blind_pages=0, tag="pickS", verify_pages=6, allow_full_scan=True,
                       cache_kind=None):
    """选列表项：可先盲翻 blind_pages 页（无 dump，最快），再逐屏校验。

    用法（美国：约第 148 项 → 盲翻 14 页 ≈ 140 项，剩下 1 屏内搞定）：
        pick_option_scroll("美国", blind_pages=14, cache_kind="country")

    找不到时的兜底：回到列表顶部，逐屏向下扫（最多 30 页），扫到就点并把
    「需要盲翻几页」写进 .pickcache.json，下次直接盲翻即可。
    """
    if blind_pages:
        swipe_list(pages=blind_pages)
    hit = _scan_forward(name, tag, verify_pages)
    if hit is not None:
        return True
    if not allow_full_scan:
        return False
    # 兜底：先拉回顶部（模态列表里向上手势不会触发刷新），再逐屏扫
    for _ in range(25):
        ph("swipe", "580", str(LIST_TOP + 60), "580", str(LIST_TOP + 1500), "700", wait=0.28)
    pages = 0
    hit = _scan_forward(name, tag + "_full", 30, record_pages=True,
                        pages_box={"n": 0})
    if hit is not None:
        if cache_kind:
            cache = _load_cache()
            cache[f"pages:{cache_kind}:{name}"] = hit
            _save_cache(cache)
        return True
    return False


def _scan_forward(name, tag, pages=6, record_pages=False, pages_box=None):
    """逐屏向下找 name；命中点选并返回 True，否则返回 None"""
    for i in range(pages + 1):
        d = dump(f"{tag}{i}")
        for n in nodes(d):
            a = n.get("attributes", {})
            if a.get("type") == "Option" and text_of(n) == name:
                b = _bounds(a.get("bounds", ""))
                if b and (b[2] - b[0]) > 0:
                    xy = center(a.get("bounds", ""))
                    if xy:
                        tap_xy(xy, wait=1.0)
                        return i if record_pages else True
        if i < pages:
            swipe_list(pages=1)
    return None


def cached_blind_pages(name, kind, default=0):
    c = _load_cache()
    return c.get(f"pages:{kind}:{name}", default)


def scroll(direction="down", distance=1100, velocity=2000, x=None, max_tries=3,
           top_markers=None):
    """滚动页面。

    实测结论（2026-11-20，Devin 设置页/Stripe 页）：
      * `uitest uiInput fling` 有效，能稳定翻页；
      * `uitest uiInput drag` 与 `uinput -M` 鼠标事件在这个 App 里**不生效**；
      * 慢速 drag 还会被识别成长按 → 放大镜/文本选择；
      * **页面顶部做「手指向下」手势 → Chrome 下拉刷新，Stripe 表单全丢**。

    因此：direction="up"（手指向下）一律先过顶部标志护栏，看到顶部标志就拒绝执行。
    """
    if direction == "up":
        markers = top_markers or ("订阅 Max",)
        if at_top(dump("guard_up"), markers):
            print("[guard] 已在页面顶部，拒绝向上手势（会触发下拉刷新）",
                  file=sys.stderr)
            return False
    x = x if x is not None else 500           # 列表主体，不贴边
    if direction == "down":                   # 内容向上翻（看下面的内容）
        y1, y2 = 1900, max(500, 1900 - distance)
    else:                                     # 内容向下翻（往回看）
        y1, y2 = 900, min(2400, 900 + distance)
    ph("fling", str(x), str(y1), str(x), str(y2), str(velocity), "300", wait=0.7)


def scroll_up(steps=2, distance=1100):
    """内容往上翻（手指下滑）。页面不在顶部时才安全；在顶部请改用 scroll_top()"""
    for _ in range(steps):
        ph("swipe", "25", "1200", "25", str(1200 + distance), "2500", wait=0.5)


def pick_option_safe(label_text, max_steps=14, step=800, tag="pick", allow_retry_top=True):
    """逐屏查找并点选列表项（比 pick_option 稳：不做大惯性 fling，绝不越过目标）。

    为什么不用 pick_option 的缓存 fling：velocity=40000×N 次会在列表刚打开时就飞过目标，
    之后往下滚再也找不回来（2026-11-25 国家列表实测踩坑）。
    命中条件：出现 type=Option 且文本等于/包含 label_text。返回 True/False。
    """
    def _hit(d):
        for n in nodes(d):
            a = n.get("attributes", {})
            if a.get("type") != "Option":
                continue
            t = text_of(n)
            if t == label_text or (len(label_text) >= 2 and label_text in t):
                xy = center(a.get("bounds", ""))
                if xy:
                    return xy
        return None

    for round_ in range(2 if allow_retry_top else 1):
        if round_ == 1:                       # 第二轮：先把列表拉回顶部再扫
            for _ in range(14):
                ph("fling", "580", "700", "580", "2100", "2500", "300", wait=0.3)
        for i in range(max_steps + 1):
            d = dump(f"{tag}_r{round_}_{i}")
            xy = _hit(d)
            if xy:
                tap_xy(xy, wait=1.2)
                return True
            if i < max_steps:
                ph("fling", "580", "1900", "580", str(max(500, 1900 - step)),
                   "1800", "250", wait=0.55)
    return False


def at_top(d, markers):
    """页面顶部标志是否就在当前控件树里。

    这是「不会触发下拉刷新」的唯一安全判据：Chrome 的下拉刷新只在 scrollTop==0
    且手指向下拖时才触发；而 scrollTop==0 时顶部标志必然可见。
    所以「看到顶部标志 → 绝不做向上手势」是完备的护栏。
    """
    return any(has_text(d, m) for m in markers)


def scroll_top(markers=None, max_steps=8, distance=380):
    """【安全版】回页面顶部。

    旧实现（已废）：先 dump 再 fling，两次 dump 一样才停 → 在顶部还会再 fling 一次，
    直接触发 Chrome 下拉刷新，Stripe 表单被清空（2026-11-25 真实事故）。

    新实现：**先判顶部标志**，看到就一步都不动；看不到才小步上滑（低速度、短距离），
    每步都重新判标志。markers 传当前页面「只在顶部出现」的文本。
    """
    if not markers:
        markers = ("订阅 Max",)                 # 兜底：Stripe 结账页顶部标题
    for _ in range(max_steps):
        d = dump("top_probe")
        if at_top(d, markers):
            return True                        # 已在顶部，零手势
        ph("fling", "580", "760", "580", "1160", "900", "200", wait=0.55)  # 小步、低惯性
    d = dump("top_probe_end")
    return at_top(d, markers)


def has_pull_refresh_risk(d, markers):
    """给调用方的体检口：在顶部就不要做任何「手指向下」的手势"""
    return at_top(d, markers)


def fling(direction="down", velocity=40000, step=300, times=1):
    for _ in range(times):
        if direction == "down":
            ph("fling", "580", "2100", "580", "700", str(velocity), str(step), wait=0.35)
        else:
            ph("fling", "580", "700", "580", "2100", str(velocity), str(step), wait=0.35)


# ---------------- 下拉列表（带缓存） ----------------
def _load_cache():
    try:
        return json.load(open(CACHE_FILE, encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(c):
    try:
        json.dump(c, open(CACHE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    except Exception:
        pass


def pick_option(label_text, max_scroll=30, step=1100, fling_first=None, tag="pick"):
    """当前已打开的列表里滚动查找并点选 label_text。命中返回 True。"""
    cache = _load_cache()
    key = "list:" + label_text
    flings = fling_first if fling_first is not None else cache.get(key, 0)
    if flings:
        fling("down", times=flings)
    for i in range(1, max_scroll + 1):
        d = dump(f"{tag}{i}")
        pos = find_text(d, label_text) if d else None
        opts = [n for n in nodes(d) if n.get("attributes", {}).get("type") == "Option"]
        if pos and opts:
            tap_xy(pos, wait=1.0)
            cache[key] = flings + max(0, i - 1)
            _save_cache(cache)
            return True
        ph("fling", "580", "1900", "580", str(max(500, 1900 - step)), "1500", "300", wait=0.35)
    return False


# ---------------- 等待 ----------------
def wait_text(needle, timeout=20, tag="wait"):
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = dump(tag)
        if has_text(d, needle):
            return True
        time.sleep(0.8)
    return False


def wait_any(needles, timeout=20, tag="wait_any"):
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = dump(tag)
        for nd in needles:
            if has_text(d, nd):
                return nd
        time.sleep(0.8)
    return None


# ---------------- 收软键盘（点击前必做） ----------------
# 血泪史（2026-11-25 三次）：
#   1) 拿 `visualViewport.height < innerHeight` 判键盘 → ArkWeb 里不成立（vv==ih）；
#   2) 用「焦点是否在输入框」当键盘判据 → 也不成立：Stripe 会**程序化聚焦**卡号框
#      （不弹键盘），这时按 Back 会触发**页面后退**，结账页直接被退回「Choose your plan」；
#   3) 结论：**任何时候都不要用 key Back 收键盘**。只用「JS blur + 点安全空白处」。
#      真正会挡住点击的键盘，用 CDP 的 scrollIntoView + click 可以直接绕过（见 run_flow._cdp_submit）。

NEUTRAL_TEXT = ("计费详情", "支付方式", "联系信息", "订阅 Max", "Devin API",
                "Choose your plan", "What's next", "Skip to main content")


def _blur_all():
    try:
        import cdp as _cdp
        if not _cdp.ensure_forward()[0]:
            return False
        for t in (_cdp.find_select_target("country"), _cdp.pick_page()):
            if t:
                _cdp.evaluate("(() => { document.activeElement && document.activeElement.blur();"
                              " return 1; })()", t)
        return True
    except Exception:
        return False


FOCUS_JS = """(() => {
  const a = document.activeElement;
  const inField = !!(a && /INPUT|TEXTAREA|SELECT/.test(a.tagName));
  return JSON.stringify({inField, tag: a ? a.tagName : null, id: a ? (a.id || null) : null});
})()"""


def _focus_state():
    """(焦点是否在输入框, 说明)。主页面与 Stripe iframe 都查。

    注意：这里只用于「要不要 blur / 点空白」，**绝不能**用来决定按不按 Back。
    """
    try:
        import json as _json
        import cdp as _cdp
        if not _cdp.ensure_forward()[0]:
            return None, "no-cdp"
        for t in (_cdp.find_select_target("country"), _cdp.pick_page()):
            if not t:
                continue
            try:
                r = _json.loads(_cdp.evaluate(FOCUS_JS, t))
            except Exception:
                continue
            if r.get("inField"):
                return True, f"{r.get('tag')}#{r.get('id')}"
        return False, "none"
    except Exception as e:
        return None, type(e).__name__


def dismiss_keyboard(max_try=2):
    """尽力收键盘：JS blur → 仍聚焦时点一个安全标题。**绝不按 Back**。

    返回 True=已 blur 过（不保证键盘一定收起，但不会造成页面后退）。
    """
    _blur_all()
    st, _ = _focus_state()
    if st is False:
        return True
    for _ in range(max_try):
        d = dump("kb_neutral")
        target = None
        for n in nodes(d):
            t = text_of(n)
            a = n.get("attributes", {})
            if t in NEUTRAL_TEXT:
                xy = center(a.get("bounds", ""))
                if xy:
                    target = xy
                    break
        if not target:
            break
        tap_xy(target, wait=0.6)          # 点标题 = blur，不会触发导航
        st, _ = _focus_state()
        if st is False:
            break
    return True


def keyboard_open():
    """⚠️ 仅作参考：焦点在输入框**不代表**键盘在弹（程序化聚焦不弹键盘）。
    这里保留只为了不破坏旧调用，别再用它做「要不要按 Back」的判断。
    """
    st, _ = _focus_state()
    return None if st is None else bool(st)


# ---------------- 异常检测 ----------------
# (code, 关键词/正则, 说明, 建议动作)
ANOMALY_RULES = [
    ("NET_UNSTABLE", r"网络连接不稳定|网站暂时不稳定|请刷新重试", "页面报网络/站点异常", "reload"),
    ("BLANK_PAGE", r"^$", "页面空白（可能是 SPA 没渲染）", "reload"),
    ("ZIP_INVALID", r"邮编无效|ZIP 无效|postal code is invalid", "邮编校验失败", "recheck_country_zip"),
    ("CARD_DECLINED", r"被拒绝|declined|卡被拒|Your card was declined", "发卡行拒绝交易", "ask_human"),
    ("CARD_INVALID", r"卡号无效|incorrect_number|卡无效", "卡号/有效期/CVV 不合法", "ask_human"),
    ("ADDRESS_MISMATCH", r"地址不匹配|address_zip_check|AVS", "AVS 地址校验失败", "ask_human"),
    ("3DS_REQUIRED", r"验证您的身份|3D Secure|验证身份", "需要 3DS 验证", "ask_human"),
    ("CODE_INVALID", r"验证码错误|验证码无效|code is invalid|无效的验证码", "验证码错误或已过期", "refetch_code"),
    ("2FA_WRONG", r"2FA验证码错误", "取卡接口 2FA 校验失败", "ask_human"),
    ("SESSION_EXPIRED", r"会话已过期|session expired|请重新开始", "会话失效", "restart_flow"),
    ("TABS_EMPTY", r"No personal access tokens yet", "（信息）PAT 列表为空", "info"),
]


def anomalies(d, include_info=False):
    """返回命中的异常列表 [{code, hint, action, sample}]"""
    txt = screen_text(d) if d else ""
    hits = []
    for code, pattern, hint, action in ANOMALY_RULES:
        if code == "BLANK_PAGE":
            if txt.strip() == "":
                hits.append({"code": code, "hint": hint, "action": action, "sample": ""})
            continue
        m = re.search(pattern, txt)
        if m:
            if action == "info" and not include_info:
                continue
            hits.append({"code": code, "hint": hint, "action": action, "sample": m.group(0)[:40]})
    return hits


def check(include_info=False, tag="chk"):
    d = dump(tag)
    hits = anomalies(d, include_info=include_info)
    return {"ok": not hits, "anomalies": hits, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}


# ---------------- 便捷：一步做完并检查 ----------------
def step(name, fn, expect=None, timeout=20):
    """执行 fn()，然后按 expect 文本确认结果；异常/超时都返回结构化结果"""
    r = fn()
    d = dump("step_" + re.sub(r"\W+", "_", name))
    hits = anomalies(d)
    ok = True
    reason = ""
    if expect:
        ok = has_text(d, expect)
        if not ok:
            reason = f"没等到预期文本「{expect}」"
    return {"step": name, "result": r, "ok": ok and not hits,
            "anomalies": hits, "reason": reason}
