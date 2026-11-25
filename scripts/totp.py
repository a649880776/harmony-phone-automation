#!/usr/bin/env python3
"""totp.py —— RFC 6238 TOTP 计算器（6 位动态码）

密钥来源优先级：--secret > 环境变量 TOTP_SECRET > 技能包 .env 里的 2FA_KEY / TOTP_SECRET。

用法：
  ./totp.py                      # 用 .env 里的密钥，取当前 6 位码
  ./totp.py --at 59 --digits 8   # 用固定时间戳验证（配合 RFC 测试向量）
  ./totp.py --secret JBSWY3DPEHPK3PXP
  ./totp.py --uri 'otpauth://totp/Dev:me?secret=XXX&issuer=Dev'

退出码：0 成功打印 6 位码；2 找不到密钥；3 密钥非法。
注意：绝不把这个码写进日志/文件；它 30 秒就换。
"""
import argparse
import base64
import binascii
import hashlib
import hmac
import os
import struct
import sys
import time
import urllib.parse

ENV_KEYS = ("TOTP_SECRET", "2FA_KEY", "TWOFA_KEY", "TOTP_KEY")
SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_ENV = os.path.join(SKILL_ROOT, ".env")


def load_env(path):
    """极简 .env 解析：KEY=VALUE，忽略注释/空行，支持两端引号和 export 前缀。"""
    env = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[len("export "):]
                k, v = line.split("=", 1)
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                env[k.strip()] = v
    except FileNotFoundError:
        pass
    return env


def normalize_secret(s):
    """接受 base32 串或 otpauth:// URI，返回无空格大写 base32。"""
    s = s.strip()
    if s.lower().startswith("otpauth://"):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(s).query)
        s = (q.get("secret") or [""])[0]
    s = s.replace(" ", "").replace("-", "").upper()
    return s.rstrip("=")


def totp(secret, at=None, digits=6, period=30, algo="sha1"):
    key = base64.b32decode(secret + "=" * ((8 - len(secret) % 8) % 8), casefold=True)
    counter = int((at if at is not None else time.time()) // period)
    digest = hmac.new(key, struct.pack(">Q", counter), getattr(hashlib, algo)).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10 ** digits)).zfill(digits)


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--secret", help="base32 密钥或 otpauth URI；默认读 .env")
    ap.add_argument("--env", default=DEFAULT_ENV, help=".env 路径")
    ap.add_argument("--at", type=float, help="指定 UNIX 时间戳（测试用）")
    ap.add_argument("--digits", type=int, default=0)
    ap.add_argument("--period", type=int, default=0)
    ap.add_argument("--algo", default="", help="sha1/sha256/sha512")
    ap.add_argument("--remaining", action="store_true", help="额外打印剩余秒数到 stderr")
    a = ap.parse_args()

    env = load_env(a.env)
    secret = a.secret
    if not secret:
        for k in ENV_KEYS:
            if env.get(k) or os.environ.get(k):
                secret = env.get(k) or os.environ.get(k)
                break
    if not secret:
        print(f"找不到 TOTP 密钥：请把 2FA_KEY=<base32> 写进 {a.env}", file=sys.stderr)
        return 2

    digits = a.digits or int(env.get("TOTP_DIGITS") or 6)
    period = a.period or int(env.get("TOTP_PERIOD") or 30)
    algo = a.algo or env.get("TOTP_ALGO") or "sha1"

    secret = normalize_secret(secret)
    try:
        code = totp(secret, a.at, digits, period, algo)
    except (binascii.Error, ValueError) as e:
        print(f"密钥非法（不是合法 base32）：{e}", file=sys.stderr)
        return 3

    print(code)
    if a.remaining:
        left = period - int((a.at if a.at is not None else time.time()) % period)
        print(f"剩余 {left}s 过期", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
