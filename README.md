# Claude-CN

Claude macOS 桌面端简体中文界面补丁。本项目针对 Claude Desktop 4.29.0，修改桌面应用的界面资源；不修改 Claude Code CLI。

## 安装与检查

可以双击“安装-Claude-中文界面.command”，也可以在 macOS 上运行：

    python3 scripts/repair_macos_429.py
    python3 scripts/repair_macos_429.py --apply

第一条仅检查当前版本、资源覆盖与语言配置；第二条会制作应用副本、补齐桌面壳层和前端中文资源、校验完整性与签名，再备份官方应用并安装。旧备份会保留。安装前请先保存 Claude 中尚未发送的草稿。

支持版本严格限定为 4.29.0。官方自动更新保持启用；升级会覆盖补丁。升级后先运行检查命令，如版本变化，必须按新版本更新资源与补丁，不要强行套用旧包。

补丁不会打包或上传 Claude.app、用户配置、会话、密钥。公开仓库只存补丁源码、翻译资源和验收说明。

## 资源结构

- scripts/repair_macos_429.py：版本检查、桌面应用补丁、校验和保留备份的安装事务。
- scripts/upstream_patch_lib.py：基于 javaht/claude-desktop-zh-cn 的 ASAR、资源和签名补丁函数。
- resources/frontend-zh-CN.json：前端简体中文消息表。
- resources/desktop-zh-CN.json：桌面壳层简体中文消息表。
- resources/frontend-hardcoded-zh-CN.json：经上下文保护的可见硬编码文案。
- 其他 resources 文件：动态文案、菜单、原生 macOS 文案。

## 来源与许可

基于 javaht/claude-desktop-zh-cn（MIT，见 LICENSE 和 THIRD_PARTY_NOTICES.md），并补入 4.29.0 的新增消息和内置 ASAR 桌面壳层适配。Claude 应用及其官方资源的权利属于 Anthropic，本仓库不提供应用安装包。

## 验收

本机 4.29.0 已验证：前端 39,482 条消息 ID、桌面壳层 947 条消息 ID、动态资源 49 条消息 ID 均完整覆盖；400 个修改后的 JavaScript 文件语法解析通过。ICU 占位符和富文本标签校验通过；ASAR 完整性与本地签名校验通过。实际首页和 macOS 原生菜单已验证中文，重新启动后语言保持。

“消息 ID 完整覆盖”不等于逐一触发了所有权限、错误、远程内容和实验性界面。产品名、模型名、技术名词和用户内容会保留必要的原文；未触发的条件页面仍可能需要后续修正。此项目不承诺汉化能跨官方更新自动保留。

可复核资源和界面代码：

    npm ci --ignore-scripts
    python3 scripts/audit_localization.py --official-backup /path/to/official/Claude.app

Python 标准库足够安装；Node.js 依赖只用于可选的严格 ICU 与 JavaScript 语法验收。运行事务回归测试：

    python3 -m unittest discover -s tests -v

## 恢复官方版本

安装会在应用旁保留带版本号和时间戳的官方备份，不清理其他备份。使用明确的同版本官方备份：

    python3 scripts/restore_claude_429.py --backup /path/to/official/Claude.app
    python3 scripts/restore_claude_429.py --backup /path/to/official/Claude.app --apply

第一条只检查，第二条执行恢复。恢复时会保留当前中文应用；版本不一致或非 Anthropic 官方签名时拒绝恢复。

## 翻译维护

scripts/translate_missing.py 可通过本机 Ollama 处理公开 UI 文案：在独立目录准备 missing.json（消息 ID 到英文的映射）和可选的 memory.json（已有精确译文），然后运行：

    python3 scripts/translate_missing.py --directory /path/to/public-ui-messages --model your-local-model

输出为 translated.json。维护者必须在合并后运行严格 ICU/标签检查；机器翻译结果不能直接作为安装验收依据。
