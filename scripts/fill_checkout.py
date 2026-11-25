#!/usr/bin/env python3
"""fill_checkout.py（已弃用，请用 fill_checkout2.py：它按标签定位并且带异常监控）

fill_checkout.py —— 5 秒内把 Stripe 结账页填完（卡信息走 API，账单地址走 profile.json）

要点：
  * 一个进程内完成取卡 + 解密 + 逐个字段输入，避免跨轮次延迟和 TOTP 过期；
  * 只用 tap + text，不用任何滑动（滑动容易触发下拉刷新/放大镜，丢数据）；
  * 每个字段输入后按 Back 收键盘，让布局回到稳定状态，坐标才不会漂；
  * 不打印任何明文，只输出掩码清单。
用法：./fill_checkout.py --card-id V3LOV --userid 8
坐标默认值对应 1216x2688 上的 Stripe 结账页（字段刚从顶部渲染时）。
"""
import argparse, json, os, re, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_card as F

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PH = os.path.join(ROOT, "scripts", "ph.sh")

def ph(*a, wait=0.0):
    subprocess.run([PH, *a], env=dict(os.environ), check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if wait: time.sleep(wait)

def get_card(card_id, userid):
    env = F.load_env()
    base = F.cfg(env, "CARD_API_BASE", default="https://agent.hiaihk.com/prod/api")
    path = F.cfg(env, "CARD_API_FETCH_PATH", default="/rentCardInfo")
    token = F.cfg(env, "CARD_API_TOKEN", "RENTCARD_TOKEN")
    secret = F.cfg(env, "2FA_KEY", "TOTP_SECRET")
    key_path = F.cfg(env, "CARD_RSA_KEY_PATH", default=F.DEFAULT_KEY)
    url = f"{base.rstrip('/')}/{path.lstrip('/')}?token={token}"
    code = F.make_totp(secret)
    status, resp = F.rent_card_info(url, {"card_id": card_id, "userid": int(userid),
                                          "code": (str(code) if code.startswith("0") else int(code))})
    if status != 200 or not resp or resp.get("success") is not True:
        print("取卡失败:", (resp or {}).get("message", f"HTTP {status}"), file=sys.stderr); sys.exit(3)
    c = F.normalize(resp.get("data") or {})
    k = F.load_private_key(key_path)
    pt, _ = F.decrypt_cvv(c["cvv_cipher"], k)
    c["cvc"] = pt.decode().strip(); pt = b"\x00" * len(pt)
    c["exp_digits"] = re.sub(r"\D", "", c["exp"])
    return c

ap = argparse.ArgumentParser()
ap.add_argument("--card-id", default="V3LOV"); ap.add_argument("--userid", default="8")
ap.add_argument("--skip-name", action="store_true")
a = ap.parse_args()

b = json.load(open(os.path.join(ROOT, "profile.json"), encoding="utf-8"))["billing"]
c = get_card(a.card_id, a.userid)

t0 = time.time()
# 1) 卡号 → Stripe 会自动把焦点跳到有效期 → 安全码
ph("tap", "609", "386", wait=0.45)
ph("text", c["pan"], wait=0.40)
ph("text", c["exp_digits"], wait=0.35)
ph("text", c["cvc"], wait=0.35)
ph("key", "Back", wait=0.30)
# 2) 全名
ph("tap", "609", "944", wait=0.40)
ph("text", b["name"], wait=0.30)
ph("key", "Back", wait=0.30)
# 3) 邮编 / 城市 / 地址1（顺序与页面一致）
for val, xy in ((b["zip"], ("609", "1226")), (b["city"], ("609", "1507")),
                (b["address1"], ("609", "1648"))):
    ph("tap", xy[0], xy[1], wait=0.40)
    ph("text", val, wait=0.30)
    ph("key", "Back", wait=0.30)

print(f"填充耗时 {time.time()-t0:.1f}s", file=sys.stderr)
print(f"卡号 {F.mask_pan(c['pan'])} · {c['exp']} · CVV *** | {b['name']} | "
      f"{b['address1']}, {b['city']}, {b['state_name']} {b['zip']}, {b['country']}")
