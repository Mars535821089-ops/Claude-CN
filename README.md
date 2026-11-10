# Claude-CN

Claude macOS 桌面端简体中文界面资源与上游变更检查工具；不修改 Claude Code CLI。

## 当前版本与更新状态

### 2026-11-10：官方 4.23118.1 只读迁移审查

官方 `RELEASES.json` 当前发布版为 4.23118.1；已在本机外置构建区下载并对
ZIP 完整性、Anthropic Developer ID、Team ID、签名和应用版本分别核验。
此前现用的 4.4212.0 中文版不再是当前安装态：本轮只读检查发现
`/Applications/Claude.app` 已是 Anthropic Developer ID 签名的 4.23118.1
官方包，且未发现正在运行的 Claude 进程。该包没有本项目的 `zh-CN` 前端/
桌面目录或 Cowork 标记；这项变化不是本轮补丁安装造成的，当前中文界面
尚未验收。

与官方 4.23118.0 对比，桌面 1,130、前端 42,695、动态 51 条英文消息
的 ID 与原文完全一致；上一候选的目录级译文因此可作为迁移输入，但不能把
两个 ASAR 的新指纹当作旧包。已审阅的 6 个界面 JS 文件中，3 个旧桌面
chunk 改名；经新源逐文件 AST 上下文复核，旧 26 处翻译规则仍可匹配。
`resources/candidates/claude-desktop-4.23118.1/` 已保留三组原字节译文和
新版源指纹，43,876 条消息的 ICU/参数/标签再次核验为零错误；其状态仅为
`locale_candidate_only_not_installed`。
Cowork HostLoop 亦换了文件名和函数绑定，版本专属补丁现在对 4.23118.1
使用精确新 SHA-256、唯一锚点和会话变量，合成测试及官方新源的 JS 语法
验证通过。旧版签名与旧版补丁分支保留。
新版本专属的 `hardcoded-ui-reviewed.json` 和
`language-whitelist-reviewed.json` 已分别绑定 4.23118.1 两份官方 ASAR、
六个界面文件、两处语言开关与对应输出 SHA。反馈面板与调试视图新增14处精确
审阅文案（含隐私说明、错误提示和按钮），在已签名官方原包中验证 AST 结构
不变；界面文案候选合计40处。已在签名官方包上重新通过三组 43,876 条译文、
两处语言开关、Cowork 合成会话钩子的只读组合校验；12 个目标 ASAR 条目不重叠。
当前安装版与此前下载版同为官方签名 4.23118.1，前端 ASAR 相同，但桌面
ASAR 因 3 个 arm64 编译缓存不同、下载版另有 3 个 x64 编译缓存而指纹
不同。逐条目比对的打包 JS 原文没有差异；审核清单现在仅允许这两组
**完整、精确配对**的 ASAR 指纹，并在包签名和每条文案源码 SHA 通过后
才重用相同翻译。未知指纹或前后端混搭仍拒绝。
本轮 `build_smoke_423118.py --version 4.23118.1` 在既定外置构建区完成
新的 **不可安装烟测包**；复制后的两份 ASAR、Electron 完整性、本地签名、
43,876条目录译文与40处界面补丁经独立回读一致。新包中反馈面板的目标
JS 条目与审阅输出 SHA 完全相同，中文可见文案存在、对应英文已移除。
候选仍只使用合成 Cowork 会话；回执明确 `smoke_only=true`、
`installed=false`，没有触碰现用 Claude.app。旧26处烟测包不再作为本轮证据。
另从当前安装的官方 arm64 变体直接复制构建了一份隔离烟测包，同样通过
40处界面文案、两份 ASAR 完整性及本地签名回读。它也**不可安装**，
不证明真实 Cowork、编译缓存运行行为或目视汉化已经通过。
此前只读盘点扫描了 5,132 个 JS 和 15 个 HTML 文件，解析错误为零。
在八类更可能显示给用户的界面上下文中，当时仍有 1,244 个不同英文候选
待逐上下文审查；本次增量后尚未重新盘点余量。它们不全是可见文案，不能
批量盲译或把目录级候选说成“全面汉化完成”。

这些只是**源级审查**。4.23118.1 的完整汉化候选、签名、真实 Cowork
会话、剩余硬编码界面和桌面视觉验收尚未完成，禁止直接安装旧 4.23118.0
烟测包或用官方原版覆盖现用中文版。

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

    /opt/homebrew/bin/python3 -m unittest discover -s tests -v
    node --test tests/*.cjs

仓库的 GitHub Actions 会运行同样的离线回归测试；CI 不包含 Claude 应用，不代表完成真实应用安装验收。

## 恢复官方版本

安装会在应用旁保留带版本号和时间戳的官方备份，不清理其他备份。使用明确的同版本官方备份：

    python3 scripts/restore_claude_429.py --backup /path/to/official/Claude.app
    python3 scripts/restore_claude_429.py --backup /path/to/official/Claude.app --apply

第一条只检查，第二条执行恢复。恢复时会保留当前中文应用；版本不一致或非 Anthropic 官方签名时拒绝恢复。

## 翻译维护

4.23118.0 的无损迁移目前处于审阅阶段，尚未成为安装基线。新增的
`scripts/audit_migration.py` 只提取、比对和审计，不改应用、不切换语言、不重启。
它先核对旧版英文目录与已审阅指纹，再验证新版 Anthropic 签名及版本；
现用旧版 `app.asar` 中有一处会话自动压缩魔改，与旧汉化安装回执不一致。
已只读逆向还原该单项改动并与原回执 SHA-256 匹配。审计器仅对该精确主资源
哈希、原安装回执及完整中英文目录指纹放行译文复用，其他主资源变更拒绝；
`cowork_hook_ported_to_candidate=false` 明确标记魔改尚未迁入新版，不能安装。
旧消息 ID 只有在英文原文完全相同时才能复用译文。歧义和未知原文保留为缺口。

    python3 scripts/audit_migration.py \
      --old-app /Applications/Claude.app \
      --new-app /path/to/signed-4.23118.0/Claude.app \
      --target-version 4.23118.0 \
      --reviewed-additions resources/migration-4.23118.0-reviewed.json \
      --output /path/outside/repository/new-review-directory

补充表仅含人工编写的译文、消息 ID 和目标英文目录的 SHA-256，不提交提取的官方
目录。版本或完整英文目录指纹变化时拒绝应用补充表；未知 ID、空译文和重复键
均拒绝。所有复用及补充译文继续接受原有严格 ICU 变量、标签、选择分支校验。
审阅目录含来源记录、未译原文与检查报告，应仅留本地，不上传 Git。

该段是初次迁移审阅时的历史缺口；后续已在
`resources/candidates/claude-desktop-4.23118.0/` 发布全量目录级译文候选。
2026-11-08 对本机官方签名 4.23118.0 的原英文目录重新核对：桌面壳层 1,130 条、
前端 42,695 条、动态资源 51 条均与候选的 ID 集合、来源指纹和文件哈希匹配；
现有 `audit_messages.cjs` 的 ICU/参数/标签/显式数值分支校验全部通过，无错误。
中文 CLDR 不使用的 `one` 复数分支可以合并至 `other`，这与逐分支字面同形检查
不同；已另做全量结构对照并保留其差异事实，不把“字面分支不同”误报成安装失败。
这是**语言目录候选**，并非新版硬编码界面、Cowork 会话魔改、安装器、实际界面
或客户端接入已验收；不得据此覆盖现用汉化版。

2026-11-08 对官方签名 4.23118.0 应用另做**只读硬编码文案盘点**：扫描了
5,131 个 JavaScript 文件和 15 个 HTML 文件，得到 9,831 个 JS 文本候选与
26 个 HTML 文本候选（合计 7,427 个不同英文原文）。现有经审阅词典能按原文
精确对应其中 2,680 次出现、2,067 个不同原文；剩余 5,360 个不同原文**只是
待分类的候选**，含日志、错误与程序字符串，不能全部当作可见界面或直接批量
替换。盘点报告保存在本机外置构建区，不包含登录态或会话，未放入 Git。先分类
和审阅真实可见内容，再迁移 Cowork 魔改、构建重签候选并做界面验收；这个盘点
不构成安装授权或“最新版已全部汉化”的证明。

为缩小审阅范围，先看 `label/title/children/placeholder/aria-label/
heading/textContent/buttons` 与 HTML 等更可能显示给用户的上下文：
共 3,101 次出现，其中旧词典精确命中 1,405 次；余下 1,696 次、
1,262 个不同英文原文仍须逐上下文判断与翻译。这个过滤仅是**优先队列**，
并不证明其他上下文没有可见文字，也不保证队列内的每条都是界面内容。

2026-11-08 先把其中 5 份官方签名 4.23118.0 源码的 8 处高优先级
`Model and effort selection`、`Some models are currently unavailable.`、
`Default model`、`Default effort level`、`Collapse all`、`Expand all` 和
`New chat` 做了逐文件审阅：旧词典已有的 4 个原文沿用既定译法，新增 3 条
单独版本化，绝不全局替换同字样的枚举或程序字符串。清单固定官方双 ASAR
摘要、五个 JS 源文件与预期产物 SHA、AST 上下文及 8 次出现数量；复用既有
`prepareBundle` 的可选词典参数，不影响旧版调用者。只读验证入口：

    python3 scripts/verify_ui_423118.py --official-app /absolute/path/to/official/Claude.app

该命令已对官方签名 4.23118.0 包通过，只在内存中验证替换后 JS 语法结构；
27 项 Node、69 项 Python 测试通过。**这不是新版已完整汉化或已安装**：
其余高优先级候选仍待审阅，Cowork 魔改与完整版候选打包、签名、实际界面
验收仍未完成。当前运行的 4.4212.0 汉化应用没有被替换或重启。

另一处升级阻断也已定位：官方 4.23118.0 前端包含**两份独立语言白名单**
`shared-67-CqVL4EFG.js` 与 `shared-99-Do2dhk0C.js`。旧版工具只改找到的
第一份，因此直接复用会使另一条语言路径回退英文。新的
`language-whitelist-reviewed.json` 将这两份官方源及其中文输出分别绑定 SHA、
一次匹配和确切文件名；`patch_whitelist_423118.py` 的命令行只做读取验证，
实际修改函数只能由后续隔离构建器对可丢弃的提取目录调用。指纹错、软链接、
只改了一份或第二份写入失败均会停止或恢复旧内容与文件权限。

综合只读检查：

    python3 scripts/verify_combined_423118.py --official-app /absolute/path/to/official/Claude.app

该检查已在官方签名包上验证 **43,876 条消息（桌面、前端、动态三组）** 的
ID、英文源摘要、中文候选摘要和 ICU 参数/分支，另核对 8 处审阅文案、
2 份语言白名单，以及 Cowork 钩子使用**合成会话**时的语法；11 个目标
ASAR 文件互不重叠。它不构建、安装或启动应用；真实 Cowork 会话、剩余
硬编码界面和视觉验收仍需继续，不能据此说新版中文版已经生效。

随后做了**不可安装的合并打包烟测**，只使用官方签名 4.23118.0 源，
只写入本机外置构建区。新官方包在 `ion-dist.asar` 旁还附有独立
`ion-dist/index.html`，供仍打开旧版本的窗口显示；它不是新版前端资源。
旧工具误判双布局为冲突，版本专用构建器现原样保留该备用页，只从 ASAR
提取前端到隔离目录进行修改。首次失败副本经签名、ASAR、Info.plist
与官方源一致性检查后移除，失败日志保留；第二次候选完整打包成功。

`build_smoke_423118.py` 会先核验官方签名、完整中文目录和所有源指纹，
拒绝覆盖既有候选或写入 `/Applications`；只在**合成 Cowork 会话**中
验证钩子打包兼容。烟测副本的双 ASAR、Electron 完整性摘要、签名、
43,876 条消息、26 处审阅文案和 2 份语言白名单经独立回读；备用
HTML 与官方源字节一致。回执标记 `smoke_only=true`、`installed=false`。
仅在新建且位于已挂载外置构建区的路径调用：

    python3 scripts/build_smoke_423118.py --official-app /absolute/official/Claude.app --stage /Volumes/Mars-私人服务器/内置盘写入统一管理/01-编译与构建/Codex-provider-switch/new-smoke/Claude.app

**不得把这个合成会话包安装给用户**：未迁移真实会话私密策略，仍有
未审阅硬编码界面、语言菜单呈现、新开/刷新默认侧栏及原生视觉验收。

scripts/translate_missing.py 可通过本机 Ollama 处理公开 UI 文案：在独立目录准备 missing.json（消息 ID 到英文的映射）和可选的 memory.json（已有精确译文），然后运行：

    python3 scripts/translate_missing.py --directory /path/to/public-ui-messages --model your-local-model

输出为 translated.json。维护者必须在合并后运行严格 ICU/标签检查；机器翻译结果不能直接作为安装验收依据。
