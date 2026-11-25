#!/usr/bin/env python3
"""cdp.py —— 通过 hdc 端口转发直连手机浏览器 WebView 的 CDP，直接操作页面 DOM。

为什么要有这个：
  下拉框（国家/州）靠「点击 + 滑动找选项」既慢又不可靠（单次 dump 1.1s、fling 落点抖动
  可达 15 行，选个国家要几十秒）。而 Chrome/ArkWeb 的 DevTools 协议可以直接
  在页面里执行 JS —— `select.value='US'` 再派发 change 事件，一次调用搞定，无需滚动。
  （2026-11-25 用户明确要求：不要靠滑动，要能直接设值）

前置：
  hdc fport tcp:9222 localabstract:webview_devtools_remote_<browser-pid>
  （pid 取 `hdc shell pidof com.huawei.hmos.browser`）

用法：
  ./cdp.py probesel                 # 列出页面里所有 select（名字/值/选项数）
  ./cdp.py eval "<JS 表达式>"        # 在页面里求值，打印 JSON 结果
  ./cdp.py setcountry US            # 设国家（select[autocomplete=country] / 兜底按 name 匹配）
  ./cdp.py targets                  # 列出可调试的页面
"""
import json
import sys
import urllib.request

import websocket

PORT = 9222
BASE = f"http://127.0.0.1:{PORT}"


# ---------------- 自动建立端口转发（流程会重启浏览器，PID 会变） ----------------
import os
import subprocess

HDC_CANDIDATES = [
    os.environ.get("HDC_BIN"),
    "/Applications/DevEco-Studio.app/Contents/sdk/default/openharmony/toolchains/hdc",
    "/Applications/HoKit.app/Contents/Resources/assets/tools/hdc/hdc",
]


def hdc_bin():
    for c in HDC_CANDIDATES:
        if c and os.access(c, os.X_OK):
            return c
    raise RuntimeError("找不到 hdc")


def _sh(*args, timeout=10):
    r = subprocess.run([hdc_bin(), *args], capture_output=True, text=True, timeout=timeout)
    return (r.stdout or "") + (r.stderr or "")


def device_id():
    out = _sh("list targets")
    for line in out.splitlines():
        line = line.strip()
        if line and not line.startswith("[") and "Empty" not in line:
            return line
    return None


def ensure_forward(port=PORT, quiet=False):
    """确保 tcp:<port> → webview_devtools_remote_<浏览器 pid> 的转发可用。

    浏览器一重启 pid 就变，旧转发会失效；这里每次都重新推导，幂等。
    返回 (ok, 说明)。
    """
    dev = device_id()
    if not dev:
        return False, "没有设备"
    out = _sh("-t", dev, "shell", "pidof com.huawei.hmos.browser")
    pid = out.strip().split()[-1] if out.strip() else ""
    if not pid:
        return False, "浏览器没在跑"
    sock = f"webview_devtools_remote_{pid}"
    if sock not in _sh("-t", dev, "shell", "cat /proc/net/unix"):
        return False, f"没有 {sock}（web 调试没开？）"
    # 已有同 port 的转发就先拆，避免指向旧 pid
    ls = _sh("-t", dev, "fport", "ls")
    for line in ls.splitlines():
        if f"tcp:{port}" in line:
            parts = line.split()
            if sock not in line:
                _sh("-t", dev, "fport", "rm", f"tcp:{port}", parts[2] if len(parts) > 2 else "")
            else:
                try:
                    urllib.request.urlopen(BASE + "/json", timeout=4)
                    return True, f"已可用（{sock}）"
                except Exception:
                    _sh("-t", dev, "fport", "rm", f"tcp:{port}", sock)
    r = _sh("-t", dev, "fport", f"tcp:{port}", f"localabstract:{sock}")
    try:
        urllib.request.urlopen(BASE + "/json", timeout=5)
        return True, f"已建立（{sock}）"
    except Exception as e:
        return False, f"转发失败：{r.strip()[:80]} / {type(e).__name__}"


def drop_forward(port=PORT):
    dev = device_id()
    if not dev:
        return False
    ls = _sh("-t", dev, "fport", "ls")
    for line in ls.splitlines():
        if f"tcp:{port}" in line:
            parts = line.split()
            _sh("-t", dev, "fport", "rm", f"tcp:{port}", parts[2] if len(parts) > 2 else "")
    return True


def targets():
    with urllib.request.urlopen(BASE + "/json", timeout=6) as r:
        return json.load(r)


def pick_page(match=("checkout.stripe.com", "app.devin.ai")):
    """优先挑顶层 page（非 iframe），且 URL 命中 match"""
    ts = targets()
    pages = [t for t in ts if t.get("type") == "page"]
    for m in match:
        for t in pages:
            if m in (t.get("url") or "") and not t.get("parentId"):
                return t
    for m in match:
        for t in ts:
            if m in (t.get("url") or ""):
                return t
    return pages[0] if pages else None


def evaluate(expr, page=None, timeout=15, await_promise=True):
    page = page or pick_page()
    if not page:
        raise RuntimeError("没有可调试的页面")
    ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=timeout)
    try:
        msg = {"id": 1, "method": "Runtime.evaluate",
               "params": {"expression": expr, "returnByValue": True,
                          "awaitPromise": await_promise, "userGesture": True}}
        ws.send(json.dumps(msg))
        while True:
            data = json.loads(ws.recv())
            if data.get("id") == 1:
                res = (data.get("result") or {}).get("result") or {}
                if "value" in res:
                    return res["value"]
                return {"__exception__": (data.get("result") or {}).get("exceptionDetails")
                        or res}
    finally:
        ws.close()


PROBE_SELECT = r"""
(() => {
  const out = [];
  document.querySelectorAll('select').forEach((s, i) => {
    out.push({
      i, name: s.name || null, id: s.id || null,
      autocomplete: s.getAttribute('autocomplete'),
      aria: s.getAttribute('aria-label'),
      value: s.value, options: s.options.length,
      visible: !!(s.offsetWidth || s.offsetHeight),
    });
  });
  return JSON.stringify(out, null, 1);
})()
"""

SET_COUNTRY = r"""
(() => {
  const name = %s, want = %s;
  const s = [...document.querySelectorAll('select')].find(x => x.name === name);
  if (!s) return null;                       // 这个 frame 没有目标 select
  const before = s.value;
  // ⚠️ 必须用原生 value setter：直接 s.value=want 会被 React 受控组件回滚成空
  //    （2026-11-25 事故：州字段回读是空，页面显示没选州）
  const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set;
  setter.call(s, want);          // 不 focus()：避免弹键盘
  s.dispatchEvent(new Event('input', {bubbles: true}));
  s.dispatchEvent(new Event('change', {bubbles: true}));
  s.blur();
  // 再确认一次（有些实现会在 change 后重排 options）
  if (s.value !== want) {
    const idx = [...s.options].findIndex(o => o.value === want);
    if (idx >= 0) { s.selectedIndex = idx;
      s.dispatchEvent(new Event('change', {bubbles: true})); }
  }
  return JSON.stringify({ok: s.value === want, name, before, after: s.value,
                         options: s.options.length, where: location.href.slice(0, 50)});
})()
"""


def navigate(url, page=None, timeout=15):
    """在当前页执行 Page.navigate（用于直达设置页，绕开 SPA 的点击路径）"""
    page = page or pick_page()
    if not page:
        raise RuntimeError("没有可调试的页面")
    ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=timeout)
    try:
        ws.send(json.dumps({"id": 1, "method": "Page.navigate", "params": {"url": url}}))
        while True:
            data = json.loads(ws.recv())
            if data.get("id") == 1:
                return (data.get("result") or {}).get("frameId") or "ok"
    finally:
        ws.close()


def find_select_target(name):
    """在主页 + 所有 iframe 里找含有 select[name=...] 的那个 target"""
    for t in targets():
        try:
            names = [x.get("name") for x in json.loads(evaluate(PROBE_SELECT, t) or "[]")]
        except Exception:
            continue
        if name in names:
            return t
    return None


def set_select(name, want):
    """直接给 select 赋值并派发 change —— 不走点击/滑动（Stripe 字段在 Elements iframe 里）"""
    t = find_select_target(name)
    if not t:
        return json.dumps({"ok": False, "why": f"没有找到 select[name={name}]"}, ensure_ascii=False)
    return evaluate(SET_COUNTRY % (json.dumps(name), json.dumps(want)), t)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    if cmd == "targets":
        for t in targets():
            print(f"{t.get('type'):8s} {t.get('id')}  parent={bool(t.get('parentId'))}  {(t.get('title') or '')[:50]}")
        return 0
    if cmd == "probesel":
        print(evaluate(PROBE_SELECT))
        return 0
    if cmd == "eval":
        if len(sys.argv) < 3:
            print("需要 JS 表达式", file=sys.stderr)
            return 2
        print(evaluate(sys.argv[2]))
        return 0
    if cmd == "setcountry":
        want = sys.argv[2] if len(sys.argv) > 2 else "US"
        print(set_select("country", want))
        return 0
    if cmd == "setselect":
        if len(sys.argv) < 4:
            print("用法: setselect <select name> <value>", file=sys.stderr)
            return 2
        print(set_select(sys.argv[2], sys.argv[3]))
        return 0
    if cmd == "closeport":
        print("提示：用 `hdc fport rm tcp:9222` 关闭转发", file=sys.stderr)
        return 0
    print("未知命令", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
