#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
虎牙自动任务：虎粮发放 + 每日打卡 + Server酱 微信推送

重构要点（对照旧版 main.py / _falsetears_main.py）
1. 打卡成功判定不再「点了就算成功」。
   抓包确认打卡走 WebSocket + WUP 二进制协议（wupuif.setFansSign），
   HTTP / XHR / fetch 层完全抓不到请求，所以判定只能看「服务端回执后的界面状态」：
   点击后轮询同一个 <a>，文本变成「已完成」或类名带 disabled 才算成功；
   点击前就是「已完成」记「今日已打卡」；点击后毫无变化记「失败」；找不到入口记「无打卡入口」。
2. 额外在页面脚本执行前（CDP Page.addScriptToEvaluateOnNewDocument）注入 WebSocket 钩子，
   记录本次是否真的发出了 setFansSign 帧，作为失败时的排查证据写进日志。
3. 去掉 webdriver_manager，改由 Selenium Manager 自动匹配 ChromeDriver，
   避免 Actions 上 chromedriver 与 runner 自带 Chrome 版本不匹配导致的 session not created。
4. 结构化结果 + Server酱 推送，标题区分成功 / 失败，失败会让 Action 变红。
"""

import os
import sys
import time
import traceback

import requests
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

import config as cfg

# 打卡状态
CK_OK = "✅ 打卡成功"
CK_DONE = "ℹ️ 今日已打卡"
CK_NO_ENTRY = "⚠️ 无打卡入口"
CK_FAIL = "❌ 打卡失败"

# 在页面任何脚本之前注入：记录 WebSocket 收发帧（打卡=wupuif.setFansSign）
WS_HOOK_JS = r"""
(function () {
  if (window.__hyWsInstalled) { return; }
  window.__hyWsInstalled = true;
  window.__hyWs = [];
  var NativeWS = window.WebSocket;
  if (!NativeWS) { return; }
  function toText(d) {
    try {
      var b = null;
      if (d instanceof ArrayBuffer) { b = new Uint8Array(d); }
      else if (ArrayBuffer.isView(d)) { b = new Uint8Array(d.buffer, d.byteOffset, d.byteLength); }
      else { return 'str:' + String(d).slice(0, 200); }
      var n = Math.min(b.length, 400), s = '';
      for (var i = 0; i < n; i++) { s += (b[i] >= 32 && b[i] < 127) ? String.fromCharCode(b[i]) : '.'; }
      return s;
    } catch (e) { return ''; }
  }
  function W(url, protocols) {
    var ws = protocols ? new NativeWS(url, protocols) : new NativeWS(url);
    try {
      var rawSend = ws.send.bind(ws);
      ws.send = function (data) {
        try { window.__hyWs.push({ t: 'send', d: toText(data) }); } catch (e) {}
        return rawSend(data);
      };
      ws.addEventListener('message', function (ev) {
        try { window.__hyWs.push({ t: 'recv', d: toText(ev.data) }); } catch (e) {}
      });
    } catch (e) {}
    return ws;
  }
  W.prototype = NativeWS.prototype;
  W.CONNECTING = 0; W.OPEN = 1; W.CLOSING = 2; W.CLOSED = 3;
  window.WebSocket = W;
})();
"""

# 在页面里找打卡按钮：文本精确匹配 + 向上找到含「打卡」的浮层，避免误命中别处的「已完成」
FIND_BTN_JS = r"""
var fn = function () {
  var nodes = document.querySelectorAll('a, button, span, div');
  for (var i = 0; i < nodes.length; i++) {
    var el = nodes[i];
    var t = (el.textContent || '').trim();
    if (t !== '打卡' && t !== '已完成' && t !== '已签到') { continue; }
    var p = el.parentElement, ok = false;
    for (var j = 0; j < 4 && p; j++) {
      if ((p.textContent || '').indexOf('打卡') >= 0) { ok = true; break; }
      p = p.parentElement;
    }
    if (!ok) { continue; }
    var r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) { continue; }
    return el;
  }
  return null;
};
var el = fn();
if (!el) { return { found: false }; }
return { found: true, text: (el.textContent || '').trim(), cls: String(el.className || '') };
"""


class HuYaTask:
    def __init__(self):
        self.cookie = os.getenv("HUYA_COOKIE", "").strip()
        self.rooms = self._parse_rooms(os.getenv("HUYA_ROOMS", ""))
        self.send_key = os.getenv("SEND_KEY", "").strip()
        self.headless = os.getenv("HEADLESS", "true").lower() != "false"
        self.dry_run = os.getenv("DRY_RUN", "").lower() == "true"

        self.logs = []          # 推送正文
        self.fatal = ""         # 致命错误（登录失败等）
        self.results = []       # 每个房间的结构化结果
        self.ws_supported = False

        if not self.cookie:
            print("[FATAL] 未设置 HUYA_COOKIE")
            sys.exit(1)
        if not self.rooms:
            print("[FATAL] 未设置 HUYA_ROOMS")
            sys.exit(1)

        self.driver = self._init_browser()
        self.wait = WebDriverWait(self.driver, 15)

    # ---------- 基础 ----------
    @staticmethod
    def _parse_rooms(raw):
        out, seen = [], set()
        for s in (raw or "").split(","):
            s = s.strip()
            if not s.isdigit():
                if s:
                    print(f"[WARN] 跳过无效房间号: {s}")
                continue
            rid = int(s)
            if rid not in seen:
                seen.add(rid)
                out.append(rid)
        return out

    def _init_browser(self):
        opts = Options()
        if self.headless:
            opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--window-size=1920,1080")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_argument("--mute-audio")
        opts.page_load_strategy = "eager"

        print("[START] 启动 Chrome（Selenium Manager 自动匹配驱动）")
        driver = webdriver.Chrome(options=opts)
        driver.set_page_load_timeout(60)
        try:
            driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": WS_HOOK_JS})
            self.ws_supported = True
            print("[WS] 已注入 WebSocket 钩子（用于核对 setFansSign 是否真的发出）")
        except Exception as e:
            print(f"[WS] 注入钩子失败（不影响主流程）: {e}")
        return driver

    def _goto(self, url, settle=3.0):
        """导航并等待，页面超时不当作失败（内容多数已就绪）"""
        try:
            self.driver.get(url)
        except TimeoutException:
            print("[WARN] 页面加载超时，继续解析")
        except WebDriverException as e:
            print(f"[WARN] 导航异常: {str(e).splitlines()[0][:160]}")
        time.sleep(settle)

    def _ws_frames(self):
        """读取页面里记录的 WebSocket 帧（可能为空）"""
        if not self.ws_supported:
            return []
        try:
            return self.driver.execute_script("return window.__hyWs || [];") or []
        except Exception:
            return []

    # ---------- 登录 ----------
    def login(self):
        print("[LOGIN] 注入 Cookie 并校验登录")
        self._goto(cfg.URLS["user_index"], settle=2)
        ok_cnt = 0
        for line in self.cookie.split(";"):
            line = line.strip()
            if "=" not in line:
                continue
            name, val = line.split("=", 1)
            try:
                self.driver.add_cookie({
                    "name": name.strip(),
                    "value": val.strip(),
                    "domain": ".huya.com",
                    "path": "/",
                })
                ok_cnt += 1
            except Exception:
                continue
        print(f"[COOKIE] 已写入 {ok_cnt} 条")
        self.driver.refresh()
        time.sleep(cfg.TIMING["page_load_wait"])
        try:
            elem = self.wait.until(
                EC.presence_of_element_located((By.ID, cfg.LOGIN["huya_num"]))
            )
            who = elem.text.strip()
            print(f"[LOGIN] 成功，账号: {who}")
            self.logs.append(f"账号: {who}")
            return True
        except Exception:
            print("[LOGIN] 失败：Cookie 可能已过期")
            self.fatal = "Cookie 失效，登录失败"
            return False

    # ---------- 虎粮 ----------
    def get_hl_count(self):
        print("[COUNT] 查询虎粮数量")
        self._goto(cfg.URLS["pay_index"], settle=4)
        try:
            tab = WebDriverWait(self.driver, 15).until(
                EC.element_to_be_clickable((By.ID, cfg.PAY_PAGE["pack_tab"]))
            )
            tab.click()
            time.sleep(2)
        except Exception:
            print("[COUNT] 背包标签点击失败")
            return 0
        n = self.driver.execute_script(
            """
            var items = document.querySelectorAll('li[data-num]');
            for (var i = 0; i < items.length; i++) {
              var t = items[i].title || items[i].innerText || '';
              if (t.indexOf('虎粮') >= 0) { return items[i].getAttribute('data-num'); }
            }
            return 0;
            """
        )
        hl = int(n) if n and str(n).isdigit() else 0
        print(f"[COUNT] 虎粮: {hl}")
        return hl

    def _read_hl_on_gift_page(self):
        """从礼物页读虎粮余额：.m-gift-item 里的 .c-count；-1 表示页面没有虎粮条目"""
        try:
            v = self.driver.execute_script(
                "var items = document.querySelectorAll('.m-gift-item');"
                "if (items.length === 0) { return -1; }"
                "for (var i = 0; i < items.length; i++) {"
                "  var t = items[i].innerText || '';"
                "  if (t.indexOf('虎粮') >= 0) {"
                "    var c = items[i].querySelector('.c-count');"
                "    var s = (c ? c.innerText : '').replace(/[^0-9]/g, '');"
                "    return s ? parseInt(s, 10) : 0;"
                "  }"
                "}"
                "return 0;"
            )
            return int(v) if v is not None else None
        except Exception:
            return None

    def _wait_balance(self, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            v = self._read_hl_on_gift_page()
            if v is not None and v >= 0:
                return v
            time.sleep(0.8)
        return None

    def _find_by_text(self, text):
        """按可见文本找元素（用于"赠送""立即送出"这类无稳定 class 的按钮）"""
        try:
            els = self.driver.find_elements(By.XPATH, "//*[normalize-space(text())='%s']" % text)
        except Exception:
            return None
        for e in reversed(els):
            try:
                if e.is_displayed():
                    return e
            except Exception:
                continue
        return None

    def _click_el(self, el):
        """优先真实鼠标点击（触发 hover 态），失败再退到 JS click"""
        try:
            self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            ActionChains(self.driver).move_to_element(el).pause(0.3).click().perform()
            return True
        except Exception:
            try:
                self.driver.execute_script("arguments[0].click();", el)
                return True
            except Exception:
                return False

    def send_gift(self, rid, count):
        """赠送虎粮。真实验证：送礼前后背包余额差 == 送出数才算成功"""
        if count <= 0:
            return "无粮可送"
        try:
            self._goto(cfg.URLS["room_base"].format(rid), settle=cfg.TIMING["room_enter_wait"])
            lp = self.driver.execute_script('return document.body.getAttribute("data-lp")')
            gid = self.driver.execute_script('return document.body.getAttribute("data-gid")')
            if not lp or not gid:
                return "⚠️ 未取到房间参数（可能未开播）"

            self._goto(cfg.URLS["gift_tab"].format(lp=lp, gid=gid), settle=cfg.TIMING["page_load_wait"])
            before = self._wait_balance()
            if before is None:
                return "❌ 读不到虎粮余额"
            if before <= 0:
                return "无粮可送"
            n = min(count, before)

            target = None
            for it in self.driver.find_elements(By.CLASS_NAME, cfg.GIFT["item_class"]):
                if "虎粮" in (it.text or ""):
                    target = it
                    break
            if target is None:
                return "❌ 礼物面板无虎粮"

            # 关键：必须真实鼠标 hover + click，否则不会展开数量输入和"赠送"按钮
            self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", target)
            ActionChains(self.driver).move_to_element(target).pause(1.2).click().perform()
            time.sleep(1.5)

            inp = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, cfg.GIFT["input_css"]))
            )
            inp.click()
            inp.clear()
            inp.send_keys(str(n))
            time.sleep(0.8)

            send_btn = self._find_by_text("赠送")
            if send_btn is None:
                for b in self.driver.find_elements(By.CLASS_NAME, cfg.GIFT["send_class"]):
                    if b.is_displayed():
                        send_btn = b
                        break
            if send_btn is None or not self._click_el(send_btn):
                return "❌ 找不到赠送按钮"

            time.sleep(1.5)
            confirm = self._find_by_text("立即送出")
            if confirm is None:
                for b in self.driver.find_elements(By.CLASS_NAME, cfg.GIFT["confirm_class"]):
                    if b.is_displayed():
                        confirm = b
                        break
            if confirm is not None:
                self._click_el(confirm)

            time.sleep(6)
            after = self._wait_balance(timeout=20)
            if after is None:
                return "⚠️ 已点击送出，但读不到余额（送前 %s）" % before
            diff = before - after
            if diff == n:
                return "🚀 送出虎粮 %s 个（余额 %s→%s）" % (n, before, after)
            if diff == 0:
                return "❌ 送礼未生效（余额 %s→%s）" % (before, after)
            return "⚠️ 送出数量不符（期望 %s，余额 %s→%s）" % (n, before, after)
        except Exception as e:
            print(f"  [GIFT] 异常: {str(e).splitlines()[0][:120]}")
            return "❌ 送礼流程异常"

    # ---------- 打卡 ----------
    def _find_btn(self):
        try:
            return self.driver.execute_script(FIND_BTN_JS) or {"found": False}
        except Exception:
            return {"found": False}

    def _hover_badge(self):
        """悬停粉丝牌唤出打卡浮层：先滚到可见处，再真实鼠标悬停，失败则用 JS 派发事件兜底"""
        badge = None
        for by, sel in ((By.CSS_SELECTOR, ".chat-host-pic"), (By.ID, "chatHostPic")):
            try:
                els = self.driver.find_elements(by, sel)
                if els:
                    badge = els[0]
                    break
            except Exception:
                continue
        if badge is None:
            return False

        try:
            self.driver.execute_script(
                "arguments[0].scrollIntoView({block: 'center'});", badge
            )
            time.sleep(0.4)
            ActionChains(self.driver).move_to_element(badge).pause(0.8).perform()
        except Exception:
            pass
        time.sleep(1.2)

        if self._find_btn().get("found"):
            return True

        # 兜底：直接派发 mouseover / mouseenter（headless 下偶发拿不到 :hover）
        try:
            self.driver.execute_script(
                """
                var b = document.querySelector('.chat-host-pic') || document.getElementById('chatHostPic');
                if (!b) { return false; }
                ['mouseover', 'mouseenter', 'mousemove'].forEach(function (type) {
                  b.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
                });
                return true;
                """
            )
        except Exception:
            pass
        time.sleep(1.2)
        return bool(self._find_btn().get("found"))

    def daily_check_in(self, rid):
        """
        返回 (状态, 明细)
        状态取值：CK_OK / CK_DONE / CK_NO_ENTRY / CK_FAIL
        """
        self._goto(cfg.URLS["room_base"].format(rid), settle=max(cfg.TIMING["room_enter_wait"], 4))

        if not self._hover_badge():
            # 没有粉丝牌 / 未开播 / 面板没出来
            self._hover_badge()  # 再试一次
        info = self._find_btn()
        if not info.get("found"):
            return CK_NO_ENTRY, "未找到打卡浮层（该房间可能没有粉丝牌或未开播）"

        text = info.get("text", "")
        cls = info.get("cls", "")
        if text in ("已完成", "已签到") or "disabled" in cls:
            return CK_DONE, "进入时已是已打卡状态"

        before = len(self._ws_frames())
        try:
            self.driver.execute_script(
                """
                var nodes = document.querySelectorAll('a, button, span, div');
                for (var i = 0; i < nodes.length; i++) {
                  var el = nodes[i], t = (el.textContent || '').trim();
                  if (t !== '打卡') { continue; }
                  var p = el.parentElement, ok = false;
                  for (var j = 0; j < 4 && p; j++) {
                    if ((p.textContent || '').indexOf('打卡') >= 0) { ok = true; break; }
                    p = p.parentElement;
                  }
                  if (!ok) { continue; }
                  var r = el.getBoundingClientRect();
                  if (r.width <= 0 || r.height <= 0) { continue; }
                  el.click();
                  return true;
                }
                return false;
                """
            )
        except Exception as e:
            return CK_FAIL, f"点击异常: {str(e).splitlines()[0][:80]}"

        # 轮询等待服务端回执后的界面变化
        deadline = time.time() + 12
        while time.time() < deadline:
            time.sleep(0.6)
            info2 = self._find_btn()
            t2 = info2.get("text", "")
            c2 = info2.get("cls", "")
            if t2 in ("已完成", "已签到") or "disabled" in c2:
                return CK_OK, "点击后状态变为「已完成」"

        # 没变化：用 WS 帧判断请求到底有没有发出去
        frames = self._ws_frames()[before:]
        sent_sign = [f for f in frames if f.get("t") == "send" and "setFansSign" in (f.get("d") or "")]
        if sent_sign:
            return CK_FAIL, "已发出 setFansSign，但界面未变（服务端可能拒绝）"
        return CK_FAIL, "点击后未发出 setFansSign 请求，界面无变化"

    # ---------- 推送 ----------
    def push(self, title, body):
        if not self.send_key:
            print("[PUSH] 未配置 SEND_KEY，跳过推送")
            return
        print("[PUSH] 发送 Server酱 通知")
        try:
            res = requests.post(
                f"https://sctapi.ftqq.com/{self.send_key}.send",
                data={"title": title[:100], "desp": body[:30000]},
                timeout=15,
            )
            print(f"[PUSH] HTTP {res.status_code} {res.text[:120]}")
        except Exception as e:
            print(f"[PUSH] 推送失败: {e}")

    # ---------- 主流程 ----------
    def run(self):
        ok = False
        try:
            print("=" * 46)
            print("[HUYA] 虎粮发放 + 每日打卡")
            print("=" * 46)
            print(f"房间: {self.rooms}")

            if not self.login():
                return False

            total = self.get_hl_count()
            self.logs.append(f"虎粮: {total}")
            if total <= 0:
                print("[HL] 暂无虎粮，跳过送礼，仅打卡")

            n_ok = n_done = n_fail = n_skip = 0
            per = total // len(self.rooms) if total > 0 else 0
            rem = total % len(self.rooms) if total > 0 else 0

            for i, rid in enumerate(self.rooms):
                cnt = per + (1 if i < rem else 0)
                print(f"\n>>> 房间 {rid}（分配 {cnt} 个）")

                gift_msg = "跳过（无虎粮）" if total <= 0 else self.send_gift(rid, cnt)
                print(f"  [GIFT] {gift_msg}")

                status, detail = self.daily_check_in(rid)
                print(f"  [CK] {status} - {detail}")

                if status == CK_OK:
                    n_ok += 1
                elif status == CK_DONE:
                    n_done += 1
                elif status == CK_FAIL:
                    n_fail += 1
                else:
                    n_skip += 1

                self.results.append({"room": rid, "gift": gift_msg, "ck": status, "detail": detail})
                self.logs.append(f"房间 {rid}：{status}\n  · 送礼：{gift_msg}\n  · 打卡：{detail}")
                time.sleep(1.5)

            ok = n_fail == 0
            head = f"新打卡 {n_ok}｜已打卡 {n_done}｜无入口 {n_skip}｜失败 {n_fail}"
            print(f"\n[DONE] {head}")
            self.logs.insert(0, head)

        except Exception as e:
            self.fatal = f"程序异常: {e}"
            print(f"[CRASH] {e}")
            traceback.print_exc()
            ok = False
        finally:
            # 推送 + 关浏览器
            title = "✅ 虎牙任务成功" if ok and not self.fatal else "❌ 虎牙任务失败"
            if self.fatal:
                self.logs.insert(0, f"⚠️ {self.fatal}")
            body = "\n\n".join(str(x) for x in self.logs) or "无内容"
            self.push(f"{title}｜{time.strftime('%m-%d')}", body)
            try:
                self.driver.quit()
                print("[EXIT] 浏览器已关闭")
            except Exception:
                pass
        return ok


def main():
    task = HuYaTask()
    sys.exit(0 if task.run() else 1)


if __name__ == "__main__":
    main()
