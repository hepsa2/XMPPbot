# -*- coding: utf-8 -*-
# Railway  XMPP 反刷屏机器人
# 保持原功能 + 增强稳定性 / 内存控制 / 自动重连

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

CLEAN_CACHE_TIME = 1800   # 更频繁清理
MAX_USERS = 1000          # 防止内存爆
MAX_MESSAGE_LENGTH = 500  # 限制消息长度


# ===== 新增 HTTP Pin Server =====
async def handle_ping(request):
    return web.Response(text="ok")

async def start_http_server():
    app = web.Application()
    app.add_routes([web.get("/ping", handle_ping)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 8000)))  # Railway 用 PORT 环境变量
    await site.start()
    print(f"✅ HTTP pin server running on port {os.getenv('PORT', 8000)}")
# ========== 用户状态 ==========
class UserInfo:
    __slots__ = ("msg_times", "last_msg", "repeat_count")

    def __init__(self):
        self.msg_times = []
        self.last_msg = ""
        self.repeat_count = 0


# ========== 单条消息刷屏检测 ==========
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


# ========== 机器人 ==========
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

        # keepalive
        self.register_plugin("xep_0199")
        self["xep_0199"].enable_keepalive(interval=60, timeout=10)

    async def start(self, event):
        self.send_presence()
        await self.get_roster()

        self.plugin["xep_0045"].join_muc(
            ROOM_JID,
            ROOM_NICK
        )

        self.loop.create_task(self.clean_cache())

        logging.warning("✅ Bot 已上线")

    async def on_message(self, msg):
        if msg["from"].bare != ROOM_JID:
            return

        nick = msg["mucnick"]
        if not nick or nick == ROOM_NICK:
            return

        body = msg["body"]
        if not body:
            return

        # ===== 限制消息长度（防攻击）=====
        if len(body) > MAX_MESSAGE_LENGTH:
            return

        user_jid = self.get_user_jid(ROOM_JID, nick)
        if not user_jid:
            return

        # ===== 限制用户缓存 =====
        if len(self.users) > MAX_USERS:
            self.users.clear()

        info = self.users.setdefault(user_jid, UserInfo())
        now = time.time()

        # ===== 单条消息刷屏 =====
        if has_spam_pattern(body):
            await self.kick(user_jid, nick, "单条消息刷屏")
            return

        # ===== 频率检测 =====
        info.msg_times = [
            t for t in info.msg_times
            if now - t < FAST_INTERVAL
        ]
        info.msg_times.append(now)

        if len(info.msg_times) >= MAX_FREQ_COUNT:
            await self.kick(user_jid, nick, "发送过快")
            return

        # ===== 连续重复 =====
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
                ROOM_JID,
                nick,
                role="none",
                reason=reason
            )

            await self.plugin["xep_0045"].set_affiliation(
                ROOM_JID,
                "outcast",
                jid=user_jid
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
            jid_ = self.plugin["xep_0045"].get_jid_property(
                room,
                nick,
                "jid"
            )
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


# ========== 主入口（关键优化） ==========
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s: %(message)s"
    )
    loop = asyncio.get_event_loop()

    async def main():
        # 启动 HTTP pin server
        await start_http_server()

        while True:
            try:
                xmpp = AntiSpamBot()
                xmpp.register_plugin("xep_0030")
                xmpp.register_plugin("xep_0045")
                if xmpp.connect():
                    await xmpp.process(forever=True)  # 异步方式取代 run_forever
                else:
                    logging.error("连接失败")
            except Exception as e:
                logging.error(f"主循环异常: {e}")
            await asyncio.sleep(5)

    loop.run_until_complete(main())
