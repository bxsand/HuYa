# 虎牙虎粮自动发放 + 每日打卡

**虎牙虎粮自动批量赠送 + 直播间每日打卡**工具。业务逻辑跑在 GitHub Actions 上，不用自建服务器；定时触发二选一——GitHub 自带 `schedule`（零依赖）或任意外部定时器（如云函数，触发时刻更准），也能随时手动运行。

需要先配好 Cookie / 房间号，并**自行选一种定时方式**（见 [快速部署](#-快速部署)），不是克隆下来就能跑。

---

## ✨ 特性
- 🔐 **安全配置**：Cookie / 房间号 / 推送 Key 全部走 GitHub Secrets，不进代码
- 🤖 **全自动**：每天北京时间 00:30 定时执行（云函数触发 或 GitHub schedule，二选一），也支持手动触发并临时指定房间
- 🎁 **精准分配**：读取背包虎粮，平均分配到多个房间并逐个赠送
- ✅ **打卡真实判定**：不靠「点了就算成功」，而是读按钮状态翻转（`打卡` → `已完成`），并注入 WebSocket 钩子核对是否真的发出 `setFansSign` 请求
- 📬 **微信通知**：可选接入 [Server酱](https://sct.ftqq.com)，跑完把结果推到微信
- 📦 **无冗余依赖**：只有 Selenium + requests，驱动由 Selenium Manager 自动匹配

---

## 📁 项目结构
```
.github/workflows/auto.yml  # GitHub Actions 工作流
requirements.txt            # selenium==4.41.0, requests
main.py                     # 主程序：虎粮赠送 + 打卡 + 推送
config.py                   # 页面选择器
```

---

## 🚀 快速部署

> ⚠️ **部署完不会自动跑** —— 必须二选一启用定时，否则只能手动触发：
>
> - **最省事**：把 `.github/workflows/auto.yml` 里 `# schedule:` 那三行前面的 `#` 删掉，用 GitHub 自带定时。零依赖，但可能延迟 2~4 小时、60 天无活动会被自动停用。
> - **最稳**：搭一个外部定时器（云函数 / cron / 任何能发 HTTP 的地方）每天调 GitHub API 触发。实测误差 <5 秒，不会自动停用。
>
> 详见 [📅 定时任务](#-定时任务)。如果只想跑一次，直接手动触发就行，不用配定时。

### 1. 拿 Cookie
1. 登录 [虎牙网页版](https://i.huya.com/)
2. F12 → 网络 → 刷新页面
3. 找到 `index.php?m=Msg&do=getMsgC` 请求
4. 复制请求头里的完整 `Cookie` 字符串

### 2. 配置 Secrets
仓库 **Settings → Secrets and variables → Actions**，添加：

| 名称 | 必需 | 说明 |
|------|------|------|
| `HUYA_COOKIE` | ✅ | 虎牙登录 Cookie |
| `HUYA_ROOMS` | ✅ | 房间号，英文逗号分隔，如 `821839,19803324,910323` |
| `SEND_KEY` | ⬜ | Server酱 SendKey，不填则跳过推送 |

### 3. 启用 Actions
进入 **Actions** → 启用工作流。

工作流里准备了**两种定时方案**，仓库里目前只配了方案 A（方案 B 已写好但注释掉）：

- **方案 A（本仓库在用）**：华为云函数每天 **北京时间 00:30** 调 GitHub API 触发。不受「60 天无活动自动停用」影响，触发时刻准。
- **方案 B（零依赖）**：GitHub 自带的 `schedule`，写在 `auto.yml` 里但**默认注释掉**。没有云函数的人只要去掉三行注释就能用。

两者细节与取舍见下方「📅 定时任务」。**不要同时启用**，否则每天会跑两遍。

---

## 🎮 手动运行

**在线**：Actions → `虎牙虎粮自动发放` → `Run workflow`，可在 `rooms` 输入框临时覆盖房间号（留空则用 `HUYA_ROOMS`）。

**本地**：
```bash
pip install -r requirements.txt
export HUYA_COOKIE="你的Cookie"
export HUYA_ROOMS="821839,19803324"
export SEND_KEY=""      # 可选
export HEADLESS="true"  # 本地想看浏览器窗口就设 false
python main.py
```

---

## 📅 定时任务

两种方案，**二选一**。

### 方案 A：外部定时器调 API（本仓库在用）

```
华为云 TIMER  CRON_TZ=Asia/Shanghai 0 30 0 * * ?   （北京 00:30）
        ↓
huya-dispatcher 云函数 —— 调 GitHub API 查「今天已成功几次」
        ↓  不足 N 次才继续（幂等保护，避免重复跑）
GitHub API  POST /actions/workflows/auto.yml/dispatches
        ↓
Actions 执行 auto.yml（送礼 + 打卡 + 推送）
```

云函数只做三件事：读今天成功次数、判断要不要跑、调 dispatch。业务逻辑全在 `main.py` 里，改函数不影响代码。

**改触发时间**：改云函数 TIMER 触发器的 cron。华为云 TIMER 是**六段** `秒 分 时 日 月 周`：

```
CRON_TZ=Asia/Shanghai 0 30 0 * * ?   → 北京 00:30:00  ✅
CRON_TZ=Asia/Shanghai 30 0 0 * * ?   → 北京 00:00:30  ⚠️ 秒写在了最前面
```

> `CRON_TZ` 前缀已实测生效（临时触发器设 03:12:00，实际 03:12:03 触发）。

### 方案 B：GitHub 自带 schedule（零依赖）

不需要任何外部服务。把 `.github/workflows/auto.yml` 里这段**取消注释**即可：

```yaml
  # schedule:
  #   - cron: '30 16 * * *'   # UTC 16:30 = 北京时间 00:30
```

cron 是 **UTC**，北京时间 = UTC+8，所以 `30 16` 对应北京 00:30。

代价（也是本项目改用方案 A 的原因）：

| 问题 | 后果 |
|------|------|
| 公共 / fork 仓库 60 天无活动，schedule 被自动停用 | 任务静默失效，很难发现 |
| 实测首跑延迟 2~4 小时且无上界 | 虎牙打卡要求「当天完成」，漂移跨天就白跑 |
| 延迟不可控、无法补偿 | 只能被动接受 |

### 怎么选

| | 方案 A | 方案 B |
|---|---|---|
| 依赖 | 一个云函数（华为云免费额度足够） | 无 |
| 触发精度 | 实测误差 <5 秒 | 延迟 2~4 小时，无上界 |
| 长期稳定 | 不会自动停用 | 60 天无活动被停用 |
| 适合 | 想准点跑、长期无人值守 | 图省事、能接受延迟 |

> ⚠️ 两种方式**不要同时开**。若同时启用，每天会触发两次；虽然 `concurrency` 会让它们排队而不是并行（不会重复送粮），但白白多耗一次 Actions 额度。

---

## 📌 运行流程
1. 校验 Cookie / 房间号
2. 注入 Cookie 并校验登录（读 `huyaNum`）
3. 打开背包页读虎粮数量，平均分配
4. 逐房间：进直播间取礼物页参数（`data-lp` / `data-gid`）→ 送虎粮
   - 送之前读一次礼物页虎粮余额，送完再读一次，**余额差 == 送出的个数**才算成功
   - 余额没变 → 记为「送礼未生效」；差值与期望不符 → 记为「送出数量不符」
   - 余额为 0 时礼物条目会整个消失，按 0 处理而不是报读取失败
5. 逐房间打卡：悬停粉丝牌 → 等浮层 → 找 `打卡` 按钮
   - 已是「已完成」/ 带 `disabled` → 记为**今日已打卡**
   - 否则点击，12 秒内轮询按钮状态是否翻转 → **打卡成功**
   - 超时则查 WebSocket 帧：发过 `setFansSign` 记为「已发出但界面未变」，没发过记为「未发出请求」
6. 汇总结果，配置了 `SEND_KEY` 就推送到微信

输出示例：
```
[DONE] 新打卡 1｜已打卡 2｜无入口 0｜失败 0
```

---

## 🔍 打卡为什么要这么判
虎牙的「每日打卡」**不是 HTTP 请求**，而是走 WebSocket 上的 WUP 二进制协议（`setFansSign`），带会话签名，纯 requests 复放不可行。所以本项目仍然用浏览器点击触发，但把「成功」的判定改成**页面状态翻转 + WebSocket 帧核对**双保险，避免以前那种「点了就报成功」的假绿。

---

## ⚠️ 重要提醒
- 请勿分享 Cookie；过期后重新获取并更新 Secret 即可
- 房间要**正常开播**才能赠送礼物
- 单账号每日虎粮数量有限，房间越多每个房间分得越少
- 本项目仅用于学习交流，请遵守平台规则

---

## 🐛 常见问题

| 现象 | 原因 / 处理 |
|------|------|
| `session not created: This version of ChromeDriver only supports Chrome version N` | 环境里塞了不匹配的 chromedriver。本工作流**不安装** driver，交给 Selenium Manager 自动匹配；别再加 `setup-chrome` 的 `install-chromedriver` |
| 登录失败 | Cookie 过期或复制不全，重新获取 |
| 打卡「无打卡入口」 | 该房间没开播 / 没有粉丝牌 / 浮层没弹出来 |
| 赠送失败 | 房间未开播、礼物面板未加载 |
| 收不到微信推送 | `SEND_KEY` 没配或填错；日志里应该有 `[PUSH]` 打头的一行 |

---

## 📄 许可证
MIT License
