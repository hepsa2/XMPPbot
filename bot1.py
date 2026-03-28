# -*- coding: utf-8 -*-
# Railway XMPP 反刷屏机器人（优化版）
# 修复与优化点：
# 1. 禁用 slixmpp 内置自动重连，防止双实例并发
# 2. on_session_start 幂等保护，防止重复加入群聊
# 3. 去除冗余 whitespace keepalive（只保留 xep_0199）
# 4. 去除不必要的 Roster 请求，节省每次重连流量
# 5. keepalive 间隔放宽至 120 秒，减少心跳流量
# 6. deque 加 maxlen 限制，防止极端情况内存膨胀
# 7. 重连时主动 del + gc，彻底释放旧实例内存

import asyncio
import time
import logging
from typing import Dict, Optional
import os
import gc
from collections import deque
import slixmpp
from slixmpp import jid
from aiohttp import web

# ========== 基础配置 ==========
BOT_JID       = os.getenv("BOT_JID")
BOT_PASSWORD  = os.getenv("BOT_PASSWORD")
ROOM_JID      = os.getenv("ROOM_JID")
ROOM_NICK     = os.getenv("ROOM_NICK")

# ========== 反刷屏参数 ==========
MAX_FREQ_COUNT    = 5     # N 秒内最多发几条
MAX_REPEAT_COUNT  = 4     # 重复消息最多几次
FAST_INTERVAL     = 5     # 频率检测窗口（秒）
MIN_SPAM_LENGTH   = 4     # 单条刷屏检测最短子串
MAX_SPAM_COUNT    = 5     # 子串重复次数阈值
CLEAN_CACHE_TIME  = 1800  # 缓存清理间隔（秒）
CACHE_EXPIRE_TIME = 3600  # 用户数据过期时间（秒）
MAX_USERS         = 1000  # 最大缓存用户数
MAX_MESSAGE_LENGTH= 500   # 忽略超长消息


# ===== HTTP Ping Server（防 Railway 休眠）=====
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
    logging.info(f"✅ HTTP ping server 运行在端口 {port}")


# ===== 用户状态 =====
class UserInfo:
    __slots__ = ("msg_times", "last_msg", "repeat_count", "last_active")
    def __init__(self):
        # ✅ 优化：加 maxlen，防止极端情况下 deque 无限增长占用内存
        self.msg_times    = deque(maxlen=MAX_FREQ_COUNT + 1)
        self.last_msg     = ""
        self.repeat_count = 0
        self.last_active  = time.time()


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


# ===== 自定义异常处理器 =====
def custom_exception_handler(loop, context):
    message = context.get("message", "")
    if "socket.send() raised exception" in message or "socket.send" in message:
        logging.debug(f"忽略 socket.send 异常: {context.get('exception')}")
        return
    loop.default_exception_handler(context)


# ===== Bot 主体 =====
class AntiSpamBot(slixmpp.ClientXMPP):
    def __init__(self):
        super().__init__(BOT_JID, BOT_PASSWORD)
        self.users: Dict[str, UserInfo] = {}
        self.is_joined    = False
        self.cleanup_task: Optional[asyncio.Task] = None
        self.start_time   = time.time()

        # ✅ 关键修复：禁用内置自动重连，完全由外层 while True 统一管理
        self.auto_reconnect = False

        self.add_event_handler("session_start",     self.on_session_start)
        self.add_event_handler("groupchat_message", self.on_message)
        self.add_event_handler(
            "muc::%s::got_online" % ROOM_JID, self.on_muc_online
        )
        self.add_event_handler("disconnected",      self.on_disconnect)
        self.add_event_handler("failed_auth",       self.on_failed_auth)
        self.add_event_handler("connection_failed", self.on_connection_failed)

        # ✅ 优化：只保留 xep_0199 心跳，去掉冗余的 whitespace_keepalive
        #         间隔放宽至 120 秒，减少不必要的心跳流量
        self.register_plugin("xep_0199")
        self["xep_0199"].enable_keepalive(interval=120, timeout=60)

    async def on_session_start(self, event):
        # ✅ 幂等保护：已加入时跳过，防止 slixmpp 内部重复触发
        if self.is_joined:
            logging.warning("⚠️ on_session_start 重复触发，跳过")
            return

        uptime = int(time.time() - self.start_time)
        logging.info(f"🔗 会话已建立 (运行时间: {uptime}秒)")
        self.send_presence()

        # ✅ 优化：群聊机器人不需要 Roster，去除该请求节省重连流量

        await asyncio.sleep(1)
        await self.join_room_with_retry()

        # ✅ 幂等保护：确保全局只有一个清理任务运行
        if self.cleanup_task is None or self.cleanup_task.done():
            self.cleanup_task = asyncio.create_task(self.clean_cache())
            logging.info("🧹 缓存清理任务已启动")
        else:
            logging.info("🧹 缓存清理任务已存在，跳过")

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
            except Exception as e:
                logging.error(f"❌ 加入群聊异常: {e}")
            await asyncio.sleep(3 * (attempt + 1))

        logging.error("❌ 无法加入群聊，所有重试均失败")
        self.is_joined = False
        return False

    def on_muc_online(self, presence):
        nick = presence["muc"]["nick"]
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
            logging.warning(f"⚠️ 用户数超限 ({len(self.users)})，执行紧急清理")
            await self.clean_old_users()

        info = self.users.setdefault(user_jid, UserInfo())
        now  = time.time()
        info.last_active = now

        # 检查单条刷屏
        if has_spam_pattern(body):
            await self.kick(user_jid, nick, "单条消息刷屏")
            return

        # 检查发送频率（仍需手动过滤超出时间窗口的记录）
        while info.msg_times and now - info.msg_times[0] >= FAST_INTERVAL:
            info.msg_times.popleft()
        info.msg_times.append(now)
        if len(info.msg_times) >= MAX_FREQ_COUNT:
            await self.kick(user_jid, nick, "发送过快")
            return

        # 检查重复消息
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
            uid for uid, info in self.users.items()
            if now - info.last_active > CACHE_EXPIRE_TIME
        ]
        for uid in to_remove:
            self.users.pop(uid, None)
        if to_remove:
            logging.info(
                f"🧹 清理 {len(to_remove)} 个过期用户，剩余 {len(self.users)} 个"
            )

    async def clean_cache(self):
        loop_count = 0
        while True:
            try:
                await asyncio.sleep(CLEAN_CACHE_TIME)
                loop_count += 1
                await self.clean_old_users()
                if loop_count % 3 == 0:
                    gc.collect()
                uptime = int(time.time() - self.start_time)
                logging.info(
                    f"📊 状态: 用户={len(self.users)}, "
                    f"已加入={self.is_joined}, 运行={uptime}秒"
                )
            except asyncio.CancelledError:
                logging.info("🧹 缓存清理任务已取消")
                break
            except Exception as e:
                logging.error(f"❌ 清理任务异常: {e}")

    def on_disconnect(self, event):
        uptime = int(time.time() - self.start_time)
        logging.warning(f"⚠️ 连接断开 (运行时间: {uptime}秒)")
        self.is_joined = False
        if self.cleanup_task and not self.cleanup_task.done():
            self.cleanup_task.cancel()

    def on_connection_failed(self, event):
        logging.error(f"❌ 连接失败: {event}")

    def on_failed_auth(self, event):
        logging.error("❌ 认证失败 - 请检查 BOT_JID / BOT_PASSWORD")
        self.disconnect()

    def safe_disconnect(self):
        """安全断开，忽略已断开时的异常"""
        try:
            if self.is_connected():
                self.disconnect()
        except Exception:
            pass


# ===== 主入口 =====
async def run_bot():
    await start_http_server()

    reconnect_delay      = 5.0
    max_delay            = 120.0
    consecutive_failures = 0

    while True:
        bot: Optional[AntiSpamBot] = None
        try:
            logging.info("🤖 正在启动机器人...")
            bot = AntiSpamBot()
            bot.register_plugin("xep_0030")
            bot.register_plugin("xep_0045")

            connected = bot.connect()
            if not connected:
                raise ConnectionError("服务器拒绝连接")

            logging.info("✅ 正在连接服务器，等待会话建立...")
            consecutive_failures = 0
            reconnect_delay      = 5.0

            # 阻塞直到本次连接完全断开
            await bot.disconnected

        except ConnectionError as e:
            logging.error(f"❌ {e}")
            consecutive_failures += 1

        except asyncio.CancelledError:
            logging.info("👋 Bot 任务被取消")
            if bot:
                bot.safe_disconnect()
            break

        except Exception as e:
            logging.error(
                f"❌ 主循环异常: {type(e).__name__}: {e}", exc_info=True
            )
            consecutive_failures += 1

        finally:
            # ✅ 保证旧实例完全销毁后再重连，防止双实例并发
            if bot:
                bot.safe_disconnect()
                await asyncio.sleep(2)
                del bot
                gc.collect()

        if consecutive_failures > 0:
            reconnect_delay = min(reconnect_delay * 1.5, max_delay)
            logging.warning(
                f"⏰ {int(reconnect_delay)}秒后重连 "
                f"(连续失败: {consecutive_failures}次)"
            )

        await asyncio.sleep(reconnect_delay)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    required_vars = ["BOT_JID", "BOT_PASSWORD", "ROOM_JID", "ROOM_NICK"]
    missing = [v for v in required_vars if not os.getenv(v)]
    if missing:
        logging.error(f"❌ 缺少环境变量: {', '.join(missing)}")
        exit(1)

    logging.info("=" * 50)
    logging.info("🚀 XMPP 反刷屏机器人启动")
    logging.info(f"📧 JID: {BOT_JID}")
    logging.info(f"🏠 群聊: {ROOM_JID}")
    logging.info(f"👤 昵称: {ROOM_NICK}")
    logging.info("=" * 50)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.set_exception_handler(custom_exception_handler)

    try:
        loop.run_until_complete(run_bot())
    except KeyboardInterrupt:
        logging.info("👋 收到退出信号，程序关闭")
    finally:
        loop.close()
