# -*- coding: utf-8 -*-
# Railway XMPP 反刷屏机器人（稳定增强版）
# 改进点：
# 1. 优化 keepalive（降低频率 + whitespace keepalive）
# 2. 添加自定义异常处理器，过滤 socket.send() 警告
# 3. 增强日志记录，便于诊断连接问题
# 4. 保持原有结构和反刷屏逻辑完全不变

import asyncio
import time
import logging
from typing import Dict
import os
import gc
from collections import deque
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
CLEAN_CACHE_TIME = 1800  # 30分钟
CACHE_EXPIRE_TIME = 3600  # 1小时后过期的用户数据
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
    logging.info(f"✅ HTTP pin server running on port {port}")

# ===== 用户状态（增加最后活动时间）=====
class UserInfo:
    __slots__ = ("msg_times", "last_msg", "repeat_count", "last_active")
    def __init__(self):
        self.msg_times = deque()
        self.last_msg = ""
        self.repeat_count = 0
        self.last_active = time.time()

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

# ===== 自定义异常处理器：过滤 socket.send() 警告 =====
def custom_exception_handler(loop, context):
    message = context.get('message', '')
    if 'socket.send() raised exception' in message or 'socket.send' in message:
        # 降级为 debug，避免日志刷屏，但仍记录
        logging.debug(f"忽略 socket.send 异常（连接短暂不稳定）: {context.get('exception')}")
        return
    # 其他异常走默认处理
    loop.default_exception_handler(context)

# ===== 机器人类（稳定性增强）=====
class AntiSpamBot(slixmpp.ClientXMPP):
    def __init__(self):
        super().__init__(BOT_JID, BOT_PASSWORD)
        self.users: Dict[str, UserInfo] = {}
        self.last_cleanup = time.time()
        self.is_joined = False
        self.cleanup_task = None
        self.start_time = time.time()

        # 事件
        self.add_event_handler("session_start", self.start)
        self.add_event_handler("groupchat_message", self.on_message)
        self.add_event_handler("muc::%s::got_online" % ROOM_JID, self.on_muc_online)
        self.add_event_handler("disconnected", self.on_disconnect)
        self.add_event_handler("failed_auth", self.on_failed_auth)
        self.add_event_handler("connection_failed", self.on_connection_failed)

        # Keepalive 配置优化
        self.register_plugin("xep_0199")
        self["xep_0199"].enable_keepalive(interval=60, timeout=40)  # 降低频率

        # 启用 whitespace keepalive（更轻量）
        self.whitespace_keepalive = True
        self.whitespace_keepalive_interval = 60

    async def start(self, event):
        logging.info(f"🔗 会话已建立 (运行时间: {int(time.time() - self.start_time)}秒)")
        
        self.send_presence()
        
        try:
            await asyncio.wait_for(self.get_roster(), timeout=30)
            logging.info("✅ Roster 获取成功")
        except asyncio.TimeoutError:
            logging.warning("⚠️ Roster 请求超时")
        except Exception as e:
            logging.warning(f"⚠️ Roster 异常: {e}")

        await asyncio.sleep(1)
        await self.join_room_with_retry()

        if self.cleanup_task is None or self.cleanup_task.done():
            self.cleanup_task = asyncio.create_task(self.clean_cache())
            logging.info("🧹 缓存清理任务已启动")

    async def join_room_with_retry(self):
        for attempt in range(5):
            try:
                logging.info(f"🚪 尝试加入群聊 (第 {attempt + 1} 次)")
                await asyncio.wait_for(
                    self.plugin["xep_0045"].join_muc(ROOM_JID, ROOM_NICK),
                    timeout=20
                )
                self.is_joined = True
                logging.info("✅ 成功加入群聊")
                return True
            except asyncio.TimeoutError:
                logging.warning(f"⚠️ MUC join 超时 (尝试 {attempt + 1}/5)")
                await asyncio.sleep(3 * (attempt + 1))
            except Exception as e:
                logging.error(f"❌ 加入群聊异常: {e}")
                await asyncio.sleep(3 * (attempt + 1))

        logging.error("❌ 无法加入群聊，所有重试均失败")
        self.is_joined = False
        return False

    def on_muc_online(self, presence):
        nick = presence['muc']['nick']
        if nick == ROOM_NICK:
            logging.info(f"✅ Bot 已在群聊中上线 (昵称: {nick})")
            self.is_joined = True

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
            logging.warning(f"⚠️ 用户数超限 ({len(self.users)}), 执行清理")
            await self.clean_old_users()

        info = self.users.setdefault(user_jid, UserInfo())
        now = time.time()
        info.last_active = now

        if has_spam_pattern(body):
            await self.kick(user_jid, nick, "单条消息刷屏")
            return

        while info.msg_times and now - info.msg_times[0] >= FAST_INTERVAL:
            info.msg_times.popleft()
        info.msg_times.append(now)
        if len(info.msg_times) >= MAX_FREQ_COUNT:
            await self.kick(user_jid, nick, "发送过快")
            return

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
            logging.info(f"🚫 KICK: {nick} ({reason})")
            self.users.pop(user_jid, None)
        except Exception as e:
            logging.error(f"❌ 踢人失败: {e}")

    def get_user_jid(self, room: str, nick: str):
        try:
            jid_ = self.plugin["xep_0045"].get_jid_property(room, nick, "jid")
            return jid.JID(jid_).bare if jid_ else None
        except Exception:
            return None

    async def clean_old_users(self):
        now = time.time()
        to_remove = [
            user_jid for user_jid, info in self.users.items()
            if now - info.last_active > CACHE_EXPIRE_TIME
        ]
        for user_jid in to_remove:
            self.users.pop(user_jid, None)
        
        if to_remove:
            logging.info(f"🧹 清理 {len(to_remove)} 个过期用户, 剩余 {len(self.users)} 个")

    async def clean_cache(self):
        loop_count = 0
        while True:
            try:
                await asyncio.sleep(CLEAN_CACHE_TIME)
                loop_count += 1
                
                await self.clean_old_users()
                
                if loop_count % 3 == 0:
                    gc.collect()
                    logging.info(f"🧹 执行垃圾回收 (运行时间: {int(time.time() - self.start_time)}秒)")
                
                logging.info(f"📊 状态: 用户={len(self.users)}, 已加入={self.is_joined}, 运行={int(time.time() - self.start_time)}秒")
                
            except asyncio.CancelledError:
                logging.info("🧹 缓存清理任务被取消")
                break
            except Exception as e:
                logging.error(f"❌ 清理任务异常: {e}")

    def on_disconnect(self, event):
        uptime = int(time.time() - self.start_time)
        logging.warning(f"⚠️ 连接断开 (运行时间: {uptime}秒)")
        self.is_joined = False

    def on_connection_failed(self, event):
        logging.error(f"❌ 连接失败: {event}")

    def on_failed_auth(self, event):
        logging.error("❌ 认证失败 - 请检查账号密码")
        self.disconnect()

# ===== 主入口（改进重连机制）=====
async def run_bot():
    await start_http_server()
    
    reconnect_delay = 5
    max_delay = 60
    consecutive_failures = 0

    while True:
        try:
            logging.info("🤖 启动机器人...")
            xmpp = AntiSpamBot()
            xmpp.register_plugin("xep_0030")
            xmpp.register_plugin("xep_0045")

            connected = await asyncio.wait_for(
                asyncio.to_thread(xmpp.connect),
                timeout=30
            )
            
            if not connected:
                raise Exception("连接失败")
            
            logging.info("✅ 已连接到服务器")
            consecutive_failures = 0
            reconnect_delay = 5
            
            await xmpp.disconnected

        except asyncio.TimeoutError:
            logging.error("❌ 连接超时")
            consecutive_failures += 1
        except Exception as e:
            logging.error(f"❌ 主循环异常: {type(e).__name__}: {e}", exc_info=True)
            consecutive_failures += 1
        
        if consecutive_failures > 0:
            reconnect_delay = min(reconnect_delay * 1.5, max_delay)
            logging.warning(f"⏰ {int(reconnect_delay)}秒后重连 (失败次数: {consecutive_failures})")
        
        await asyncio.sleep(reconnect_delay)

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    # 设置自定义异常处理器
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.set_exception_handler(custom_exception_handler)
    
    required_vars = ["BOT_JID", "BOT_PASSWORD", "ROOM_JID", "ROOM_NICK"]
    missing = [var for var in required_vars if not os.getenv(var)]
    if missing:
        logging.error(f"❌ 缺少环境变量: {', '.join(missing)}")
        exit(1)
    
    logging.info("=" * 50)
    logging.info("🚀 XMPP 反刷屏机器人启动")
    logging.info(f"📧 JID: {BOT_JID}")
    logging.info(f"🏠 群聊: {ROOM_JID}")
    logging.info(f"👤 昵称: {ROOM_NICK}")
    logging.info("=" * 50)
    
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logging.info("👋 收到退出信号，程序关闭")
