import re
import random
import base64
import time
import requests
from urllib.parse import urlparse, parse_qs
from bs4 import BeautifulSoup
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

SENTRY_BASE = "https://sentry.platorelay.com/.gs/pow/captcha"
AD_URL = "https://qm.qq.com/q/D5Yf8lLIYM"

BASE_TELEMETRY = {
    "dwellMs": 446629,
    "moves": 592,
    "velocityVar": 17.2058786473109,
    "velocityMedian": 1.455788671386738,
    "velocityAvg": 3.2309785421350123,
    "velocityMin": 0.0005871534893303571,
    "velocityMax": 18.108148421848494,
    "velocityP25": 0.42923229467905805,
    "velocityP75": 3.793246599138705,
    "directionChanges": 31,
    "keypresses": 0,
    "speedSamples": 592,
    "moveDensity": 754.4408783783783
}

DETECT_DOMAINS = [
    "auth.platoboost.com",
    "auth.platorelay.com",
    "auth.platoboost.net",
    "auth.platoboost.click",
    "auth.platoboost.app",
    "auth.platoboost.me",
    "deltaios-executor.com"
]

def generate_telemetry(variation=0.1):
    telemetry = {}
    for key, value in BASE_TELEMETRY.items():
        factor = 1 + random.uniform(-variation, variation)
        telemetry[key] = value * factor
    telemetry["dwellMs"] = int(telemetry["dwellMs"])
    telemetry["moves"] = int(telemetry["moves"])
    telemetry["directionChanges"] = int(telemetry["directionChanges"])
    telemetry["keypresses"] = 0
    telemetry["speedSamples"] = telemetry["moves"]
    return telemetry

def generate_fingerprint():
    return "-" + ''.join(random.choices("0123456789abcdef", k=8))

def decode_base64_url(raw_url):
    parsed = urlparse(raw_url)
    if parsed.path.endswith('/a') or 'a?' in raw_url:
        query = parse_qs(parsed.query)
        if 'd' in query:
            d_param = query['d'][0]
            try:
                decoded = base64.b64decode(d_param).decode('utf-8')
                if decoded.startswith('http'):
                    return decoded
                else:
                    return f"{parsed.scheme}://{parsed.netloc}{decoded}"
            except Exception:
                pass
    return raw_url

def extract_card_key(html):
    soup = BeautifulSoup(html, 'html.parser')
    selectors = [
        '#card-key', '.voucher-code', 'pre', 'code',
        'div[class*="card"]', 'p[class*="key"]', 'span[class*="code"]'
    ]
    for selector in selectors:
        elem = soup.select_one(selector)
        if elem:
            text = elem.get_text(strip=True)
            if text.startswith("FREE_"):
                return text
    match = re.search(r'FREE_[0-9a-f]{32}', html, re.IGNORECASE)
    if match:
        return match.group()
    match = re.search(r'FREE_[A-Za-z0-9]+', html)
    if match:
        return match.group()
    return None

class CaptchaSolver:
    def solve(self, puzzle_data):
        instruction = puzzle_data["puzzle"]["instruction"].lower()
        shapes = puzzle_data["puzzle"]["shapes"]

        if "largest" in instruction or "smallest" in instruction:
            return self._solve_size_comparison(instruction, shapes)
        elif "find" in instruction:
            return self._solve_find_object(instruction, shapes)
        elif "rotate" in instruction or "align" in instruction:
            return self._solve_rotate(instruction, shapes)
        else:
            raise ValueError(f"未知指令: {instruction}")

    def _solve_size_comparison(self, instruction, shapes):
        match = re.search(r"(largest|smallest) (\w+)", instruction)
        if not match:
            raise ValueError(f"无法解析大小比较指令: {instruction}")
        comparator = match.group(1)
        shape_type = match.group(2)

        candidates = [(i, s) for i, s in enumerate(shapes) if s["type"].lower() == shape_type.lower()]
        if not candidates:
            raise ValueError(f"未找到类型 {shape_type}")

        if comparator == "largest":
            target = max(candidates, key=lambda x: x[1]["size"])
        else:
            target = min(candidates, key=lambda x: x[1]["size"])
        return target[0]

    def _solve_find_object(self, instruction, shapes):
        words = instruction.split()
        target_type = words[-1] if words else ""
        for i, s in enumerate(shapes):
            if s["type"].lower() == target_type.lower():
                return i
        raise ValueError(f"未找到类型 {target_type}")

    def _solve_rotate(self, instruction, shapes):
        if not shapes:
            raise ValueError("没有图形")
        current_orientation = shapes[0].get("orientation", 0)
        required_rotation = (360 - current_orientation) % 360
        return required_rotation

def bypass_captcha(session):
    telemetry = generate_telemetry()
    fingerprint = generate_fingerprint()
    req_payload = {
        "telemetry": telemetry,
        "deviceFingerprint": fingerprint,
        "forcePuzzle": False
    }
    try:
        r = session.post(f"{SENTRY_BASE}/request", json=req_payload, timeout=15)
        r.raise_for_status()
        puzzle_response = r.json()
    except Exception as e:
        raise Exception(f"获取拼图失败: {e}")
    if "puzzle" not in puzzle_response:
        raise Exception("响应中无拼图数据")
    try:
        solver = CaptchaSolver()
        answer = solver.solve(puzzle_response)
    except Exception as e:
        raise Exception(f"解答拼图失败: {e}")
    verify_payload = {
        "id": puzzle_response["id"],
        "answer": answer
    }
    try:
        v = session.post(f"{SENTRY_BASE}/verify", json=verify_payload, timeout=15)
        v.raise_for_status()
        verify_result = v.json()
    except Exception as e:
        raise Exception(f"验证失败: {e}")
    return session

@register("delta_bypass", "YourName", "Delta 卡密自动获取插件，支持人机验证绕过", "1.0.0")
class DeltaBypass(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.switch_key = "delta_bypass_enabled"

    @filter.command("delta")
    async def delta(self, event: AstrMessageEvent):
        parts = event.message_str.strip().split()
        if len(parts) < 2:
            yield event.plain_result("用法：/delta on 开启，/delta off 关闭")
            return
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("请在群聊中使用")
            return
        if parts[1].lower() == "on":
            await self.context.update_group_permission(group_id, self.switch_key, "1")
            yield event.plain_result("Delta 绕过已开启")
        elif parts[1].lower() == "off":
            await self.context.update_group_permission(group_id, self.switch_key, "0")
            yield event.plain_result("Delta 绕过已关闭")
        else:
            yield event.plain_result("用法：/delta on 开启，/delta off 关闭")

    @filter.command("getkey")
    async def getkey(self, event: AstrMessageEvent):
        parts = event.message_str.strip().split(maxsplit=1)
        if len(parts) < 2:
            yield event.plain_result("请提供链接，例如：/getkey https://auth.platorelay.com/a?d=...")
            return

        raw_url = parts[1].strip()
        yield event.plain_result(f"⏳ 检测到 Delta 链接，开始分析...")

        start_time = time.time()
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })

        try:
            target_url = decode_base64_url(raw_url)
            yield event.plain_result(f"🔍 目标地址: {target_url}")

            resp = session.get(target_url, timeout=15)
            if resp.status_code != 200:
                elapsed = time.time() - start_time
                yield event.plain_result(f"❌ 页面访问失败，状态码: {resp.status_code}（耗时 {elapsed:.2f} 秒）")
                return

            if 'sentry' in resp.url or 'captcha' in resp.text.lower():
                yield event.plain_result("🛡️ 检测到验证码，尝试绕过...")
                try:
                    session = bypass_captcha(session)
                except Exception as e:
                    elapsed = time.time() - start_time
                    yield event.plain_result(f"❌ 验证码绕过失败: {e}（耗时 {elapsed:.2f} 秒）")
                    return
                resp = session.get(target_url, timeout=15)
                if resp.status_code != 200:
                    elapsed = time.time() - start_time
                    yield event.plain_result(f"❌ 验证后页面访问失败，状态码: {resp.status_code}（耗时 {elapsed:.2f} 秒）")
                    return

            card_key = extract_card_key(resp.text)
            elapsed = time.time() - start_time
            if card_key:
                user_name = event.get_sender_name()
                at_user = f"[At,qq={event.get_sender_id()},name={user_name}]"
                msg = f"{at_user}\n您的\nDelta卡密: {card_key}\n耗时: {elapsed:.2f}秒\n完成啦可以加入我们\n请不要频繁发送Delta链接\n可以加入大寒脚本主群吗 {AD_URL}"
                yield event.plain_result(msg)
            else:
                elapsed = time.time() - start_time
                yield event.plain_result(f"❌ 未找到卡密（耗时 {elapsed:.2f} 秒）")

        except Exception as e:
            elapsed = time.time() - start_time
            yield event.plain_result(f"❌ 处理异常: {e}（耗时 {elapsed:.2f} 秒）")

    @filter.on_decorating_result()
    async def on_msg(self, event: AstrMessageEvent):
        if not event.is_group:
            return
        group_id = event.get_group_id()
        if not group_id:
            return
        enabled = await self.context.get_group_permission(group_id, self.switch_key)
        if enabled != "1":
            return

        content = event.message_str
        found_url = None
        for domain in DETECT_DOMAINS:
            pattern = rf'(?:https?://)?{re.escape(domain)}[^\s]+'
            match = re.search(pattern, content)
            if match:
                found_url = match.group()
                break

        if not found_url:
            return

        if not found_url.startswith(('http://', 'https://')):
            found_url = 'https://' + found_url

        yield event.plain_result(f"检测到 Delta 链接 请等待1min(៸៸᳐⦁⩊⦁៸៸᳐ )੭连续发私募\n可以加入大寒脚本主群吗 {AD_URL}")

        start_time = time.time()
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })

        try:
            target_url = decode_base64_url(found_url)
            logger.info(f"目标地址: {target_url}")

            resp = session.get(target_url, timeout=15)
            if resp.status_code != 200:
                elapsed = time.time() - start_time
                yield event.plain_result(f"❌ 页面访问失败，状态码: {resp.status_code}（耗时 {elapsed:.2f} 秒）")
                return

            if 'sentry' in resp.url or 'captcha' in resp.text.lower():
                yield event.plain_result("🛡️ 检测到验证码，尝试绕过...")
                try:
                    session = bypass_captcha(session)
                except Exception as e:
                    elapsed = time.time() - start_time
                    yield event.plain_result(f"❌ 验证码绕过失败: {e}（耗时 {elapsed:.2f} 秒）")
                    return
                resp = session.get(target_url, timeout=15)
                if resp.status_code != 200:
                    elapsed = time.time() - start_time
                    yield event.plain_result(f"❌ 验证后页面访问失败，状态码: {resp.status_code}（耗时 {elapsed:.2f} 秒）")
                    return

            card_key = extract_card_key(resp.text)
            elapsed = time.time() - start_time
            if card_key:
                user_name = event.get_sender_name()
                at_user = f"[At,qq={event.get_sender_id()},name={user_name}]"
                msg = f"{at_user}\n您的\nDelta卡密: {card_key}\n耗时: {elapsed:.2f}秒\n完成啦可以加入我们\n请不要频繁发送Delta链接\n可以加入大寒脚本主群吗 {AD_URL}"
                yield event.plain_result(msg)
            else:
                elapsed = time.time() - start_time
                yield event.plain_result(f"❌ 未找到卡密（耗时 {elapsed:.2f} 秒）")

        except Exception as e:
            elapsed = time.time() - start_time
            yield event.plain_result(f"❌ 处理异常: {e}（耗时 {elapsed:.2f} 秒）")
