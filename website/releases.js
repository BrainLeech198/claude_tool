// 官网的版本清单，最新的排在最前面。
//
// 平时不用手改：跑 build/打包.bat 的时候，最后一步会调 build/update_releases.py，
// 把刚打出来的这一版补进来（同一个版本号只更新、不重复，已经写过的说明保留）。
// 手改也行，格式就这么简单。
//
// 用 .js 不用 .json：json 得走 fetch，本地双击打开会被 CORS 拦掉、页面直接空一片。
// 这么写是同步加载的，挂在 Pages 上也一样，永远不会出现"加载中"。

// 安装包放在 Gitee 的「发行版」附件里，每版一个 tag（v0.1.0），附件名就是 Output 里
// 那个 setup.exe 的名字。以后要是加镜像源，改这一行就行。
window.DOWNLOAD_BASE = "https://gitee.com/Mr_brainleech/claude_tool/releases/download";

// 一条记录 = 一个版本。windows / linux / macos 三个字段，有包就填成 {file, size}，
// 还没有就写 null——页面上那一格会自动画成灰的「还没做」。以后 Linux 的包出来了，
// 把 null 换成 {"file": "...", "size": 12345} 就行，页面一个字都不用动。
//
// 包里还能再填一个可选的 url：填了就直接拿它当下载地址，不填才按上面的
// DOWNLOAD_BASE 拼。指到别处（镜像源、别的盘）就填这个。重新打包不会把它抹掉。
window.RELEASES = [
  {
    "version": "0.1.0",
    "date": "2026-09-19",
    "notes": "第一个能转发出去给人装的版本。",
    "windows": {
      "file": "ClaudeLauncher-0.1.0-Setup.exe",
      "size": 10145146
    },
    "linux": null,
    "macos": null
  }
];
