# Research OS

[English](README.md)

Research OS 是一个运行在你自己电脑上的个人科研工作台：本地 Python 后端 + 浏览器里打开的网页应用（PWA），
也可以把 iPad 配对到这台电脑上使用。

它把一条完整的科研流程串起来：**导入文献 → 只基于你自己的资料回答问题、每句话都链接到出处页码 → 规划实验 →
受控运行 → 诊断结果 → 写报告**。数据准确和科学严谨排在所有功能之前。

> 状态：早期版本（0.1.x）。在 Linux 上构建和测试；Windows 安装包在 Linux 上编译，开发者尚未在真实 Windows
> 机器上验证安装。GPU 与 iPad 验收：未进行。

## 功能

- **资料库与带引用问答**：导入 PDF 和笔记；回答只引用你的资料，每句话都链接到对应页。
- **文献检索**：Crossref、OpenAlex、PubMed、arXiv、Semantic Scholar；文献筛选综述；BibTeX / RIS / Zotero 导入。
- **实验与研究**：预注册实验计划、带种子的采样器（QMC、TPE、高斯过程搜索）、停止规则、失败记忆、基于方差分析的敏感性分析。
- **写作**：带引用和数字核查的文档编辑器；导出 Markdown、Word、LaTeX 和幻灯片。
- **模型路由**：使用你自己的 API 密钥（OpenAI、Anthropic、DeepSeek、智谱或任意 OpenAI 兼容接口）；密钥只保存在本机，
  日志中会被遮盖，接口永不返回。
- **插件**：生物医学信号（心电/脑电）、DICOM 影像、诊断统计、人体活动数据集、文献检索、报告导出、Zotero 导入。
  见 [docs/PLUGIN_DEV.md](docs/PLUGIN_DEV.md)。
- **中英文界面**：在“设置”里随时切换，立即生效。
- **一键更新**：“设置 → 关于”里检查最新版本（发布信息必须带有效的 Ed25519 签名）、下载安装包、校验 sha256 后自动安装，保留你的数据；新版本启动失败时自动回退。不点就不会联网检查。

## 安装（Windows）

1. 在 [Releases](../../releases) 页面下载 `ResearchOS-Setup-<版本>.exe`。
2. 双击，一路点“下一步”直到“完成”。不需要管理员权限，安装过程不联网下载。
3. 从桌面或开始菜单打开“Research OS”，它会在浏览器中打开。第一次启动会显示一次性设置口令，用它创建账号。

研究数据保存在安装目录的 `data` 文件夹中，更新时保留，卸载时默认也保留。更多说明见
[packaging/README_INSTALL.md](packaging/README_INSTALL.md)。

## 从源码运行（Linux / macOS / Windows）

需要：Python 3.11–3.13、[uv](https://docs.astral.sh/uv/)、Node.js 20+ 和 pnpm（构建网页端）。

```sh
uv sync --extra dev --extra experiments --extra plugins
(cd apps/web && pnpm install && pnpm build)
sh scripts/start.sh                 # Windows：scripts\start.ps1
```

然后打开 <http://127.0.0.1:8443>。配置与运维（数据目录、模型中转、代理、备份与恢复）见 [docs/OPS.md](docs/OPS.md)。

## 开发

- 架构与契约：[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)；决策记录：[docs/DECISIONS.md](docs/DECISIONS.md)。
- 全部质量门禁（ruff、mypy、import-linter、架构健康检查、插件、pytest、网页端 lint/类型检查/测试/构建）：
  `sh scripts/check.sh`。
- 参与贡献：[CONTRIBUTING.md](CONTRIBUTING.md)；安全问题：[SECURITY.md](SECURITY.md)。

## 许可证

[MIT](LICENSE)
