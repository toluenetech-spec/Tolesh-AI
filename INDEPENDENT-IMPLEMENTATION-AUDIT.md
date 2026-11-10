# Tolesh — 独立实施与项目状态报告

> **性质**：实施审计（implementation audit），不是进度公告。本报告只描述当前工作区里**真实存在**的应用。
> **方法**：只读源文件和已有产物；重新运行测试、真实浏览器回归、真实桌面启动尝试、HTTP 探测与 API 实测。
> **未做的事**：未修改任何源代码、未创建占位实现、未改动任何测试。`git status --short` 在审计前后均为
> `28` 个已跟踪改动（`M`）+ `35` 个未跟踪条目（`??`），数量一致；浏览器回归与临时产物全部输出到 `/tmp`，
> 未覆盖工作区文件。
> **环境**：Ubuntu 24.04.3 LTS 无头沙箱（非 Zorin OS），`DISPLAY=:0`，无
> `XDG_CURRENT_DESKTOP`、`DBUS_SESSION_BUS_ADDRESS`，`/dev/snd` 为空、无 `arecord`、
> 无 `libxcb-cursor0`。这些限制会如实反映在各项结论里。

审计命令在 2026-11-10 执行。所有结论以文件路径、命令与实际输出为依据；凡是仅由"模块/文件存在"
或"单元测试通过"支撑的能力，一律不标记为已验证可用。

---

## 0. 审计后的变更（重要，阅读正文前请先看这里）

本报告是**某一时刻的快照**。审计完成后执行了「统一应用集成」里程碑，其中若干条审计结论
**已经不再成立**。为免读者误用旧结论，逐条列出：

| 审计中的结论 | 现在的状态 | 证据 |
| --- | --- | --- |
| 三个互不相连的组件（PyQt6 壳 / 语音守护进程 / FastAPI 后端） | **已合并为一个应用**：`desktop_app` 在进程内启动后端，AI 窗口加载 `web/` | `src/tolesh/server/embedded.py`、`src/desktop_app/ai_window.py`、`src/desktop_app/app.py:2338-2421` |
| `src/desktop_app/app.py` 不 import 新模块 | **已 import 并调用**：`start_ai_backend` / `show_ai_window` / `stop_ai_backend`，`main()` 在启动时调用 | `app.py:3929`、`app.py:3955-3958` |
| `AppState()` 默认 `:memory:`，重启即丢 | **已改为持久库** `$TOLESH_DATA_DIR/tolesh.db`，四个存储共用 WAL 连接 | `src/tolesh/server/app.py:56-79`、`src/tolesh/_sqlite.py` |
| `requirements.txt` 缺 `fastapi`/`uvicorn`/`httpx` | **已补齐** | `requirements.txt` |
| `tolesh_desktop.spec` 不含 `tolesh.server`/`tolesh.ai`/`web/` | **已补齐** hiddenimports 与 `web/` datas | `tolesh_desktop.spec` |
| 桌面启动因缺 `libxcb-cursor0` 崩溃（`DESKTOP_EXIT=134`） | **已在真实 X 服务器（Xvfb + xcb）下成功启动并截图** | `docs/desktop-launch-report.json`、`docs/img/tolesh-desktop-integrated.png` |
| 全套测试 `1 failed, 5944 passed` | **`5961 passed, 27 skipped, 0 failed`**；原唯一失败已修复 | `TESTING.md` §8–§9 |
| `scripts/run_linux.sh` 启动的是无头守护进程 | **默认启动桌面应用**，`--daemon` 保留原行为，并增加 Qt 依赖预检 | `scripts/run_linux.sh` |

**仍然成立**的审计结论：无 provider 凭据（真实 provider 未验证）、无 `/dev/snd` 与 `arecord`
（原生语音未验证）、无系统托盘宿主（Xvfb 无 tray）、商业分发仍被非商业许可阻塞。

---

## 1. 实际实施

分类标尺：

| 级别 | 含义 |
| --- | --- |
| **1** | Fully implemented and verified — 完整实现，且有端到端验证 |
| **2** | Implemented but not fully verified — 代码完整，仅有单元/API/浏览器级验证，无真实环境端到端验证 |
| **3** | Partially implemented — 部分实现，或存在明确缺口/断连 |
| **4** | Documentation only — 仅文档/声明 |
| **5** | Not implemented — 未实现 |

| 功能 | 级别 | 主要证据（文件路径 / 行号 / 命令输出） | 说明 |
| --- | --- | --- | --- |
| 后端实现 | **2** | `src/tolesh/server/app.py`（295 行，`/api/*` 路由 118–285 行，`app = create_app()` 295 行）；`src/tolesh/server/schemas.py`；`tests/test_tolesh_server.py` 用 `TestClient` → `33 passed`；`scripts/verify_web_ui.py` → `VERIFY_EXIT=0` | FastAPI + SSE 服务端代码完整、API 契约测试与浏览器回归通过。但它是一条**独立轨道**：`src/tolesh/daemon.py`、`src/desktop_app/app.py` 都不导入它，PyInstaller 打包也不包含它（见 §3）。 |
| 前端实现 | **2** | `web/index.html`、`web/js/app.js`（475 行）、`api.js`（79 行）、`components.js`、`motion.js`、`voice.js`、`web/styles/*.css`、`web/vendor/gsap.min.js`；浏览器回归 `passed: true`、`console_and_page_errors: []` | 静态前端真实存在且可交互（会话、模型选择、记忆、用量、工具、权限审批面板均已接线，见 `web/js/app.js:126–132, 368–398`）。但只由手工启动的 uvicorn 提供，**桌面应用不加载它**（`app.py:1684` 的 `QWebEngineView` 只打开 Memory Viewer 本地端口）。 |
| 桌面应用 | **2** | `src/desktop_app/app.py`（3879 行）、`ToleshSystemTray`（1776 行）、`QSystemTrayIcon`（1859 行）、`settings_window.py`、`setup_wizard.py`、`chat_window.py`、`memory_viewer.py`；`scripts/run_desktop_app.sh` | 继承自上游的完整 PyQt6 桌面应用（聊天窗、系统托盘、设置、向导、记忆查看器、更新器）。真实启动尝试：`QT_QPA_PLATFORM=offscreen` → `EXIT=124`（持续运行，进入 setup wizard）；`xvfb-run` + xcb → `EXIT=134`（`Could not load the Qt platform plugin "xcb"`）。代码能初始化，但**本次环境无法显示图形界面**，无真实 Linux 桌面端到端验证。 |
| AI provider 集成 | **3** | 继承：`src/tolesh/llm/ollama.py`（421 行）、`openai_compatible.py`（532 行）、`http.py`、`factory.py`，真实 `requests` HTTP；新增：`src/tolesh/ai/providers/openai_compatible.py`（httpx 适配器）、`offline.py`、`registry.py`（323 行，`_catalog()` 117–211 行声明 9 个 provider） | 两套 provider 实现并存。**新增的 9 个 provider 只是 registry 声明**：`configured` 依赖环境变量 / `~/.config/tolesh/providers.json`；实测 `TOLESH_*` 变量为空、配置文件不存在；`api.jev.chat`、`api.gonka.ai`、`api.dahl.ai` 在当前沙箱 `HTTP 000`（无法连接），`api.deepseek.com` `401`（需凭据），本地 Ollama `11434` `000`（未运行）。**没有一次真实 provider 认证/流式/切换验证**。 |
| 模型路由与回退 | **2** | `src/tolesh/ai/router.py`（221 行）：`ModelRouter.stream` 78–186 行含 `select → 重试/指数退避 → reset 清除部分输出 → fallback → usage/done`；`registry.py:TASK_MODEL_ORDER` 与 `candidates()` 269–317 行；`tests/test_tolesh_core.py` 覆盖 | 任务级选模、重试、可重试状态码、失败切换、离线兜底、用量记账代码完整且单测通过。但**从未用真实 provider 端到端验证**，仅在离线引擎和 `TestClient` 上跑过。 |
| 对话管理 | **3** | 新增 `src/tolesh/conversation/manager.py`（122 行，SQLite `CREATE TABLE`）；继承 `src/tolesh/memory/conversation.py`、日记/摘要 | 两套并存且**不互通**：server 轨道用 `ConversationManager`，daemon 轨道用继承的 `DialogueMemory`。server 默认 `:memory:`（见下），重启即丢。 |
| 持久记忆 | **3** | 新增 `src/tolesh/memory_ext/store.py`（177 行，SQLite）；继承 `src/tolesh/memory/db.py`（`Database(db_path)`，`config.py:_default_db_path()` → `~/.local/share/tolesh/tolesh.db`）；`src/tolesh/server/app.py:48–55` | 存储层代码支持磁盘持久化（继承轨道默认用磁盘 DB），但 **server 模块级 `app = create_app()` 全部使用 `:memory:`**，且 `src/tolesh/server/` 内不存在任何把 `db_path` 指向磁盘的配置入口。实测见 §2。 |
| 语音识别与语音生成 | **2** | 继承 `src/tolesh/listening/listener.py`（2664 行）、`wake_detection.py`（115 行）、`echo_detection.py`、`intent_judge.py`、`whisper_capabilities.py`（faster-whisper + webrtcvad）；`src/tolesh/output/tts.py`（1201 行，Piper / Chatterbox）；`src/tolesh/config.py` 含 `wake_word`(170)、`vad_enabled`(186)、`tts_enabled`(143) 全套配置；44 个语音相关测试文件 | 原生管线代码规模大、功能齐全（唤醒词、VAD、STT、TTS、打断/回声消除）。但 `/dev/snd` 为空、无 `arecord`，所有语音测试用 `monkeypatch` 替换 `InputStream`（如 `tests/test_audio_capture_pipeline.py:154`）——**零真实设备端到端验证**。`web/js/voice.js` 是浏览器 Web Speech API，其第 7–10 行自述唤醒词/神经 VAD/原生 TTS 不属于该模块，**不能替代原生桌面语音**。 |
| 电脑使用能力 | **3** | `src/tolesh/tools_ext/computer.py`（122 行）实现 `get_time`、`system_info`、`list_directory`、`read_text_file`、`write_text_file`、`open_application`；`permissions/policy.py:21–34` `TOOL_POLICY`；`web/js/app.js:364–371` 调用 `/api/tools/invoke` | 6 个安全/提升级动作有真实实现（含路径沙箱、应用白名单、200 KB 读限制）。`_NOT_IMPLEMENTED`（`computer.py:26–29`）明确拒绝 `delete_file`、`run_command`、`shutdown`、`restart`、`send_message`、`network_request`。任务要求的"搜索选定目录""导航允许文件夹"**没有独立工具**（仅 `list_directory`）；未在真实桌面验证打开应用/写文件。 |
| 权限与安全系统 | **2** | `src/tolesh/permissions/gate.py`（179 行，审批令牌 + SQLite 审计）、`policy.py`（88 行，三级风险 + 路径边界）、`server/app.py:106–115` `TOLESH_API_TOKEN` 守卫、`registry.py:redacted()` 密钥脱敏、`tests/test_tolesh_core.py`/`test_tolesh_server.py` | 代码完整、单测通过、前端有审批面板（`web/index.html:139`、`app.js:217–222, 383–400`）。但仅 API/单元级验证，无真实桌面端到端。注意 `policy.py:66–88` 默认沙箱根为整个 `Path.home()`，边界偏宽。 |
| 配置与设置 | **3** | `src/tolesh/config.py`（`load_settings`）、`src/desktop_app/settings_window.py:143–171`、`examples/config.json`、`src/tolesh/ai/registry.py:CONFIG_PATH` | 继承的设置窗口只提供 `ollama` / `openai_compatible` 两种选择（`settings_window.py:150–151`），**没有**新 registry 里 9 个 provider 的配置；`web/` 全库 `grep -iE 'api.key|type="password"|providers.json|base_url'` 无命中，前端只在缺密钥时弹提示（`app.js:177`）。新 provider 只能靠环境变量或手写 `~/.config/tolesh/providers.json`（该文件实测不存在）。 |
| 打包与安装 | **3** | `tolesh_desktop.spec`（`Analysis(['src/desktop_app/app.py'])` 263 行；hiddenimports 含 `tolesh.main`、`tolesh.memory.conversation`、`tolesh.tools.registry` 等继承模块）、`scripts/build_installer.sh:29`、`scripts/run_linux.sh:11,16`、`requirements.txt` | spec **不含** `tolesh.server` / `tolesh.ai` / `web/` / `fastapi` / `uvicorn`；`requirements.txt` **未声明 fastapi / uvicorn / httpx / starlette**（仅 pydantic 为 MCP），而 `run_linux.sh` 只 `pip install -r requirements.txt` → 全新环境装不上新 server。当前环境碰巧已装（fastapi 0.128.2、uvicorn 0.40.0、httpx 0.28.1），但这不构成安装正确性。未产出或验证任何安装包。 |
| 后台服务 | **2** | `src/tolesh/daemon.py`（1363 行，`main()` 854 行、`while True` 1217 行）、`daemon_lock.py`、`src/desktop_app/app.py:ToleshSystemTray`；`scripts/run_linux.sh:16` `python -m tolesh.daemon`；`src/tolesh/main.py` | 继承的语音守护进程 + 系统托盘代码完整且为 Linux 启动脚本的实际入口。但它走继承轨道，与新 server/新模块**完全无连接**；无真实桌面会话验证。 |

---

## 2. 实际应用可用性

> 严格区分"API 测试 / 单元 / 浏览器自动化"与"真实应用可用性"。下表"能否"一列指**当前工作区、当前环境、端到端**。

| 能力 | 当前能否 | 证据 |
| --- | --- | --- |
| 作为 Linux 桌面应用启动 | **受限** | `xvfb-run` + xcb：`DESKTOP_EXIT=134`，`Could not load the Qt platform plugin "xcb"`（缺 `libxcb-cursor0`，`ldconfig -p \| grep -c libxcb-cursor` → `0`）。`QT_QPA_PLATFORM=offscreen`：`EXIT=124`，输出 `Setup required - launching setup wizard...` 后持续运行。代码可初始化，GUI 未在真实桌面显示。 |
| 显示图形界面 | **否（本次环境）** | 同上；无 `XDG_CURRENT_DESKTOP`、无 D-Bus 会话。 |
| 连接真实 AI provider | **否** | `TOLESH_*` 配置变量为空、`~/.config/tolesh/providers.json` 不存在；`/api/health` 实测 `providers_configured: ["offline","ollama"]`、`default_model: null`；`offline` 是规则脚本，`ollama` 本地端点 `HTTP 000`；`api.jev.chat` / `api.gonka.ai` / `api.dahl.ai` 在沙箱 `HTTP 000`，`api.deepseek.com` `401`。 |
| 进行对话 | **是（仅 server+web 轨道，离线引擎）** | `POST /api/chat` SSE、会话 CRUD、消息窗口均实现；浏览器回归 `streamed_reply: 'How far! I dey here, no '`。该回复来自 `src/tolesh/ai/providers/offline.py`（第 3 行自述 "This is NOT a language model"），**不是真实模型回答**。 |
| 流式响应 | **是（SSE 通路，内容为离线脚本）** | `app.js:chatStream`、`server/app.py:139–171`；浏览器回归无 JS 错误。 |
| 重启后记住信息 | **否（server 轨道实测）** | `AppState()` 默认 `":memory:"`（`app.py:48–55`），模块级 `app = create_app()` 无参数。实测：`POST /api/memory` → 重启前 `stats.total = 1` → 新 `AppState()` 后 `stats.total = 0`、`items = []`；`src/tolesh/server/` 内无任何磁盘 DB 配置入口。继承 daemon 默认用 `~/.local/share/tolesh/tolesh.db`（代码存在），但**未在真实重启中端到端验证**，且与 server 记忆不是同一份。 |
| 通过麦克风收听 | **否** | `/dev/snd` 条目数 `0`、无 `arecord`；语音测试用 `monkeypatch.setattr(capture.sd, 'InputStream', ...)`。 |
| 通过扬声器说话 | **否** | 同上；`output/tts.py` 代码存在，无设备验证。 |
| 检测唤醒词 | **否** | `wake_detection.py` / `config.py:wake_word` 存在；无音频输入，零端到端验证。 |
| 执行经批准的电脑动作 | **部分** | `ComputerTools.execute` 6 个动作 + `/api/tools/invoke` + `/api/permissions/{id}/approve` + 前端审批面板均存在；但 6 个敏感动作明确拒绝，无搜索/导航工具，且**未在真实桌面打开应用或写文件**。 |
| 从 provider / 应用故障恢复 | **部分** | `ModelRouter.stream` 有重试、退避、丢弃部分输出、切换下一模型、离线兜底（代码 + 单测）；`updater.py` 有安装失败恢复测试。但**无真实 provider 故障注入、无真实应用崩溃/重启恢复的端到端验证**。 |

`docs/verification-report.json`（既有）与本次 `/tmp/tolesh-audit-browser-report.json` 的 `health` 一致：
`providers_configured: ["offline","ollama"]`、`default_model: null`——即此前所有"浏览器验证通过"都建立在**离线规则引擎**之上。

---

## 3. 代码库检查

### 3.1 主入口点

| 入口 | 实际内容 | 属于哪条轨道 |
| --- | --- | --- |
| `scripts/run_linux.sh:16` | `python -m tolesh.daemon` | 继承：语音守护进程 |
| `src/tolesh/main.py:9` | `from .daemon import main` | 继承 |
| `src/tolesh/daemon.py:854` | `main(smoke_test=False)`，`while True` 1217 行 | 继承 |
| `src/desktop_app/__main__.py` → `src/desktop_app/app.py` | PyQt6 GUI、系统托盘、设置/向导 | 继承 |
| `src/tolesh/server/app.py:4,295` | `uvicorn tolesh.server.app:app --port 8787`（**仅 docstring**）、`app = create_app()` | 新增：FastAPI |
| `tolesh_desktop.spec:263` | `Analysis(['src/desktop_app/app.py'])` | 打包只收继承轨道 |

`src/tolesh/server/app.py:7` 声称"the desktop QtWebEngine shell (`desktop/tolesh_desktop.py`)"消费该 API——
`find . -name 'tolesh_desktop.py'` **无输出，该文件不存在**。同文件 8–9 行的 "future Cloudflare Pages web client"、
"future Android client" 是规划，不是实现。

### 3.2 继承功能（上游 `isair/jarvis`，`git ls-files src/tolesh` = 121 个已跟踪文件）

`llm/`（Ollama + OpenAI-compatible 真实 HTTP）、`memory/`（SQLite 图/向量记忆、日记、摘要）、
`listening/`（2664 行 `VoiceListener`：唤醒词、webrtcvad、faster-whisper、回声消除、意图判定）、
`output/tts.py`（Piper / Chatterbox）、`dictation/`、`tools/`（内置工具 + MCP 客户端）、
`reply/`、`daemon.py`、`daemon_lock.py`、`config.py`、整个 `src/desktop_app/`、320 个测试文件中的绝大多数。

### 3.3 Tolesh 新增功能（全部为 untracked，`tracked=0`）

`src/tolesh/ai/`（16 文件：registry / router / usage / providers）、`conversation/`（4）、
`memory_ext/`（4）、`permissions/`（6）、`persona/`（4）、`server/`（6）、`tools_ext/`（4）、
`web/`（19 文件）、`scripts/verify_web_ui.py`、`tests/test_tolesh_core.py`、`tests/test_tolesh_server.py`、
以及根目录的 8 份设计/合规文档。

### 3.4 Mock 实现 / 替代实现

| 项 | 性质 | 位置 |
| --- | --- | --- |
| `OfflineProvider` | **不是 mock，但是规则脚本替代真实模型**；自述 "NOT a language model"，按关键词返回固定 Pidgin/Yoruba 文案 | `src/tolesh/ai/providers/offline.py:1–3, 34–97` |
| `_StubServer` | 进程内 OpenAI 形状 HTTP stub；测试文件第 3–8 行明确写 "without depending on a live vendor server" | `tests/test_openai_compatible_e2e.py:28–90` |
| 音频设备 | `monkeypatch` 替换 `sounddevice.InputStream` / `query_devices` | `tests/test_audio_capture_pipeline.py:154, 173, 215` 等 |
| API 测试 | `fastapi.testclient.TestClient` + `AppState(in_memory=True)`，`monkeypatch` 环境变量 | `tests/test_tolesh_server.py:24–36`、`test_tolesh_core.py` |
| 断网 fixture | `no_network` 阻断 `socket.connect`（仅放行 `AF_UNIX`） | `tests/conftest.py` |

**源码中没有用于冒充真实产品的 mock provider**；但要清楚：`offline` 引擎承担了当前所有"对话"演示，它不是 AI 模型。

### 3.5 占位函数 / 未实现动作

- `src/tolesh/tools_ext/computer.py:26–29` `_NOT_IMPLEMENTED = {delete_file, run_command, shutdown, restart, send_message, network_request}`；`execute()` 59–61 行对它们返回 `{"status":"refused"}`。这是**有意的安全边界**，不是遗漏。
- `src/tolesh/tools/external/mcp_client.py:200, 297` `raise NotImplementedError`（仅支持 `stdio` transport）— 继承限制。
- `src/desktop_app/setup_wizard.py:398, 410–411` 为可选 location 特性提供导入期 stubs。
- 全库 `^\s*(pass|return None|return \{\})$` 命中集中在 `utils/vram.py`、`utils/location.py`、
  `listening/state_manager.py`、`debug.py` 等，属于可选依赖降级与异常处理，不是功能占位。

### 3.6 死代码 / 断连代码（最重要的架构发现）

1. **新增模块只被 `src/tolesh/server/app.py:32–37` 引用**。全库 `grep` 显示 `ModelRouter` /
   `LayeredMemory` / `ConversationManager` / `PermissionGate` / `ComputerTools` 的 import **只出现在
   server、模块自身的 `__init__.py` 与测试中**。`src/desktop_app/app.py`（3879 行）与
   `src/tolesh/daemon.py`（1363 行）的 import 列表里没有任何一个新模块。
2. **没有任何启动脚本启动 FastAPI server**。`uvicorn` 只出现在 `server/app.py:4` 与
   `scripts/verify_web_ui.py:6` 的 docstring（手工说明）。`run_linux.sh` 启动的是 `tolesh.daemon`。
3. **打包不包含新增轨道**：`tolesh_desktop.spec` 的入口是 `src/desktop_app/app.py`，hiddenimports
   无 `tolesh.server` / `tolesh.ai` / `web/`，`datas` 也不含 `web/`。桌面安装产物里不会有新后端或新前端。
4. **文档引用了不存在的文件**：`server/app.py:7` 的 `desktop/tolesh_desktop.py`。
5. **两套记忆、两套 LLM、两套工具互不连通**：server 用 `memory_ext`/`conversation`/`ai`/`tools_ext`；
   daemon 用 `memory.db`/`memory.conversation`/`llm`/`tools`。用户在桌面端存入的记忆不会出现在 web 端，反之亦然。

### 3.7 架构问题汇总

- **三条未合并的轨道**：继承的语音 daemon、继承的 PyQt6 桌面 GUI、新增的 FastAPI + 静态 web。
  产品打包只覆盖第二条；浏览器回归只覆盖第三条。
- **server 默认全内存**：`AppState()` → 4 个 SQLite 对象都是 `":memory:"`；无磁盘配置入口（§2 实测重启丢记忆）。
- **依赖声明缺失**：`requirements.txt` 缺 fastapi/uvicorn/httpx/starlette，无 `pyproject.toml`/`setup.py`。
- **provider 配置断点**：registry 声明 9 个 provider，但图形设置只支持 2 种（且是继承的 Ollama/OpenAI-compatible），
  web 前端无密钥输入界面。
- **安全边界偏宽**：`permissions/policy.py:66–88` 默认允许整个 `Path.home()`，仅排除少量 dot 目录。
- **语音双实现**：原生 Python 管线（`listening/` + `output/tts.py`）与浏览器 Web Speech（`web/js/voice.js`）
  并存；后者不能替代前者。

---

## 4. 测试证据

### 4.1 自动化测试（本次重新运行）

| 范围 | 命令 | 实际输出 |
| --- | --- | --- |
| 完整套件 | `xvfb-run -a env PYTHONPATH=src python3.11 -m pytest -q`（4m43s） | `1 failed, 5944 passed, 27 skipped, 2 deselected, 3 warnings in 281.22s`；`PYTEST_EXIT=1` |
| 唯一失败 | 同上 | `FAILED tests/test_wizard_layout.py::test_controls_fit_on_short_display[960-780-OpenAICompatiblePage]` → `assert scroll.verticalScrollBar().maximum() == 0` / `E assert 13 == 0`（`tests/test_wizard_layout.py:61`） |
| 后端核心 + 服务端契约 | `PYTHONPATH=src python3.11 -m pytest tests/test_tolesh_core.py tests/test_tolesh_server.py -q` | `33 passed in 0.77s`（使用 `TestClient` + `in_memory=True`） |
| 桌面 wizard 布局 | `xvfb-run -a env PYTHONPATH=src python3.11 -m pytest tests/test_wizard_layout.py -q` | `1 failed, 40 passed in 7.31s`（同一 960×780 溢出） |
| 浏览器回归 | 先 `PYTHONPATH=src TOLESH_WEB_DIR=web python3.11 -m uvicorn tolesh.server.app:app --port 8787`，再 `PYTHONPATH=src python3.11 scripts/verify_web_ui.py --url http://127.0.0.1:8787 --report /tmp/... --out /tmp/...` | `VERIFY_EXIT=0`；`passed: true`、`errors: []`、`console_and_page_errors: []`、`reduced_motion_ok: true`；`streamed_reply: 'How far! I dey here, no '`；`health.providers_configured: ["offline","ollama"]` |
| 许可一致性 | 14 项仓库级扫描（上一轮审计） | `14 PASS / 0 FAIL` |

`TESTING.md:196–210` 记录的最终结果与本次重跑一致（`1 failed, 5944 passed, 27 skipped, 2 deselected`），
说明该失败是**稳定存在**的，未通过放宽断言或删除测试消除。

### 4.2 Provider 集成结果

- **无真实 provider 集成测试**。`tests/test_openai_compatible_e2e.py:3–8` 自述是进程内 stub，
  "without depending on a live vendor server"；`test_tolesh_core.py`/`test_tolesh_server.py` 用
  `monkeypatch` 设假密钥并断言脱敏，不发起真实请求。
- 端点探测（无凭据，仅验证可达性）：

  | 端点 | 结果 |
  | --- | --- |
  | `https://api.jev.chat/v1` | `HTTP 000`（当前沙箱无法连接） |
  | `https://api.gonka.ai/v1` | `HTTP 000` |
  | `https://api.dahl.ai/v1` | `HTTP 000` |
  | `https://api.deepseek.com/v1` | `HTTP 401`（需凭据） |
  | `https://api.openai.com/v1` | `HTTP 404`（根路径） |
  | `https://api.groq.com/openai/v1` | `HTTP 404` |
  | `https://openrouter.ai/api/v1` | `HTTP 404` |
  | `http://127.0.0.1:11434/v1` | `HTTP 000`（Ollama 未运行） |

  `000` 只说明**当前沙箱内无法连接**，不能据此断言域名不存在；但足以证明**没有完成任何真实连接验证**。
  认证、模型发现、流式、切换、自动回退、限流处理、超时/连接失败处理、用量跟踪均**未用真实凭据验证**。

### 4.3 语音集成结果

- 44 个语音相关测试文件；`tests/test_audio_capture_pipeline.py` 等用 `monkeypatch` 替换
  `sounddevice.InputStream`（第 154、173、215、237 行），`tests/test_voice_listener.py`、
  `test_piper_tts.py` 等使用 `Mock`/`patch`。
- `/dev/snd` 条目数 `0`、`arecord` 不存在。麦克风输入、VAD、STT、TTS、唤醒词、打断、
  连续对话、设备不可用恢复**全部没有真实设备端到端验证**。
- `web/js/voice.js` 的浏览器 Web Speech 有 barge-in 逻辑，但按项目要求不算原生桌面语音。

### 4.4 真实世界 / 人工验证（本次实际执行，非自动化套件）

| 项目 | 命令 | 结果 |
| --- | --- | --- |
| 桌面 GUI 启动 | `timeout 25s xvfb-run -a env PYTHONPATH=src python3.11 -m desktop_app` | `DESKTOP_EXIT=134`，Qt xcb 插件加载失败 |
| 桌面初始化（离屏） | `timeout 20s env QT_QPA_PLATFORM=offscreen PYTHONPATH=src python3.11 -m desktop_app` | `OFFSCREEN_EXIT=124`，进入 setup wizard 后持续运行 |
| server 记忆重启 | `AppState()` → `POST /api/memory` → 新 `AppState()` → `GET /api/memory` | `total 1 → 0`，`items = []` |
| 真实浏览器 | Playwright + Chromium 对 uvicorn 上运行的 web 前端 | 通过，但仅离线引擎 |
| 环境阻塞 | `env` / `ls /dev/snd` / `command -v arecord` / `ldconfig -p` | 无凭据、无音频设备、无 `libxcb-cursor0`、无桌面会话 |

**未完成/失败项**：wizard 960×780 溢出（`assert 13 == 0`）；桌面 GUI 在本次环境无法渲染；
真实 provider / 原生语音 / 跨重启记忆 / 真实桌面电脑动作**均无端到端验证**。

---

## 5. 许可

### 5.1 精确的上游限制

`LICENSE`（与 `LICENSES/isair-jarvis-LICENSE.txt`、`/workspace/_sources/jarvis/LICENSE` 字节一致，
sha256 前缀 `7f8a55ae4137087d`）为 **"Jarvis AI Assistant License"，Copyright (c) 2025 Baris Sencan**：

1. 仅授予**非商业目的**的使用、复制、修改、合并、发布、分发权。
2. 所有副本必须保留版权声明与许可声明。
3. **不得出售、出租、租赁或以其他方式商业化**本软件。
4. **任何衍生作品亦按相同条款授权**——Tolesh 作为派生作品继承该限制。
5. 商业使用须另行取得版权所有者的单独书面许可（联系方式 `baris@writeme.com`）。

另：`FatihMakes/Mark-LV` 为 **CC BY-NC 4.0**（仅概念借鉴，未复制代码）；
`jev-chat/jev-chat-jarvis` 为 **MIT + NOTICE**（仅勘察，未作为代码基础，因此**不能**改善 Tolesh 的条款）。

### 5.2 第三方义务

| 资产 | 义务 | 状态 |
| --- | --- | --- |
| GSAP 3.13.0（`web/vendor/gsap.min.js`） | 专有 GSAP Standard "No Charge" License，须保留 header、不得移除声明 | header 已保留（`/*! GSAP 3.13.0 ... @license Copyright 2025, GreenSock ...`）；**商业发布需律师审查** |
| Bodoni Moda / Jost woff2 | SIL OFL 1.1，须随附许可文本 | `LICENSES/OFL-Bodoni-Moda.txt`、`OFL-Jost.txt` 已随附 |
| PyQt6 | GPL / 商业双许可 | **未做律师审查** |
| Python 依赖树 | 各自许可 | **未做 `pip-licenses` 清单审计** |

### 5.3 商业分发阻塞（仍然有效）

- **商业分发不被允许**，除非取得 Baris Sencan 的单独书面商业许可；未接触版权所有者、未获许可、未取得法律意见。
- **公开站点 licence 链接仍为 404**：`website/site.json` 指向未发布的 `tolesh-ai/tolesh`，生成输出中
  7 个该仓库链接 + 4 个 canonical `barissencan.com/tolesh/` 链接均 `404`（`LICENSING-ASSESSMENT.md §6b/§6c/§9`）。
- `site.json` `source_branch: main` 与本地 `master` 不一致（已记录，未改）。
- **本审计及此前的文档改动不产生、不暗示任何商业权利**；文档只是如实记录限制与要求。

---

## 6. 最终评估

### 6.1 关于"完成度百分比"

**不给出百分比**。原因：项目没有一份可度量的验收基线（功能清单、验收标准、端到端用例）来定义分母；
"实现"与"可用"之间没有客观换算关系；本章审计已证明大量模块虽存在且单测通过，却未接线到产品、
未用真实 provider/设备验证、或默认使用内存存储。任何百分比都会是主观估计，违背本报告要求。
以下改用**可核查的分层清单**描述真实状态。

### 6.2 真正工作的（有端到端或等同证据）

- 继承的 PyQt6 桌面应用**可以初始化**（离屏模式持续运行并进入 setup wizard）；系统托盘、设置、向导、
  聊天窗、记忆查看器代码完整（`src/desktop_app/app.py`）。
- **FastAPI 后端 + 静态 web 前端在手工启动 uvicorn 后可完整交互**：会话、SSE 流式、模型列表、
  记忆面板、用量、工具列表、权限审批面板（`web/js/app.js` 全部接线）；浏览器回归 `passed: true`、
  零 JS 错误、reduced-motion 通过。
- **自动化测试基础扎实**：5944 个测试通过；后端契约 33 个通过；许可一致性 14 项通过。
- 模型路由/回退代码、权限门、离线降级引擎、SQLite 存储层均为真实实现（非占位）。

### 6.3 只是代码、没有端到端验证的

- 9 个 AI provider 的认证、模型发现、真实流式、切换、自动回退、限流与超时处理、用量跟踪——
  有 `httpx` 适配器与 registry 声明，**零真实凭据验证**。
- 原生语音全链路（麦克风、唤醒词、VAD、STT、TTS、打断、连续对话、设备恢复）——
  代码规模大（listener 2664 行、tts 1201 行），**零真实设备验证**。
- 跨重启记忆——代码支持磁盘 DB，但 server 默认内存（实测丢数据）、daemon 路径未端到端验证、
  两套记忆不互通。
- 电脑动作——6 个动作有实现 + 前端审批 UI，**未在真实桌面打开应用/写文件**。
- 应用级故障恢复、打包产物、Linux 系统托盘在真实桌面的表现。

### 6.4 只存在于文档中的

- `desktop/tolesh_desktop.py`（`server/app.py:7` 引用，文件不存在）。
- "future Cloudflare Pages web client"、"future Android client"（`server/app.py:8–9`）。
- `ARCHITECTURE.md`、`DESIGN.md`、`SKILLS.md`、`COMPONENT-LIBRARY.md`、`ANIMATION-GUIDELINES.md`、
  `ACCESSIBILITY.md` 等设计文档中描述的部分目标能力（多平台、原生语音接线、商业分发等）。
- registry 中标注为 "planned provider" 的 JEV 等（`registry.py:160` notes）。

### 6.5 仍需构建的

1. 把新增的 FastAPI + web 轨道**真正接入桌面产品**：统一入口、让桌面 shell 加载新前端、
   把 `web/` 与 `tolesh.server`/`tolesh.ai` 纳入 `tolesh_desktop.spec` 与 `requirements.txt`。
2. 给 server 一个**磁盘 DB 配置入口**并与继承记忆合并，实现真正的跨重启记忆。
3. 用项目所有者提供的真实凭据完成 **provider 端到端验证**（认证、发现、流式、切换、回退、
   限流、超时、用量）。
4. 在带麦克风/扬声器的真实 Linux（Zorin/Ubuntu）桌面**接线并验证原生语音管线**，而非浏览器 Web Speech。
5. 补齐图形化 provider 配置、搜索/导航类安全电脑动作、真实桌面权限提示与错误通知。
6. 修好 960×780 wizard 溢出（在真实桌面字体/DPI 下复测），并解决 Qt xcb 依赖（`libxcb-cursor0`）。
7. 许可侧：取得商业许可 / 完成 PyQt6 与依赖树律师审查 / 发布仓库以修复 404 链接。

### 6.6 最重要的下一个实施里程碑

> **消除"双轨断连"，让桌面产品真正运行新后端**。

当前最根本的问题不是缺某个功能，而是**新增的后端/前端/路由/权限/记忆与真正被打包、被启动的桌面应用
是两套互不连通的代码**。在完成这一接线之前，任何 provider 凭据、原生语音或电脑动作的验证都无法
代表"Tolesh 桌面助手可用"；浏览器回归通过只证明一个需要手工启动的独立 web 服务能跑离线脚本。

建议按可验证的最小闭环推进：
`统一启动入口 → 打包纳入新模块/web → 磁盘 DB 持久化 → 真实 provider 凭据端到端 → 原生语音接线 → 真实桌面回归`。
每一步都需给出实际命令、输出与失败项，不以模块存在或单测通过作为完成依据。

---

## 附：本报告未做的声明

- 未声称任何功能"因为文件存在/单测通过"而可用。
- 未把规划中的 provider、多平台客户端、原生语音接线当作已实现。
- 未把浏览器 Web Speech 当作原生桌面语音。
- 未声称项目可商业分发；未声称公开站点 licence 链接已修复。
- 未修改源码、未创建占位实现、未改动测试；`git status` 前后一致（28 `M` / 35 `??`）。
