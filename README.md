# XMPP防刷屏机器人搭建

> 零成本部署，以手机为例教学
> 使用pin服务+Railway免费计划
> 长期稳定上线，掉线自动重连

**如果使用cloudflare worker做ping**<br>
还需要settings→Trigger Events→cron,填写```*/8 * * * *```
## 准备工作
- 匿名非临时邮箱
- 注册github账号（最好开启2fa验证，F-droid下载Aegis即可获取验证码）
- 通过github注册Railway.com（不推荐直接用邮箱注册）
- 准备好给机器人的XMPP账号，设置和JID（XMPP地址）不一样的昵称
- 提前把机器人拉入预定公开频道，并赋予管理员权限（所有者才可赋予）
## 操作环境

> 以安卓系统手机，Fennec浏览器为例

> 推荐使用尊重隐私的Fennec浏览器，F-droid可下载

## 操作步骤
### 1. github方面
- [点击访问页面](https://github.com/hepsa2/XMPPbot)
![fork仓库](https://raw.githubusercontent.com/hepsa2/aps/refs/heads/main/test/001.jpg)
- 点击该红圈标出部分按钮
![设置](https://raw.githubusercontent.com/hepsa2/aps/refs/heads/main/test/002.jpg)
- 之后选择右下角create fork
- 登陆到你的Railway控制面板，点击新增一个project
- 此时会跳转到github平台，下滑可直接点右下方绿色按钮确认
- 之后会回到Railway网页版，选择你之前fork的仓库名称
### 2. Railway方面
#### 设置环境变量
- 选择项目页面上面一栏的variables
- 右边+new variable
- 你需要新增四个variable

<table border="1" cellspacing="0" cellpadding="6">
  <tr>
    <!-- 左边索引，占4行 -->
    <td rowspan="4">name/value</td>

  </tr>
  <tr>
    <td>第一次添加</td>
    <td>第二次添加</td>
    <td>第三次添加</td>
    <td>第四次添加</td>
  </tr>

  <tr>
    <!-- 第二行的4列 -->
    <td>BOT_JID</td>
    <td>BOT_PASSWORD</td>
    <td>ROOM_JID</td>
    <td>ROOM_NICK</td>
  </tr>

  <tr>
    <!-- 第三行的4列 -->
    <td>机器人账号@xxx.xx</td>
    <td>机器人密码</td>
    <td>频道@xxx.xx.xx</td>
    <td>机器人昵称</td>
  </tr>
</table>

- 然后点击右下角deploy保存设置<br>
- 之后再在上面一栏向左滑动，找到右边的settings,下拉找到Networking<br>
- 出现三个按钮，点击最前面的 ```Generate Domain```<br>
端口号port输入默认的8080<br>
然后保存设置。
- 再点击settings,找到Deploy→custom build command,填写 ```pip install -r requirements.txt```
再在custom start command输入 ```python bot1.py```
### 3. pin服务方面

> 为了防止免费计划中Railway的容器自动休眠，需要配合代码，外部定期pin

在Railway里你的仓库页面Deployments栏目，看到🌏标识，右边还有.up.railway.app这行字。<br>
长按这行字，复制网址，示例如下：
**https://xxx.up.railway.app**
接下来：
**[点击注册平台](https://uptimerobot.com)**<br>
然后建议用github注册账号（register）

点击new,在URL to monitor栏目删去原有内容，把之前复制的xxx.up.railway.app/粘贴到框内，并在末尾加上```ping```
效果是：
**https://xxx.up.railway.app/ping**

然后点击create monitor即可。

再回到Railway,如果显示ACTIVE,那么应该没有问题，XMPP也能正常上线。
如果报错或者出现异常情况，可以点击最前面卡片的右边三个点，选择View logs查看日志。

⚠️注意最好每隔两三个月登陆Railway和uptimerobot.com
以防账号不活跃被系统清除。

遇到问题可以把日志里的报错内容复制给AI（推荐问Claude），寻求帮助。


cf worker代码

```
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
```
