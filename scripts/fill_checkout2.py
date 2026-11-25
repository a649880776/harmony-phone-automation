#!/usr/bin/env python3
"""fill_checkout2.py —— Stripe 结账页快速填充（标签定位 + 每阶段异常监控）

相比第一版（fill_checkout.py 固定坐标）的改进：
  * 全部按「标签 → 字段」定位，布局随国家/键盘变化也不会写错位置（第一版把邮编写进了地址1）；
  * 每个阶段结束跑一次 monitor 检查，出现异常立即中止并输出 JSON（由编排器决定问人/重试）；
  * 滚动一律走 lib_ui.scroll（左边缘 + 快滑），避免放大镜与下拉刷新。

顺序（业务方确认）：卡号 → 有效期 → CVV → 全名 → 国家 → 地址1 → 地址2(空) → 城市 → 州 → 邮编

用法：
  ./fill_checkout2.py --card-id V3LOV --userid 8            # 填完并自检
  ./fill_checkout2.py --card-id V3LOV --dry-run             # 只打印将要填的内容（掩码）
退出码：0 成功；3 取卡失败；4 解密失败；5 填表阶段异常（见 JSON）；6 关键字段缺失
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_ui as U
import fetch_card as F

ROOT = U.ROOT
PROFILE = os.path.join(ROOT, "profile.json")
OUT = {"stages": [], "ok": False}


def stage(name, ok, **kw):
    rec = {"stage": name, "ok": ok}
    rec.update(kw)
    OUT["stages"].append(rec)
    if not ok:
        OUT["ok"] = False
        OUT["stopped_at"] = name
        print(json.dumps(OUT, ensure_ascii=False))
        sys.exit(kw.get("exit", 5))
    return rec


def guarded(name):
    """阶段后置检查：有异常就中止"""
    r = U.check(tag="mon_" + re.sub(r"\W+", "_", name))
    if not r["ok"]:
        OUT["anomalies"] = r["anomalies"]
        stage(name, False)
    return r


def label_map(d):
    """labelText 与字段共享 y1 → {label: (x,y)}"""
    labels, boxes = {}, []
    for n in U.nodes(d):
        a = n.get("attributes", {})
        t = U.text_of(n)
        b = a.get("bounds", "")
        if a.get("type") == "labelText" and t:
            r = U._bounds(b)
            if r:
                labels[r[1]] = t
        elif a.get("type") in ("textField", "comboBoxSelect"):
            r = U._bounds(b)
            if r:
                boxes.append((r[1], U.center(b)))
    out = {}
    for y, lab in labels.items():
        for by, xy in boxes:
            if by == y and xy:
                out[lab] = xy
                break
    return out


def card_field(d):
    """卡号框 = 最靠上、值里不含 @ 的 textField（邮箱框会被排除）"""
    cands = []
    for n in U.nodes(d):
        a = n.get("attributes", {})
        if a.get("type") == "textField" and "@" not in (a.get("text") or ""):
            xy = U.center(a.get("bounds", ""))
            if xy:
                cands.append((xy[1], xy))
    return min(cands)[1] if cands else None


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
                                      "code": (str(code) if code.startswith("0") else int(code))})
    if st != 200 or not resp or resp.get("success") is not True:
        print(f"取卡失败: {(resp or {}).get('message', st)}", file=sys.stderr)
        sys.exit(3)
    c = F.normalize(resp.get("data") or {})
    pt, how = F.decrypt_cvv(c["cvv_cipher"], F.load_private_key(key_path))
    c["cvc"] = pt.decode().strip()
    pt = b"\x00" * len(pt)
    c["exp_digits"] = re.sub(r"\D", "", c["exp"])
    c["padding"] = how
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--card-id", default=None)
    ap.add_argument("--userid", default=os.environ.get("CARD_API_USER_ID", "8"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--resume", action="store_true",
                    help="卡信息/姓名/国家已填好时只补地址/城市/州/邮编（绝不重新取卡）")
    a = ap.parse_args()

    prof = json.load(open(PROFILE, encoding="utf-8"))
    b = prof["billing"]
    card_id = a.card_id or prof.get("card_id")

    if a.resume:
        # 卡已经取过（一次性）。再取一次等于废掉一张卡，所以 resume 绝不碰卡字段。
        OUT["card"] = {"pan": "(已填，跳过取卡)", "resume": True}
        OUT["billing"] = {k: b.get(k) for k in ("name", "country", "address1", "city",
                                                "state_name", "zip")}
        if a.dry_run:
            OUT["ok"] = True
            print(json.dumps(OUT, ensure_ascii=False, indent=2))
            return 0
        fill_address(b, skip_fullname=True)
        print(json.dumps(OUT, ensure_ascii=False, indent=2))
        return 0 if OUT["ok"] else 6

    c = get_card(card_id, a.userid)
    OUT["card"] = {"pan": F.mask_pan(c["pan"]), "exp": c["exp"], "area": c.get("card_area"),
                   "padding": c.get("padding")}
    OUT["billing"] = {k: b.get(k) for k in ("name", "country", "address1", "city", "state_name", "zip")}

    if a.dry_run:
        OUT["ok"] = True
        print(json.dumps(OUT, ensure_ascii=False, indent=2))
        return 0

    # ── 铁律：先定国家，再填其它 ─────────────────────────────────────
    # Stripe「计费详情」在没定国家前不让填其它字段；国家默认是浏览器所在地（CN），
    # 美国邮编会被判「您的邮编无效」，州下拉也没有选项。所以国家必须第一个设。
    xy = ensure_label("国家或地区")
    if xy:
        U.tap_xy(xy, wait=1.4)
        if not U.pick_option_safe("美国", tag="country"):
            stage("选择国家", False, exit=6)
    guarded("国家")

    d = U.dump("fc0")
    xy = card_field(d)
    if not xy:
        stage("定位卡号框", False, exit=6)
    U.tap_xy(xy, wait=0.8)
    U.type_text(c["pan"], wait=0.5)        # Stripe 会自动把焦点跳到有效期 → CVV
    U.type_text(c["exp_digits"], wait=0.45)
    U.type_text(c["cvc"], wait=0.45)
    U.back(wait=0.5)
    guarded("卡信息")

    xy = ensure_label("全名")
    if xy:
        U.fill_field(xy, b["name"])
    guarded("全名")

    fill_address(b, skip_fullname=True)
    print(json.dumps(OUT, ensure_ascii=False, indent=2))
    return 0 if OUT["ok"] else 6


def ensure_label(lab, max_steps=6, step=650, tag="findlab"):
    """拿到某个标签对应输入框的坐标；不在可视区就**只向下滚**去找。

    绝不向上滚：Stripe 页在顶部做「手指向下」手势会触发下拉刷新，表单全丢
    （2026-11-25 事故）。向下滚 = 内容上移，安全。
    """
    for i in range(max_steps + 1):
        d = U.dump(f"{tag}_{i}")
        lm = label_map(d)
        if lab in lm:
            return lm[lab]
        if i < max_steps:
            U.scroll("down", distance=step)
    return None


def fill_address(b, skip_fullname=True):
    """地址第 1 行 → 城市 → 州 → 邮编（选完国家后字段会整体重排，必须每步重新 dump）"""
    for lab in ("地址第 1 行", "城市"):
        val = b["address1"] if lab.startswith("地址") else b["city"]
        xy = ensure_label(lab)
        if xy and val:
            U.fill_field(xy, val)
        else:
            stage(f"填写{lab}", False, exit=6)
    guarded("地址与城市")

    xy = ensure_label("州") or ensure_label("省")
    state_ok = False
    if xy:
        U.tap_xy(xy, wait=1.4)
        want = b.get("state_name") or b.get("state")
        state_ok = U.pick_option_safe(want, tag="state")
        if not state_ok:
            stage("选择州", False, exit=6)
    else:
        stage("填写州", False, exit=6)
    guarded("州")

    xy = ensure_label("邮政编码") or ensure_label("邮编")
    if xy:
        U.fill_field(xy, b["zip"])
    else:
        stage("填写邮编", False, exit=6)
    guarded("邮编")

    d = U.dump("fc6")
    # 注意：combobox 的控件树文本永远是占位符（「州」），选中的值不会出现在 text 里，
    # 所以 has_state 只能用「点选动作是否成功」判定，不能靠 has_text（2026-11-25 教训）。
    OUT["verify"] = {
        "has_name": U.has_text(d, b["name"]) if not skip_fullname else True,
        "has_address": U.has_text(d, b["address1"]),
        "has_city": U.has_text(d, b["city"]),
        "has_zip": U.has_text(d, b["zip"]),
        "has_state": state_ok,
    }
    OUT["ok"] = all(OUT["verify"].values())
    return OUT["ok"]


if __name__ == "__main__":
    sys.exit(main())
