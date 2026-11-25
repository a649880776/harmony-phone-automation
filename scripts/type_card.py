#!/usr/bin/env python3
"""type_card.py —— 取卡后把 卡号/有效期/CVV 连续打进 Stripe 卡表单

一次进程内：取卡 → 点卡号框 → 输卡号 → （Stripe 自动跳焦）输有效期 → 输 CVV。
键入走 ph.sh，明文只在内存，只输出脱敏状态。
用法：./type_card.py --card-id V3LOV --pan-xy 609,781
"""
import argparse, os, re, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_card as F

def ph(*args):
    env = dict(os.environ, SHOTS_DIR=os.environ.get("SHOTS_DIR", "/tmp/phshots"))
    subprocess.run([os.path.join(F.SCRIPTS, "ph.sh"), *args], env=env, check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

ap = argparse.ArgumentParser()
ap.add_argument("--card-id", default="V3LOV")
ap.add_argument("--userid", default="8")
ap.add_argument("--pan-xy", default="609,781")
a = ap.parse_args()

env = F.load_env()
base = F.cfg(env, "CARD_API_BASE", default="https://agent.hiaihk.com/prod/api")
path = F.cfg(env, "CARD_API_FETCH_PATH", default="/rentCardInfo")
token = F.cfg(env, "CARD_API_TOKEN", "RENTCARD_TOKEN")
secret = F.cfg(env, "2FA_KEY", "TOTP_SECRET")
key_path = F.cfg(env, "CARD_RSA_KEY_PATH", default=F.DEFAULT_KEY)

url = f"{base.rstrip('/')}/{path.lstrip('/')}?token={token}"
code = F.make_totp(secret)
status, resp = F.rent_card_info(url, {"card_id": a.card_id, "userid": int(a.userid),
                                      "code": int(code) if not code.startswith("0") else code})
if status != 200 or not resp or resp.get("success") is not True:
    print("取卡失败:", (resp or {}).get("message", f"HTTP {status}"), file=sys.stderr); sys.exit(3)
c = F.normalize(resp.get("data") or {})
key = F.load_private_key(key_path)
pt, how = F.decrypt_cvv(c["cvv_cipher"], key)
cvc = pt.decode().strip(); pt = b"\x00" * len(pt)
exp = re.sub(r"\D", "", c["exp"])
pan = c["pan"]

x, y = (int(v) for v in a.pan_xy.split(","))
ph("tap", str(x), str(y)); time.sleep(1.2)
ph("text", pan);  print(f"卡号已填 {F.mask_pan(pan)}", file=sys.stderr); time.sleep(1.2)
ph("text", exp);  print(f"有效期已填 {exp[:2]}/**", file=sys.stderr); time.sleep(1.2)
ph("text", cvc);  print(f"CVV 已填 ***（{how} 解密）", file=sys.stderr); time.sleep(1.0)
