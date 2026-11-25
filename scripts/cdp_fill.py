#!/usr/bin/env python3
"""cdp_fill.py —— 用 CDP 直接给 Stripe 结账页赋值（不再靠点击/输入法/滚动）

为什么：
  * 国家下拉靠滑动选，单次要几十秒且会误触（2026-11-25）；
  * Stripe 的字段全在 `elements-inner-habanero` iframe 里，都是普通 input/select，
    直接 `el.value=... + dispatch input/change` 即可，一次调用 ~250ms。
  * 卡号/有效期/CVV 也在这个 iframe（payment-numberInput / payment-expiryInput / payment-cvcInput）。

安全：
  * 卡号/CVV 只在内存里传给页面，本脚本只输出掩码（前 4 后 4）；
  * 不写临时文件、不 echo、不进日志。

用法：
  ./cdp_fill.py                       # 取卡 → 填卡 + 账单地址 + 勾选协议 → 自检
  ./cdp_fill.py --no-consent          # 不勾协议（只想填字段时）
  ./cdp_fill.py --dry-run             # 只打印将要填的内容（掩码）
  ./cdp_fill.py --submit              # 填完后点「订阅」（危险：真扣款）
退出码：0 成功；3 取卡失败；6 字段回读不一致
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cdp
import fetch_card as F

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE = os.path.join(ROOT, "profile.json")

# 设置一个输入框：写值 + 派发 input/change/blur，逼 Stripe 的校验与格式化跑起来
#
# 关键：结账页是 React 受控组件。直接 `el.value = x` 只改 DOM，不改 React state；
# 下一次重渲染（例如随后又去选「州」）会把值**回滚成空**，回读就全是空字符串
# （2026-11-25 实测：卡号留住了，地址/姓名全被清空）。必须用原生 value setter
# 绕开 React 对 value 的劫持，让 input 事件真正进入它的 onChange。
SET_INPUT = r"""
(() => {
  const sel = %s, val = %s;
  const el = document.querySelector(sel);
  if (!el) return JSON.stringify({ok:false, why:'not found', sel});
  const before = el.value;
  const proto = el instanceof HTMLSelectElement ? HTMLSelectElement.prototype
                                                : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
  // ⚠️ 绝不 el.focus()：焦点一进输入框，系统就会弹数字键盘挡住页面（用户 2026-11-25 指出）
  setter.call(el, val);
  el.dispatchEvent(new Event('input', {bubbles:true}));
  el.dispatchEvent(new Event('change', {bubbles:true}));
  el.blur();
  // 兜底：把当前 frame 和父页面上的焦点都清掉
  try { document.activeElement && document.activeElement.blur(); } catch (e) {}
  return JSON.stringify({ok: el.value !== '', sel, before, after: el.value,
                         focused: !!(document.activeElement && /INPUT|SELECT/.test(document.activeElement.tagName))});
})()
"""

READBACK = r"""
(() => {
  const g = s => { const e = document.querySelector(s); return e ? e.value : null; };
  return JSON.stringify({
    number: g('#payment-numberInput'),
    expiry: g('#payment-expiryInput'),
    cvc: g('#payment-cvcInput'),
    name: g('#billingAddress-nameInput'),
    country: g('#billingAddress-countryInput'),
    address1: g('#billingAddress-addressLine1Input'),
    locality: g('#billingAddress-localityInput'),
    state: g('#billingAddress-administrativeAreaInput'),
    zip: g('#billingAddress-postalCodeInput'),
    consent: (() => { const e = document.querySelector('#paymentForm-termsOfServiceConsentInput');
                      return e ? e.checked : null; })(),
    errors: [...document.querySelectorAll('[role=alert], .Error, [class*=Error]')]
              .map(e => (e.innerText || '').trim()).filter(Boolean).slice(0, 6),
  });
})()
"""

CLICK_SUBMIT = r"""
(() => {
  const btns = [...document.querySelectorAll('button')].filter(b => /订阅|Subscribe|Pay/.test(b.innerText || ''));
  const b = btns[0];
  if (!b) return JSON.stringify({ok:false, why:'no submit button',
                                 buttons: [...document.querySelectorAll('button')].map(x=>x.innerText)}) ;
  if (b.disabled) return JSON.stringify({ok:false, why:'disabled'});
  b.click();
  return JSON.stringify({ok:true, text:(b.innerText||'').trim()});
})()
"""


def mask(pan):
    return f"{pan[:4]} **** **** {pan[-4:]}"


def get_card(card_id, userid):
    env = F.load_env()
    base = F.cfg(env, "CARD_API_BASE", default="https://agent.hiaihk.com/prod/api")
    path = F.cfg(env, "CARD_API_FETCH_PATH", default="/rentCardInfo")
    token = F.cfg(env, "CARD_API_TOKEN", "RENTCARD_TOKEN")
    secret = F.cfg(env, "2FA_KEY", "TOTP_SECRET")
    key_path = F.cfg(env, "CARD_RSA_KEY_PATH", default=F.DEFAULT_KEY)
    url = f"{base.rstrip('/')}/{path.lstrip('/')}?token={token}"
    code = F.make_totp(secret)
    st, resp = F.rent_card_info(url, {"card_id": card_id, "userid": int(userid),
                                      "code": (str(code) if str(code).startswith("0") else int(code))})
    if st != 200 or not resp or resp.get("success") is not True:
        print(f"取卡失败: {(resp or {}).get('message', st)}", file=sys.stderr)
        sys.exit(3)
    c = F.normalize(resp.get("data") or {})
    pt, _ = F.decrypt_cvv(c["cvv_cipher"], F.load_private_key(key_path))
    c["cvc"] = pt.decode().strip()
    pt = b"\x00" * len(pt)
    c["exp_digits"] = re.sub(r"\D", "", c["exp"])
    return c



def find_form_target():
    """定位 Stripe 表单 iframe：优先卡号框（card-only 场景没有国家下拉）。"""
    for probe in ("#payment-numberInput", "#billingAddress-countryInput"):
        for t in cdp.targets():
            try:
                r = json.loads(cdp.evaluate(
                    "(() => JSON.stringify({has: !!document.querySelector(%s)}))()" % json.dumps(probe), t))
                if r.get("has"):
                    return t
            except Exception:
                continue
    return None



BLUR_JS = r"""
(() => {
  try { document.activeElement && document.activeElement.blur(); } catch (e) {}
  const a = document.activeElement || {};
  return JSON.stringify({focused: /INPUT|TEXTAREA|SELECT/.test(a.tagName || ''),
                         tag: a.tagName || null, id: a.id || null});
})()
"""


def settle_focus(t, label, out):
    """每填完一个字段就清一次焦点，顺带记录是否还有输入框处于聚焦（=键盘可能弹着）。

    用户 2026-11-25 明确要求：账单页里每填一个字段都要把键盘收掉，
    否则数字键盘会挡住页面，后续点击全部落到键盘上。
    """
    res = {}
    for name, target in (("iframe", t), ("page", cdp.pick_page())):
        if not target:
            continue
        try:
            res[name] = json.loads(cdp.evaluate(BLUR_JS, target))
        except Exception:
            res[name] = None
    still = [k for k, v in res.items() if isinstance(v, dict) and v.get("focused")]
    out.setdefault("focus_after", []).append({"field": label, "still_focused": still})
    return not still


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--card-id")
    ap.add_argument("--userid", default=os.environ.get("CARD_API_USER_ID", "8"))
    ap.add_argument("--no-consent", action="store_true")
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--card-only", action="store_true",
                    help="只重填卡号/有效期/CVC（拒卡后账单地址会被锁，不能再动地址）")
    a = ap.parse_args()

    prof = json.load(open(PROFILE, encoding="utf-8"))
    b = prof["billing"]
    card_id = a.card_id or prof.get("card_id")
    out = {"ok": False, "steps": []}

    t = find_form_target()
    if not t:
        print(json.dumps({"ok": False, "why": "找不到 Stripe 表单 iframe（页面没加载完？）"},
                         ensure_ascii=False))
        return 6

    # ── 第 0 步（铁律）：必须先选国家 ───────────────────────────────────
    # Stripe「计费详情」在没定国家之前不给填其它字段：国家默认是浏览器所在地（CN），
    # 于是美国邮编一律被判「您的邮编无效」，州下拉也没有选项。
    # 顺序必须是：国家 → 卡/姓名/地址/城市/邮编 → 州 → 邮编复核。
    want_country = (b.get("country") or "US").upper()
    if not a.dry_run and not a.card_only:
        r0 = json.loads(cdp.set_select("country", want_country))
        country_ok = bool(r0.get("ok"))
        out["steps"].append({"field": "国家", "ok": country_ok,
                             "after": r0.get("after"), "before": r0.get("before")})
        if not country_ok:
            out["why"] = f"国家没设成（want={want_country}）：{r0.get('why') or r0}"
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 6

    if not a.dry_run:
        c = get_card(card_id, a.userid)
        out["card"] = {"pan": mask(c["pan"]), "exp": c["exp"], "area": c.get("card_area")}
        # 卡 → 有效期 → CVC
        for sel, val, label in (
            ("#payment-numberInput", c["pan"], "卡号"),
            ("#payment-expiryInput", c["exp_digits"], "有效期"),
            ("#payment-cvcInput", c["cvc"], "CVC"),
        ):
            r = json.loads(cdp.evaluate(SET_INPUT % (json.dumps(sel), json.dumps(val)), t))
            out["steps"].append({"field": label, "ok": bool(r.get("ok")),
                                 "after": mask(r["after"]) if label == "卡号" and r.get("after")
                                 else ("***" if label == "CVC" else r.get("after"))})
            settle_focus(t, label, out)      # 每填一个就收键盘
        # 账单地址（card-only 模式跳过：Stripe 拒卡后禁止改地址）
        for sel, val, label in ([] if a.card_only else [
            ("#billingAddress-nameInput", b["name"], "全名"),
            ("#billingAddress-addressLine1Input", b["address1"], "地址1"),
            ("#billingAddress-localityInput", b["city"], "城市"),
            ("#billingAddress-postalCodeInput", b["zip"], "邮编"),
        ]):
            r = json.loads(cdp.evaluate(SET_INPUT % (json.dumps(sel), json.dumps(val)), t))
            out["steps"].append({"field": label, "ok": bool(r.get("ok")), "after": r.get("after")})
            settle_focus(t, label, out)
        # 州（select）—— 选项依赖国家，必须在国家之后；赋值后必须回读确认
        # 坑：select 直接赋 value 会被 React 回滚成空（页面显示"没有选州"）→
        #     cdp.set_select 已改用原生 setter，这里再回读校验 + 最多重试 3 次。
        state = "" if a.card_only else (b.get("state") or "")
        state_ok = False
        for attempt in range(3):
            r = json.loads(cdp.set_select("administrativeArea", state))
            rb_state = json.loads(cdp.evaluate(READBACK, t)).get("state") or ""
            state_ok = bool(r.get("ok")) and rb_state == state
            out["steps"].append({"field": "州", "ok": state_ok,
                                 "after": rb_state, "attempt": attempt + 1})
            if state_ok:
                settle_focus(t, "州", out)
                break
            time.sleep(0.5)
        # 复核国家：选州会触发重渲染，有概率把国家打回默认值
        rb0 = json.loads(cdp.evaluate(READBACK, t))
        if not a.card_only and (rb0.get("country") or "").upper() != want_country:
            out["steps"].append({"field": "国家(复核后重设)", "ok": True,
                                 "after_before": rb0.get("country")})
            cdp.set_select("country", want_country)
            # 国家一变，州/邮编的校验上下文就变了 → 重新补州和邮编
            cdp.set_select("administrativeArea", state)
            r = json.loads(cdp.evaluate(
                SET_INPUT % (json.dumps("#billingAddress-postalCodeInput"), json.dumps(b["zip"])), t))
    else:
        out["card"] = {"pan": "(dry-run)", "exp": "-"}

    if not a.no_consent and not a.dry_run:
        # 点完立刻读 checked 会拿到 false：React 异步重渲染，dom 属性还没落地（本轮假失败根因）。
        # 所以点完要轮询几次，最终以 READBACK.consent 为准。
        cdp.evaluate(
            "(() => { const e=document.querySelector('#paymentForm-termsOfServiceConsentInput');"
            " if(e && !e.checked) e.click(); return 1; })()", t)
        consent_now = False
        for _ in range(6):
            time.sleep(0.4)
            consent_now = bool(json.loads(cdp.evaluate(READBACK, t)).get("consent"))
            if consent_now:
                break
        out["steps"].append({"field": "协议", "ok": consent_now})

    rb = json.loads(cdp.evaluate(READBACK, t))
    out["readback"] = {k: (mask(v) if k == "number" and v else ("***" if k == "cvc" and v else v))
                       for k, v in rb.items()}
    # 国家的最终校验：国家不对，整张表都是错的（邮编/州都按国家校验）
    country_final = (rb.get("country") or "").upper()
    out["country_final"] = country_final
    # 协议以最终回读为准（瞬时读到 false 不算失败）
    consent_final = bool(rb.get("consent"))
    out["consent_final"] = consent_final

    # 逐字段校验：任何关键字段空着都算失败。
    # （州曾因 React 回滚被静默清空，而旧代码只校验国家/协议 → 漏判，2026-11-25 用户发现）
    expect = [("number", "卡号"), ("expiry", "有效期"), ("cvc", "CVC"),
              ("name", "全名"), ("address1", "地址1"), ("locality", "城市"),
              ("state", "州"), ("zip", "邮编")]
    if not a.card_only:
        expect.append(("country", "国家"))
    missing = [label for key, label in expect if not rb.get(key)]
    if not (consent_final or a.no_consent or a.dry_run):
        missing.append("协议")
    if not a.card_only and country_final != want_country:
        missing.append(f"国家(期望{want_country}实际{country_final or '空'})")
    out["missing"] = missing
    # 页面上可能还残留「上一次提交被拒」的横幅，那不是本次填表失败 → 单独记录、不参与判定
    alerts = rb.get("errors") or []
    stale = [a for a in alerts if ("被拒" in a) or ("拒绝" in a) or ("declined" in str(a).lower())]
    fresh = [a for a in alerts if a not in stale]
    out["page_alerts"] = alerts
    out["stale_decline_banner"] = stale[:1]
    out["ok"] = (not missing and not fresh)
    # 注意：拒卡后 Stripe 会回一句「如需更改您的账单地址，请离开此页面并重新结账。」
    # 这属于「地址被锁」的提示，不是本次提交的失败原因 —— 交回调用方判断。
    if country_final != want_country:
        out["why"] = f"最终国家是 {country_final or '(空)'}，期望 {want_country}"

    if a.submit:
        r = cdp.evaluate(CLICK_SUBMIT, t)
        out["submit"] = json.loads(r)
        out["ok"] = out["ok"] and out["submit"].get("ok")

    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out["ok"] else 6


if __name__ == "__main__":
    sys.exit(main())
