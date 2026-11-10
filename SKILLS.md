# Tolesh Skills — Installation, Review & Usage Record

> Every skill below was **verified available to the coding agent** before
> being called installed. The verification: `Skill(caveman)` returned
> `Base directory for this skill: /root/.codebuddy/skills/caveman`, proving the
> installed directory is on the agent's skill path.

## 1. Method (and what was actually done)

1. **Inspected the current machine first.** `/workspace/.codebuddy/skills`
   already contained 55 skills (including `frontend-design`,
   `web-design-guidelines`, the full 8-module GSAP set, `ui-ux-pro-max` and
   `ui-animation`). Jarvis's historical `.agents/skills/` was checked too.
2. **Cloned 12 official repositories** to a scratch dir, read each README,
   inspected the SKILL.md files, counted them, recorded commits, and checked
   supported agents / dependencies / compatibility.
3. **Discovered that `podo/design-agent-skills` is a catalogue, not a skill
   pack** — its SKILL.md files are stub pointers (e.g. `fixing-accessibility`
   → `ibelick/ui-skills`). Installing the stubs would have been a false success,
   so the two upstream full-skill repositories were cloned instead.
4. **Tried the official CLI as requested**: `npx skills add
   https://github.com/ibelick/ui-skills -a claude-code -s fixing-accessibility
   -y --copy`. It succeeded but installed to
   `/workspace/.claude/skills/fixing-accessibility`, a directory the CodeBuddy
   agent does **not** scan. That path is therefore *not* counted as installed.
5. **Installed 28 complete skills** by copying each skill directory (SKILL.md
   plus all references/scripts) from the official repo at a pinned commit into
   `/root/.codebuddy/skills/<name>/`, validating frontmatter on every one.
6. **Verified** with the Skill tool, `node --check`, and
   `npx skills list`. The machine-readable record is
   `skill-install-manifest.json` in the session artifact directory.

## 2. Successfully installed (28)

All entries: status `installed`, frontmatter valid, **not** a catalogue stub,
README of the source repository reviewed, SKILL.md inspected, installed under
`/root/.codebuddy/skills/<name>/`.

### 2.1 设计 / UI-UX (8)

#### `impeccable`

- **Official repo**: `pbakaus/impeccable` @ `ec229b4`
- **Installed path**: `/root/.codebuddy/skills/impeccable` (58 files)
- **README reviewed**: yes (`impeccable` root README) · **SKILL.md inspected**: yes
- **What it does**: 设计/重设计/critique/audit/polish/animate/document；DESIGN.md 体系
- **When to use / how to invoke**: Skill 工具按名称加载，或 `/<name>`；设计/重设计/审查界面前
- **Limitation**: `scripts/impeccable context` 启动器依赖其自带二进制；按技能自身规则，启动器不可用时应直接读取项目 DESIGN.md/PRODUCT.md
- **Applied in Tolesh**: 设计系统评审词汇与 craft floor；用于 `web/` 重设计的 critique/polish 判断，DESIGN.md 的『反模板决策』表

#### `design-taste-frontend`

- **Official repo**: `leonxlnx/taste-skill` @ `f6b7657`
- **Installed path**: `/root/.codebuddy/skills/design-taste-frontend` (1631 files)
- **README reviewed**: yes (`taste-skill` root README) · **SKILL.md inspected**: yes
- **What it does**: 反模板化前端，浏览器 finish gate 检查
- **When to use / how to invoke**: Skill 工具按名称加载，或 `/<name>`；设计/重设计/审查界面前
- **Limitation**: 需要 Node 22+ 与 Chrome/Edge/Chromium；其 Stop hook 针对 Claude 目录布局，在 CodeBuddy 下不会自动触发
- **Applied in Tolesh**: 反模板化前端与浏览器 finish-gate 思路；用于首屏文案、空状态与截图复核流程

#### `frontend-ui-engineering`

- **Official repo**: `addyosmani/agent-skills` @ `301b02b`
- **Installed path**: `/root/.codebuddy/skills/frontend-ui-engineering` (1 files)
- **README reviewed**: yes (`agent-skills` root README) · **SKILL.md inspected**: yes
- **What it does**: 生产级、可访问、响应式 UI 构建规范
- **When to use / how to invoke**: Skill 工具按名称加载，或 `/<name>`；设计/重设计/审查界面前
- **Limitation**: Standard Agent Skills format; no additional runtime dependency required for reading its instructions.
- **Applied in Tolesh**: 组件架构与生产级 UI 规范；`web/js/components.js` 的自包含组件、转义与契约

#### `api-and-interface-design`

- **Official repo**: `addyosmani/agent-skills` @ `301b02b`
- **Installed path**: `/root/.codebuddy/skills/api-and-interface-design` (1 files)
- **README reviewed**: yes (`agent-skills` root README) · **SKILL.md inspected**: yes
- **What it does**: 接口契约与边界设计
- **When to use / how to invoke**: Skill 工具按名称加载，或 `/<name>`；设计/重设计/审查界面前
- **Limitation**: Standard Agent Skills format; no additional runtime dependency required for reading its instructions.
- **Applied in Tolesh**: 接口契约设计；`/api/*` 的 JSON/SSE 契约与 `RouterEvent` 事件模型

#### `create-design-md`

- **Official repo**: `ibelick/ui-skills` @ `97dea9e`
- **Installed path**: `/root/.codebuddy/skills/create-design-md` (1 files)
- **README reviewed**: yes (`ui-skills` root README) · **SKILL.md inspected**: yes
- **What it does**: 生成 DESIGN.md 设计系统文档
- **When to use / how to invoke**: Skill 工具按名称加载，或 `/<name>`；设计/重设计/审查界面前
- **Limitation**: Standard Agent Skills format; no additional runtime dependency required for reading its instructions.
- **Applied in Tolesh**: DESIGN.md 的结构与令牌表组织方式

#### `improve-ui`

- **Official repo**: `ibelick/ui-skills` @ `97dea9e`
- **Installed path**: `/root/.codebuddy/skills/improve-ui` (3 files)
- **README reviewed**: yes (`ui-skills` root README) · **SKILL.md inspected**: yes
- **What it does**: 界面改进循环（覆盖 improve-animations 诉求）
- **When to use / how to invoke**: Skill 工具按名称加载，或 `/<name>`；设计/重设计/审查界面前
- **Limitation**: Standard Agent Skills format; no additional runtime dependency required for reading its instructions.
- **Applied in Tolesh**: 界面改进循环；移动端抽屉、审批卡片等缺陷的迭代修复

#### `brand-guidelines`

- **Official repo**: `anthropics/skills` @ `2c5958e`
- **Installed path**: `/root/.codebuddy/skills/brand-guidelines` (2 files)
- **README reviewed**: yes (`skills` root README) · **SKILL.md inspected**: yes
- **What it does**: 品牌视觉一致性
- **When to use / how to invoke**: Skill 工具按名称加载，或 `/<name>`；设计/重设计/审查界面前
- **Limitation**: Standard Agent Skills format; no additional runtime dependency required for reading its instructions.
- **Applied in Tolesh**: 品牌一致性；Tolesh 命名、语气、品牌 SVG 资源

#### `theme-factory`

- **Official repo**: `anthropics/skills` @ `2c5958e`
- **Installed path**: `/root/.codebuddy/skills/theme-factory` (13 files)
- **README reviewed**: yes (`skills` root README) · **SKILL.md inspected**: yes
- **What it does**: 主题/令牌生成
- **When to use / how to invoke**: Skill 工具按名称加载，或 `/<name>`；设计/重设计/审查界面前
- **Limitation**: Standard Agent Skills format; no additional runtime dependency required for reading its instructions.
- **Applied in Tolesh**: 主题/令牌生成；`web/styles/tokens.css` 的分组与命名

### 2.2 动效 / 动画 (2)

#### `design-motion-principles`

- **Official repo**: `kylezantos/design-motion-principles` @ `4a9ca87`
- **Installed path**: `/root/.codebuddy/skills/design-motion-principles` (15 files)
- **README reviewed**: yes (`design-motion-principles` root README) · **SKILL.md inspected**: yes
- **What it does**: 动效原则与动画审计（Emil Kowalski 等）
- **When to use / how to invoke**: Skill 工具按名称加载；编写或审查动画、性能调优前
- **Limitation**: Standard Agent Skills format; no additional runtime dependency required for reading its instructions.
- **Applied in Tolesh**: 动效原则与动画审计；一次性入场取代无限循环、GSAP matchMedia

#### `fixing-motion-performance`

- **Official repo**: `ibelick/ui-skills` @ `97dea9e`
- **Installed path**: `/root/.codebuddy/skills/fixing-motion-performance` (1 files)
- **README reviewed**: yes (`ui-skills` root README) · **SKILL.md inspected**: yes
- **What it does**: 动画性能：layout thrashing / 合成器属性
- **When to use / how to invoke**: Skill 工具按名称加载；编写或审查动画、性能调优前
- **Limitation**: Standard Agent Skills format; no additional runtime dependency required for reading its instructions.
- **Applied in Tolesh**: 动画性能；仅用 transform/opacity、显式 transition 属性、避免 layout thrash

### 2.3 Caveman / 输出精简 (4)

#### `caveman`

- **Official repo**: `JuliusBrussee/caveman` @ `3dc497a`
- **Installed path**: `/root/.codebuddy/skills/caveman` (2 files)
- **README reviewed**: yes (`caveman` root README) · **SKILL.md inspected**: yes
- **What it does**: 精简输出但保留全部技术事实
- **When to use / how to invoke**: Skill 工具按名称加载；`/caveman`、上下文压缩、最小补丁前
- **Limitation**: 是语气/输出风格，不改变代码与架构事实；加载 `/caveman status` 不算激活
- **Applied in Tolesh**: 输出精简但不丢技术事实；本轮状态汇报与文档保持高信噪比（未激活压缩语气）

#### `caveman-compress`

- **Official repo**: `JuliusBrussee/caveman` @ `3dc497a`
- **Installed path**: `/root/.codebuddy/skills/caveman-compress` (10 files)
- **README reviewed**: yes (`caveman` root README) · **SKILL.md inspected**: yes
- **What it does**: 上下文压缩
- **When to use / how to invoke**: Skill 工具按名称加载；`/caveman`、上下文压缩、最小补丁前
- **Limitation**: Standard Agent Skills format; no additional runtime dependency required for reading its instructions.
- **Applied in Tolesh**: 上下文压缩策略；长会话中的信息保留

#### `surgical-patch`

- **Official repo**: `JuliusBrussee/caveman` @ `3dc497a`
- **Installed path**: `/root/.codebuddy/skills/surgical-patch` (2 files)
- **README reviewed**: yes (`caveman` root README) · **SKILL.md inspected**: yes
- **What it does**: 最小改动式补丁（保护既有功能）
- **When to use / how to invoke**: Skill 工具按名称加载；`/caveman`、上下文压缩、最小补丁前
- **Limitation**: Standard Agent Skills format; no additional runtime dependency required for reading its instructions.
- **Applied in Tolesh**: 最小改动式补丁；保护上游既有功能，只做精确替换并逐项断言

#### `verify-and-stop`

- **Official repo**: `JuliusBrussee/caveman` @ `3dc497a`
- **Installed path**: `/root/.codebuddy/skills/verify-and-stop` (2 files)
- **README reviewed**: yes (`caveman` root README) · **SKILL.md inspected**: yes
- **What it does**: 验证后即停，避免过度工程
- **When to use / how to invoke**: Skill 工具按名称加载；`/caveman`、上下文压缩、最小补丁前
- **Limitation**: Standard Agent Skills format; no additional runtime dependency required for reading its instructions.
- **Applied in Tolesh**: 验证后即停；避免对已通过检查的重复改动

### 2.4 逆向工程 / 规格 (4)

#### `reverse-engineer-anything`

- **Official repo**: `morluto/rea` @ `d4a7c20`
- **Installed path**: `/root/.codebuddy/skills/reverse-engineer-anything` (9 files)
- **README reviewed**: yes (`rea` root README) · **SKILL.md inspected**: yes
- **What it does**: 二进制/打包产物逆向；仓库分析时不使用
- **When to use / how to invoke**: Skill 工具按名称加载；仅在需要二进制/打包产物逆向时
- **Limitation**: 安装技能不等于注册 REA MCP server；仓库明确说明普通源码仓库分析不应运行 REA
- **Applied in Tolesh**: 逆向方法论参照；本次为完整源码仓库分析，按其规则未运行 REA 引擎

#### `rea-analysis-optimization`

- **Official repo**: `morluto/rea` @ `d4a7c20`
- **Installed path**: `/root/.codebuddy/skills/rea-analysis-optimization` (3 files)
- **README reviewed**: yes (`rea` root README) · **SKILL.md inspected**: yes
- **What it does**: REA 分析流程优化
- **When to use / how to invoke**: Skill 工具按名称加载；仅在需要二进制/打包产物逆向时
- **Limitation**: Standard Agent Skills format; no additional runtime dependency required for reading its instructions.
- **Applied in Tolesh**: REA 分析流程优化参考；未对二进制目标使用

#### `spec-miner-architecture-designer`

- **Official repo**: `Jeffallan/claude-skills` @ `078a25c`
- **Installed path**: `/root/.codebuddy/skills/spec-miner-architecture-designer` (6 files)
- **README reviewed**: yes (`claude-skills` root README) · **SKILL.md inspected**: yes
- **What it does**: 架构规格推导（Spec Miner 集合内选型）
- **When to use / how to invoke**: Skill 工具按名称加载；仅在需要二进制/打包产物逆向时
- **Limitation**: Standard Agent Skills format; no additional runtime dependency required for reading its instructions.
- **Applied in Tolesh**: 架构规格推导；ARCHITECTURE.md 的模块图与数据流

#### `spec-miner-code-documenter`

- **Official repo**: `Jeffallan/claude-skills` @ `078a25c`
- **Installed path**: `/root/.codebuddy/skills/spec-miner-code-documenter` (9 files)
- **README reviewed**: yes (`claude-skills` root README) · **SKILL.md inspected**: yes
- **What it does**: 从源码生成规格文档
- **When to use / how to invoke**: Skill 工具按名称加载；仅在需要二进制/打包产物逆向时
- **Limitation**: Standard Agent Skills format; no additional runtime dependency required for reading its instructions.
- **Applied in Tolesh**: 从源码生成规格文档；COMPONENT-LIBRARY.md 的接口提取

### 2.5 Web 规范 / 无障碍 (3)

#### `fixing-accessibility`

- **Official repo**: `ibelick/ui-skills` @ `97dea9e`
- **Installed path**: `/root/.codebuddy/skills/fixing-accessibility` (1 files)
- **README reviewed**: yes (`ui-skills` root README) · **SKILL.md inspected**: yes
- **What it does**: ARIA/键盘/焦点/对比度/表单错误修复
- **When to use / how to invoke**: Skill 工具按名称加载；无障碍审查、UI 基线清理前
- **Limitation**: Standard Agent Skills format; no additional runtime dependency required for reading its instructions.
- **Applied in Tolesh**: ARIA/键盘/焦点/对比度修复；`ACCESSIBILITY.md` 的审计与整改

#### `baseline-ui`

- **Official repo**: `ibelick/ui-skills` @ `97dea9e`
- **Installed path**: `/root/.codebuddy/skills/baseline-ui` (1 files)
- **README reviewed**: yes (`ui-skills` root README) · **SKILL.md inspected**: yes
- **What it does**: 间距/层级/字阶/交互基线反 AI 味
- **When to use / how to invoke**: Skill 工具按名称加载；无障碍审查、UI 基线清理前
- **Limitation**: Standard Agent Skills format; no additional runtime dependency required for reading its instructions.
- **Applied in Tolesh**: 间距/层级/字阶反 AI 味基线；`transition: all` 等 4 处整改

#### `react-view-transitions`

- **Official repo**: `vercel-labs/agent-skills` @ `1a2c79d`
- **Installed path**: `/root/.codebuddy/skills/react-view-transitions` (9 files)
- **README reviewed**: yes (`agent-skills` root README) · **SKILL.md inspected**: yes
- **What it does**: 视图过渡规范（备用）
- **When to use / how to invoke**: Skill 工具按名称加载；无障碍审查、UI 基线清理前
- **Limitation**: 面向 React/Next 项目；Tolesh 当前前端为原生 ES modules，未应用
- **Applied in Tolesh**: 视图过渡规范备用；当前原生 JS 界面未引入 React，故未直接落地

### 2.6 测试 / 工程质量 (7)

#### `webapp-testing`

- **Official repo**: `anthropics/skills` @ `2c5958e`
- **Installed path**: `/root/.codebuddy/skills/webapp-testing` (6 files)
- **README reviewed**: yes (`skills` root README) · **SKILL.md inspected**: yes
- **What it does**: Web 应用端到端测试流程
- **When to use / how to invoke**: Skill 工具按名称加载；测试设计、代码审查、安全加固、文档编写前
- **Limitation**: Standard Agent Skills format; no additional runtime dependency required for reading its instructions.
- **Applied in Tolesh**: Web 端到端测试流程；Chromium/Playwright 回归脚本设计

#### `browser-testing-with-devtools`

- **Official repo**: `addyosmani/agent-skills` @ `301b02b`
- **Installed path**: `/root/.codebuddy/skills/browser-testing-with-devtools` (1 files)
- **README reviewed**: yes (`agent-skills` root README) · **SKILL.md inspected**: yes
- **What it does**: 浏览器 DevTools 验证
- **When to use / how to invoke**: Skill 工具按名称加载；测试设计、代码审查、安全加固、文档编写前
- **Limitation**: Standard Agent Skills format; no additional runtime dependency required for reading its instructions.
- **Applied in Tolesh**: 浏览器 DevTools 验证；console/pageerror 采集

#### `test-driven-development`

- **Official repo**: `addyosmani/agent-skills` @ `301b02b`
- **Installed path**: `/root/.codebuddy/skills/test-driven-development` (1 files)
- **README reviewed**: yes (`agent-skills` root README) · **SKILL.md inspected**: yes
- **What it does**: TDD 流程
- **When to use / how to invoke**: Skill 工具按名称加载；测试设计、代码审查、安全加固、文档编写前
- **Limitation**: Standard Agent Skills format; no additional runtime dependency required for reading its instructions.
- **Applied in Tolesh**: TDD 流程；新增模块先定义契约再实现

#### `code-review-and-quality`

- **Official repo**: `addyosmani/agent-skills` @ `301b02b`
- **Installed path**: `/root/.codebuddy/skills/code-review-and-quality` (1 files)
- **README reviewed**: yes (`agent-skills` root README) · **SKILL.md inspected**: yes
- **What it does**: 代码审查与质量门禁
- **When to use / how to invoke**: Skill 工具按名称加载；测试设计、代码审查、安全加固、文档编写前
- **Limitation**: Standard Agent Skills format; no additional runtime dependency required for reading its instructions.
- **Applied in Tolesh**: 代码审查与质量门禁；`node --check`、编译与残留违规扫描

#### `security-and-hardening`

- **Official repo**: `addyosmani/agent-skills` @ `301b02b`
- **Installed path**: `/root/.codebuddy/skills/security-and-hardening` (2 files)
- **README reviewed**: yes (`agent-skills` root README) · **SKILL.md inspected**: yes
- **What it does**: 密钥与输入安全加固
- **When to use / how to invoke**: Skill 工具按名称加载；测试设计、代码审查、安全加固、文档编写前
- **Limitation**: Standard Agent Skills format; no additional runtime dependency required for reading its instructions.
- **Applied in Tolesh**: 密钥与输入安全；环境变量密钥、`esc()` 转义、token 门

#### `documentation-and-adrs`

- **Official repo**: `addyosmani/agent-skills` @ `301b02b`
- **Installed path**: `/root/.codebuddy/skills/documentation-and-adrs` (1 files)
- **README reviewed**: yes (`agent-skills` root README) · **SKILL.md inspected**: yes
- **What it does**: 文档与架构决策记录
- **When to use / how to invoke**: Skill 工具按名称加载；测试设计、代码审查、安全加固、文档编写前
- **Limitation**: Standard Agent Skills format; no additional runtime dependency required for reading its instructions.
- **Applied in Tolesh**: 文档与架构决策记录；本套 8 份文档与归属/许可证决策

#### `ci-cd-and-automation`

- **Official repo**: `addyosmani/agent-skills` @ `301b02b`
- **Installed path**: `/root/.codebuddy/skills/ci-cd-and-automation` (1 files)
- **README reviewed**: yes (`agent-skills` root README) · **SKILL.md inspected**: yes
- **What it does**: CI/CD 与自动化
- **When to use / how to invoke**: Skill 工具按名称加载；测试设计、代码审查、安全加固、文档编写前
- **Limitation**: Standard Agent Skills format; no additional runtime dependency required for reading its instructions.
- **Applied in Tolesh**: CI/CD 参考；pytest 标记分层与可复现验证命令

## 3. Already present — reused, not reinstalled

These were found on the machine during step 1 and used directly. Reinstalling
would have been duplicate work.

| Skill | Source | Where applied in Tolesh |
| --- | --- | --- |
| `frontend-design` | `/workspace/.codebuddy/skills` (Anthropic `frontend-design`) | Anti-template calibration; headline word-accent and ALL-CAPS kicker removed |
| `web-design-guidelines` | `/workspace/.codebuddy/skills` (Vercel, v1.0.0) | Fetched the live `web-interface-guidelines/command.md` and audited all `web/` files |
| `ui-ux-pro-max` | `/workspace/.codebuddy/skills` | "Premium black + gold / AI-Native UI" direction → "Lagos Nocturne" |
| `ui-animation` | `/workspace/.codebuddy/skills` | Motion quality floor |
| `gsap-core`, `gsap-timeline`, `gsap-react`, `gsap-performance`, `gsap-utils`, `gsap-frameworks`, `gsap-plugins`, `gsap-scrolltrigger` | `/workspace/.codebuddy/skills` (from `greensock/gsap-skills`, MIT) | `web/js/motion.js`: `gsap.timeline()`, `gsap.matchMedia()`, transform/opacity-only, reduced-motion |
| `frontend-ui-engineering`, `web-design-guidelines`, `fixing-accessibility`, `baseline-ui` equivalents | also present in the podo catalogue as stubs | Deliberately installed the **full** upstream versions instead (§2) |

## 4. Unavailable, incompatible, or name-mismatched (honest record)

| Requested | Finding | Action |
| --- | --- | --- |
| `a16z/lean-rea` | `git clone` and `git ls-remote` both fail with `could not read Username for 'https://github.com'` — repo private or does not exist | Not installed. Compared Spec Miner vs REA first as asked; installed `morluto/rea` (2 skills) + 2 Spec Miner skills |
| `microsoft/playwright-skills` | Repo does not exist. **Playwright is a testing library, not an agent skill** — the brief explicitly warns against confusing the two | Not installed as a skill. Playwright (Python) was used for real browser verification instead |
| `review-animations`, `improve-animations`, `animation-performance` | **No such SKILL.md** in `podo/design-agent-skills` (verified by listing all motion-related dirs). Real names are `motion-design-skill`, `animate-skill`, `fixing-motion-performance`, `design-motion-principles` | Installed the full upstream `design-motion-principles`, `fixing-motion-performance` and `improve-ui` which cover the same capabilities |
| `podo/design-agent-skills` skills (`framer-motion-skills`, `animate-skill`, `css-animation-skill`, `motion`, …) | All are **catalogue stubs** pointing to upstream repos, not runnable skills | Not installed as-is; upstream full implementations used instead |
| Emil Kowalski design-engineering skills | `emilkowalski/animations-on-the-web` unreachable. `kylezantos/design-motion-principles` explicitly states it is based on Emil Kowalski's restraint principles | Installed `design-motion-principles` (full upstream) as the concrete representative |
| Framer Motion / motion.dev upstream parsing error | No reproducible record of that parse error exists in the accessible session history; the podo `framer-motion-skills` entry is a stub | Not reproduced, not claimed fixed. GSAP (fully installed + vendored) is the animation engine actually used |
| `npx skills add … -a claude-code` | Installs into `.claude/skills/`, invisible to this CodeBuddy agent | Recorded as a compatibility finding; skills installed to `/root/.codebuddy/skills/` instead |
| `npx impeccable install` | Requires the Impeccable CLI and a Claude/Cursor-style agent layout | Impeccable installed from its official repo by copying `.claude/skills/impeccable` (58 files incl. references/scripts) |

## 5. Skills actually applied across the project lifecycle

| Phase | Skills | Concrete effect |
| --- | --- | --- |
| Before interface development | `ui-ux-pro-max`, `frontend-design`, `impeccable`, `design-taste-frontend` | Defined "Lagos Nocturne" token system, palette, type scale, layout and anti-template rules |
| During frontend implementation | `frontend-ui-engineering`, `theme-factory`, `brand-guidelines`, `api-and-interface-design` | Built `web/` components, tokens, brand SVGs, API client and SSE contract |
| During animation | `gsap-core`, `gsap-timeline`, `gsap-performance`, `design-motion-principles`, `fixing-motion-performance`, `ui-animation` | One orchestrated load timeline, `gsap.matchMedia()`, transform/opacity only, reduced-motion, no endless autoplay loop |
| During source transformation | `surgical-patch`, `verify-and-stop`, `caveman`, `spec-miner-architecture-designer`, `code-documenter`, `security-and-hardening` | Precise rebrand without breaking upstream; new modules added in parallel namespaces; keys kept in env |
| During QA | `web-design-guidelines`, `fixing-accessibility`, `baseline-ui`, `improve-ui`, `webapp-testing`, `browser-testing-with-devtools`, `code-review-and-quality`, `test-driven-development` | Live-guideline audit produced ~20 real findings, all fixed; Chromium regression; `node --check`; pytest |

## 6. Verification commands

```bash
# Confirm the agent can load an installed skill
# (Skill tool: skill="caveman" → base directory /root/.codebuddy/skills/caveman)
ls -la /root/.codebuddy/skills | head -40
npx skills list            # shows project + global skills
python3.11 - <<'PY'
import pathlib, re
root = pathlib.Path('/root/.codebuddy/skills')
for d in sorted(root.iterdir()):
    f = d / 'SKILL.md'
    if not f.is_file(): continue
    t = f.read_text(encoding='utf-8')
    ok = bool(re.match(r'^---\s*\n.*?name:.*?\n---', t, re.S))
    print(d.name, 'frontmatter_ok' if ok else 'BAD')
PY
```

## 7. What was deliberately NOT claimed

- No skill is called installed merely because its repository was cloned.
- The `npx skills add -a claude-code` result is reported as a compatibility
  finding, not a successful agent-visible install.
- Stub catalogue entries, unreachable repositories and non-existent skill
  names are listed in §4 rather than being silently dropped.
- Skills whose instructions were read but whose runtime prerequisite was
  absent (e.g. Impeccable's launcher, tasteskill's Node/Chrome gate hook) state
  that limitation explicitly.
