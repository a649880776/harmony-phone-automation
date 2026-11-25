#!/usr/bin/env python3
"""quick_fetch.py —— 一个进程内完成「算码 → 立即请求 → 失败自动换窗口重试」

背景：TOTP 只有 30 秒有效，跨工具调用的往返经常让码过期。
本脚本把算码和请求放在同一次执行里，失败时自动等到下一个时间窗口再试（最多 --tries 次）。

用法：
  ./quick_fetch.py                      # 用 .env 的 2FA_KEY 自动算码，取 profile 里的 card_id
  ./quick_fetch.py --code 713310        # 先用你给的码试一次，失败再自动算
  ./quick_fetch.py --card-id V3LOV --json
退出码：0 成功；2 配置缺失；3 接口报错；4 连续 2FA 错误；5 无卡号
"""
import argparse, json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_card as F


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--card-id"); ap.add_argument("--code", action="append", default=[])
    ap.add_argument("--tries", type=int, default=4)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    env = F.load_env()
    base = F.cfg(env, "CARD_API_BASE", "RENTCARD_BASE", default="https://agent.hiaihk.com/prod/api")
    path = F.cfg(env, "CARD_API_FETCH_PATH", default="/rentCardInfo")
    token = F.cfg(env, "CARD_API_TOKEN", "RENTCARD_TOKEN")
    userid = F.cfg(env, "CARD_API_USER_ID")
    secret = F.cfg(env, "TOTP_SECRET", "2FA_KEY", "TWOFA_KEY", "TOTP_KEY")
    key_path = F.cfg(env, "CARD_RSA_KEY_PATH", default=F.DEFAULT_KEY)
    card_id = a.card_id
    if not card_id and os.path.exists(F.PROFILE):
        p = json.load(open(F.PROFILE, encoding="utf-8"))
        card_id = p.get("card_id") or p.get("card_ref")
    if not (token and userid and card_id and secret):
        print("缺配置（token/userid/card_id/2FA_KEY 之一）", file=sys.stderr); return 2
    url = f"{base.rstrip('/')}/{path.lstrip('/')}?token={token}"

    codes = list(a.code)
    for attempt in range(1, a.tries + 1):
        if codes:
            code = str(codes.pop(0))
        else:
            left = 30 - int(time.time() % 30)
            if left < 6:                      # 太接近边界，直接等下一窗口
                time.sleep(left + 1)
                left = 30 - int(time.time() % 30)
            code = F.make_totp(secret)
        t0 = time.time()
        status, resp = F.rent_card_info(url, {"card_id": card_id, "userid": int(userid), "code": (str(code) if str(code).startswith("0") else int(code))})
        msg = (resp or {}).get("message", "") if isinstance(resp, dict) else ""
        ok = status == 200 and isinstance(resp, dict) and resp.get("success") is True
        print(f"[{attempt}/{a.tries}] code={code} → HTTP {status} {'OK' if ok else msg} "
              f"({time.time()-t0:.2f}s, 窗口余 {30-int(time.time()%30)}s)", file=sys.stderr)
        if ok:
            c = F.normalize(resp.get("data") or {})
            if not c["pan"]:
                print("响应无 card_no", file=sys.stderr); return 5
            if c["cvv_cipher"]:
                try:
                    k = F.load_private_key(key_path)
                    pt, how = F.decrypt_cvv(c["cvv_cipher"], k)
                    c["cvc"] = pt.decode("utf-8", "replace").strip()
                    pt = b"\x00" * len(pt)
                    print(f"CVV 解密成功（{how}）", file=sys.stderr)
                except Exception as e:
                    print(f"CVV 解密失败 {type(e).__name__}: {e}", file=sys.stderr)
            if a.json:
                print(json.dumps({"pan": F.mask_pan(c["pan"]), "exp": c["exp"],
                                  "cvc": "***" if c.get("cvc") else "", "card_key": c["card_key"],
                                  "holder": c["holder"], "street": c["street"], "city": c["city"],
                                  "state": c["state"], "zip": c["zip"], "area": c["card_area"]},
                                 ensure_ascii=False))
            else:
                print(F.masked_view(c))
            return 0
        if "2FA" in msg or "验证码" in msg:
            if attempt < a.tries:
                wait = (30 - int(time.time() % 30)) + 1
                print(f"    等 {wait}s 进下一窗口重试", file=sys.stderr)
                time.sleep(wait)
            continue
        return 3
    print("连续 2FA 错误：种子与 userid 不匹配，需人工核对", file=sys.stderr)
    return 4


if __name__ == "__main__":
    sys.exit(main())
