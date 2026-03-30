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
