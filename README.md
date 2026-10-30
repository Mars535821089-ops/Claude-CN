# Claude-CN

Claude macOS 桌面端简体中文界面资源与上游变更检查工具；不修改 Claude Code CLI。

## 当前版本与更新状态

### 2026-10-31：4.4212.0 显式安装入口

已增加独立的 `scripts/install_current.py`，只接受 `current-source.json` 中审阅过的版本、
build、官方发行方及两份 ASAR 指纹。它重新生成预览并与仓库清单逐项比较，拒绝消息缺口、
源码漂移、路径越界及符号链接；旧版安装器和预览拒绝保护不变。

先构建并验证候选（`--review` 目录必须不存在且在仓库外；候选留在被忽略的 `work/`）：

    python3 scripts/install_current.py --stage "$PWD/work/current-4.4212.0.app" --review /tmp/claude-cn-build-review-unique

确认已保存 Claude 草稿、接受本地临时签名及退出 Claude 后，再显式安装：

    python3 scripts/install_current.py --stage "$PWD/work/current-4.4212.0.app" --install

安装器会退出 Claude，将原官方应用保留在 `work/backups/`，验证候选后替换应用，
仅将已有的 `Claude` 和 `Claude-3p` 配置中的 locale 设为 `zh-CN`；不修改会话、
供应商、模型映射或更新偏好。失败时恢复原应用与语言配置。安装器不自动重开应用，
完成后从 `/Applications/Claude.app` 打开即可。源码可同步 GitHub，但应用、合并的官方
消息目录、备份和本地检查回执不能上传。完整安装记录见 `docs/4.4212.0-validation.md`。

本机已安装并目视确认当前会话、输入框、模型强度和原生菜单为中文；37 项 Python、
17 项 Node 回归通过。未重启 Codex。此处不宣称所有实验页面、服务端错误、工具名称或
历史内容完全无英文。后文的“预览未安装”是 10 月 30 日的历史边界，不代表本次部署状态。
临时签名不是 Anthropic 官方签名；更新检查器仍会拒绝把修改后的应用当作纯净官方源。
官方更新仍可能覆盖汉化，新版本必须重新适配，不能跳过指纹保护。

2026-10-30 安装前核对：本机安装版和 build 均为 **4.4212.0**，与[官方 macOS 通用版公开发布源](https://downloads.claude.ai/releases/darwin/universal/RELEASES.json)的 `currentRelease` 一致（该条目发布日期为 2026-10-29）。校验了 Anthropic Developer ID、Team ID `Q6L2SF6YDW`、bundle ID 和完整代码签名；该官方源现以备份保留。

**旧安装器 `repair_macos_429.py` 仍只支持 4.29.0，不能用于 4.4212.0。** 当前版使用上述独立入口，不改变旧版门控。不能把“软件已最新”误报为“汉化已安装且最新”。

新版包含 724 条与当前英文消息 ID 匹配的官方桌面 `zh-Hans` 译文，但前端和动态消息没有对应的打包中文目录，默认前端语言列表也不含中文。它证明存在部分官方本地化资源，不证明完整原生中文已经可用，更不是第三方外置语言包接口。没有因此修改用户语言设置、实验开关或应用签名。

核对官方公开版本与本地资源兼容性：

    python3 scripts/update_guard.py --check-latest --json

联网仅获取固定官方 HTTPS 发布源，不发送账号、配置、会话或安装文件，不下载或安装更新。网络失败、发布元数据异常或非 Anthropic 签名会明确停止；不会把失败当成“已最新”。公开发布源不代表每个账号、平台或灰度通道。即使检查基线未变化，只要安装器不支持当前版本，检查仍返回需要处理的退出码 `2`。

从当前官方应用生成可复核的本地迁移预览：

    npm ci --ignore-scripts
    python3 scripts/preview_current.py --check-latest --output /tmp/claude-cn-current-review

输出目录必须尚不存在，且不能在应用包或此仓库内。命令先校验版本、build、发行方、完整签名、两个 ASAR 的 SHA-256 和消息目录指纹，结束前再核对一次。未知源码不会自动接受为新基线。

迁移优先使用本机官方 `zh-Hans` 的相同消息 ID，再使用当前审阅词典、明确保留的产品名/技术示例、旧版相同 ID，以及无歧义的精确原文记忆。所有复用会记录来源；没有旧版英文来源的 ID 复用不等于已验证语义完全未变。对生成的每条消息检查 ICU 参数和富文本标签；对硬编码 UI 检查文件/上下文/匹配次数、输出指纹和 AST 结构。文件访问弹窗的 `preview/open` 局部别名只有在全部引用均是弹窗文本或已审阅日志时才补译；若用于条件判断或其他调用则不修改。新增设置/语言选择控件和一批最新内置更新说明也纳入审阅。

当前版三个消息目录共 41,253 个 ID，合并预览的 ID 缺口为零（前端 40,242、桌面 961、动态 50）。其中官方桌面译文匹配 724 个 ID，其余由仓库译文、当前增量和明确保留项组成；产品名、键位、协议与技术示例保留原文，不计作中文译文。当前增量词典共 1,332 条独立译文，另明确保留 3 条技术示例/仅占位符原文。新增译文单独严格校验 `select` 分支、精确数值 `plural` 分支及偏移，不能以中文不使用单复数为由丢失条件。

本轮完整静态扫描了 4,598 个 JavaScript 文件、12 个可执行内联脚本及 12 个 HTML 页面，解析错误为零；8,935 个英文 JS 候选及 19 个 HTML 候选并不都是漏译。只将审阅过的真实文案生成 120 个文件、1,043 处替换的精确预览，另补了 16 条最新内置更新说明；其他历史更新说明、实验/远程内容和无法静态追踪的数据流仍需按实际界面审阅。消息目录 ID 齐全不等于候选全部处理或运行时全部汉化。

本地输出包含：

- `report.json`：安装版、签名/指纹、官方版本、逐类来源和缺口、输入资源 SHA-256、格式校验结果。
- `*-zh-CN.json`、`*-provenance.json`：按当前 ID 合并的译文及来源。
- `*-unreviewed-en.json`：未审阅原文；没有把英文回退算作翻译。
- `ui-patch-preview.json`：当前源码的精确替换预览，标记为 `preview_only`，JavaScript 和 HTML 安装入口均拒绝应用。

完整合并产物含本机官方资源，只保存在本机，不提交或上传。仓库只保存自己维护的增量词典、源码指纹、审阅清单、脚本和测试。预览命令总以退出码 `2` 表示仍需安装/运行时验收，不代表脚本执行失败；真正的验证失败返回 `1`，且不发布不完整输出目录。它不退出 Claude、不改变偏好或数据、不覆盖或重签应用。因此，当前版本的完整应用安装与运行时覆盖仍未验收，不能宣称所有界面已完成汉化。

## 官方本地化能力与安全边界

旧版安装器针对的 Claude Desktop 4.29.0 没有可由本项目使用的官方外置中文语言包接口。旧版中文界面依赖修改 Electron ASAR、前端资源及语言白名单；即使重签名后能运行，也会改变官方应用包的签名，且官方更新会替换这些修改。该机制并非更新兼容的官方扩展点。

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
- scripts/official_release.py：公开官方 macOS 版本源的严格校验与版本比较。
- scripts/preview_current.py：当前已审阅官方源码的只读译文迁移及 UI 精确预览。
- resources/current-source.json：当前版 build、ASAR 与消息目录指纹，不含官方完整资源。
- resources/current-*-zh-CN.json、current-preserved.json：当前版人工审阅的增量译文和明确保留的技术原文。
- scripts/upstream_patch_lib.py：基于 javaht/claude-desktop-zh-cn 的 ASAR、资源和签名补丁函数。
- resources/frontend-zh-CN.json：前端简体中文消息表。
- resources/desktop-zh-CN.json：桌面壳层简体中文消息表。
- resources/frontend-hardcoded-zh-CN.json：经上下文保护的可见硬编码文案。
- resources/ui-reviewed-zh-CN.json：人工审阅的 UI 硬编码译文，包括权限说明、无障碍标签和原生弹窗。
- resources/ui-patch-manifest.json：逐文件 SHA-256、逐上下文匹配次数及预期输出指纹；不包含完整上游源码。
- scripts/audit_ui_sources.py、audit_ui.cjs：对两个 ASAR 中的 JavaScript、HTML 和可执行内联脚本进行只读扫描。
- scripts/ui_localization.cjs、ui_html.py：仅在临时解包目录中应用经过审阅的精确补丁。
- 其他 resources 文件：动态文案、菜单、原生 macOS 文案。

## 从 UI 源码检查漏译

仅核对消息 ID 会漏掉硬编码按钮、提示和权限弹窗。新增扫描器使用 Acorn 解析 JavaScript，识别渲染文案、按钮、占位符、无障碍属性、DOM 文本和原生弹窗说明；HTML 扫描覆盖静态文案、属性及可执行内联脚本。不会把图标名、消息 ID、条件判断中的状态字符串或翻译人员备注直接当作可替换文案。

先安装锁定的 Node.js 依赖，再对本机应用做只读扫描。完整候选报告保留在本机，不要把包含上游源码片段的报告提交到仓库：

    npm ci --ignore-scripts
    python3 scripts/audit_ui_sources.py --output /tmp/claude-ui-candidates.json
    python3 scripts/audit_ui_sources.py --verify-manifest --output /tmp/claude-ui-verification.json

当前 4.29.0 样本扫描了 4,508 个 JavaScript 文件、12 个可执行内联脚本和 12 个 HTML 页面，全部可解析；找到 7,422 个英文 JavaScript 候选及 19 个 HTML 候选。候选不是漏译计数：其中也有日志、工具描述、技术字符串、产品名和历史更新说明。759 条经审阅的译文形成了 118 个文件的精确补丁，共 948 处替换（JavaScript 935 处，HTML/内联脚本 13 处）。补齐的类别包括 Google Drive 连接/重新连接、复制/换行操作、计划审批提示、文件夹授权及“不再询问”选项、原生通知、设置、诊断和错误页面；同时修正 VS Code、PowerPoint、Chrome 被误译为普通词语的问题。

补丁以“文件指纹 + UI 上下文 + 原文 + 预期出现次数”定位，不全局替换 `Copy` 等可能兼作图标或枚举的字符串。JavaScript 修改后再次解析并比较 AST，保留标识符、调用、条件判断、模板表达式及其顺序；HTML 保留原始排版并转义译文。重复应用已匹配输出指纹的补丁不会重复修改。未知指纹、缺失文件、次数变化、路径越界、符号链接、语法或占位符异常都会显式失败；安装器只在临时应用副本中执行这些步骤，失败时不会替换已安装应用。

**基线限制：**本次可用样本是已经汉化的 4.29.0，而不是全新官方安装包。新增指纹针对旧补丁之后的源码，已接入旧安装器的相应阶段，但尚未从纯净官方包验证完整的打包、签名和安装流程。若官方来源或旧补丁产物与该基线不同，应停止并复核，不能移除指纹检查来强行安装。验证本次新增补丁没有改动 `/Applications/Claude.app`。

**旧版审计边界：**这不是“所有界面已无英文”的证明。4.29.0 样本仍保留 701 条英文历史更新说明，以及未审阅的候选；动态拼接出的操作值、服务端返回、实验性页面和用户内容不保证被静态字面量扫描覆盖。当前版另有上述独立迁移预览，但也不能以候选数量当作全部覆盖。协议错误、模型使用的工具描述和技术标识不会仅因包含英文而替换。后续发现新的可见文案时，需要审阅其实际调用上下文，不能直接把全部候选交给全局翻译。

维护者在确认上游版本、来源及所有新增文案后，才可显式重新生成规则，并复核版本、匹配次数和指纹差异；生成失败不会覆盖现有输出文件：

    python3 scripts/audit_ui_sources.py --generate-manifest --output /tmp/ui-patch-manifest.json

确认规则后更新仓库中的清单，再运行上述只读验证。重新生成清单本身并不证明新版本兼容。

## 来源与许可

基于 javaht/claude-desktop-zh-cn（MIT，见 LICENSE 和 THIRD_PARTY_NOTICES.md），并补入 4.29.0 的新增消息和内置 ASAR 桌面壳层适配。Claude 应用及其官方资源的权利属于 Anthropic，本仓库不提供应用安装包。

## 验收

原有安装版本的验收记录：本机 4.29.0 前端 39,482 条消息 ID、桌面壳层 947 条消息 ID、动态资源 49 条消息 ID 均完整覆盖；400 个修改后的 JavaScript 文件语法解析通过。ICU 占位符和富文本标签、ASAR 完整性与本地签名校验通过；实际首页和 macOS 原生菜单已验证中文，重新启动后语言保持。这些历史记录不代表本次新增补丁已经安装。

本次 UI 补丁已在内存中逐文件验证预期输出，并从本机应用提取目标文件到临时目录，通过安装器实际入口执行两个 ASAR 的补丁、重复应用和指纹核对；118 个目标文件均与清单一致。只读验证前后已安装 ASAR 的 SHA-256 不变。仓库译文的三组消息 ID、ICU 参数和富文本标签也重新通过校验。

“消息 ID 完整覆盖”不等于逐一触发了所有权限、错误、远程内容和实验性界面。产品名、模型名、技术名词和用户内容会保留必要的原文；未触发的条件页面仍可能需要后续修正。此项目不承诺汉化能跨官方更新自动保留。

可复核资源和界面代码：

    npm ci --ignore-scripts
    python3 scripts/audit_localization.py --official-backup /path/to/official/Claude.app

更新检查器只需 Python 标准库。新增 UI 审计、精确补丁和显式旧版安装需要 Node.js 及锁定依赖，必须先运行 `npm ci --ignore-scripts`。运行事务、更新检查器、UI/HTML 精确替换和拒绝异常输入的回归测试：

    python3 -m unittest discover -s tests -v
    node --test tests/*.cjs

仓库的 GitHub Actions 会运行同样的离线回归测试；CI 不包含 Claude 应用，不代表完成真实应用安装验收。

## 恢复官方版本

安装会在应用旁保留带版本号和时间戳的官方备份，不清理其他备份。使用明确的同版本官方备份：

    python3 scripts/restore_claude_429.py --backup /path/to/official/Claude.app
    python3 scripts/restore_claude_429.py --backup /path/to/official/Claude.app --apply

第一条只检查，第二条执行恢复。恢复时会保留当前中文应用；版本不一致或非 Anthropic 官方签名时拒绝恢复。

## 翻译维护

scripts/translate_missing.py 可通过本机 Ollama 处理公开 UI 文案：在独立目录准备 missing.json（消息 ID 到英文的映射）和可选的 memory.json（已有精确译文），然后运行：

    python3 scripts/translate_missing.py --directory /path/to/public-ui-messages --model your-local-model

输出为 translated.json。维护者必须在合并后运行严格 ICU/标签检查；机器翻译结果不能直接作为安装验收依据。
