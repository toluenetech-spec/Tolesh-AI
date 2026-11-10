# Tolesh Skill Matrix

> Skill → task → concrete application → verification. Generated directly from
> `skill-install-manifest.json`, so every repository name and commit below is
> the one that was actually cloned, read and installed into
> `/root/.codebuddy/skills/`. See [SKILLS.md](SKILLS.md) for the per-skill
> README review and invocation details.

## 1. Coverage at a glance

| Metric | Value |
| --- | --- |
| Skills installed into the agent | 28 |
| Design / UI-UX | 8 |
| Motion | 2 |
| Token / output discipline | 4 |
| Reverse engineering / spec | 4 |
| Web guidelines / a11y | 3 |
| Testing / engineering quality | 7 |
| Actively applied to code or docs | 21 |
| Process-only (no code artifact) | 3 |
| Honestly unused / spare / pending | 4 |

## 2. Full matrix

| Skill | Category | Official repo @ commit | Task | Where it landed in Tolesh | Verification | Status |
| --- | --- | --- | --- | --- | --- | --- |
| `impeccable` | design | `pbakaus/impeccable` @ `ec229b4` | 设计体系与 critique/audit/polish | DESIGN.md「Lagos Nocturne」；tokens.css/app.css；移除首屏模板化痕迹 | Chromium 截图 + 反模板检查表 | 已应用 |
| `design-taste-frontend` | design | `leonxlnx/taste-skill` @ `f6b7657` | 反模板化前端 finish gate | index.html 无 ALL-CAPS kicker、无单词斜体强调；editorial 首屏 | 桌面/移动端截图对比 | 已应用 |
| `frontend-ui-engineering` | design | `addyosmani/agent-skills` @ `301b02b` | 生产级、响应式、可访问 UI | app.css 断点、touch-action、safe-area-inset、显式 transition 属性 | 1440×900 与 390×844 回归截图 | 已应用 |
| `api-and-interface-design` | design | `addyosmani/agent-skills` @ `301b02b` | 接口契约与边界设计 | server/schemas.py；/api/* 路由；RouterEvent SSE 事件契约 | tests/test_tolesh_server.py | 已应用 |
| `create-design-md` | design | `ibelick/ui-skills` @ `97dea9e` | 生成设计系统文档 | DESIGN.md | 文档与 tokens.css 变量逐项对照 | 已应用 |
| `improve-ui` | design | `ibelick/ui-skills` @ `97dea9e` | 界面改进循环 | 对话框焦点陷阱/焦点恢复、列表框方向键导航、?view= URL 状态 | Playwright 回归脚本 | 已应用 |
| `brand-guidelines` | design | `anthropics/skills` @ `2c5958e` | 品牌视觉一致性 | tolesh-mark.svg、adire.svg、face.png 黄铜/靛蓝重述、upstream-face.png 留存 | web/assets/ 与 docs/img/ 文件 | 已应用 |
| `theme-factory` | design | `anthropics/skills` @ `2c5958e` | 主题与令牌生成 | tokens.css 的 oklch 色板、字阶、间距、圆角、动效令牌 | CSS 变量清单 | 已应用 |
| `design-motion-principles` | motion | `kylezantos/design-motion-principles` @ `4a9ca87` | 动效原则与动画审计 | ANIMATION-GUIDELINES.md；motion.js 单条入场时间轴；用户触发的聆听脉冲 | reduced-motion 检查 | 已应用 |
| `fixing-motion-performance` | motion | `ibelick/ui-skills` @ `97dea9e` | 动画性能：避免 layout thrashing | 移除 transition: all；仅 transform/opacity；无限自动光晕改为一次性收敛 | CSS grep + 截图 | 已应用 |
| `caveman` | token | `JuliusBrussee/caveman` @ `3dc497a` | 精简输出但保留全部技术事实 | 报告与文档措辞精简（过程性技能，不产出代码） | 本对话输出 | 过程性 |
| `caveman-compress` | token | `JuliusBrussee/caveman` @ `3dc497a` | 上下文压缩 | 长会话压缩（过程性技能，不产出代码） | 本对话输出 | 过程性 |
| `surgical-patch` | token | `JuliusBrussee/caveman` @ `3dc497a` | 最小改动式补丁，保护既有功能 | 上游模块保持原样；新能力放在 ai/ memory_ext/ 等并行命名空间 | git status 显示原文件仅品牌修改、新模块为新增 | 已应用 |
| `verify-and-stop` | token | `JuliusBrussee/caveman` @ `3dc497a` | 验证后即停，避免过度工程 | 未引入多余框架；Cloudflare/Neon/Clerk 等按用户要求未强行落地 | README 已知限制 | 过程性 |
| `reverse-engineer-anything` | re | `morluto/rea` @ `d4a7c20` | 二进制/打包产物逆向 | 本次是源码改造，无二进制逆向对象 | — | 未使用（诚实记录） |
| `rea-analysis-optimization` | re | `morluto/rea` @ `d4a7c20` | REA 分析流程优化 | 用于三仓库选型时的对比方法参考 | ARCHITECTURE.md §1 | 弱应用 |
| `spec-miner-architecture-designer` | re | `Jeffallan/claude-skills` @ `078a25c` | 架构规格推导 | ARCHITECTURE.md 模块图、数据流、平台路线图 | 文档与 src/tolesh/ 目录对照 | 已应用 |
| `spec-miner-code-documenter` | re | `Jeffallan/claude-skills` @ `078a25c` | 从源码生成规格文档 | COMPONENT-LIBRARY.md、SKILLS.md、SKILL-MATRIX.md | 文档与导出符号清单对照 | 已应用 |
| `fixing-accessibility` | a11y | `ibelick/ui-skills` @ `97dea9e` | ARIA / 键盘 / 焦点 / 表单修复 | skip link、aria-expanded/controls、装饰图标 aria-hidden、name/autocomplete、对话框焦点陷阱 | tests + 键盘回归 | 已应用 |
| `baseline-ui` | a11y | `ibelick/ui-skills` @ `97dea9e` | 间距 / 层级 / 字阶基线 | tokens.css 间距与字阶；app.css 平板与移动端基线 | CSS 变量与断点 | 已应用 |
| `react-view-transitions` | a11y | `vercel-labs/agent-skills` @ `1a2c79d` | React 视图过渡规范 | 项目为 vanilla ES modules，无 React | — | 备用未应用 |
| `webapp-testing` | testing | `anthropics/skills` @ `2c5958e` | Web 端到端测试流程 | Chromium/Playwright 回归：桌面、移动、流式对话、对话框、零 JS 错误 | tolesh-audit-*.png + console/pageerror 采集 | 已应用 |
| `browser-testing-with-devtools` | testing | `addyosmani/agent-skills` @ `301b02b` | 浏览器 DevTools 验证 | console.error / pageerror 采集与断言 | 回归脚本输出 | 已应用 |
| `test-driven-development` | testing | `addyosmani/agent-skills` @ `301b02b` | TDD 流程 | tests/test_tolesh_core.py、tests/test_tolesh_server.py（33 项） | pytest 结果 | 已应用 |
| `code-review-and-quality` | testing | `addyosmani/agent-skills` @ `301b02b` | 代码审查与质量门禁 | 发现并修复 ConversationManager.window() 取错上下文；恢复 examples/config.json 与 README 截图 | 新增测试 + 上游回归复跑 | 已应用 |
| `security-and-hardening` | testing | `addyosmani/agent-skills` @ `301b02b` | 密钥与输入安全加固 | Registry.redacted()；TOLESH_API_TOKEN；路径沙箱；_NOT_IMPLEMENTED 边界 | test_registry_never_exposes_provider_secrets 等 | 已应用 |
| `documentation-and-adrs` | testing | `addyosmani/agent-skills` @ `301b02b` | 文档与架构决策记录 | 8 份强制文档 + README + ARCHITECTURE.md 选型决策 | 文件清单 | 已应用 |
| `ci-cd-and-automation` | testing | `addyosmani/agent-skills` @ `301b02b` | CI/CD 与自动化 | 未提供 CI 配置文件 | — | 未落地（列为待办） |

## 3. Lifecycle mapping

| Stage | Skills used | Concrete outcome |
| --- | --- | --- |
| 1. Source inspection | `spec-miner-architecture-designer`, `rea-analysis-optimization`, `surgical-patch` | Three repos cloned and compared; `isair/jarvis` selected; verbatim copy + rebrand |
| 2. Architecture | `api-and-interface-design`, `spec-miner-code-documenter`, `documentation-and-adrs` | `ARCHITECTURE.md`, `schemas.py`, `/api` contract, `RouterEvent` SSE shape |
| 3. Design system | `impeccable`, `create-design-md`, `theme-factory`, `brand-guidelines`, `baseline-ui` | `DESIGN.md`, `tokens.css`, `app.css`, SVG/PNG brand assets |
| 4. Interface build | `frontend-ui-engineering`, `design-taste-frontend`, `improve-ui`, `fixing-accessibility` | `web/index.html`, `components.js`, `app.js`, responsive + keyboard-safe UI |
| 5. Motion | `design-motion-principles`, `fixing-motion-performance` | `motion.js`, `ANIMATION-GUIDELINES.md`, reduced-motion scoping |
| 6. Security | `security-and-hardening`, `code-review-and-quality` | Redacted registry, API token guard, path sandbox, `_NOT_IMPLEMENTED` boundary |
| 7. Verification | `webapp-testing`, `browser-testing-with-devtools`, `test-driven-development`, `verify-and-stop` | Playwright regression + 33 new Tolesh unit/integration tests |
| 8. Documentation | `spec-miner-code-documenter`, `documentation-and-adrs`, `caveman` | 8 mandatory documents and this matrix |

## 4. What is deliberately NOT claimed

- `reverse-engineer-anything` was installed but not used: this task transforms source code, not binaries.
- `react-view-transitions` is installed as a spare. Tolesh's frontend is vanilla ES modules; there is no React tree to attach transitions to.
- `ci-cd-and-automation` informed the test strategy but no CI config file was written. It remains outstanding work.
- `microsoft/playwright-skills` is not a usable repository. Microsoft Playwright was used as a **testing library**, never counted as an agent skill.
- `podo/design-agent-skills` entries were catalogue stubs; the complete upstream skills from `ibelick/ui-skills` and `kylezantos/design-motion-principles` were installed instead.
- `npx skills add ... -a claude-code` wrote to `/workspace/.claude/skills/`, which this agent does not scan. Only the 28 skills copied into `/root/.codebuddy/skills/` are counted as installed.

