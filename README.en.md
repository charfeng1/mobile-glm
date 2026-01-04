# MobileGLM - AI-Powered Phone Automation

[![zh](https://img.shields.io/badge/lang-zh-red.svg)](README.md)
[![en](https://img.shields.io/badge/lang-en-blue.svg)](README.en.md)

AI-powered Android phone automation using **Claude Agent SDK** + **AutoGLM-Phone-9B**.

Control your Android phone with natural language commands, featuring iOS remote control, intelligent stuck detection, and user preference learning.

## Features

- **Natural Language Control** - "Open Taobao and search for headphones" → Agent handles it
- **Claude Agent SDK Orchestration** - Task planning, decomposition, and error recovery
- **AutoGLM-Phone-9B** - Zhipu's vision model for phone UI understanding and control
- **iOS Remote Control** - View Android screen and control via touch on iOS device (~50-80ms latency)
- **Stuck Detection** - 7 heuristic detectors automatically identify and recover from failures
- **User Preferences** - Learns and remembers your preferences for personalized automation

## Quick Start

### Prerequisites

- **Python 3.11+**
- **Android device** with USB debugging enabled
- **API Keys**:
  - [Zhipu API key](https://open.bigmodel.cn/) for AutoGLM (required)
  - [Anthropic API key](https://console.anthropic.com/) for Claude Agent SDK (required)

### Installation

```bash
# Clone repository
git clone https://github.com/charfeng1/mobile-glm.git
cd mobile-glm

# Install dependencies (automatically installs Open-AutoGLM from GitHub)
uv sync

# Or using pip
pip install -e .
```

### Configuration

```bash
# Copy environment template
cp .env.example .env

# Edit .env and add your API keys:
# ZHIPU_API_KEY=your_zhipu_api_key_here
# ANTHROPIC_API_KEY=your_anthropic_api_key_here
```

**Note:** You don't need to use Claude's cloud models! The system works with any Anthropic-compatible API. For example, you can use Zhipu's GLM models through their Anthropic-compatible endpoint by setting:

```bash
ANTHROPIC_BASE_URL=https://api.z.ai/api/anthropic
ANTHROPIC_DEFAULT_HAIKU_MODEL=glm-4.7
```

This allows you to use local or alternative models while keeping the same interface.

### Connect Android Device

```bash
# Connect via USB and enable USB debugging
adb devices

# You should see your device listed
```

### Run Agent

```bash
# Start the interactive agent CLI
uv run python agent_sdk.py

# Example commands:
# - "Open Settings"
# - "Search for restaurants on Meituan"
# - "Turn on airplane mode"
```

## Architecture

```
User Command ("Open Taobao and search for headphones")
    │
    ▼
┌─────────────────────────────────────────┐
│ Claude Agent SDK (Orchestrator)         │
│ • Understands natural language          │
│ • Plans multi-step tasks                │
│ • Calls phone_task tool                 │
│ • Handles errors and retries            │
└─────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────┐
│ phone_tool.py (Execution Layer)         │
│ • Step limiting & safety checks         │
│ • Action/app allowlists                 │
│ • 7 stuck detection heuristics          │
│ • Session management                    │
└─────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────┐
│ AutoGLM-Phone-9B API (Vision Model)     │
│ • Screenshot analysis                   │
│ • UI element recognition                │
│ • Action planning & execution           │
└─────────────────────────────────────────┘
    │
    ▼
Android Device (via ADB)
```

## Project Structure

```
mobile-glm/
├── agent_sdk.py          # Claude Agent SDK orchestrator
├── phone_tool.py         # AutoGLM execution wrapper
├── preference_tool.py    # User preference storage
├── scrcpy_ws_bridge.py   # WebSocket H.264 bridge (for iOS viewer)
├── security/             # Prompt injection defense & image filtering
├── MobileGLM-iOS/        # iOS remote viewer app
├── .env.example          # Environment template
└── pyproject.toml        # Python dependencies
```

## iOS Remote Control

View and control your Android phone directly from an iOS device with real-time screen streaming and touch control.

### Architecture

```
iOS Device (View & Touch)
    │
    │ WebSocket Connection
    │ ├─ Receive: H.264 video stream
    │ └─ Send: Touch events (x, y, action)
    │
    ▼
WebSocket Bridge (scrcpy_ws_bridge.py)
    │ Runs on your computer
    │
    ├─ Video Encoding: Android screen → H.264 → iOS
    └─ Touch Forwarding: iOS touch → ADB commands → Android
    │
    ▼
Android Device
    ├─ MediaProjection: Screen capture
    ├─ MediaCodec: H.264 hardware encoding
    └─ ADB: Receives and executes touch commands
```

**Performance Benefits:**
- iOS VideoToolbox hardware H.264 decoding: ~5ms
- Metal rendering: ~8ms
- Touch latency: ~8ms
- **Total latency: ~50-80ms** (vs ~100-150ms for web-based solutions)

### Setup

1. **Download scrcpy-server** (one-time setup):
   ```bash
   # Download from https://github.com/Genymobile/scrcpy/releases
   # Place scrcpy-server.jar in project root
   ```

2. **Start WebSocket bridge**:
   ```bash
   uv run python scrcpy_ws_bridge.py
   # Server runs on ws://0.0.0.0:8765
   ```

3. **Build iOS app**:
   ```bash
   open MobileGLM-iOS/MobileGLM.xcodeproj
   ```

   In Xcode:
   - Select your development team (Signing & Capabilities)
   - Connect your iOS device or select a simulator
   - Click the Run button (⌘R)

   First-time use:
   - Enter the IP address of the computer running the bridge
   - Enter the port (default 8765)
   - Tap Connect

   Tip: Run `ifconfig | grep "inet "` on Mac to find your local IP address

### Bridge Protocol

- **Video Stream**: H.264 NAL units over WebSocket
- **Control**: JSON commands for touch/gestures
  ```json
  {"type": "touch", "action": "down", "x": 0.5, "y": 0.5}
  {"type": "home"}
  {"type": "back"}
  ```

## API Usage

### High-level Agent API

```python
from agent_sdk import TelemetryAgentSDK

# Initialize agent
agent = TelemetryAgentSDK()

# Execute task with natural language
result = agent.invoke("Open Settings and turn on airplane mode")

print(result['final_response'])
```

### Direct phone_task API

```python
from phone_tool import phone_task

# Execute single task
result = phone_task(
    goal="Open the Settings app",
    max_steps=5,
)

print(result)  # JSON with status, steps_taken, etc.
```

## Stuck Detection

The system automatically detects when the phone agent gets stuck:

- **Repeated failed app launches** (2+ failures)
- **Repetitive actions** (same action 4+ times in last 5 steps)
- **Too many steps** (15+ steps without completion)
- **Infinite loops** (same screen state repeating)

When stuck, the system returns guidance requests to the orchestrator for recovery.

## Security Features

- **Prompt injection defense** - Detects and blocks malicious instructions in screenshots
- **Image filtering** - Preprocesses screenshots to remove low-contrast injection attempts
- **Sensitive screen detection** - Automatically stops on login/payment screens
- **Action allowlists** - Restrict which actions can be executed
- **App allowlists** - Restrict which apps can be launched

## Requirements

- Python 3.11+
- Android device with ADB
- [uv](https://github.com/astral-sh/uv) or pip
- Zhipu API key (for AutoGLM)
- Anthropic API key (for Claude)

## License

MIT

## Acknowledgments

- Built with [Claude Agent SDK](https://github.com/anthropics/agent-sdk-python)
- Powered by [AutoGLM-Phone-9B](https://open.bigmodel.cn/) from Zhipu AI
- Uses [Open-AutoGLM](https://github.com/zai-org/Open-AutoGLM) SDK

## Contributing

Contributions welcome! Please feel free to submit issues and pull requests.
