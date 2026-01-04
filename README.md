# MobileGLM - AI-Powered Phone Automation

[![en](https://img.shields.io/badge/lang-en-blue.svg)](#english)
[![zh](https://img.shields.io/badge/lang-zh-red.svg)](#中文)

---

<a name="english"></a>

## English

AI-powered Android phone automation using **Claude Agent SDK** + **AutoGLM-Phone-9B**.

Control your Android phone with natural language commands through an intelligent multi-agent system.

### Features

- **Natural Language Control** - "Open Taobao and search for headphones" → Agent handles it
- **Claude Orchestration** - Task planning, decomposition, and error recovery
- **AutoGLM-Phone-9B** - Zhipu's vision model for phone UI understanding
- **Stuck Detection** - 7 heuristics automatically identify and recover from failures
- **User Preferences** - Learns your preferences for personalized automation
- **iOS Remote Viewer** (Optional) - Stream Android screen with ~50-80ms latency

### Quick Start

**Prerequisites:**
- Python 3.11+
- Android device with USB debugging
- [Zhipu API key](https://open.bigmodel.cn/) (AutoGLM)
- [Anthropic API key](https://console.anthropic.com/) (Claude)

**Installation:**

```bash
git clone https://github.com/YOUR-USERNAME/mobile-glm.git
cd mobile-glm
uv sync  # Auto-installs Open-AutoGLM from GitHub
```

**Configure:**

```bash
cp .env.example .env
# Edit .env with your API keys
```

**Run:**

```bash
adb devices  # Connect Android device
uv run python agent_sdk.py
```

**Example commands:**
- "Open Settings"
- "Search for restaurants on Meituan"
- "Turn on airplane mode"

### Architecture

```
Natural Language → Claude SDK → phone_tool → AutoGLM API → Android (ADB)
```

**Claude Agent SDK** plans tasks and orchestrates
**phone_tool.py** manages execution with safety checks
**AutoGLM-Phone-9B** analyzes screenshots and executes UI actions

### iOS Remote Viewer (Optional)

Stream Android screen to iOS device:

```bash
# 1. Download scrcpy-server.jar to project root
# 2. Start bridge
uv run python scrcpy_ws_bridge.py

# 3. Build iOS app
open MobileGLM-iOS/MobileGLM.xcodeproj
```

### API Example

```python
from agent_sdk import TelemetryAgentSDK

agent = TelemetryAgentSDK()
result = agent.invoke("Open Settings and enable airplane mode")
print(result['final_response'])
```

### License

MIT

---

<a name="中文"></a>

## 中文

基于 **Claude Agent SDK** + **AutoGLM-Phone-9B** 的 AI 手机自动化系统。

使用自然语言命令控制 Android 手机，通过智能多代理系统实现。

### 功能特性

- **自然语言控制** - "打开淘宝搜索耳机" → 代理自动处理
- **Claude 编排** - 任务规划、分解和错误恢复
- **AutoGLM-Phone-9B** - 智谱 AI 视觉模型，理解和控制手机 UI
- **卡顿检测** - 7 种启发式自动识别并恢复失败
- **用户偏好** - 学习您的偏好，实现个性化自动化
- **iOS 远程查看器**（可选）- 以 ~50-80ms 延迟串流 Android 屏幕

### 快速开始

**前置要求：**
- Python 3.11+
- 已启用 USB 调试的 Android 设备
- [智谱 API 密钥](https://open.bigmodel.cn/)（AutoGLM）
- [Anthropic API 密钥](https://console.anthropic.com/)（Claude）

**安装：**

```bash
git clone https://github.com/YOUR-USERNAME/mobile-glm.git
cd mobile-glm
uv sync  # 自动从 GitHub 安装 Open-AutoGLM
```

**配置：**

```bash
cp .env.example .env
# 编辑 .env 添加您的 API 密钥
```

**运行：**

```bash
adb devices  # 连接 Android 设备
uv run python agent_sdk.py
```

**示例命令：**
- "打开设置"
- "在美团上搜索餐厅"
- "打开飞行模式"

### 系统架构

```
自然语言 → Claude SDK → phone_tool → AutoGLM API → Android (ADB)
```

**Claude Agent SDK** 规划任务并编排
**phone_tool.py** 管理执行并进行安全检查
**AutoGLM-Phone-9B** 分析截图并执行 UI 动作

### iOS 远程查看器（可选）

将 Android 屏幕串流到 iOS 设备：

```bash
# 1. 下载 scrcpy-server.jar 到项目根目录
# 2. 启动桥接
uv run python scrcpy_ws_bridge.py

# 3. 构建 iOS 应用
open MobileGLM-iOS/MobileGLM.xcodeproj
```

### API 示例

```python
from agent_sdk import TelemetryAgentSDK

agent = TelemetryAgentSDK()
result = agent.invoke("打开设置并启用飞行模式")
print(result['final_response'])
```

### 许可证

MIT

---

## Contributing | 贡献

Contributions welcome! | 欢迎贡献！

Please submit issues and pull requests. | 请提交问题和拉取请求。
