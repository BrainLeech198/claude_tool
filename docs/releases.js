// 官网的版本清单，最新的排在最前面。
//
// 平时不用手改：跑 build/打包.bat 的时候，最后一步会调 build/update_releases.py，
// 把刚打出来的这一版补进来（同一个版本号只更新、不重复，已经写过的说明保留）。
// 手改也行，格式就这么简单。
//
// 用 .js 不用 .json：json 得走 fetch，本地双击打开会被 CORS 拦掉、页面直接空一片。
// 这么写是同步加载的，挂在 Pages 上也一样，永远不会出现"加载中"。

// 安装包放在 Gitee 的「发行版」附件里，每版一个 tag（0.1.0，不带 v 前缀），附件名就是 Output 里
// 那个 setup.exe 的名字。以后要是加镜像源，改这一行就行。
window.DOWNLOAD_BASE = "https://gitee.com/Mr_brainleech/claude_tool/releases/download";

// 同一个安装包在 GitHub 上也传了一份（GitHub 是 Gitee 的仓库镜像，但发行版附件不跟着
// 镜像走，是手动传的）。填了它就多给一个「GitHub 下载」的入口，不填就只有上面那个主源。
// 单独某一版不想给镜像链接，就在那条记录里写 "mirror": null。
window.MIRROR_BASE = "https://github.com/BrainLeech198/claude_tool/releases/download";

// 一条记录 = 一个版本。windows / linux / macos 三个字段，有包就填成 {file, size}，
// 还没有就写 null——页面上那一格会自动画成灰的「还没做」。以后哪个平台的包出来了，
// 把 null 换成 {"file": "...", "size": 12345} 就行，**链接不用在这儿写**：Gitee 和
// GitHub 那两个地址都是拿上面两个 base 加版本号、加文件名现拼的，附件传上去就通。
// 唯一的例外是平台卡上那句说明（比如 Linux 的"64 位，解开放着就能跑"），那份写在
// docs/index.html 的 PLATFORMS 里，跟这儿是两处。
//
// 包里还能再填一个可选的 url：填了就直接拿它当下载地址，不填才按上面的
// DOWNLOAD_BASE 拼。指到别处（镜像源、别的盘）就填这个。重新打包不会把它抹掉。
//
// claude 那一格是"这一版配着哪个版本的 claude 测过的"，打包时自动读打包机上的
// claude --version 写进来（读不到就留空，页面上不显示那一格）。启动器查更新时
// 也拿官网上这个数当兜底（npm 问不到的时候用）——改它没用，那是打包时写死的。
window.RELEASES = [
  {
    "version": "0.4.0",
    "date": "2026-09-24",
    "notes": "主窗整个重做：左边多出一列工作区导航，右边是详情区——工作区那些动作（改名、搬迁、上下挪、删交接文档）原来挤在底下一行里，现在都归到右栏，地方宽裕、也好找。设置从一个弹窗搬进独立窗口，左边分四页（模型／工作区／行为开关／关于），「关于」那页还多了一颗「检查更新」，不用等启动。底下那个「自动查启动器新版」的勾去掉了——现在每次启动自己查，查到就在顶栏给你一颗按钮。另外修了一批版式：页面四边的空档原来有三套数（20／16／14）在混用，左栏和右栏各自凸出去 6 像素，现在统一成 20；右栏几块原来整体错位（日常动作那三个按钮把「管理」那排和插件区挤到右半栏，宽度从 604 压到 339，最宽状态那颗「删交接文档」直接被裁掉看不见），现在各占一行、各归其位。窗口最窄宽度跟着右栏实测重算（713 → 804），最小高度不变。",
    "claude": "2.1.150",
    "windows": {
      "file": "ClaudeLauncher-0.4.0-Setup.exe",
      "size": 10286761
    },
    "linux": null,
    "macos": null
  },
  {
    "version": "0.3.1",
    "date": "2026-09-23",
    "notes": "常用供应商那张表改成配置化：加模型那扇窗里多一格「更新这张表…」，能拿你已经配好的某个预设去打一次 Messages API，让它把当下各家在卖什么、地址是什么列成 JSON；也能从官网拉一份社区维护的同格式清单（GitHub 不通时自动退到 Gitee）。两条路都只回行、不写盘，结果落在可勾选的复核列表里，标出「新」「地址变了；模型 a → b」，这趟没提到的原有行不丢，勾完点「写入」才落盘。文件不在、读坏了、一条合法行都没有，都回退到内置那份，所以升级上来的行为跟以前一样。顺带修了一个真 bug：内置表里有 5 家名字带空格（「智谱 GLM」这种），从下拉里挑一家会把一个存不下去的名字填进「预设名称」那一格，选了却保存不了。",
    "claude": "2.1.150",
    "windows": {
      "file": "ClaudeLauncher-0.3.1-Setup.exe",
      "size": 12877738
    },
    "linux": null,
    "macos": null
  },
  {
    "version": "0.3.0",
    "date": "2026-09-20",
    "notes": "顶栏那颗「有新版」不再只是把你送去官网，点开就是升级：先看你这份 claude 当初是哪条路装的（winget / Homebrew / npm / 官方脚本），把那条对的升级命令填好、预选上，其余每条都写明「不是这条装的，选它会再装一份」——认不出来就不替你选。另外多了一条装法「便携版 Node + npm」：机器上连 Node 都没有时，它把官方那个 Node 便携包整个下到 ~/.claude_tool/node 里，再用它自带的 npm 装 claude，不动系统 PATH。最后修了底下四个勾选框没对齐。",
    "claude": "2.1.150",
    "windows": {
      "file": "ClaudeLauncher-0.3.0-Setup.exe",
      "size": 10242935
    },
    "linux": {
      "file": "ClaudeLauncher-0.3.0-linux-x86_64.tar.gz",
      "size": 13832425
    },
    "macos": null
  },
  {
    "version": "0.2.1",
    "date": "2026-09-20",
    "notes": "修了「帮我装 claude」那个面板：撞上官方那个地址的地区限制、拿回来的是一张网页时，不再把它当脚本喂给解释器，而是直接告诉你去挂代理、或者换 winget / npm 那两条路。",
    "claude": "2.1.150",
    "windows": {
      "file": "ClaudeLauncher-0.2.1-Setup.exe",
      "size": 10236707
    },
    "linux": {
      "file": "ClaudeLauncher-0.2.1-linux-x86_64.tar.gz",
      "size": 13827653
    },
    "macos": null
  },
  {
    "version": "0.2.0",
    "date": "2026-09-20",
    "notes": "没装 claude 时能开个面板挑一种装法装上；启动器自己也认得出有没有新版，点一下就把安装包下下来、问一句再打开。",
    "claude": "2.1.150",
    "windows": {
      "file": "ClaudeLauncher-0.2.0-Setup.exe",
      "size": 10234495
    },
    "linux": {
      "file": "ClaudeLauncher-0.2.0-linux-x86_64.tar.gz",
      "size": 13825319
    },
    "macos": null
  },
  {
    "version": "0.1.0",
    "date": "2026-09-19",
    "notes": "第一个能转发出去给人装的版本。",
    "claude": "2.1.150",
    "windows": {
      "file": "ClaudeLauncher-0.1.0-Setup.exe",
      "size": 10145146
    },
    "linux": {
      "file": "ClaudeLauncher-0.1.0-linux-x86_64.tar.gz",
      "size": 13787980
    },
    "macos": null
  }
];
