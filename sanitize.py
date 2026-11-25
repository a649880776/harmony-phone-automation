#!/usr/bin/env python3
"""sanitize.py —— 入库前把真实业务数据替换成占位符（幂等）

本仓库是公开仓库，而技能目录的原件含真实 token / 账号 / 卡号。
sync.sh 在 rsync 之后会调用本脚本，保证任何一次同步都不会把敏感数据推上去。

只处理两类文件：
  SKILL.md                → 占位化
  scripts/fetch_card.py   → 自测夹具换成测试卡号与假身份

用法：
  ./sanitize.py            # 就地处理本仓库副本
  ./sanitize.py --check    # 只检查是否还有残留，有则退出码 1
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKILL = ROOT / "SKILL.md"
FETCH_CARD = ROOT / "scripts" / "fetch_card.py"

CARD_CODES = [
    "GLH5JB", "PS5QR", "0V5DPB", "VSMOV", "ZGCLCB",
    "K3RMUB", "ADQSOB", "UDTVI", "PQAR6", "V3LOV",
]

RULES = [
    # 完整卡号先处理，避免被片段规则切碎
    (re.compile(r"\b4937\d{12}\b"), "<CARD_PAN>"),
    # 打码卡号：4937 **** **** 0501 / 4937 …3257 / 4937…7009
    (re.compile(r"4937\s*[*….\s]{0,12}\s*\d{4}"), "<CARD_PAN>"),
    # 接口 token（已是占位符的不动）
    (re.compile(r"token=(?!<|\{)[A-Za-z0-9]{6,}"), "token=<CARD_API_TOKEN>"),
    # 账号邮箱
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "<account>"),
    # 设备号
    (re.compile(r"9CN0\d{10,}"), "<DEVICE_ID>"),
    # PAT 尾号：…nwoq / 尾号 `nwoq`
    (re.compile(r"…[a-z0-9]{4}\b"), "…<PAT>"),
    (re.compile(r"(尾号\s*`)[a-z0-9]{3,6}(`)"), r"\1<PAT>\2"),
]

for _code in CARD_CODES:
    RULES.append((re.compile(rf"\b{_code}\b"), "<CARD>"))

# fetch_card.py 自测夹具：一次性字面替换（已脱敏后为空操作）
FIXTURE = [
    ('card_no: "4937...",', 'card_no: "<16 位明文卡号>",'),
    ('"card_no": "4937241015316417"', '"card_no": "4242424242424242"'),
    ('"holder_name": "Melody Little"', '"holder_name": "Test Holder"'),
    ('"bill_address": "207 South Broadway,Missouri,63102,US"',
     '"bill_address": "123 Main St,Missouri,00000,US"'),
    ('"street": "207 South Broadway"', '"street": "123 Main St"'),
    ('"zipcode": "63102"', '"zipcode": "00000"'),
    ('c["pan"].endswith("6417")', 'c["pan"].endswith("4242")'),
    ('c["zip"] == "63102"', 'c["zip"] == "00000"'),
    ('c["holder"] == "Melody Little"', 'c["holder"] == "Test Holder"'),
    ("GLH5JB", "<卡代号>"),
]

LEFTOVER = re.compile(
    r"4937\d|@(bpniprf|totiger2|ccsnxpy)\.|9CN0\d{6,}|token=1\d{7,}|cog_[a-z0-9]{6,}"
)


def sanitize_text(text: str):
    counts = {}
    for pat, rep in RULES:
        text, n = pat.subn(rep, text)
        if n:
            counts[rep] = counts.get(rep, 0) + n
    return text, counts


def main() -> int:
    if "--check" in sys.argv:
        bad = []
        for f in (SKILL, FETCH_CARD):
            if f.exists():
                for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                    if LEFTOVER.search(line):
                        bad.append(f"{f.relative_to(ROOT)}:{i}: {line.strip()[:80]}")
        if bad:
            print("✗ 发现疑似敏感残留：")
            print("\n".join(bad))
            return 1
        print("✓ 未发现残留")
        return 0

    if SKILL.exists():
        text, counts = sanitize_text(SKILL.read_text(encoding="utf-8"))
        SKILL.write_text(text, encoding="utf-8")
        total = sum(counts.values())
        print(f"SKILL.md 脱敏 {total} 处" + (f"：{counts}" if total else "（无变化）"))

    if FETCH_CARD.exists():
        s = FETCH_CARD.read_text(encoding="utf-8")
        n = 0
        for old, new in FIXTURE:
            if old in s:
                s = s.replace(old, new)
                n += 1
        if n:
            FETCH_CARD.write_text(s, encoding="utf-8")
        print(f"fetch_card.py 夹具脱敏 {n} 处" + ("" if n else "（无变化）"))

    return 0


if __name__ == "__main__":
    sys.exit(main())
