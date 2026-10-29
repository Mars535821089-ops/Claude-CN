# Claude-CN

Claude macOS 桌面端简体中文界面资源与上游变更检查工具；不修改 Claude Code CLI。

## 官方本地化能力与安全边界

当前仓库针对的 Claude Desktop 4.29.0 没有可由本项目使用的官方外置中文语言包接口。现有中文界面依赖修改 Electron ASAR、前端资源及语言白名单；即使重签名后能运行，也会改变官方应用包的签名，且官方更新会替换这些修改。该机制并非更新兼容的官方扩展点。

因此，双击“安装-Claude-中文界面.command”现在只做上游资源检查，不会修改、替换或重签 Claude.app。仓库仍保留旧版安装器，供明确接受这些限制的用户手动使用；它只严格支持 4.29.0：

    python3 scripts/repair_macos_429.py --apply

旧安装器会复制并替换整个 Claude.app、重签应用、改变原始签名状态。请先退出 Claude 并保存草稿；它不属于低维护方案，不会自动套用到新版本。不要对未知版本强行安装。

## 建立更新基线并自动检查

更新检查器只读取官方签名应用中的英文桌面壳层和前端消息目录，不读取用户会话、设置或凭据，也不会更改应用文件。首次使用时记录本机官方版本的资源基线：

    python3 scripts/update_guard.py --record-baseline

每次检查会报告版本、资源指纹、汉化覆盖情况，以及相对本机基线新增、删除或英文内容变化的消息 ID。发现变化时只生成差异，不会更新基线或安装补丁。检查后人工审阅差异，再明确接受新基线：

    python3 scripts/update_guard.py --json
    python3 scripts/update_guard.py --accept-current

若希望在 Claude 自动更新后定时检查，可在已记录基线后安装每小时运行一次的 macOS LaunchAgent；变化或检查失败会显示通知并记录到 `~/Library/Application Support/Claude-CN/update-monitor.log`。LaunchAgent 调用此仓库中的脚本；仓库移动或删除后需重新安装监控：

    python3 scripts/update_guard.py --install-monitor
    python3 scripts/update_guard.py --remove-monitor

更新检查器不自动翻译新消息、不自动修改 Claude.app，也不会自动接受未知版本。它的资源基线保存在用户本机 `~/Library/Application Support/Claude-CN/upstream-baseline.json`，不要提交该文件。版本或资源改变时，维护者仍需人工审阅、更新翻译资源和显式版本适配；若 Claude 改变资源格式、签名状态或内部路径，检查器会停止并报告，不尝试修复或覆盖。

补丁源码、翻译资源、检查器和测试均在本仓库。不会将 Claude.app、用户配置、会话、密钥或本机检查基线上传。

## 资源结构

- scripts/repair_macos_429.py：版本检查、桌面应用补丁、校验和保留备份的安装事务。
- scripts/update_guard.py：只读官方资源指纹与逐消息差异检查、显式基线管理及定时检查器。
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

Python 标准库足够运行检查器；Node.js 依赖只用于可选的严格 ICU 与 JavaScript 语法验收。运行事务和更新检查器回归测试：

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
