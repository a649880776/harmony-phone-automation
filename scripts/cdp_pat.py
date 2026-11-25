#!/usr/bin/env python3
"""cdp_pat.py —— 用 CDP 直接建 PAT 并落盘（取代「点侧栏→找设置→滚列表」）

为什么：
  `stage_pat` 靠点侧栏齿轮 → 找 Settings → 滚动找 Devin API，本轮在「打开设置」就卡住了
  （App 已经停在设置面板里，点齿轮反而切走）。CDP 可以直接导航到设置页 + 点 DOM 元素，
  一次到位，且 token 能从 DOM 里精确读出（不用读手机剪贴板）。

用法：
  ./cdp_pat.py                 # 导航 → PATs → Create token → 取 token → 写 devin_token.json
  ./cdp_pat.py --dry-run       # 只导航并打印页面结构，不建 token
退出码：0 成功；6 失败（见输出 JSON）
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cdp
import token_store as T

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE = os.path.join(ROOT, "profile.json")

CLICK_BY_TEXT = r"""
(() => {
  const want = %s, tag = (%s || '').toLowerCase();
  const els = [...document.querySelectorAll('button, a, [role=tab], [role=button], div, span')];
  // 优先精确匹配、且元素可见
  const vis = e => !!(e.offsetWidth || e.offsetHeight);
  let hit = els.find(e => vis(e) && (e.innerText || '').trim() === want && (!tag || e.tagName.toLowerCase() === tag));
  if (!hit) hit = els.find(e => vis(e) && (e.innerText || '').trim() === want);
  if (!hit) {
    const short = els.filter(e => vis(e) && (e.innerText || '').trim().startsWith(want)
                              && (e.innerText || '').length < want.length + 20);
    hit = short.sort((a, b) => (a.innerText || '').length - (b.innerText || '').length)[0];
  }
  if (!hit) return JSON.stringify({ok: false, why: 'not found', want});
  hit.click();
  return JSON.stringify({ok: true, tag: hit.tagName, text: (hit.innerText || '').trim().slice(0, 40)});
})()
"""

CLICK_TAB = r"""
(() => {
  const want = %s;
  const tabs = [...document.querySelectorAll('[role=tab]')].filter(e => e.offsetWidth || e.offsetHeight);
  const t = tabs.find(e => (e.innerText || '').trim() === want);
  if (!t) return JSON.stringify({ok: false, why: 'no tab', tabs: tabs.map(x => (x.innerText || '').trim())});
  t.click();
  return JSON.stringify({ok: true, text: (t.innerText || '').trim()});
})()
"""

SET_INPUT = r"""
(() => {
  const sel = %s, val = %s;
  const el = document.querySelector(sel);
  if (!el) return JSON.stringify({ok: false, why: 'no input'});
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  el.focus();
  setter.call(el, val);
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
  return JSON.stringify({ok: el.value === val, after: el.value});
})()
"""

SET_TOKEN_NAME = r"""
(() => {
  const want = %s;
  // 只认「弹窗里的、不是搜索框的」输入框：真实占位符是 e.g. CI/CD pipeline, local development
  const cands = [...document.querySelectorAll('input[type=text], input:not([type])')]
    .filter(e => (e.offsetWidth || e.offsetHeight))
    .filter(e => !/Search/i.test(e.getAttribute('placeholder') || ''));
  const el = cands.find(e => /CI\/CD|pipeline/i.test(e.getAttribute('placeholder') || ''))
          || cands[cands.length - 1];
  if (!el) return JSON.stringify({ok: false, why: 'no name input'});
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  el.focus();
  setter.call(el, want);
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
  return JSON.stringify({ok: el.value === want, ph: el.getAttribute('placeholder'), after: el.value});
})()
"""

TOKEN_TEXT = r"""
(() => {
  const t = document.body.innerText || '';
  const m = t.match(/\b[a-z]{3}_[A-Za-z0-9]{20,}\b/);
  if (m) return JSON.stringify({ok: true, token: m[0], src: 'text'});
  for (const el of document.querySelectorAll('input, textarea')) {
    if (/^[a-z]{3}_[A-Za-z0-9]{20,}$/.test((el.value || '').trim()))
      return JSON.stringify({ok: true, token: el.value.trim(), src: 'input'});
  }
  return JSON.stringify({ok: false});
})()
"""


def wait_text(needle, timeout=30, page=None, interval=1.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            r = json.loads(cdp.evaluate(
                "(() => JSON.stringify({hit: (document.body.innerText||'').includes(%s)}))()"
                % json.dumps(needle), page))
            if r.get("hit"):
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--name", default=None)
    a = ap.parse_args()

    out = {"ok": False, "steps": []}
    ok, why = cdp.ensure_forward()
    out["cdp"] = why
    if not ok:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 6

    page = cdp.pick_page(("app.devin.ai",))
    if not page:
        out["why"] = "没有 app.devin.ai 页面"
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 6
    m = re.search(r"/org/([^/]+)", page["url"])
    if not m:
        out["why"] = f"URL 里没有 org slug：{page['url'][:80]}"
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 6
    slug = m.group(1)
    out["org"] = slug

    # 1) 直达 Devin API 设置页（先把 onboarding 的拦截绕开）
    cdp.navigate(f"https://app.devin.ai/org/{slug}/settings/devin-api")
    time.sleep(3)
    if not wait_text("PATs", timeout=30):
        out["why"] = "设置页没出现 PATs（可能白屏，刷新一次）"
        cdp.evaluate("(() => { location.reload(); return 1; })()")
        time.sleep(6)
        if not wait_text("PATs", timeout=30):
            out["steps"].append({"field": "打开 Devin API 页", "ok": False})
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 6
    out["steps"].append({"field": "打开 Devin API 页", "ok": True})

    if a.dry_run:
        out["ok"] = True
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    # 2) PATs 页签
    r = json.loads(cdp.evaluate(CLICK_TAB % json.dumps("PATs"), page))
    out["steps"].append({"field": "点 PATs 页签", "ok": bool(r.get("ok")), "detail": r})
    time.sleep(1.5)

    # 3) Create token
    if not wait_text("Create token", timeout=20):
        out["why"] = "没有 Create token 按钮"
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 6
    r = json.loads(cdp.evaluate(CLICK_BY_TEXT % (json.dumps("Create token"), json.dumps("button")), page))
    out["steps"].append({"field": "点 Create token（开弹窗）", "ok": bool(r.get("ok"))})
    time.sleep(1.5)

    # 4) 填名字
    name = a.name or ("pat-" + time.strftime("%m%d") + "-" + os.urandom(2).hex())
    # 注意：不能用 input[type=text] 通配 —— 会命中侧栏的 "Search settings…" 搜索框（本轮踩坑）
    r = json.loads(cdp.evaluate(SET_TOKEN_NAME % json.dumps(name), page))
    out["steps"].append({"field": "填 token 名", "ok": bool(r.get("ok")),
                         "name": name, "placeholder": r.get("ph")})
    time.sleep(0.8)

    # 5) 弹窗里的 Create token（此时有两个同名按钮，取最后一个可见的）
    r = json.loads(cdp.evaluate(r"""
    (() => {
      const bs = [...document.querySelectorAll('button')]
        .filter(b => (b.innerText||'').trim() === 'Create token' && (b.offsetWidth||b.offsetHeight));
      if (!bs.length) return JSON.stringify({ok:false, why:'no dialog button'});
      bs[bs.length - 1].click();
      return JSON.stringify({ok:true, count: bs.length});
    })()""", page))
    out["steps"].append({"field": "提交创建", "ok": bool(r.get("ok"))})
    time.sleep(4)

    # 6) 读 token（只显示一次）
    tok = None
    for _ in range(10):
        r = json.loads(cdp.evaluate(TOKEN_TEXT, page))
        if r.get("ok"):
            tok = r["token"]
            break
        time.sleep(1)
    if not tok:
        out["why"] = "没读到 token 文本"
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 6
    out["token_tail"] = tok[-4:]
    out["steps"].append({"field": "读取 token", "ok": True, "tail": tok[-4:]})

    # 7) 落盘（带 status=ok）
    try:
        prof = json.load(open(PROFILE, encoding="utf-8"))
    except Exception:
        prof = {}
    n = T.append_event({
        "email": prof.get("email"), "card_id": prof.get("card_id"),
        "status": "ok", "stage": "创建 PAT", "token": tok, "token_name": name,
        "token_tail": tok[-4:], "org": slug,
        "card_masked": os.environ.get("CARD_MASKED"),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    out["steps"].append({"field": "写入 devin_token.json", "ok": True, "total": n})

    # 8) 关掉弹窗
    cdp.evaluate(CLICK_BY_TEXT % (json.dumps("I saved the token"), json.dumps("")), page)

    out["ok"] = True
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
