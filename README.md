# harmony-phone-automation

> 通过 `hdc` 直连控制已投屏的鸿蒙 (HarmonyOS) 手机：截图、读控件树、点击/长按/滑动/输入、
> 开关控制中心（移动数据、双卡切换）、管理自带浏览器进程与标签页（含无痕窗口）、打开网址。
> 与 HoKit 投屏共用同一条 hdc 通道。

这是一个 **DSH / Claude 风格的技能包（skill）**：`SKILL.md` 是给模型看的操作手册，`scripts/` 是可直接调用的命令行工具。

## 依赖

- macOS
- 已投屏（HiCar/HoKit 或 DevEco Studio 连接）的鸿蒙设备
- `hdc`（DevEco Studio 或 HoKit 内置，可用 `HDC_BIN` 覆盖）
- `python3`（脚本只用标准库）

## 安装

把本仓库放到技能目录，技能名即目录名：

```bash
git clone git@github.com:a649880776/harmony-phone-automation.git \
  ~/.dsh/skills/harmony-phone-automation
```

然后按需创建本地配置（**都不进版本库**）：

```bash
cd ~/.dsh/skills/harmony-phone-automation
cp .env.example .env              # 填入 2FA_KEY / CARD_API_USER_ID
cp profile.example.json profile.json   # 填入自己的账号与账单资料
chmod 600 .env profile.json
```

## 快速开始

```bash
./scripts/ph.sh targets           # 列出设备号
./scripts/ph.sh shot s1           # 截图 -> shots/s1.jpeg
./scripts/ph.sh ui s1             # 控件树 -> shots/s1.json
./scripts/ph.sh find "移动数据"    # 在控件树里找元素中心坐标
./scripts/ph.sh tap 478 2041      # 点击
```

**铁律**：投屏（HoKit）运行期间禁止 `hdc kill` / `hdc start` / `kill-server`，会连带打断投屏通道。
需要驱动设备时只用 `uitest` / `aa` / `snapshot_display` / `file recv` 这些子命令。

## 目录结构

```
SKILL.md              技能说明与完整操作手册（模型入口）
scripts/ph.sh         主入口：截图 / 控件树 / 查找 / 点击 / 滑动 / 输入
scripts/lib_ui.py     控件树解析与坐标计算的公共库
scripts/preflight.py  开跑前的环境自检（hdc、设备、投屏通道）
scripts/totp.py       本地算 6 位动态码
scripts/vault.sh      凭据存取封装
country_order.json    国家/地区下拉顺序（用于按文本定位选项）
design/               设计稿
```

## 密钥与隐私

`.gitignore` 已屏蔽 `.env`、`profile.json`、`devin_token.json`、`shots/`、`*.pem`、`*.key` 等。
**提交前请确认没有把真实密钥、卡号、身份资料带进版本库**；`profile.example.json` 只是占位模板。

## 脱敏说明

本仓库为公开仓库，`SKILL.md` 与 `scripts/` 中的真实业务数据已做占位化替换：

| 真实数据 | 占位符 |
| --- | --- |
| 卡后台接口 token、账号邮箱 | `<CARD_API_TOKEN>` / `<account>` |
| 卡号（明文与打码片段） | `<CARD_PAN>` |
| 卡代号（如 5 位业务码） | `<CARD>` / `<卡代号>` |
| PAT 尾号 | `<PAT>` |
| 设备号 | `<DEVICE_ID>` |

本地运行所需的真实密钥请只写进 `.env` / `profile.json`（均已被 `.gitignore` 屏蔽）。

## 版本同步

本仓库是技能包的唯一版本源。日常改动在技能目录里做，然后：

```bash
./sync.sh "描述这次改动"
```

`sync.sh` 会把 `~/.dsh/skills/harmony-phone-automation` 同步到本仓库（自动跳过密钥文件）并提交推送。
