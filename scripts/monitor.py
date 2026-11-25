#!/usr/bin/env python3
"""monitor.py —— 运行时监控：页面健康检查 / 异常判定 / 是否该中断问人

用法：
  ./monitor.py --check                  # 单次检查，输出 JSON
  ./monitor.py --watch 60 --interval 3  # 观察 60 秒，一出现异常立刻退出
  ./monitor.py --check --include-info   # 连「信息级」提示也报（如 PAT 空列表）

输出 JSON：{"ok":bool,"anomalies":[{code,hint,action,sample}],"suggest":"...","ts":...}

异常处置策略（供编排器/AI 决策）：
  reload             → 自动刷新页面后重试（SPA 白屏、网络抖动）
  refetch_code       → 重新取验证码（验证码过期/错误）
  recheck_country_zip→ 重新核对国家与邮编（邮编无效）
  restart_flow       → 会话失效，需要从流程起点重来
  ask_human          → 必须停下来问人（拒卡、3DS、AVS、2FA 种子问题）

退出码：0 无异常；1 有异常（need_human 见 JSON 的 need_human 字段）
"""
import argparse
import json
import sys
import time

import lib_ui as U

POLICY = {
    "ask_human": True,          # 必须人工决策
    "reload": False,            # 可自动刷新重试
    "refetch_code": False,
    "recheck_country_zip": False,
    "restart_flow": True,
}


def check(include_info=False):
    r = U.check(include_info=include_info)
    need_human = any(POLICY.get(a["action"], True) for a in r["anomalies"])
    r["need_human"] = need_human
    r["suggest"] = (r["anomalies"][0]["action"] if r["anomalies"] else "continue")
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--watch", type=int, default=0, help="观察秒数")
    ap.add_argument("--interval", type=float, default=3.0)
    ap.add_argument("--include-info", action="store_true")
    a = ap.parse_args()

    if not a.watch:
        r = check(a.include_info)
        print(json.dumps(r, ensure_ascii=False))
        return 0 if r["ok"] else 1

    t0 = time.time()
    while time.time() - t0 < a.watch:
        r = check(a.include_info)
        if not r["ok"]:
            r["elapsed"] = round(time.time() - t0, 1)
            print(json.dumps(r, ensure_ascii=False))
            return 1
        time.sleep(a.interval)
    print(json.dumps({"ok": True, "anomalies": [], "suggest": "continue",
                      "elapsed": round(time.time() - t0, 1)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
