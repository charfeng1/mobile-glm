# MobileGLM - AI 手机自动化系统

[![zh](https://img.shields.io/badge/lang-zh-red.svg)](README.md)
[![en](https://img.shields.io/badge/lang-en-blue.svg)](README.en.md)

基于 **Claude Agent SDK** + **AutoGLM-Phone-9B** 的 AI 手机自动化系统。

使用自然语言命令控制 Android 手机，支持 iOS 远程操控、智能卡顿检测和用户偏好学习。

## 功能特性

- **自然语言控制** - "打开淘宝搜索耳机" → 代理自动处理
- **Claude Agent SDK 编排** - 任务规划、分解和错误恢复
- **AutoGLM-Phone-9B** - 智谱 AI 的视觉模型，理解和控制手机 UI
- **iOS 远程操控手机** - 在 iOS 设备上查看 Android 屏幕并触摸操作（~50-80ms 延迟）
- **卡顿检测** - 7 种启发式检测器自动识别并恢复失败
- **用户偏好** - 学习并记住您的偏好，实现个性化自动化

## 快速开始

### 前置要求

- **Python 3.11+**
- **Android 设备**，已启用 USB 调试
- **API 密钥**:
  - [智谱 API 密钥](https://open.bigmodel.cn/) 用于 AutoGLM（必需）
  - [Anthropic API 密钥](https://console.anthropic.com/) 用于 Claude Agent SDK（必需）

### 安装

```bash
# 克隆仓库
git clone https://github.com/charfeng1/mobile-glm.git
cd mobile-glm

# 安装依赖（自动从 GitHub 安装 Open-AutoGLM）
uv sync

# 或使用 pip
pip install -e .
```

### 配置

```bash
# 复制环境变量模板
cp .env.example .env

# 编辑 .env 并添加您的 API 密钥：
# ZHIPU_API_KEY=您的智谱API密钥
# ANTHROPIC_API_KEY=您的Anthropic API密钥
```

**注意：** 您不需要使用 Claude 的云端模型！系统支持任何 Anthropic 兼容的 API。例如，您可以通过设置以下变量来使用智谱的 GLM 模型：

```bash
ANTHROPIC_BASE_URL=https://api.z.ai/api/anthropic
ANTHROPIC_DEFAULT_HAIKU_MODEL=glm-4.7
```

这样您可以使用本地或其他替代模型，同时保持相同的接口。

### 连接 Android 设备

```bash
# 通过 USB 连接并启用 USB 调试
adb devices

# 您应该看到设备已列出
```

### 运行代理

```bash
# 启动交互式代理 CLI
uv run python agent_sdk.py

# 示例命令：
# - "打开设置"
# - "在美团上搜索餐厅"
# - "打开飞行模式"
```

## 系统架构

```
用户指令（"打开淘宝搜索耳机"）
    │
    ▼
┌─────────────────────────────────────────┐
│ Claude Agent SDK（编排器）               │
│ • 理解自然语言                          │
│ • 规划多步骤任务                        │
│ • 调用 phone_task 工具                  │
│ • 处理错误和重试                        │
└─────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────┐
│ phone_tool.py（执行层）                  │
│ • 步数限制和安全检查                    │
│ • 动作/应用白名单                       │
│ • 7 种卡顿检测启发式                    │
│ • 会话管理                              │
└─────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────┐
│ AutoGLM-Phone-9B API（视觉模型）         │
│ • 截图分析                              │
│ • UI 元素识别                           │
│ • 动作规划和执行                        │
└─────────────────────────────────────────┘
    │
    ▼
Android 设备（通过 ADB）
```

## 项目结构

```
mobile-glm/
├── agent_sdk.py          # Claude Agent SDK 编排器
├── phone_tool.py         # AutoGLM 执行包装器
├── preference_tool.py    # 用户偏好存储
├── scrcpy_ws_bridge.py   # WebSocket H.264 桥接（用于 iOS 查看器）
├── security/             # 提示注入防御和图像过滤
├── MobileGLM-iOS/        # iOS 远程查看器应用
├── .env.example          # 环境变量模板
└── pyproject.toml        # Python 依赖
```

## iOS 远程操控手机

在 iOS 设备上实时查看 Android 屏幕并直接触摸控制，无需电脑作为中介。

### 架构

```
iOS 设备（查看和触摸）
    │
    │ WebSocket
    │ ├─ 接收：H.264 视频流
    │ └─ 发送：触摸事件 (x, y, action)
    │
    ▼
WebSocket 桥接（scrcpy_ws_bridge.py）
    │ 在电脑上运行
    │
    ├─ 视频编码：Android 屏幕 → H.264 → iOS
    └─ 触摸转发：iOS 触摸 → ADB 命令 → Android
    │
    ▼
Android 设备
    ├─ MediaProjection：屏幕捕获
    ├─ MediaCodec：H.264 硬件编码
    └─ ADB：接收并执行触摸命令
```

**性能优势：**
- iOS VideoToolbox 硬件解码 H.264：~5ms
- Metal 渲染：~8ms
- 触摸延迟：~8ms
- **总延迟：~50-80ms**（对比 Web 方案的 100-150ms）

### 设置

1. **下载 scrcpy-server**（一次性设置）：
   ```bash
   # 从 https://github.com/Genymobile/scrcpy/releases 下载
   # 将 scrcpy-server.jar 放在项目根目录
   ```

2. **启动 WebSocket 桥接**：
   ```bash
   uv run python scrcpy_ws_bridge.py
   # 服务器运行在 ws://0.0.0.0:8765
   ```

3. **构建 iOS 应用**：
   ```bash
   open MobileGLM-iOS/MobileGLM.xcodeproj
   ```

   在 Xcode 中：
   - 选择您的开发团队（Signing & Capabilities）
   - 连接 iOS 设备或选择模拟器
   - 点击运行按钮（⌘R）

   首次使用：
   - 在应用中输入运行桥接服务的计算机 IP 地址
   - 输入端口（默认 8765）
   - 点击连接

   提示：在 Mac 上运行 `ifconfig | grep "inet "` 查找您的本地 IP 地址

### 桥接协议

- **视频流**: 通过 WebSocket 传输 H.264 NAL 单元
- **控制命令**: 触摸/手势的 JSON 命令
  ```json
  {"type": "touch", "action": "down", "x": 0.5, "y": 0.5}
  {"type": "home"}
  {"type": "back"}
  ```

## API 使用

### 高级代理 API

```python
from agent_sdk import TelemetryAgentSDK

# 初始化代理
agent = TelemetryAgentSDK()

# 使用自然语言执行任务
result = agent.invoke("打开设置并开启飞行模式")

print(result['final_response'])
```

### 直接使用 phone_task API

```python
from phone_tool import phone_task

# 执行单个任务
result = phone_task(
    goal="打开设置应用",
    max_steps=5,
)

print(result)  # 包含 status、steps_taken 等的 JSON
```

## 卡顿检测

系统自动检测手机代理何时卡顿：

- **重复失败的应用启动**（2+ 次失败）
- **重复性动作**（最近 5 步中同一动作 4+ 次）
- **步数过多**（15+ 步未完成）
- **无限循环**（相同屏幕状态重复）

卡顿时，系统会向编排器返回指导请求以进行恢复。

## 安全功能

- **提示注入防御** - 检测并阻止截图中的恶意指令
- **图像过滤** - 预处理截图以移除低对比度注入尝试
- **敏感屏幕检测** - 在登录/支付屏幕上自动停止
- **动作白名单** - 限制可执行的动作
- **应用白名单** - 限制可启动的应用

## 系统要求

- Python 3.11+
- 带 ADB 的 Android 设备
- [uv](https://github.com/astral-sh/uv) 或 pip
- 智谱 API 密钥（用于 AutoGLM）
- Anthropic API 密钥（用于 Claude）

## 许可证

MIT

## 致谢

- 使用 [Claude Agent SDK](https://github.com/anthropics/agent-sdk-python) 构建
- 由智谱 AI 的 [AutoGLM-Phone-9B](https://open.bigmodel.cn/) 提供支持
- 使用 [Open-AutoGLM](https://github.com/zai-org/Open-AutoGLM) SDK

## 贡献

欢迎贡献！请随时提交问题和拉取请求。
