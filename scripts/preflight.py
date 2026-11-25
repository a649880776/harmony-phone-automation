#!/usr/bin/env python3
"""preflight.py —— 跑流程前的环境自检（只读，不改变手机状态）

检查项：
  1. hdc 设备是否在线；
  2. .env 必需键是否齐全（只打印长度，不打印值）；
  3. profile.json 是否通过校验；
  4. RSA 私钥是否存在且可加载；
  5. TOTP 实现是否正确（RFC 向量）＋当前码是否算得出来；
  6. 外网连通性：devin.ai / agent.hiaihk.com；
  7. 卡接口 userid 是否为数字（这个字段错会直接报 2FA 错误，很坑）。

退出码：0 全通过；1 有阻塞项。
用法：./preflight.py [--check-card 8]
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

REQUIRED_KEYS = ["2FA_KEY", "CARD_API_BASE", "CARD_API_FETCH_PATH",
                 "CARD_API_USER_ID", "CARD_API_TOKEN", "CARD_RSA_KEY_PATH"]


def load_env():
    env = {}
    p = os.path.join(ROOT, ".env")
    try:
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return env


def run(cmd, timeout=20):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except Exception as e:
        return 99, "", type(e).__name__


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check-card", help="顺带检查这个 card_id 的 2FA 是否能通过（会真实调接口）")
    a = ap.parse_args()

    results = []

    def add(name, ok, detail=""):
        results.append((name, ok, detail))

    # 1. 设备
    rc, out, err = run([os.path.join(SCRIPTS, "ph.sh"), "targets"])
    dev_ok = rc == 0 and out and "Empty" not in out
    add("hdc 设备", bool(dev_ok), out.splitlines()[0] if out else (err or "无设备"))

    # 2. .env
    env = load_env()
    missing = [k for k in REQUIRED_KEYS if not env.get(k)]
    add(".env 必需键", not missing,
        "全部就位" if not missing else "缺: " + ", ".join(missing) + "（只报键名）")

    # 3. profile 校验
    rc, out, err = run([sys.executable, os.path.join(SCRIPTS, "profile_validate.py")])
    add("profile.json", rc == 0, (out or err).splitlines()[0] if (out or err) else "")

    # 4. 私钥
    keyp = os.path.expanduser(env.get("CARD_RSA_KEY_PATH", "~/.config/devin-vault/card_private.pem"))
    ok_key, detail = False, "不存在: " + keyp
    if os.path.exists(keyp):
        try:
            from cryptography.hazmat.primitives import serialization
            pem = open(keyp, "rb").read()
            k = serialization.load_pem_private_key(pem, password=None)
            ok_key = True
            detail = f"可加载 {type(k).__name__} {getattr(k,'key_size','?')}bit，权限 {oct(os.stat(keyp).st_mode)[-3:]}"
            if b"ENCRYPTED" in pem:
                detail += "（有口令，需 CARD_KEY_PASSPHRASE）"
        except Exception as e:
            detail = f"加载失败 {type(e).__name__}"
    add("RSA 私钥", ok_key, detail)

    # 5. TOTP
    try:
        from totp import normalize_secret, totp
        vector_ok = totp(normalize_secret("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"), at=59, digits=8) == "94287082"
        cur = totp(normalize_secret(env["2FA_KEY"])) if env.get("2FA_KEY") else None
        add("TOTP 算法", vector_ok and bool(cur),
            f"RFC 向量{'通过' if vector_ok else '失败'}；当前码{'可算' if cur else '缺种子'}（余 {30-int(time.time()%30)}s）")
    except Exception as e:
        add("TOTP 算法", False, type(e).__name__)

    # 6. 外网：只要拿到任何 HTTP 响应就算可达（4xx/5xx 也是通）
    def reachable(url):
        import urllib.error
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (preflight)"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                r.read(1)
            return True, f"{(time.time()-t0)*1000:.0f}ms"
        except urllib.error.HTTPError as e:
            return True, f"HTTP {e.code}（可达）"
        except Exception as e:
            return False, type(e).__name__

    # 入口域名用 .env 里配的（devin.ai 已长期超时，别再拿它当判据 → 一直是误报红色）
    import re as _re
    entry = env.get("DEVIN_ENTRY_URL") or "https://app.devin.ai"
    origin = _re.sub(r"^(https?://[^/]+).*$", r"\1", entry)
    for url, name in ((env.get("CARD_API_BASE", "https://agent.hiaihk.com/"), "卡接口主机"),
                      (origin, f"入口 {origin}")):
        ok, detail = reachable(url)
        add(name, ok, detail)

    # 7. userid 必须数字（这个错会伪装成 2FA 错误）
    uid = env.get("CARD_API_USER_ID", "")
    add("userid 数字", uid.isdigit(), f"userid={uid!r}")

    # 可选：真实 2FA 校验
    if a.check_card:
        import quick_fetch  # 复用取卡逻辑
        code = os.system(f'cd "{ROOT}" && CARD_API_USER_ID={uid} python3 scripts/quick_fetch.py '
                         f'--card-id {a.check_card} --json >/tmp/pf_card.out 2>/tmp/pf_card.err')  # noqa: E501
        ok = code == 0
        add("2FA 实测（会消耗卡）", ok,
            (open("/tmp/pf_card.out").read().strip()[:60] if ok else
             (open("/tmp/pf_card.err").read().strip().splitlines()[-1:] or ["失败"])[-1]))

    print("=" * 66)
    for name, ok, detail in results:
        print(f"{'✅' if ok else '❌'} {name:<16} {detail}")
    print("=" * 66)
    bad = [r for r in results if not r[1]]
    print(f"{len(results)-len(bad)}/{len(results)} 通过")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
