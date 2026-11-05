#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
虎牙虎粮自动发放 - 精简稳定版
环境变量配置，核心逻辑极简
"""

import os
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

import config as cfg


class HuYaAuto:
    """精简稳定版虎牙虎粮自动发放"""

    def __init__(self):

        self.debug = ""

        if self.debug :
            print("从文件获取 HUYA_COOKIE")
            try:
                with open("cookie", "r", encoding="utf-8") as f:
                    self.cookie = f.read().strip()
            except FileNotFoundError:
                self.cookie = ""  # 文件不存在时为空
            self.rooms = ["998"]
        else :
            print("从环境变量获取 HUYA_COOKIE")
            self.cookie = os.getenv('HUYA_COOKIE', '').strip()
            self.rooms = self._parse_rooms(os.getenv('HUYA_ROOMS', ''))

        if not self.cookie:
            print("[ERROR] 未设置 HUYA_COOKIE")
            sys.exit(1)

        if not self.rooms:
            print("[WARN] 未设置房间号，使用默认房间")
            self.rooms = [518512, 518511]

        self.driver = self._init_browser()
        self.wait = WebDriverWait(self.driver, 5)

    def _parse_rooms(self, rooms_str):
        rooms = []
        for s in rooms_str.split(','):
            s = s.strip()
            if s:
                try:
                    rooms.append(int(s))
                except ValueError:
                    print(f"[WARN] 跳过无效房间号: {s}")
        return rooms

    def _init_browser(self):
        chrome_options = Options()

        if not self.debug:
            chrome_options.add_argument('--headless')

        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-gpu')

        # 页面加载策略：eager（DOMContentLoaded 即返回，避免 hd.huya.com 挂起）
        chrome_options.page_load_strategy = 'eager'

        chrome_options.add_argument('--disable-images')

        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        chrome_options.add_argument('--window-size=1920,1080')

        chrome_bin = os.environ.get('CHROME_BIN')
        if chrome_bin and os.path.exists(chrome_bin):
            chrome_options.binary_location = chrome_bin

        chromedriver = os.environ.get('CHROMEWEBDRIVER')
        print("[START] 启动浏览器")
        if chromedriver and os.path.exists(chromedriver):
            from selenium.webdriver.chrome.service import Service
            driver = webdriver.Chrome(service=Service(chromedriver), options=chrome_options)
        else:
            driver = webdriver.Chrome(options=chrome_options)
        driver.set_page_load_timeout(60)
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        return driver

    def login(self):
        print("[LOGIN] 登录中")
        self.driver.get(cfg.URLS["user_index"])
        time.sleep(cfg.TIMING["implicit_wait"])

        cnt = 0
        for line in self.cookie.split(';'):
            line = line.strip()
            if '=' not in line:
                continue
            name, val = line.split('=', 1)
            try:
                self.driver.add_cookie({
                    'name': name.strip(),
                    'value': val.strip(),
                    'domain': '.huya.com',
                    'path': '/'
                })
                cnt += 1
            except Exception:
                continue

        print(f"[COOKIE] 已添加 {cnt} 个Cookie")
        self.driver.refresh()
        time.sleep(cfg.TIMING["page_load_wait"])

        try:
            elem = self.wait.until(
                EC.presence_of_element_located((By.ID, cfg.LOGIN["huya_num"]))
            )
            username = elem.text.strip()
            print(f"[SUCCESS] 登录成功: {username}")
            return True
        except Exception:
            print("[ERROR] 登录失败")
            return False

    def get_hl_count(self):
        print("[SEARCH] 查询虎粮数量")
        self.driver.get(cfg.URLS["pay_index"])

        # 强制等待页面完全加载（GitHub Action 必须加长）
        time.sleep(3)

        try:
            # 等待【背包】标签可点击后用 JS 点击（headless 下比 Selenium click 更可靠）
            WebDriverWait(self.driver, 15).until(
                EC.element_to_be_clickable((By.ID, cfg.PAY_PAGE["pack_tab"]))
            )
            self.driver.execute_script("document.getElementById('packTab').click()")
            time.sleep(2)  # 点击后必须等面板渲染 + AJAX 拉取虎粮

        except Exception:
            print("[WARN] 点击背包标签失败")
            return 0

        # 强化版 JS 获取虎粮（Python 侧轮询，避免 async/await 语法问题）
        get_hl_js = '''
            (function() {
                const items = document.querySelectorAll('li[data-num]');
                for (let item of items) {
                    let title = item.title || item.innerText || '';
                    if (title.includes('虎粮')) {
                        return item.getAttribute('data-num');
                    }
                }
                return 0;
            })()
        '''
        n = 0
        for _ in range(40):
            n = self.driver.execute_script(get_hl_js)
            if n and str(n).isdigit() and int(n) > 0:
                break
            time.sleep(0.3)

        hl = int(n) if n and str(n).isdigit() else 0
        if hl <= 0:
            # 诊断：打印页面文本，便于定位 headless 下未匹配原因
            try:
                txt = self.driver.execute_script("return document.body.innerText.slice(0,800)")
                print("[DIAG] 页面文本:", txt)
            except Exception as e:
                print("[DIAG] 获取页面文本失败:", e)
        print(f"[COUNT] 虎粮数量: {hl}")
        return hl

    def send_to_room(self, room_id, count):
        print(f"[GIFT] 房间 {room_id} 发送 {count} 个")
        if count <= 0:
            return 0

        try:
            self.driver.get(cfg.URLS["room_base"].format(room_id))
            time.sleep(cfg.TIMING["room_enter_wait"])

            lp = self.driver.execute_script('return document.body.getAttribute("data-lp")')
            gid = self.driver.execute_script('return document.body.getAttribute("data-gid")')

            if not lp or not gid:
                print("[ERROR] 获取房间参数失败")
                return 0

            self.driver.get(cfg.URLS["gift_tab"].format(lp=lp, gid=gid))
            time.sleep(cfg.TIMING["page_load_wait"])

            # 查找虎粮项
            items = self.wait.until(
                EC.presence_of_all_elements_located((By.CLASS_NAME, cfg.GIFT["item_class"]))
            )
            hu_liang = None
            for item in items:
                if "虎粮" in item.text:
                    hu_liang = item
                    break
            if not hu_liang:
                print("[ERROR] 未找到虎粮")
                return 0

            # 悬停
            ActionChains(self.driver)\
                .move_to_element(hu_liang)\
                .pause(1)\
                .perform()
            time.sleep(1)

            # 自定义数量
            inp = self.wait.until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, cfg.GIFT["input_css"]))
            )
            inp.click()
            inp.clear()
            inp.send_keys(str(count))

            # 赠送
            send_btn = self.wait.until(
                EC.element_to_be_clickable((By.CLASS_NAME, cfg.GIFT["send_class"]))
            )
            send_btn.click()
            time.sleep(1)

            confirm_btn = self.wait.until(
                EC.element_to_be_clickable((By.CLASS_NAME, cfg.GIFT["confirm_class"]))
            )
            confirm_btn.click()
            time.sleep(1)

            print(f"[SUCCESS] 赠送成功: {count} 个")
            return count

        except Exception as e:
            print(f"[CRASH] 赠送失败: {e}")
            return 0

    def run(self):
        success = False
        try:
            print("=" * 40)
            print("[HUYA] 虎牙虎粮自动发放")
            print("=" * 40)
            print(f"房间列表: {self.rooms}")

            if not self.login():
                return False

            total = self.get_hl_count()
            if total <= 0:
                print("❌ 暂无虎粮")
                return False

            print(f"[TOTAL] 虎粮总数: {total}")

            # 分配
            n = len(self.rooms)
            per = total // n
            rem = total % n
            plan = []
            for i, rid in enumerate(self.rooms):
                c = per + 1 if i < rem else per
                plan.append((rid, c))

            print("\n[PLAN] 分配方案:")
            for rid, c in plan:
                print(f"  {rid}: {c}个")

            print("\n[SEND] 开始发送...")
            sent = 0
            for rid, c in plan:
                sent += self.send_to_room(rid, c)

            print(f"\n[DONE] 完成！已发送 {sent}/{total}")
            success = sent > 0
            return success

        except Exception as e:
            print(f"\n[CRASH] 程序异常: {e}")
            return False

        finally:
            # 必关浏览器，修复进程泄漏
            if hasattr(self, 'driver'):
                self.driver.quit()
                print("[EXIT] 浏览器已关闭")

            return success


def main():
    huya = HuYaAuto()
    res = huya.run()
    sys.exit(0 if res else 1)


if __name__ == '__main__':
    main()
