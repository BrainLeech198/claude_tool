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
