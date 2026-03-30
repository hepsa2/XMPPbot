/**
 * XMPP Bot Keep-Alive Worker
 * 目标: https://xxxxc.up.railway.app/ping
 *
 * 策略：每 8 分钟触发一次 Cron，加入 0~90 秒随机抖动，
 *       制造不定时效果；失败自动重试一次。
 *       夜间（00:00-06:00 UTC+8）降频至 50% 概率跳过，节省额度。
 */

const TARGET_URL  = "https://xmppbot-production.up.railway.app/ping";
const TIMEOUT_MS  = 8_000;   // 单次请求超时
const JITTER_MAX  = 90_000;  // 最大随机延迟（毫秒）
const NIGHT_START = 16;      // UTC 16:00 = SGT/CST 00:00
const NIGHT_END   = 22;      // UTC 22:00 = SGT/CST 06:00

// ── 工具：带超时的 fetch ────────────────────────────────────────
async function fetchWithTimeout(url, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, {
      method: "GET",
      signal: controller.signal,
      headers: { "User-Agent": "CF-KeepAlive/1.0" },
    });
    return { ok: res.ok, status: res.status };
  } catch (err) {
    return { ok: false, status: 0, error: err.message };
  } finally {
    clearTimeout(timer);
  }
}

// ── 工具：sleep ────────────────────────────────────────────────
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ── 核心 ping 逻辑（含一次重试）──────────────────────────────
async function doPing(log) {
  // 第一次尝试
  let result = await fetchWithTimeout(TARGET_URL, TIMEOUT_MS);
  log.push(`[attempt 1] status=${result.status} ok=${result.ok}${result.error ? " err=" + result.error : ""}`);

  // 失败则等 3 秒后重试一次
  if (!result.ok) {
    await sleep(3_000);
    result = await fetchWithTimeout(TARGET_URL, TIMEOUT_MS);
    log.push(`[attempt 2] status=${result.status} ok=${result.ok}${result.error ? " err=" + result.error : ""}`);
  }

  return result.ok;
}

// ── 判断当前是否处于夜间节能窗口 ─────────────────────────────
function isNightWindow(utcHour) {
  return utcHour >= NIGHT_START || utcHour < NIGHT_END;
}

// ── Scheduled 入口（Cron 触发）────────────────────────────────
async function handleScheduled(event) {
  const now     = new Date();
  const utcHour = now.getUTCHours();
  const log     = [`[${now.toISOString()}] Cron fired`];
  let   skipped = false;

  // 夜间 50% 概率跳过，节省 Railway 额度
  if (isNightWindow(utcHour) && Math.random() < 0.5) {
    log.push(`[night-skip] UTC ${utcHour}h, skipping this round`);
    skipped = true;
  }

  if (!skipped) {
    // 随机抖动（制造不定时效果）
    const jitter = Math.floor(Math.random() * JITTER_MAX);
    log.push(`[jitter] sleeping ${(jitter / 1000).toFixed(1)}s`);
    await sleep(jitter);

    const success = await doPing(log);
    log.push(success ? "✅ ping ok" : "❌ ping failed after retry");
  }

  // Cloudflare 不提供原生 console 持久化，用 console.log 写入实时日志
  console.log(log.join(" | "));
}

// ── HTTP Fetch 入口（手动触发 / 健康检查）─────────────────────
async function handleFetch(request) {
  const url = new URL(request.url);

  // GET /ping → 立即执行一次 ping，返回结果
  if (url.pathname === "/ping") {
    const log     = [];
    const success = await doPing(log);
    const body    = JSON.stringify({
      target:  TARGET_URL,
      success,
      detail:  log,
      time:    new Date().toISOString(),
    }, null, 2);
    return new Response(body, {
      status:  success ? 200 : 502,
      headers: { "Content-Type": "application/json" },
    });
  }

  // GET / → 状态页
  const body = `XMPP Bot Keep-Alive Worker
  Target : ${TARGET_URL}
  Cron   : every 8 min (+ 0~90s jitter)
  Night  : 50% skip between UTC ${NIGHT_START}:00 ~ ${NIGHT_END}:00
  Manual : GET /ping`;

  return new Response(body, { headers: { "Content-Type": "text/plain" } });
}

// ── 导出 ──────────────────────────────────────────────────────
export default {
  fetch:     handleFetch,
  scheduled: handleScheduled,
};
