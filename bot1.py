# -*- coding: utf-8 -*-
# Railway XMPP 反刷屏机器人（修复版）
# 功能：反刷屏 + HTTP Pin Server + 自动重连 + 稳定性增强
# 修复点：使用 await xmpp.disconnected 代替 process(forever=True)

import asyncio
import time
import logging
from typing import Dict
import os
import slixmpp
from slixmpp import jid
from aiohttp import web

# ========== 基础配置 ==========
BOT_JID = os.getenv("BOT_JID")
BOT_PASSWORD = os.getenv("BOT_PASSWORD")
ROOM_JID = os.getenv("ROOM_JID")
ROOM_NICK = os.getenv("ROOM_NICK")

# ========== 反刷屏参数 ==========
MAX_FREQ_COUNT = 5
MAX_REPEAT_COUNT = 4
FAST_INTERVAL = 5
MIN_SPAM_LENGTH = 4
MAX_SPAM_COUNT = 5
CLEAN_CACHE_TIME = 1800
MAX_USERS = 1000
MAX_MESSAGE_LENGTH = 500

# ===== HTTP Pin Server =====
async def handle_ping(request):
    return web.Response(text="ok")

async def start_http_server():
    app = web.Application()
    app.add_routes([web.get("/ping", handle_ping)])
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"✅ HTTP pin server running on port {port}")

# ===== 用户状态 =====
class UserInfo:
    __slots__ = ("msg_times", "last_msg", "repeat_count")
    def __init__(self):
        self.msg_times = []
        self.last_msg = ""
        self.repeat_count = 0

# ===== 单条消息刷屏检测 =====
def has_spam_pattern(text: str) -> bool:
    if len(text) < MIN_SPAM_LENGTH * MAX_SPAM_COUNT:
        return False
    max_check_length = min(20, len(text) // 2)
    for length in range(MIN_SPAM_LENGTH, max_check_length + 1):
        counter = {}
        for i in range(len(text) - length + 1):
            substr = text[i:i + length]
            counter[substr] = counter.get(substr, 0) + 1
            if counter[substr] >= MAX_SPAM_COUNT:
                return True
    return False

# ===== 机器人类 =====
class AntiSpamBot(slixmpp.ClientXMPP):
    def __init__(self):
        super().__init__(BOT_JID, BOT_PASSWORD)
        self.users: Dict[str, UserInfo] = {}
        self.last_cleanup = time.time()

        # 事件
        self.add_event_handler("session_start", self.start)
        self.add_event_handler("groupchat_message", self.on_message)
        self.add_event_handler("disconnected", self.on_disconnect)
        self.add_event_handler("failed_auth", self.on_failed_auth)

        # Keepalive
        self.register_plugin("xep_0199")
        self["xep_0199"].enable_keepalive(interval=60, timeout=10)

    async def start(self, event):
        self.send_presence()

        # roster 请求加超时
        try:
            await asyncio.wait_for(self.get_roster(), timeout=30)
        except asyncio.TimeoutError:
            logging.warning("⚠ Roster 请求超时")

        await asyncio.sleep(2)

        # MUC join 自动重试
        joined = False
        for attempt in range(5):
            try:
                await asyncio.wait_for(
                    self.plugin["xep_0045"].join_muc(ROOM_JID, ROOM_NICK),
                    timeout=15
                )
                joined = True
                logging.warning("✅ Bot 已上线")
                break
            except asyncio.TimeoutError:
                logging.warning(f"⚠ MUC join 超时，第 {attempt + 1} 次重试")
                await asyncio.sleep(5)
            except Exception as e:
                logging.error(f"加入群聊异常: {e}")
                await asyncio.sleep(5)

        if not joined:
            logging.error("❌ Bot 未能加入群聊，稍后主循环会重试")

        # 启动缓存清理
        asyncio.create_task(self.clean_cache())

    async def on_message(self, msg):
        if msg["from"].bare != ROOM_JID:
            return
        nick = msg["mucnick"]
        if not nick or nick == ROOM_NICK:
            return
        body = msg["body"]
        if not body or len(body) > MAX_MESSAGE_LENGTH:
            return

        user_jid = self.get_user_jid(ROOM_JID, nick)
        if not user_jid:
            return

        if len(self.users) > MAX_USERS:
            self.users.clear()

        info = self.users.setdefault(user_jid, UserInfo())
        now = time.time()

        # 单条消息刷屏
        if has_spam_pattern(body):
            await self.kick(user_jid, nick, "单条消息刷屏")
            return

        # 频率检测
        info.msg_times = [t for t in info.msg_times if now - t < FAST_INTERVAL]
        info.msg_times.append(now)
        if len(info.msg_times) >= MAX_FREQ_COUNT:
            await self.kick(user_jid, nick, "发送过快")
            return

        # 连续重复检测
        if body == info.last_msg:
            info.repeat_count += 1
        else:
            info.repeat_count = 1
            info.last_msg = body
        if info.repeat_count >= MAX_REPEAT_COUNT:
            await self.kick(user_jid, nick, "重复刷屏")

    async def kick(self, user_jid: str, nick: str, reason: str):
        try:
            await self.plugin["xep_0045"].set_role(
                ROOM_JID, nick, role="none", reason=reason
            )
            await self.plugin["xep_0045"].set_affiliation(
                ROOM_JID, "outcast", jid=user_jid
            )
            self.send_message(
                mto=ROOM_JID,
                mbody=f"{nick} 被移除：{reason}",
                mtype="groupchat"
            )
            logging.warning(f"KICK: {nick}")
            self.users.pop(user_jid, None)
        except Exception as e:
            logging.error(f"踢人失败: {e}")

    def get_user_jid(self, room: str, nick: str):
        try:
            jid_ = self.plugin["xep_0045"].get_jid_property(room, nick, "jid")
            return jid.JID(jid_).bare if jid_ else None
        except Exception:
            return None

    async def clean_cache(self):
        while True:
            await asyncio.sleep(CLEAN_CACHE_TIME)
            self.users.clear()
            logging.warning("缓存清理")

    def on_disconnect(self, event):
        logging.warning("⚠ 掉线")

    def on_failed_auth(self, event):
        logging.error("❌ 登录失败")
        self.disconnect()

# ===== 主入口 =====
async def run_bot():
    await start_http_server()

    while True:
        try:
            xmpp = AntiSpamBot()
            xmpp.register_plugin("xep_0030")
            xmpp.register_plugin("xep_0045")

            # 关键修复：同步 connect + 等待 disconnected Future
            xmpp.connect()
            await xmpp.disconnected  # 断开时自动继续（官方推荐方式）

        except Exception as e:
            logging.error(f"主循环异常: {e}")
        await asyncio.sleep(5)  # 重连间隔

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.WARNING,  # 建议临时改成 INFO 看更多日志
        format="%(asctime)s %(levelname)s: %(message)s"
    )
    asyncio.run(run_bot())
