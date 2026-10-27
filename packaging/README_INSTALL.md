# Research OS — install / 安装

**双击 `ResearchOS-Setup-<版本>.exe`** → 下一步 → 下一步 → 完成（可勾选“Launch Research OS”）。之后点桌面或开始菜单里的
“Research OS”图标，应用会在浏览器中打开。无需管理员权限，安装时不联网下载（Python 运行时和所有组件已打包在安装程序里）。
**Double-click `ResearchOS-Setup-<version>.exe`** → Next → Next → Finish (tick "Launch Research OS"). Then use the
"Research OS" icon on the Desktop or in the Start Menu; it opens in your browser. No admin rights, no download during
install (the Python runtime and all components are inside the installer).

- 默认目录 `D:\AI\ResearchOS`（没有 D 盘时为 `%LOCALAPPDATA%\ResearchOS`）；日志：安装目录下 `install.log`。
  Default folder `D:\AI\ResearchOS` (no D: drive: `%LOCALAPPDATA%\ResearchOS`); log: `install.log` there.
- 第一次打开时会弹窗显示一次性设置口令（已复制到剪贴板），用它在浏览器里创建账号。
  First start shows a one-time setup token in a dialog (copied to the clipboard) to create your account.
- 卸载：“设置 → 应用”或开始菜单中的“Uninstall Research OS”；研究数据（`data` 文件夹）默认保留。重新运行安装程序即可更新，
  `data` 不会被改动。Uninstall from Settings → Apps; your `data` folder is kept by default. Re-run setup to update.

Status: the installer was built and checked on Linux only; running it on Windows is NOT RUN by the developer.
