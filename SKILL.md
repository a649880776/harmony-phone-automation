---
name: harmony-phone-automation
description: 通过 hdc 直接控制已投屏的鸿蒙 (HarmonyOS) 手机——截图、读控件树、点击/长按/滑动/输入、开关控制中心（移动数据、双卡切换）、管理自带浏览器进程与标签页（含无痕窗口）、打开网址。当用户要求「操作手机 / 控制投屏 / 帮我点手机上的某功能 / 把手机操作做成流程」时使用。与 HoKit 投屏共用同一条 hdc 通道。
compatibility: 需要 macOS、已投屏的鸿蒙设备、hdc（DevEco Studio 或 HoKit 内置），以及 python3。
---

# 鸿蒙手机自动化（hdc 直连）

投屏窗口只负责「看」，真正的手要落在 `hdc` 上：截图 + 控件树 + `uitest uiInput` 注入事件。
这样比在投屏窗口里猜坐标可靠得多，也不受 HoKit 窗口被最小化/换 Space 的影响。

## 0. 连接

```bash
hdc list targets                      # 应输出设备号，如 <DEVICE_ID>
./scripts/ph.sh targets               # 脚本侧确认
```

hdc 优先级：`/Applications/DevEco-Studio.app/Contents/sdk/default/openharmony/toolchains/hdc`，
退化为 `/Applications/HoKit.app/Contents/Resources/assets/tools/hdc/hdc`（可用 `HDC_BIN` 覆盖）。

> **铁律**：投屏（HoKit）运行期间禁止 `hdc kill` / `hdc start` / `kill-server`，会连带打断投屏通道。
> 需要驱动设备就只用 `uitest` / `aa` / `snapshot_display` / `file recv` 这些子命令。

多设备时设 `PHONE_ID=<设备号>`。

## 1. 通用套路（每一步都这样走）

1. `./scripts/ph.sh shot <名>` 截图，用肉眼（或读图）确认当前画面；
2. `./scripts/ph.sh ui <名>` 导出控件树，`./scripts/ph.sh find <文本>` 拿到元素中心坐标；
3. `./scripts/ph.sh tap <x> <y>` 操作，再截图验证结果。

**永远从控件树取坐标，不要凭截图比例估算**——屏幕是 1216×2688（本机），估计值偏 100px 就会点到隔壁开关
（真实事故：想点「卡 1 数据」结果点掉了「静音」，想点铃声开关结果点开了「超级终端」）。

控件树里 `bounds` 是设备像素，格式 `[x1,y1][x2,y2]`；`clickable=true` 的节点才是可点目标，
文本节点本身往往 `clickable=false`，此时点它的中心也有效（父容器可点）。

## 2. 命令速查

```bash
./scripts/ph.sh shot s1            # 截图 -> shots/s1.jpeg
./scripts/ph.sh ui s1              # 控件树 -> shots/s1.json
./scripts/ph.sh find "移动数据"     # 输出 type/文本/x,y/bounds
./scripts/ph.sh tap 478 2041
./scripts/ph.sh lpress 478 2041    # 长按
./scripts/ph.sh swipe 1150 20 1150 1500 3000   # 从右上角下拉出控制中心
./scripts/ph.sh key Back
./scripts/ph.sh inputtext 400 165 "https://devin.ai"  # 会替换输入框原内容
./scripts/ph.sh text "纯文本"       # 输入到已聚焦输入框
./scripts/ph.sh app com.huawei.hmos.browser MainAbility
./scripts/ph.sh kill com.huawei.hmos.browser
./scripts/ph.sh ps hmos.browser
./scripts/ph.sh sh "ping -c 2 devin.ai"
```

分工：`shot/ui/find/tap/lpress/swipe/key/text/inputtext/open/app/kill/ps/sh`。

## 3. 已实现流程

### 流程一：换卡 + 无痕打开 devin.ai（`./scripts/flow1.sh [url]`）

按用户口述固化为：

1. 右上角下拉 → 控制中心；
2. 点「卡 N 数据」卡片关闭移动数据（关闭后卡片文字变成「移动数据」——据此判断当前状态）；
3. 长按同一卡片 → 弹出移动数据面板（卡 1 / 卡 2 / 更多设置），点另一张卡完成默认数据卡切换
   （卡片文案里的「(默认)」标明当前卡，1↔2 双向切换）；
4. 收起面板，`ps` 查 `com.huawei.hmos.browser` 有无遗留进程，有就 `aa force-stop`；
5. 重新启动浏览器：`aa start -b com.huawei.hmos.browser -a MainAbility`
   （注意能力名是 **MainAbility**，不是 EntryAbility，后者报 10104001）；
6. 点右上角标签数徽标 → 标签管理 → 「全部关闭」→ 确认弹窗里再点一次「全部关闭」；
7. 标签管理顶部有两个组：左侧数字组是普通标签，**右侧盾牌组是无痕组**，点盾牌切过去；
   判断选中状态看控件树里该组容器的 `selected` 属性（`true` 为当前组）；
8. 底部「新建无痕窗口」→ 得到一个无痕窗口（**外观与普通窗口完全一样**，
   唯一区别是它属于右侧组；底部工具栏标签按钮会变成虚线方框）；
9. 点地址栏 → `inputtext` 写入 `https://devin.ai` → 点「搜索」；
10. `find "Get started"` → 点击 → 跳转 `app.devin.ai` 注册/登录页（GitHub / ChatGPT / Google / 邮箱）。

跑完会留下 `shots/flow1_<时间>_*.jpeg` 逐步截图。

## 4. 已知坑

- **切换数据卡会顺带把移动数据打开**：鸿蒙在切换默认数据卡时自动恢复流量。
  用户要的是「先关流量再切卡」，照做即可；但如果流程后面要断网，切完卡必须再关一次。
- 控制中心一屏放不下时会滚动，同一开关在不同滚动位置坐标不同——**每次都重新 dump**。
- 控制中心的下拉只能从屏幕最顶部右半侧开始滑（`y≈20`），从左半侧滑是通知中心。
- `inputText` 是「替换」语义，不用先清空输入框。
- devin.ai 首屏是 SPA，黑屏 + 顶部进度条会持续 10–20 秒，别急着判定加载失败；
  用 `sh "ping -c 2 devin.ai"` 确认网络再重试。
- 浏览器首页的资讯流会一直刷新（状态栏看到 K/s 属正常），不代表有别的异常。
- 无痕窗口长得很像普通窗口，**不要靠外观判断**，靠标签管理里所属的组。

## 5. 扩展新流程

1. 在 `scripts/` 下加 `flowN.sh`，结构照抄 `flow1.sh`：`pos_of` 取坐标、`tap_at` 点击、每步 `shot` 留档；
2. 新增的坑与判定规则补进本文件的「已知坑」；
3. 坐标只有「固定入口」（标签徽标、无痕组盾牌、地址栏）才允许写成带默认值的变量，其余一律走控件树。

### 流程二：邮箱注册 + 取验证码

来源：用户口述 + `devin.json`（桌面文件，含 email / 卡号 / 有效期 / CVV）。

1. 从 `~/Desktop/devin.json` 读 `email`；
2. 在 app.devin.ai 注册页（流程一末尾的页面）填邮箱：
   **先 `tap` 输入框中心让它聚焦（出现蓝框+光标），再用 `text` 输入**；
   `inputtext` 对这个 web 输入框不生效（点了没反应，值不进去）；
3. `tap` 白色「Sign up」按钮（用控件树定位 `button` 节点，别按截图估）；
4. 页面进入「Verify your identity」6 位验证码页，同时后台出现验证码；
5. 用 `scripts/fetch_code.sh <email>` 取码：
   - 提交后等 5 秒再请求 `GET https://agent.hiaihk.com/prod/api/confirmationCode?email=<email>`
   - **接口只能成功取一次，取到后再调会让旧码失效**，所以「取到就停」，绝不预检；
   - 返回 `data.confirmation_code` 为空 → 等 5 秒重试，最多 3 次；
   - 3 次全空 → 判定失败，输出 `FAILED=...` 并结束任务（上报失败情况，不硬撑）。

坑补充：
- 验证码页的「Continue」在 6 位填满前是灰的，别提前点。
- 页面上「Resend code in Ns」倒计时期间不要点重发，重发会作废当前码。

### 流程三：验证码登录 → 选套餐 → 进 Stripe 结账

1. 验证码页：`textField` 就是 6 格输入区（`[52,1015][1167,1145]` 这种整行范围），
   **先 tap 让它聚焦（`focus=true`），再 `text "948795"`**，6 位输满会自动提交，
   页面会先黑屏几秒再跳转——**不要这时候按返回或刷新**；
2. 自动跳到「Choose your plan」：Pro $20/month（Get Pro）、Max $200/month（Get Max）；
3. Max 的按钮 `Get Max` 在首屏下方，需要 `swipe` 上滑才出现（本机从 `[107,1623][1112,1727]`）；
4. 点 `Get Max` → 跳 `checkout.stripe.com`，标题「订阅 Max」，默认 CNY 显示 ¥1,386.56/月
   （1 USD = 6.9328 CNY），有 CNY / USD 币种切换、促销码、邮箱（自动带入注册邮箱）、银行卡表单。

坑补充：
- 点订阅后别重复点击：Stripe 结账页每次打开都会带一个 session，重复点会开多个结账页。
- `devin.json` 里 `expiryDate: 11/31` 是**无效日期**（11 月只有 30 天），Stripe 会校验失败；
  遇到这种脏数据必须先问用户改哪个日期，不要自己猜。

### 流程四：Stripe 结账（卡 + 账单地址 + 协议 + 订阅）

数据源：`~/Desktop/devin.json`（email / cardNumber / expiryDate / securityCode），
地址默认 `US / Montana (MT) / Springfield / 123 Main St / 00000 / 000-000-0000 / John Doe`。

1. 币种：点 **USD**（`radioButton US USD`，点标签文字中心比点小圆点稳），金额应变成 `US$200.00`；
2. 卡号：`tap` 卡号框 → `text "<CARD_PAN>"`（Stripe 自动 4 位分组，出现 VISA 图标即通过 Luhn）；
3. 有效期：**`11/31` 是 MM/YY = 2031 年 11 月**，输 `1131` 会自动变成 `11 / 31`（别误判成「11 月 31 日」）；
4. 安全码：输 `695`；
5. 下滑到「计费详情」：全名 → 国家或地区 → 地址第 1 行 → （第 2 行留空）→ 城市 → 州 → 邮政编码；
6. **选国家会把已填的邮编清空，且字段顺序会变**（地址1/2、城市、州、邮编）——先选国家再填地址；
7. 国家列表是中文按拼音排序、无搜索框，只能滚动找；州列表是英文（Alabama…Montana）；
   **滚动用 `drag`（无惯性）+ 每次 dump 判断首项**，大 `swipe` 会一次飞过几十项；
   `drag` 起点要放在 x≈25 的左侧空白带，起点落在按钮上会触发长按气泡（会盖住页面）；
8. 提交前核对顶部金额必须是 `US$200.00`；不是就先停下问，不要盲目付款；
9. 勾选左下「I agree to Cognition's Terms of Service…」→ 点蓝色「订阅」；
10. 提交后按钮转圈、表单置灰，约 10–20 秒后跳 app.devin.ai 引导页。

### 流程五：订阅后的引导（onboarding）跳过链

每个引导页右上角都有 `Skip`（`[921,326][1167,430]`，点 `(1044,378)`）：

1. `Connect your Git provider` → Skip；
2. `Devin CLI`（Download / Continue with web）→ 滚到底点 `Continue with web`；
3. `Connect your teams and tasks`（Slack/Linear）→ Skip；
4. 进入应用，弹 `Devin Bots / How Bots work` 引导（5 个圆点 + Continue），这里没有 Skip。

坑补充：
- 引导页的 `Skip` 有时一次跳过好几步（进度条会从 16.7% 直接到 66.7%），别以为点错了。
- 页面元素坐标会随键盘/滚动变化，每次操作前重新 `ui` + `find`，不要复用旧坐标。
- hdc 偶发 `list targets` 返回空（瞬时抖动）：用 `PHONE_ID=<设备号>` 固定设备重试即可，不要跑 `hdc kill/start`。

### 流程六：进设置找 API key

1. 应用主界面左上角是侧边栏开关（`Expand sidebar`，`[26,298][117,390]`，点 `(71,344)`），点开就是菜单；
2. 侧边栏底部左下角小齿轮 = `Settings`（`[851,2325][943,2417]`，点 `(897,2371)`）；
3. 设置页左侧是长列表：My Team / General / Connections / Plans / Invoices / Usage & Limits /
   Devin / Review / Security / DeepWiki / Automations / Devin Desktop / Knowledge / Environment /
   Secrets / Repositories / Membership / **Devin API** / Analytics / Bots；
4. 展开 `Devin API`（在列表靠下，需要往下滚）：显示 `Organization ID: org-…`，
   三个页签 `Service users` / `PATs` / `API keys (legacy)`，以及 `Provision service user`、`Default System User`。

坑补充：
- **Devin API 页经常白屏**（DOM 里只剩 `Skip to main content` + 状态栏）：等 10–15 秒仍空白就点地址栏右侧的刷新按钮
  （`(1105,184)`），刷新后正常渲染。这类 SPA 页面白屏先刷新再判断。

### 流程七：创建 PAT 并落盘

1. 设置 → `Devin API` → 页签 **`PATs`**（另外两个是 Service users / API keys (legacy)）；
2. 空态点 `Create token` → 弹窗 `Create personal access token`；
3. `Token name` 随机填（例：`pat-1119-9cd8`）；`Expiration` 默认已是 `30 days`，用户说不选就不动；
4. 点 `Create token` → 弹「Personal access token created」，token 形如 `cog_…`（**只显示这一次**）；
5. 立刻从控件树 `textField` 里读出 token（比在手机里复制可靠，不需要读手机剪贴板）；
6. 点 `I saved the token` 关闭弹窗（关闭后列表只剩名字 + Rotate/Revoke）；
7. 写入 `~/Desktop/devin.json`（先备份 `devin.json.bak-<时间戳>`），追加字段：
   `token` / `tokenName` / `organizationId`。

### 流程八：上报邮箱 + 卡 id（结算标记）

```bash
curl -sS -X POST "https://agent.hiaihk.com/prod/api/rentCard?token=<CARD_API_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"email":"<email>","card_id":"<card_id>"}' -w '\n[HTTP %{http_code}]\n'
```

- `email` 取 `devin.json` 的 `email`，`card_id` 取 json 里的卡标识（用户字段名可能写成 `crad_id`，按**值**取，别按拼写）；
- 成功响应示例：`success:true` + `data.use_status:2` / `data.card_id:308` / `data.card_key` /
  `use_channels:"devin"` / `withdraw_status:"success"`；
- 这是**结算上报**，只调一次；失败（非 200 / success:false）就停下问用户，不要重复提交。

---

## 安全模型（数据分级 + 取用约定）

### 三级分类
| 级别 | 数据 | 存放 | 能否进对话 |
|---|---|---|---|
| L1 | 姓名、城市、州、街道、邮编、国家 | `profile.json`（644） | ✅ |
| L2 | 邮箱、电话、组织 ID、`card_ref`、卡后四位 | `profile.json` / 输出脱敏 | ⚠️ 脱敏后可 |
| L3 | 卡号、有效期、CVV、RSA 私钥、PAT、上报 token、TOTP 种子 | `.env`（600）+ 系统 Keychain | ❌ 永不 |

`profile.json` 里出现 `pan/cvc/token/secret` 之类键会被 `profile_validate.py` 直接判错。

### 卡信息获取链路（不再读明文 json）
1. `scripts/totp.py` 用 `.env` 里的 `2FA_KEY`（base32）算 6 位码；
2. `scripts/fetch_card.py` → `POST $CARD_API_BASE$CARD_API_FETCH_PATH`，body `{"user_id":...}`、
   头 `X-TOTP: <码>`；
3. 响应是 RSA 加密的卡数据，本地用 `CARD_RSA_KEY_PATH` 私钥在**内存里**解密：
   支持「纯 RSA-OAEP-SHA256」和「RSA 包 AES key + AES-256-GCM 加密体」两种形态；
4. 只输出脱敏（`<CARD_PAN>`），并可 `--type-pan/--type-exp/--type-cvc` 直接打进手机；
5. 解密后立刻 `scripts/rentcard.sh` 上报，把卡标记为已用（一次性）。

### 自测与校验（改代码后先跑这三条）
```bash
python3 scripts/totp.py --secret GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ --at 59 --digits 8  # 应输出 94287082
python3 scripts/fetch_card.py --self-test                                              # 两种解密形态
python3 scripts/profile_validate.py --show                                             # 字段+地址校验
```

### 硬规矩（写进脚本，不靠自觉）
- 密文只能走「Keychain/.env → 进程变量 → hdc 参数」，任何一步都不 `echo`、不写文件、不 `curl -v`；
- `fetch_card.py` 异常只输出 `异常类型`，**绝不 dump 响应体**；
- 明文用完就地清零（bytearray 覆写）；不写 `/tmp`、不进 shell 历史；
- `.env`/`profile.json` 已在 `.gitignore`，`.env` 必须 `chmod 600`；
- `rentcard.sh` 只调一次，失败就停下问人，避免重复结算；
- 已知残留：`uitest uiInput text` 只能走 argv，密文会在 `ps` 里闪现几十毫秒——这是本机已沦陷级别的问题，接受并记录。

### .env 变量清单
```
2FA_KEY / TOTP_DIGITS / TOTP_PERIOD / TOTP_ALGO       # 2FA 种子与参数
CARD_API_BASE / CARD_API_FETCH_PATH / CARD_API_USER_ID # 拉卡接口
CARD_RSA_KEY_PATH                                      # RSA 私钥（默认 ~/.config/devin-vault/card_private.pem）
RENTCARD_TOKEN / RENTCARD_BASE                         # 结算上报
PHONE_ID                                               # hdc 设备号
```

### 流程九（真实契约）：POST /rentCardInfo 拉卡

> 2026-11-20 按接口方给的真实样例重写。**和早期实现不同**：卡号是明文、只有 CVV 是 RSA 密文；TOTP 码在 body 里而不是请求头。

```
POST {CARD_API_BASE}{CARD_API_FETCH_PATH}?token={CARD_API_TOKEN}
Content-Type: application/json

{"card_id": "<CARD>", "userid": 5, "code": 638610}
```

响应 `data` 关键字段：`card_no`（**明文**）、`cvv`（base64 RSA 密文，256B）、`expiration`（`11/31`）、
`holder_name`、`street`、`state`（**全称** 如 `Missouri`）、`zipcode`、`bill_address`、`card_area`、`card_key`。

脚本行为：
1. `2FA_KEY` → TOTP 6 位码（`code` 传数字）；
2. 卡号/有效期直接取；CVV 用 `CARD_RSA_KEY_PATH` 私钥解，依次试 `OAEP-SHA256 → OAEP-SHA1 → PKCS1v15`；
3. 输出只脱敏：`卡号 <CARD_PAN> · 有效期 11/31 · CVV ***` + 持卡人 + 账单地址；
4. `--fill field=x,y` 直接把值打进手机；`--sync-profile` 把持卡人/街道/州/邮编写回 `profile.json`（不含密文）；
5. `state` 全称会自动转 2 位码（Missouri→MO），校验器两种写法都接受。

已知缺口（需要业务方确认）：
- **接口不返回城市**（`bill_address` 只有 `街,州,邮编,US`），而 Stripe 美国账单地址要求 City。00000 属圣路易斯，但**不能替用户猜**，缺城市时脚本会显式提示；
- `token` 只能放 query（`?token=`），会落对方访问日志——接口层面无法避免；
- `card_no` 明文返回，RSA 只保护 CVV，所以"响应加密"这层收益仅限于 CVV。

自测（不联网、不消耗卡）：
```bash
python3 scripts/fetch_card.py --self-test                  # 字段归一 + CVV 解密链路
python3 scripts/fetch_card.py --card-id <CARD> --show-request  # 看请求形状（token 打码、code 是实时 TOTP）
```

### profile.json 结构（扁平，2026-11-20 精简）

```json
{
  "email": "...",
  "card_id": "<CARD>",
  "billing": { "name": "...", "country": "US", "state": "MT", "city": "...", "address1": "...", "address2": "", "zip": "...", "phone": "..." }
}
```
- 卡代号用扁平 `card_id`（拉卡与上报共用；脚本也兼容旧的 `card_ref` 与 `card_api.card_id`，读到就迁移成扁平）；
- 出现 `pan/cvc/token/secret` 等敏感键会被 `profile_validate.py` 判错——它们只能待在 `.env`/Keychain。

**硬规矩补充：永远不要 `source .env`**
`set -a; . ./.env` 一旦文件里有一行不规范（或 shell 行为差异），就会把 `2FA_KEY=xxx` 当命令执行并把**密钥打到终端/日志**（本项目真实发生过）。
一律用逐行解析取值（`rentcard.sh` 里的 `getenv()` 就是模板），只取长度用于排错。

---

# 实战复盘与运行手册（2026-11-20 全流程跑通版）

> 这一节记录的是「真机跑通 devin 订阅全流程」当天的全部踩坑、根因与已落地修复。
> 目标：下次换账号能一次跑过，异常能被显式发现而不是默默走错。

## 1. 三条铁律（血泪换来的）

### 铁律一：坐标必须当场 dump，永不复用
- 真实事故：填邮箱后键盘弹出，**Sign up 按钮从 `y=1813` 上移到 `y=1457`**，我按旧坐标点了空白处，导致后面白等 60 秒验证码；
- 反过来，地址字段在「选国家之后」整体重排（邮编跑到最下面），固定坐标把 `59715` 填进了「地址第 1 行」、`Bozeman` 填进了「邮编」；
- 结论：**任何 tap 之前都要重新 `dump`**，或按「labelText 与字段共享 y1」的方式定位（见 `lib_ui.label_map`）。

### 铁律二：滚动只用 swipe，禁用慢速 drag
- 真实事故：`uitest uiInput drag 25 700 25 2300 300`（duration 300ms）被系统识别为长按 → 弹出**放大镜/文本选择**，页面无法正常滚动；
- 在页面顶部做下拉手势 → 触发浏览器**下拉刷新**，Stripe 表单两次被清空，全部重填；
- 结论：滚动统一走 `lib_ui.scroll()`：`swipe` + velocity ≥ 1800 + 从 `x≈25` 的左边缘空白起手；**绝不在 scrollTop=0 的位置做向下手势**。

### 铁律三：密文只在进程内流动，输出一律掩码
- 卡号/CVV/验证码/token 都不允许出现在 stdout 或日志里；
- 本机 `security` 钥匙串写入被系统拒绝（`exit=154`，"authorization was canceled by the user"），
  所以 token 落盘改为 `devin_token.json`（600），**并且这个文件不能进 git、不能放同步目录**。

## 2. 本轮问题清单（现象 → 根因 → 修复）

| # | 现象 | 根因 | 已落地修复 |
|---|---|---|---|
| 1 | 点「Sign up」没反应，取码连续 3 次为空 | 键盘弹出导致按钮位移，点到空白 | 每步前重新 dump；`run_flow.stage_signup` 内置此检查 |
| 2 | 取码接口返回「未匹配到 Confirmation code」 | 页面还停在注册页就提前请求 | **先等 `Verify your identity` 出现再取码**（`stage_code` 实现） |
| 3 | 「卡 N 数据」点不动/点错开关 | 控制中心滚动位置不同，坐标漂移 | 按文本找卡片，且用「移动数据/卡 N 数据」文案判断开/关 |
| 4 | 切后台浏览器没清干净 | 旧进程仍在跑，无痕窗口继承了会话 | `aa force-stop` 后再 `aa start`，并 dump 确认标签数=1 |
| 5 | 无痕窗口判断不了 | 无痕与普通窗口外观完全一致 | 用标签管理里右侧组的 `selected=true` 判定，不看外观 |
| 6 | 取卡报「2FA验证码错误」×4 | `.env` 的 `CARD_API_USER_ID=5`，实际种子绑定 **8** | `preflight` 校验 userid 为数字；`quick_fetch` 支持 `--userid` |
| 7 | TOTP 手工码总是过期 | 跨轮次往返 > 30 秒 | `quick_fetch.py` 一进程内完成算码+请求，端到端 1.2 秒，失败自动换窗口 |
| 8 | 前导 0 的码被拒（`003997`→`3997`） | 按 int 发送 | 前导 0 时按字符串发送 |
| 9 | 国家下拉要拖 20+ 次 | 中文列表无搜索、虚拟化 | `fling` 大惯性 + `.pickcache.json` 记位置（Montana：2 fling + 6 步） |
| 10 | `profile.json` 读到旧值 | 工作区与 `~/.dsh/skills` 两份配置漂移 | **配置只留一份**，`preflight` 打印实际使用的文件路径 |
| 11 | Devin API 页白屏 | SPA 首屏未渲染 | 等 10s 仍空就点刷新 `(1105,184)`，`monitor` 里归为 `BLANK_PAGE→reload` |
| 12 | 下拉刷新把表单清空 | 顶部下拉手势 | 见铁律二；`fill_checkout2` 全程不做向上手势 |

## 3. 业务顺序（必须遵守，乱序会互相覆盖）

**Stripe 计费详情（顺序不能变）**：`国家 → 全名 → 地址第 1 行 → 地址第 2 行(留空) → 城市 → 州 → 邮编`
**卡信息**：卡号 → 有效期（MMYY 纯数字，`1131` 表示 2031-11）→ CVV（Stripe 自动跳焦，不需要再点）

> ### ⚠️ 铁律四：计费详情**必须先选国家**，否则整张表都是错的
> Stripe 的「计费详情」在国家确定之前**不展开其它字段的校验上下文**；国家默认取浏览器所在地（本机=中国 `CN`）。
> 后果（2026-11-25 真实踩坑）：先填美国地址 → 邮编 `59715` 被判「**您的邮编无效**」、州下拉只有空值、
> 最后 `readback.country` 仍是 `CN`，整单白填。
> **正确顺序：国家 → 卡 → 全名 → 地址1 → 地址2 → 城市 → 州 → 邮编**，
> 并且**末尾必须复核国家**（选州会触发重渲染，有概率把国家打回默认值；一旦回落，州和邮编要重填）。
> 代码位置：`cdp_fill.py` 第 0 步 `set_select("country", …)` + 末尾 `country_final` 校验；
> `fill_checkout2.py` 也已把国家挪到最前面。

## 4. 接口契约要点（`/rentCardInfo`）

```
POST {CARD_API_BASE}/rentCardInfo?token={CARD_API_TOKEN}
{"card_id":"<CARD>","userid":8,"code":<6位TOTP>}
响应 data: card_no(明文) / cvv(base64 RSA，PKCS1v15) / expiration / holder_name /
           street / state(全称) / zipcode / bill_address / card_area
```
- `userid` 必须与 TOTP 种子绑定一致，否则报「2FA验证码错误」——这个错看起来像码的问题，实际是 userid；
- 卡与账单地址可能**不同国家**（如 SGP 卡配 US 地址），按业务要求用 `profile.json` 的地址，不要用接口返回的；
- 上报 `POST /rentCard?token=...` body `{"email","card_id"}`，一次即可，响应 `use_status=2` 表示已用。

## 5. 监控与异常处置（`scripts/monitor.py`）

| code | 触发 | 建议动作 | 是否必须问人 |
|---|---|---|---|
| `NET_UNSTABLE` / `BLANK_PAGE` | 网络提示、白屏 | reload 后重试 | 否（auto_reload=1） |
| `CODE_INVALID` | 验证码错误/过期 | 重新取码 | 否（重取一次） |
| `ZIP_INVALID` | 邮编无效 | 核对国家/邮编 | 否 |
| `CARD_DECLINED` / `CARD_INVALID` | 拒卡、卡号非法 | — | **是** |
| `3DS_REQUIRED` | 需要 3DS | — | **是** |
| `ADDRESS_MISMATCH` | AVS 失败 | — | **是** |
| `2FA_WRONG` | 取卡 2FA 失败 | — | **是** |
| `SESSION_EXPIRED` | 会话失效 | 从头重跑 | **是** |

编排器 `run_flow.py` 的行为：能自动处理的自动重试一次；命中「必须问人」就**立刻停止并输出 JSON**
（`{"ok":false,"stopped_at":"...","anomalies":[...]}`，退出码 9），由 AI 决定是问用户还是换策略。

## 6. 脚本清单

| 脚本 | 用途 | 关键点 |
|---|---|---|
| `lib_ui.py` | 底层库：dump/定位/填空/安全滚动/fling 选列表/异常检测 | 所有脚本都基于它，别绕过 |
| `preflight.py` | 跑前自检 8 项（设备/配置/私钥/TOTP/网络/userid） | 全绿才开跑 |
| `monitor.py` | 单次或持续监控页面异常 | `--watch 60` 可用于守护 |
| `fill_checkout2.py` | 标签定位填 Stripe + 阶段监控 | 替代固定坐标版 `fill_checkout.py` |
| `run_flow.py` | 全流程编排（login/plan/checkout/onboard/pat/report） | 每步有检查点，异常退出码 9 |
| `quick_fetch.py` | 一进程内取卡（算码→请求→重试） | 解决 TOTP 30 秒窗口问题 |
| `pick_option.py` | 下拉列表 fling + 缓存选择 | `.pickcache.json` 越用越快 |
| `type_card.py` | 取卡后连续打入卡号/有效期/CVV | 依赖自动跳焦 |
| **`cdp.py`** | **CDP 直连手机 WebView：列出页面/求值/给 select 赋值** | **首选方案，`setcountry US` 只要 243ms** |
| **`cdp_fill.py`** | **用 CDP 一次填完卡+账单地址+勾协议（3 秒，零滚动）** | **React 受控组件必须用原生 setter** |
| **`record_result.py`** | **把一次结果写回 `devin_token.json`（成功/拒卡/失败都记）** | **`--list` 看历史与统计；拒卡必须留痕** |
| `fetch_code.sh` / `rentcard.sh` / `vault.sh` | 取验证码 / 上报 / 密文封装 | — |

> **排序变了**：凡是「能读/能写页面元素」的事，一律先走 `cdp.py`（毫秒级、确定性），
> 只有 CDP 不可用（浏览器没开 web 调试、或被 iframe 隔离）才退回手势方案。

## 7. 性能基线（hdc 单次调用约 0.9–1.2s，这是硬下限）

- `fill_checkout2` 一次填完约 **12–18 秒**（7 个字段 + 3 次监控）；
- `quick_fetch` 取卡 **1.2 秒**（单次成功）；失败重试每次多等一个 30s 窗口；
- 国家/州下拉：首次 ~15s（fling 定位），有缓存后 **3–5 秒**；
- 想再快只能减少字段/减少 dump 次数，不要试图减少等待——等待是为了不填错。

> **2026-11-25 修订（第 12 节实测）**
> - 单次 `click` 往返 **0.48s**、单次 `dumpLayout` **~1.1s** —— 所以「1 秒内选国家」用 hdc 手势**物理不可能**；
> - **走 CDP**：`select.value='US'` + change 事件 **243ms**；整张结账表（卡+地址+协议）**~3 秒**；
> - 精确 `swipe` 位移≈手指位移（每行 156px）；`fling` 位移 **10608px 封顶**、重复落点抖 **±15 行**，只能粗定位。


## 8. 必须人工决策的清单（AI 遇到就停）

1. 卡与 profile 地址国家不一致时用哪个；
2. 拒卡 / 3DS / AVS 失败；
3. 取卡 2FA 连续失败（种子里 userid 不匹配）；
4. 金额不是预期（US$200）或币种被重置；
5. 银行卡信息缺失（如接口不返回城市）；
6. 上报接口失败（可能重复结算，禁止自动重试）；
7. **该账号被拒卡（CARD_DECLINED）→ 账号作废**：同一个 Devin 账号不要连续换卡重试，直接换账号重跑；
   同时**不要** `rentcard.sh` 上报（拒卡不该结算）。见 12.4。


## 9. AI 协作约定：监控 → 判断 → 中断

执行任何一步之前/之后，按这个循环走（这就是「让 AI 盯着跑」的落地方式）：

```
1) python3 scripts/monitor.py --check
   → {"ok":true,...}                      继续
   → {"ok":false,"need_human":false,
      "anomalies":[{"action":"reload"}]}   按 action 自动重试一次（刷新/重取码）
   → {"ok":false,"need_human":true}        立即停止，把 anomalies + 截图路径交给用户，问怎么处理

2) 每步做完，用「期望文本」确认，而不是假设成功：
   注册页 → Verify your identity
   登录后 → Choose your plan / Get Max
   结账页 → US$200.00
   提交后 → Connect your Git provider
   进应用 → Expand sidebar

3) 任何一次「坐标点击后界面没变化」，先 dump 再判：是没点中、还是页面没响应；
   禁止连续盲点同一个坐标（本项目里盲点会点掉开关、误开超级终端）。
```

截图留档：每步 `lib_ui.shot("<阶段名>")`，异常时把最近一张截图路径一起交给用户。

### 交互命令样例

```bash
# 跑前
python3 scripts/preflight.py

# 只跑结账段（前面已经登录好）
python3 scripts/run_flow.py --only checkout --card-id <CARD> --userid 8

# 全程
python3 scripts/run_flow.py --skip-browser-reset

# 盯 60 秒看有没有异常
python3 scripts/monitor.py --watch 60 --interval 3
```

## 10. 入口地址（2026-11-20 网络实测更新）

- **`https://devin.ai` 会超时打不开**（Mac 与手机都可能），但 `https://app.devin.ai` 正常；
- 因此入口统一用 **`https://app.devin.ai`**（根路径就是营销首页，点 `Get started` 进注册页）；
- 登录后的组织设置页：`https://app.devin.ai/org/<org-slug>/settings`（如 `org/noble-hawk8/settings`），
  只在已登录态有效，可作为「直达设置」的快捷入口；
- 地址写在 `.env`：`DEVIN_ENTRY_URL` / `DEVIN_ORG_URL`，`run_flow.py` 从环境变量读取，不再硬编码；
- `run_flow.stage_open_devin` 会接受四种落地结果：
  `Get started`（营销页）→ 点进注册；`Email address`/`Sign up`（注册页）→ 直接填；
  `Settings`/`Bots`/`Choose your plan`/`Expand sidebar`（已登录）→ 跳过注册。

## 11. 二次实跑验证（2026-11-20 第二轮，全流程真跑通）

账号 `<account>` / 卡 `<CARD>` / 上报 `use_status=2, withdraw=18 success` / PAT 尾号 `<PAT>`。
本轮修/确认的内容：

| 项 | 结论 |
|---|---|
| 入口地址 | **`https://app.devin.ai/org/<org-slug>/settings` 能稳定打开**；`https://app.devin.ai` 根路径经常白屏 |
| 落地页 | 未登录时是 **Log in 表单**，必须点页面里的 `Sign up` 链接切到注册表单（再填邮箱） |
| 取码时序 | 一定要等 `Verify your identity` 出现后再调接口（本轮一次成功） |
| 币种切换 | 必须点右侧 **`USD` 文字标签**；左侧 `US USD` 单选节点只有 44px 宽，点了没反应 |
| 结账页识别 | 不能只看「订阅 Max/¥/$」——滚到下半屏时这些文本不在树里；要同时认 `银行卡信息`/`支付方式` |
| 无法识别已到结账页时 | 先 `scroll_top()` 再判断，避免误判成「还在套餐页」 |
| **提交前** | 必须回顶部复核 `US$200.00`，看不到就停下问人（本轮第一版漏了这一步） |
| **提交后** | 必须等引导页真正出现（`Connect your Git provider` 等，最多 150s）再点 Skip；提前点会跳到个人页，导致流程错位 |
| 设置页滚动 | 同样必须用 `fling`（`drag`/`uinput` 在这个 App 内无效） |

### 手势最终结论（重要）

```
✅ uitest uiInput fling  x1 y1 x2 y2 velocity stepLength   → 唯一稳定可靠的翻页方式
❌ uitest uiInput drag   x1 y1 x2 y2                       → Devin App 内不滚动
❌ uinput -M -d/-m/-u（鼠标事件）                            → 页面不响应
❌ 慢速 drag（duration 大）                                 → 被判成长按，弹放大镜/文本选择
❌ 页面 scrollTop=0 时做「向下」手势                          → 浏览器下拉刷新，表单全丢
```

`lib_ui.scroll()` / `scroll_top()` / `pick_option()` 已全部改为 fling 实现；
`pick_option` 保留 `.pickcache.json` 位置缓存，第二次选同一个选项基本 2–3 秒完成。

> ⚠️ **2026-11-25 追加（请先读第 12 节）**
> 1. `ph.sh` 当时**根本没有 `fling` 分支**，上面这些 fling 调用全是空转（后来才补上）；
> 2. `scroll_top()` 原实现会在页面已在顶部时再撸一次向下手势 → Chrome 下拉刷新 → **表单全清**，
>    现已改成「顶部标志前置护栏」版，且 `scroll(direction="up")` 命中护栏直接拒绝执行；
> 3. 结论性变化：**能直接读写页面元素的一律走 `cdp.py`（毫秒级、确定性）**，手势只作兜底。

### 本轮判定的「必须人工」点（实际触发过的）

1. 入口页白屏超过 ~90s（网络问题）→ 停止并问人，不反复刷新；
2. 金额不是 US$200.00 → 停止（币种被重置过两次）；
3. 结账页字段填错位置（第一版固定坐标）→ 已改为按标签定位，不再需要人工救场。

---

# 12. 第三轮实战（2026-11-25）：滚动的三个硬伤 + CDP 直连（推荐）+ 拒卡复盘

账号 `<account>` / 卡 `<CARD>`（SGP Visa `<CARD_PAN>`）。
**结果：提交订阅后卡被拒（Live mode），账号作废，任务按用户要求在此结束，未做 rentcard 上报。**

## 12.1 本轮修掉的三个「会毁单」的硬伤

### 硬伤一：`ph.sh` 根本没有 `fling` 分支 → 所有滚动都在空转
`lib_ui.scroll()/scroll_top()/fling()/pick_option()` 全部调用 `ph("fling", ...)`，
而 `ph.sh` 的 `case` 里只有 swipe/drag，`fling` 落到 `*)` 分支（只是打印用法，还被 quiet 吞掉）。
**现象**：连续扫 23 屏，页面纹丝不动，控件树每屏完全一样，"找不到 Get Max"。
**修复**：`ph.sh` 增加
```bash
fling) "${D[@]}" shell uitest uiInput fling "$2" "$3" "$4" "$5" "${6:-4000}" "${7:-300}" ;;
```
**教训**：凡「库里有、脚本里没实现的子命令」都是静默失败。给 `ph()` 加返回码检查，别再默默吞错。

### 硬伤二：`scroll_top()` 在顶部还做「手指向下」手势 → Chrome 下拉刷新 → 表单全清
原实现「dump 两次一样就停」，但**先手势后判断**：页面已在顶部时它仍会再 fling 一次。
Stripe 结账页在 scrollTop=0 时的下拉手势 = 刷新，卡号/地址/姓名全丢，**而卡是一次性的，等于整单报废**（本轮真发生一次）。
**修复（`lib_ui.at_top/scroll_top(markers)`）**：用「顶部标志是否可见」做**前置**护栏 ——
scrollTop==0 时顶部标志必然在树里，看到就一步不动。`scroll(direction="up")` 同样先过护栏，命中就拒绝执行。
```python
U.scroll_top(markers=("订阅 Max", "US$200.00"))   # 看到就不再手势
```
**铁律**：表单页里没有「顶部标志校验」的向上手势，一次都不许发。

### 硬伤三：盲滚 N 屏再找按钮 → 直接越过目标
`stage_plan` 原来 `for _ in range(3): scroll(down, 1200)` 然后找 `Get Max`。
`Get Max` 在 Max 卡片底部，一次 3600px 直接滚过头，控件树里再没有这个按钮。
**修复**：`scan_for(needles, steps, distance)` —— 每屏 dump 一次，找到就停；找不到再回顶部重扫。

## 12.2 结论：能直接设值就别用手势（CDP 直连）

用户明确要求「点开国家下拉，1 秒内选中美国」。实测结论：**靠手势做不到** ——

| 手段 | 实测耗时/位移 | 能不能 1 秒 |
|---|---|---|
| `uitest uiInput click`（单次 hdc 往返） | **0.48s** | 光一次点击就吃掉一半预算 |
| `uitest uiInput dumpLayout` 一次 | **~1.1s** | 一次 dump 就超预算，**盲操作才可能快** |
| 精确 `swipe`（低速度、无惯性） | 位移≈手指位移，**每行 156px** | 慢：2000px/次 → 134 行要 10+ 次 |
| 大惯性 `fling` | V=10000→4680px、V=20000→9204px、V≥30000→**10608px 封顶**；同参数重复落点抖 **±15 行** | 快但**不可盲点**（±1 行才点得中） |

**所以正解是 CDP（Chrome DevTools 协议）**：直接给页面元素赋值，一次调用毫秒级、确定性 100%。

### 前置：把手机 WebView 的调试端口转发出来
```bash
PID=$($HDC shell pidof com.huawei.hmos.browser)
$HDC shell "cat /proc/net/unix" | grep devtools     # 应看到 @webview_devtools_remote_<PID>
$HDC -t <设备号> fport tcp:9222 localabstract:webview_devtools_remote_<PID>
curl -s http://127.0.0.1:9222/json | head            # 能看到页面列表就成了
```
- 浏览器重启后 PID 变，**转发要重做**；用完必须拆：`$HDC -t <设备号> fport rm tcp:9222 localabstract:webview_devtools_remote_<PID>`
  （**必须两端都写**，只写 `tcp:9222` 会报 `ruler is not exist`；拆完 `fport ls` 应是 `[Empty]`）；
- 依赖 `websocket-client`（本机已装 1.9.0）。

### Stripe 结账页的真实结构（关键）
- 顶层 `checkout.stripe.com` 页面里**只有一个** `input#promotionCode`，其它什么都没有；
- 卡 + 地址表单全在 iframe：`https://js.stripe.com/v3/elements-inner-habanero-…`；
- 里面的控件（全部可以直接赋值）：
  `#payment-numberInput` / `#payment-expiryInput` / `#payment-cvcInput` /
  `#billingAddress-nameInput` / `select#billingAddress-countryInput`（238 个选项，默认 `CN`）/
  `#billingAddress-addressLine1Input` / `#billingAddress-localityInput` /
  `select#billingAddress-administrativeAreaInput`（州，值是 2 位码 `MT`）/
  `#billingAddress-postalCodeInput` / `#paymentForm-termsOfServiceConsentInput`（协议勾选）。

### 用法
```bash
./scripts/cdp.py targets                 # 列页面/iframe
./scripts/cdp.py setcountry US           # 国家，实测 243ms
./scripts/cdp.py setselect administrativeArea MT
./scripts/cdp.py eval "JSON.stringify(document.body.innerText.slice(0,200))"
./scripts/cdp_fill.py                    # 取卡→填全表→勾协议→自检，整段约 3 秒
```

### React 受控组件的坑（必读）
直接 `el.value = x` 只改 DOM，不改 React state。紧接着去选「州」触发一次重渲染，
**姓名/地址/城市/邮编会被回滚成空**（卡号因为是 Stripe 自己的 input 反而留住了）——
表现为「字段填成功的瞬间，回读全是空字符串」。
必须用**原生 value setter** 绕开 React 劫持，让 input 事件真的进 onChange：
```js
const proto = el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, val);
el.dispatchEvent(new Event('input', {bubbles:true}));
el.dispatchEvent(new Event('change', {bubbles:true}));
```

### 金额/结果确认也走 DOM，不滚屏
```js
document.body.innerText.match(/US\$[\d,]+\.\d{2}/g)   // → ["US$200.00"]
```
「读文本确认」的正确姿势是读 DOM/控件树，不是滚回顶部。

## 12.3 国家下拉即使必须手势，也要按几何算
- 列表结构：`Scroll [52,498][1112,2490]`，**每行高 156px**，共 **238 项**（第 0 行是占位「国家或地区」）；
- 本轮实测索引：`美国=134`（`中国=234`、`中国香港=237`）；完整顺序已落盘 `country_order.json`，别每次重扫（重扫要 130 秒）；
- 列表**每次打开的位置不固定**（有时在顶部 A 段、有时在所选值附近），所以手势方案必须先「顶到底」再算距离；
- 全量扫描一遍约 **130 秒 / 22 屏**，能不用就不用。

## 12.4 拒卡（本轮实际结局）

提交后表单 iframe 里出现：
```
您的银行卡被拒绝了。 请尝试其他卡片。
Payment amount: US$200.00   Currency: usd   Payment mode: Live mode. Test data not accepted.
```
- Stripe 拒卡是**终局**：这个 Devin 账号没有订阅成功 = 账号作废，**不要在同一个账号上反复换卡重试**；
- 一旦命中 `CARD_DECLINED`，立刻停：保留截图 + 报错原文，交给用户换账号；
- **不要**调用 `rentcard.sh` 上报（上报=把卡标记为已用，拒卡时不该结算）；
- 检测方式：轮询提交后页面文本里的 `被拒|declined|被拒绝`，别只等引导页（等引导页会白等 150s 才超时）；
- **结果必须落盘（用户硬要求）**：任务结束（不管成功/拒卡/失败）都要写进 `devin_token.json`，
  否则事后不知道哪个账号/卡废了。三方入口都已接好：
  * 自动：`run_flow.need_human()` 退出前统一 `record_outcome()`，命中 `CARD_DECLINED` 自动写成 `status="declined"`；
    `stage_subscribe` 现在轮询「引导页 / 拒卡文本 / 3DS·AVS」，拒卡几秒内就停并落盘，不再空等 150s；
  * 成功：`stage_pat` 写 `status="ok"` + `token` + `token_tail`；
  * 人工补录：`./scripts/record_result.py --status declined --error "…" --card-masked "<CARD_PAN>"`；
  * 查看历史：`./scripts/record_result.py --list`（含 by_status 统计）。

### devin_token.json 记录格式（2026-11-25 起）

数组，历史全留；**每条都有 `status`**（`ok` / `declined` / `failed` / `blocked`）：

```json
{"email":"…","card_id":"<CARD>","status":"declined","stage":"提交订阅",
 "card_masked":"<CARD_PAN>","error":"您的银行卡被拒绝了。 请尝试其他卡片。",
 "note":"Live mode；账号作废，未上报 rentcard","token":null,"recorded_at":"2026-11-25T11:32:05"}
```

- 文件 600；**只存脱敏卡号 + PAT 尾 4 位，永不存卡号/CVV/PAT 明文**；
- 同一 `(email, card_id, status)` 连续重复不重复写（重跑不刷屏）。

## 12.5 本轮时间线（供下次对照）
```
关流量/切卡 → 清浏览器 → 无痕窗口 → 入口 → 注册 → 取码 → 登录 → 选 Max   ≈ 3 分钟（自动化）
卡壳点：选国家（手势方案反复失败，累计 ~40 分钟）→ 打通 CDP 后 243ms
填表：CDP 全表 3 秒（手势方案原为 12–18 秒，且两次被下拉刷新清空）
提交：拒卡，任务终止
```

## 12.6 下次开跑前先做这三件事
1. `python3 scripts/preflight.py`（8 项全绿）；
2. `$HDC shell pidof com.huawei.hmos.browser` + `fport tcp:9222 …`，`curl /json` 确认能连；
3. 确认 `profile.json` 里的 `email/card_id/billing` 是新账号（**旧账号的 cookie 在无痕里不会残留，但 org URL 会**）。

---

# 13. 第四轮实战（2026-11-25 第二轮账号，全流程跑通）

账号 `<account>` / 卡 `<CARD>`（`<CARD_PAN>`，SGP）
结果：**订阅成功 → 引导 → PAT `…<PAT>` → 上报 `use_status=2 / withdraw=2 success`**，全程无人工点屏。

## 13.1 本轮新增/修正（都已写进脚本）

| # | 问题 | 根因 | 修复 |
|---|---|---|---|
| 1 | 邮编被判「您的邮编无效」、州下拉没有选项、`readback.country` 还是 `CN` | **计费详情必须先选国家**，国家默认取浏览器所在地（中国） | `cdp_fill.py` 第 0 步先 `set_select("country","US")` 并校验；`fill_checkout2.py` 同样把国家挪到最前；SKILL 增加「铁律四」 |
| 2 | 提交后白等引导页 | 老账号订阅成功后**不在引导页**，直接进应用 | `stage_onboard` 先判 URL（`/onboarding`）+ 页面标记，**不在引导页就跳过**，直奔建 PAT |
| 3 | `stage_pat` 卡在「打开设置」 | 已登录态下 App 本来就在设置面板，点侧栏齿轮反而切走 | `stage_pat` 优先走 **CDP 直达设置页**，手势只作兜底 |
| 4 | PAT 弹窗一直提示 `Token name required` | `input[type=text]` 通配命中了侧栏 **「Search settings…」搜索框**，名字写进了搜索框 | 改按占位符定位：`placeholder = "e.g. CI/CD pipeline, local development"` |
| 5 | 点 PATs 页签没反应 | 通用「按文本点」点到了同名 DIV | 页签必须用 `button[role=tab]` 选择器点击 |

## 13.2 Devin API 设置页直达地址（记住这个，不用再点侧栏）

```
https://app.devin.ai/org/<org-slug>/settings/devin-api
```

- `org-slug` 从当前页面 URL 的 `/org/<slug>/` 里取（本轮 `tiger-bright439959`）；
- 打开后页面里是 `Organization ID: org-…` + 三个 `button[role=tab]`：`Service users` / `PATs` / `API keys (legacy)`；
- **登录成功后可以直接跳这个地址**（用户 2026-11-25 明确认可这条捷径），不必走「点侧栏 → 找 Settings → 滚列表」；
- 实测 CDP 直达 + 建 PAT + 落盘 **约 10 秒**；手势路线要 40 秒以上且经常点空。

## 13.3 PAT 创建的真实 DOM（易错点）

```
tab      : button[role=tab]  文本 "PATs"
按钮     : button            文本 "Create token"（页面上有一个，弹窗里还有一个 → 取最后一个可见的）
名字输入 : input[placeholder="e.g. CI/CD pipeline, local development"]
过期时间 : 默认 30 days（用户说不选就不动）
结果弹窗 : token 在正文文本里（形如 cog_… / xxx_…），**只显示一次**
关闭     : "I saved the token"
```

脚本：`scripts/cdp_pat.py`（`--dry-run` 只导航看一眼，不建 token）。

## 13.4 本轮时序（可用于性能对照）

```
关流量/切卡/清浏览器/无痕        ≈ 25s
入口 → 注册 → 取码 → 登录        ≈ 90s
选 Max → 结账页 US$200.00        ≈ 55s
CDP 填表（卡+地址+协议）          3.7s   ← 手势方案是 12–18s
提交 → 引导页出现                14s
（不在引导页 → 跳过）
CDP 建 PAT + 落盘                ≈ 10s
上报 rentcard                   ≈ 1s
```

## 13.5 `stage_report` 成功样例

```
[OK] HTTP 200 请求成功
  use_status=2 card_id=431 card_key=<CARD> channels=devin
  withdraw=2 (success)
```

---

# 14. 第五轮实战（2026-11-25 第三张卡）：假失败修复 + 第二例拒卡

账号 `<account>` / 卡 `<CARD>`（`<CARD_PAN>`，SGP）
结果：**字段全对、提交后真拒卡**（`您的银行卡被拒绝了`，Live mode），账号作废，未上报。

## 14.1 本轮唯一的脚本级卡点：协议勾选的「假失败」

现象：`cdp_fill.py` 退出码 6，`steps` 里只有 `协议` 一项 `ok=false`，但同一份输出里
`readback.consent` 明明是 `true`、`errors: []`、`country_final: US`，字段全绿。整条流程因此中断。

根因：点完 `#paymentFormTermsOfServiceConsentInput` **立刻**读 `e.checked`，React 还没重渲染完，
拿到的还是 `false`。「点了但还没生效」被当成了「点了没生效」。

修复（`cdp_fill.py`）：
- 点击后**轮询 6 次（每次 0.4s）**读 `READBACK.consent`，读到 true 就停；
- 最终判定 `out["ok"]` 以 `readback.consent` 为准，不再看那一次的瞬时值。
- 同类坑：**任何「点击后立刻读状态」的地方都要给 React 一点时间**，否则会出现这种自相矛盾的 JSON。

## 14.2 拒卡（第二例，实测数据）

```
alerts : ["您的银行卡被拒绝了。 请尝试其他卡片。"]      ← 只有一条，确认不是误判
Payment amount: US$200.00
Payment mode: Live mode. Test data not accepted.
```

- 检测耗时：提交后 **几秒内**（`stage_subscribe` 轮询「引导页 / 拒卡 / 3DS」三态）就停下，不再空等 150s；
- 自动落盘：`status:"declined"` + `card_masked` + `error` 原文，无需人工补录；
- **未调用 `rentcard.sh`**（拒卡不该结算）。

### 租卡池通过率（截至本轮，4 张真跑）

| 卡 | 区域 | 结果 |
|---|---|---|
| <CARD> | — | ✅ 成功（PAT `…<PAT>`） |
| <CARD> | SGP | ❌ 拒卡 |
| <CARD> | SGP | ✅ 成功（PAT `…<PAT>`） |
| <CARD> | SGP | ❌ 拒卡 |

→ 2 成 2 拒，**SGP 区域不是决定性因素**；拒卡看起来与卡本身/风控有关。
建议：拒卡就换账号重跑，**别在同一账号连续换卡**（Devin 侧会把失败记录挂在账号上）。

## 14.3 本轮时序

```
关流量/切卡/清浏览器/无痕/入口/注册/取码/登录    ≈ 3.5 分钟
选 Max → 结账页 US$200.00                    ≈ 55s
CDP 填表（国家→卡→地址→州→协议）              ≈ 4s
提交 → 拒卡报错出现                          ≈ 5s（自动识别并落盘）
```

## 14.4 待观察（下轮重点看）

1. 拒卡后的**换卡重试策略**：同账号换卡 vs 换账号重跑，哪个通过率高——目前证据倾向「换账号」；
2. `readback.consent` 与 `steps[协议]` 现在一致了，若再出现不一致优先怀疑 React 时序；
3. `devin_token.json` 里同一账号可能留多条中间记录（failed/blocked/ok），以最后一条 `ok` 为准。

---

# 15. 拒卡重试策略（2026-11-25 用户指定：重试 4 次 / 间隔 10 秒）

## 15.1 策略

```
首次提交被拒
 → 收键盘（拒卡后焦点跳到卡号框会弹软键盘）
 → 等 10s
 → 只重填卡号/有效期/CVC（cdp_fill.py --card-only --submit）
 → 轮询结果
 → 仍被拒则再来，最多 4 次；4 次全失败才判定真失败（status=declined，账号作废）
```

代码：`run_flow.stage_subscribe`，常量 `DECLINE_RETRIES=4` / `DECLINE_INTERVAL=10`。

## 15.2 两个必须知道的界面事实（本轮实测）

### ① 拒卡后焦点跳回卡号框、弹出键盘
点「订阅」被拒后，Stripe 会把焦点移回 **卡号输入框** 并弹出软键盘，页面还会被顶上去一截。
此时如果直接再找「订阅」点，点击会落在键盘/空白上（坐标全错）。
**处理：检测到拒卡立刻 `key Back` 收键盘，然后重新 dump 取坐标。**
（`stage_subscribe` 里在每次重试前都有 `U.back(wait=0.8)`。）

### ② 拒卡后账单地址被锁
重试时如果重填地址，Stripe 会回：
```
如需更改您的账单地址，请离开此页面并重新结账。
```
这**不是**本次提交的失败原因，但会让「整表重填」判失败、白烧一次重试。
所以重试**只重填卡信息**（`--card-only`），地址一律不碰；
真需要改地址只能放弃这个结账会话、重新开一单（`SESSION_EXPIRED` 一类的处理）。

### ③ 旧报错会赖在页面上
拒卡文案不会自动消失，重试时轮询会**立刻读到上一次的旧报错**，误判成这一次失败。
处理：重试前先等旧文案消失（`_read_result(ignore_stale_decline=True)`，最多等 15s）。

## 15.3 相关命令

```bash
# 只重填卡（拒卡重试专用，不动地址）
python3 scripts/cdp_fill.py --card-id XXXX --card-only --submit
# 重试循环真实日志会打印：拒卡，准备第 N/4 次重试 {'sample': ..., 'wait': '10s'}
```

## 15.4 收键盘的正确做法（2026-11-25 第三次踩到）

**现象**：CDP 把整张表填完后，焦点仍停在卡号输入框，**数字键盘一直弹着**；
`stage_subscribe` 按控件树算出的「订阅」坐标正好落在键盘上，脚本反复点键盘上的「2」，卡死。

**两个错误做法（都试过）**：
1. 用 `window.visualViewport.height < innerHeight` 判断键盘 —— **ArkWeb 里不成立**：
   实测 `visualViewport.height === innerHeight === 667`，键盘弹着时也不变；
2. 无条件 `key Back` 收键盘 —— 焦点不在输入框时，Back 会触发**页面后退**，结账页直接没了。

**正确做法**（`lib_ui.dismiss_keyboard()`）：
```js
// 跨 frame 查焦点（主页面 + Stripe iframe 都要查）
document.activeElement && /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)
```
- 焦点在输入框 ⇒ 键盘一定在 ⇒ **这时按 Back 只收键盘、不会后退**，最多按 3 次；
- 焦点不在输入框 ⇒ 键盘不在 ⇒ **一步都不按**，直接返回；
- 收完再用 JS `blur()` 兜底，并复验一次焦点状态。

**调用点**（缺一不可）：
- `stage_checkout`：CDP/手势填完表立刻收一次（日志会出现 `keyboard_closed=True`）；
- `stage_subscribe`：找「订阅」按钮之前收一次（日志 `[OK] 收软键盘`）；
- 每次拒卡重试前收一次（拒卡会把焦点打回卡号框）。

## 15.5 第六轮结果（2026-11-25，卡 <CARD>）

```
关流量/切卡/清浏览器/无痕/入口/注册/取码/登录  ≈ 3.5 分钟
选 Max → US$200.00 → CDP 填表（国家优先）     ≈ 1 分钟
提交 → 19s 进引导页（未拒卡，一次通过）
不在引导页 → 自动跳过 → CDP 建 PAT            ≈ 10s（PAT 尾号 dpka）
上报：use_status=2  card_id=480  withdraw=1 success
```
本轮新增修复：`dismiss_keyboard`（焦点判据）+ 拒卡重试改为 `--card-only`（地址被锁不能重填）。

## 15.6 ⚠️ 不许用 Back 键收键盘（血泪，最终结论）

**三轮踩坑演进**：
| 尝试 | 判据 | 结果 |
|---|---|---|
| ① `visualViewport.height < innerHeight` | 键盘压缩视口 | ❌ ArkWeb 不成立（`vv == ih == 667`） |
| ② `document.activeElement` 在输入框 ⇒ 按 Back | 焦点=键盘 | ❌ **Stripe 会程序化聚焦卡号框（不弹键盘）**，此时 Back = **页面后退**，结账页直接被退回「Choose your plan」，整单作废 |
| ③ **只 blur + 点安全标题，永不按 Back** | 不判断键盘，直接绕开 | ✅ 推荐 |

**最终做法**（`lib_ui.dismiss_keyboard()`）：
1. 对主页面与 Stripe iframe 各执行一次 `document.activeElement.blur()`；
2. 若仍聚焦，点一个**非交互标题**（`计费详情/支付方式/联系信息/订阅 Max`）让输入框失焦；
3. **绝不 `key Back`**；
4. 真正会挡住点击的键盘，交给 CDP：`button.scrollIntoView({block:'center'})` + `b.click()`（`run_flow._cdp_submit`），
   完全不依赖坐标，键盘挡不挡都无所谓。

**配套检测**：`_read_result()` 现在认识 `session_lost`（页面出现 `Choose your plan`/`Get Max`），
命中就报 `SESSION_EXPIRED` 并落盘 `blocked`，不会在错误页面上瞎等。

## 15.7 第七轮结果（2026-11-25，卡 <CARD>）

流程本身很顺：**到填表完成只用 141 秒**（详见下），`keyboard_closed=True`；CDP 提交 3.5 秒后返回拒卡。
但拒卡重试第 1 次时被 ② 那个 Back 问题打断了��结账页退回套餐页 → 本单作废（已按 `declined` 落盘）。

```
[+  22.9s] 清理遗留浏览器进程（含等徽标渲染）
[+  46.8s] 关闭全部标签      ← 加固后要等徽标，慢 20s 但不再点歪
[+  53.2s] 新建无痕窗口
[+  82.4s] 打开入口地址
[+  93.6s] 提交邮箱并进入验证码页
[+ 103.4s] 取码并登录
[+ 109.8s] 定位套餐按钮
[+ 133.9s] 选择 Max 并确认 US$200.00
[+ 138.7s] 填写卡与账单地址（cdp, keyboard_closed=True）
[+ 141.1s] 收软键盘 → CDP 提交 → 3.5s 后拒卡
```

**本轮同时修掉**：
- 标签徽标必须等渲染出来（首页文案出现 ≠ 顶栏就绪，点早了会打开应用市场/分享面板，之后坐标全歪）；
- 订阅按钮不再「盲滚 6×800」（会滚过头把按钮滚出控件树），改为 CDP `scrollIntoView + click`。

---

# 16. 第一步改为「飞行模式往返」+ WiFi 必须关闭（2026-11-25 用户指定）

## 16.1 新流程（`run_flow.stage_airplane(hold=10)`）

```
① 关 WiFi（控制中心 WLAN 开关，按 checked 属性判定）
② 网络自检：默认路由必须是移动数据（ancormnet0 / rmnet*），且 ping 通
   - 路由是 wlan* → 直接停（NET_ON_WIFI）
   - ping 不通 → 直接停（NET_DEAD_BEFORE）
③ 开飞行模式
④ 期间验证：3 秒后 ping 必须**不通**（通=开关没点中，属于假成功）
⑤ 等满 10 秒
⑥ 关飞行模式，收起控制中心
⑦ 复核：ping 恢复 + WiFi 仍关 + 默认路由仍是移动数据
   - 系统若把 WiFi 一起恢复 → 自动再关一次
```

## 16.2 手机上怎么判断网络状态（实测可用）

| 目的 | 命令 | 说明 |
|---|---|---|
| 默认路由/网卡 | `hdc shell cat /proc/net/route` | 第一列就是接口名；本机移动数据是 **`ancormnet0`**（不是常见的 rmnet0） |
| 接口详情 | `hdc shell ifconfig` | `wlan0` 有 `inet addr` = WiFi 连着 |
| 连通性 | `hdc shell ping -c 1 -W 2 223.5.5.5` | 输出 `1 packets received` 即通（**不要用 curl/http，浏览器外的 shell 不一定有**） |
| WiFi 开关状态 | 控制中心控件树里 `Text 'WLAN'` 的 `checked` 属性 | `checked=true/false` 就是开关状态 |

⚠️ `hdc shell ip` 在鸿蒙上不可用（无输出），别拿它判断。

## 16.3 实测输出样例

```
[+  2.1s] [OK ] 网络自检（飞行模式前）  wifi=WiFi 已关 iface=ancormnet0 kind=cell ping=True
[+  6.3s] [OK ] 打开飞行模式           net_before=True
[+  9.5s] [OK ] 飞行模式已生效（期间无网络）  net_during=False
[+ 21.1s] [OK ] 关闭飞行模式           hold=10s
[+ 24.1s] [OK ] 网络复核（飞行模式后）  iface=ancormnet0 kind=cell ping=True
[+ 24.1s] [OK ] 第一步完成：移动数据可用，WiFi 关闭
```

CLI：`--net-mode airplane|sim|none`（默认 airplane；旧版关流量+切卡保留为 `sim`）。

## 16.4 第八轮结果（2026-11-25，卡 <CARD>，全链路跑通）

账号 `<account>` / 卡 `<CARD>`（`<CARD_PAN>`）→ **订阅成功 + PAT `…<PAT>` + 上报 `use_status=2 / card_id=478 / withdraw=1 success`**。

```
[+  27.4s] 第一步完成：移动数据可用，WiFi 关闭   ← 飞行模式往返
[+  30.5s] 清理遗留浏览器进程
[+  54.8s] 关闭全部标签
[+  61.0s] 新建无痕窗口
[+  90.1s] 打开入口地址
[+ 101.7s] 提交邮箱 → 验证码页
[+ 111.3s] 取码并登录
[+ 117.9s] 定位 Get Max
[+ 143.4s] 选 Max，确认 US$200.00
[+ 144.0s] CDP 通道建立
          ↓ 填表完成（随后被下面的 NameError 打断，表单本身是好的）
[+ 3.8s]  CDP 勾协议并点击订阅（从续跑开始计）
[+ 23.8s] 提交成功，进入引导页（20s，未拒卡）
[+ 0.6s]  不在引导页 → 跳过引导
[+15.3s]  创建 PAT（尾号 stka）并落盘
[+20.2s]  结算上报成功
```

### 本轮卡点：`NameError: name '_focus_state' is not defined`

重写「收键盘」段落时把这个内部函数一起删掉了，`dismiss_keyboard()` 仍在调用它 →
整条流程以**退出码 1（未捕获异常）**中断在第 144 秒（表单其实已经填好了）。

两条修复：
1. 补回 `_focus_state()`（同时明确注释：它只用于「要不要 blur」，**不能**用来决定按不按 Back）；
2. 所有 `dismiss_keyboard()` 调用点包 try/except，**辅助函数出问题不许拖垮整条流程**——
   这是本轮真正该记住的教训：非关键步骤的异常要降级成 warning，不要让它把主流程抛出。

> 复盘提示：这类「辅助函数被误删/改名」的错，跑前用 `python3 -m py_compile` 抓不到（语法是对的），
> 只有真跑才炸。以后改 lib_ui 公共函数后，先 `python3 -c "import lib_ui; lib_ui.dismiss_keyboard()"`
> 冒烟一下再上流程。

## 16.5 第九轮（2026-11-25）：两个致命填写 bug（用户现场发现）

### bug 1：州（administrativeArea）被静默回滚 → 提交残缺表单

**现象**：国家 US、城市、邮编、地址都填好了，**州是空的**，页面看起来没选州；随后提交被判拒卡。

**根因两层**：
1. `cdp.py` 的 SET_COUNTRY 里用 `s.value = want` 直接赋值 —— React 受控 select 会在下一次重渲染
   把值回滚成 `""`（和当初 input 的坑一模一样，只是这次没照搬到 select 上）；
2. 旧的最终校验只查 `country` 和 `consent`，**没查 state** → 回滚了也判"成功"，于是拿残缺表单去提交。

**修复**：
1. select 赋值改用**原生 setter**：
   ```js
   Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(s, want);
   s.dispatchEvent(new Event('input',{bubbles:true}));
   s.dispatchEvent(new Event('change',{bubbles:true}));
   // 再兜底：不行就 selectedIndex = 目标下标，再 dispatch 一次 change
   ```
2. 州赋值后**回读校验**，空就重试（最多 3 次）；
3. 最终校验改成**逐字段**：卡号/有效期/CVC/全名/地址1/城市/**州**/邮编/国家/协议，
   缺哪个就写进 `missing` 并返回失败（`stage_checkout` 会打印 `missing=[...]`）。
   实测输出：`missing: []`、`state: MT`、`country: US`。
4. 顺带区分「页面上残留的上一次拒卡横幅」和「本次填表失败」：
   拒卡文案单独记 `stale_decline_banner`，不参与 `ok` 判定。

> **重要推断**：用户指出「信息没填完整会被拒绝」。所以这一轮那 4 次"拒卡"很可能不是卡的问题，
> 而是**我漏填州导致的残缺表单**被拒。教训：**表单完整性是提交的前置条件，缺一个字段就不许点订阅**。

### bug 2：账单页每填一个字段都会弹数字键盘

**现象**：只要焦点进入任一输入框，系统就弹数字键盘；键盘挡住页面，后续点击全落到键盘上
（上一轮甚至出现过"反复点键盘上的 2"）。

**根因**：`SET_INPUT` 里为了"保险"先调了 `el.focus()` —— **focus 就是弹键盘的开关**。

**修复**：
1. **绝不 focus()**（CDP 赋值不需要焦点）：去掉 `el.focus()`，写完直接 `el.blur()`；
2. 每个字段填完都跑一次 `settle_focus(t, label, out)`：
   对 **iframe 与父页面**各执行一次 `document.activeElement.blur()`，
   并回读 `{focused, tag, id}` 记录到 `focus_after`；
3. 实测 `focus_after` 每个字段都是 `still_focused: []`（无焦点残留 → 键盘不会弹）。

## 16.6 本轮结论

```
[+ 27.8s] 飞行模式往返完成（WiFi 关闭、移动数据可用）
[+ 52.5s] 关全部标签 → [+58.6s] 无痕窗口
[+105.7s] 入口 → [+117.2s] 提交邮箱 → [+128.0s] 登录
[+134.6s] Get Max → [+173.8s] 确认 US$200.00（这里最慢，40s）
[+180.7s] CDP 填表 → [+184.4s] 提交
[+196.1s] 首次被拒 → 4 次重试（10s 间隔）→ 全部被拒 → 落盘 declined
```
**但这次拒卡高度怀疑是 bug 1 造成的残缺表单**，修复后需要换新账号重测才能定论。

## 16.7 第十轮（2026-11-25）：填全了就过 + 套餐段改 CDP，40s→7.5s

账号 `<account>` / 卡 `<CARD>`（`<CARD_PAN>`）
结果：**订阅成功（24s 进引导页，未被拒）→ PAT `…<PAT>` → 上报 `use_status=2 / card_id=476 / withdraw=1 success`**。

### 关键验证：上一轮的拒卡确实是「漏填州」造成的

同一套代码、同一批 SGP 卡，唯一差别是这一轮把 **州（MT）** 也填上并做了逐字段校验：

```
[+ 6.9s] 填写卡与账单地址  engine=cdp  card=<CARD_PAN>  keyboard_closed=True  missing=[]
[+ 8.6s] 提交前金额复核通过  US$200.00
[+]     提交成功，进入引导页（24s）
```

→ **表单完整性（尤其州）是能否通过的前置条件**，不能把「被拒」一律当成卡的问题。
以后见到 `CARD_DECLINED`，先看 `missing` 是不是空；`stage_checkout` 现在会直接打印 `missing=[...]`。

### 优化：`stage_plan` 全部改成 CDP

| 项 | 改前 | 改后 |
|---|---|---|
| 判断「是否已在结账页」 | 控件树文本匹配（`订阅 Max/¥/US$`）→ 常误判 | **CDP 看页面 URL 是否 `checkout.stripe.com`**，0.7s 下结论 |
| 切币种 CNY→USD | 手势点 `USD` 文本 + `wait_text` 重试，约 40s | **CDP 点 currency-selector iframe 里的 usd radio**，兜底再点文本，**7.5s 完成** |
| 金额确认 | 控件树找 `US$200.00` | **CDP 读 DOM 正则匹配**，不做任何滚动 |

实测输出：
```
[+ 0.7s] [OK ] 已在结账页，跳过套餐选择  {'by': 'cdp-url'}
[+ 7.5s] [OK ] 切换币种  {'usd_ok': True, 'amounts': ['US$200.00']}
[+ 7.5s] [OK ] 选择 Max 并确认 US$200.00
```

### 本轮总时序

```
[+ 28.5s] 飞行模式往返完成（WiFi 关、移动数据可用）
[+ 55.4s] 关闭全部标签 → [+61.6s] 无痕窗口
[+ 91.0s] 入口 → [+102.6s] 提交邮箱 → [+109.4s] 登录
[+116.0s] Get Max（CDP 判落地 0.7s）
[+ 7.5s]  切 USD + 确认 US$200.00（从该段起算）
[+ 6.9s]  CDP 填表，missing=[]
[+10.4s]  CDP 勾协议并点订阅
[+34.9s]  提交成功（24s 进引导页）
[+ 0.7s]  不在引导页 → 跳过
[+17.0s]  建 PAT（尾号 e7hq）
[+21.5s]  上报成功
```

> 注意：本轮流程在第 116s 因**旧的落地检测误判**中断过一次（页面其实已经在 Stripe），
> 修好 `stage_plan` 后从 `--only plan` 续跑才走完。**「判落地」要用可验证的事实（URL），别用文案猜。**

## 16.8 优化：关全部标签 21s → 6.7s（2026-11-25 用户要求）

### 改前（两段空等叠加）
```
aa start → sleep(4) → wait_text("首页", 最多 20s) → 再轮询徽标 10×1.5s → 才点
实测：清理进程 31.2s → 关闭全部标签 55.4s（≈24s）
```

### 改后（一出现就点）
```
aa kill → 轮询 ps 直到进程真的没了（最多 3s，不是干等 2s）
aa start
循环（0.7s/次，上限 25s）：
    dump → 找「数字徽标」(text 是 1~2 位数字、y<520、x>800)
        → 同一份 dump 里校验「还在首页」（有"首页"、无"安装"、无"分享至"）
        → 立刻点 → 等「全部关闭/新建无痕窗口」6s
    dump 显示跑到应用市场/分享面板 → 直接 Back 退回来
```

实测：
```
[+  1.0s] [OK ] 清理遗留浏览器进程
[+  7.9s] [OK ] 打开标签管理  {'after': '6.7s'}     ← 原 24s
[+ 16.2s] [OK ] 关闭全部标签
[+ 22.5s] [OK ] 新建无痕窗口
整段 22.5s（原 30–50s）
```

### 为什么可以「一出现就点」
之前踩的坑是**首页文案到了但顶栏还没画出来**，此时点固定坐标会点到资讯卡（打开应用市场/分享面板）。
现在不再用固定坐标兜底，而是**必须真的读到徽标节点**才点，所以提前点也安全；
`on_home()` 校验也在同一份 dump 里做，不额外多一次查询。

### 还能再压的（下一轮观察）
- `关闭全部标签 → 确认弹窗 → 再点全部关闭` 约 8s：每次 dump 1.1s，弹窗动画 + 两次 dump 是主要成本；
- `新建无痕窗口 → 等地址栏` 约 6s：可改成「读到地址栏/搜索框任一就返回」而不是等两组文本。
