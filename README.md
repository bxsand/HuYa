# 虎牙虎粮自动发放 + 每日打卡

轻量、开箱即用的 **虎牙虎粮自动批量赠送 + 直播间每日打卡工具**，跑在 GitHub Actions 上，无需自备服务器。

---

## ✨ 特性
- 🔐 **安全配置**：Cookie / 房间号 / 推送 Key 全部走 GitHub Secrets，不进代码
- 🤖 **全自动**：每天北京时间 00:30 定时执行，也支持手动触发并临时指定房间
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
进入 **Actions** → 启用工作流。之后每天 **北京时间 00:30** 自动跑。

> fork 仓库的定时任务若连续 60 天没有活动，GitHub 会自动停用，去 Actions 页面点一下重新启用即可。

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
```yaml
on:
  schedule:
    - cron: '30 16 * * *'   # UTC 16:30 = 北京时间 00:30
```
改时间就改这行（cron 是 UTC，北京时间 = UTC+8）。

> 为什么是 00:30 而不是 00:01：GitHub 定时任务在整点前后排队严重，00:01 常常延迟很久；00:30 更稳。

---

## 📌 运行流程
1. 校验 Cookie / 房间号
2. 注入 Cookie 并校验登录（读 `huyaNum`）
3. 打开背包页读虎粮数量，平均分配
4. 逐房间：进直播间 → 送虎粮
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
