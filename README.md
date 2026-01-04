# MobileGLM - Phone Automation System

AI-powered phone automation using Claude Agent SDK + AutoGLM-Phone-9B.

## Features

- **iOS Remote Control** - Stream Android screen to iOS device (~50-80ms latency)
- **Intelligent Orchestration** - Claude Agent SDK for task planning and tool calling
- **Stuck Detection** - 7 heuristic detectors + automatic recovery
- **User Preferences** - Remember user preferences for personalized execution

## Quick Start

### 1. Clone Repository

```bash
git clone https://github.com/user/mobile-glm.git
cd mobile-glm

# Clone dependency
git clone https://github.com/AdarshHH/Open-AutoGLM.git
```

### 2. Install Dependencies

```bash
# Using uv (recommended)
uv sync

# Or using pip
pip install -e .
```

### 3. Configure Environment

```bash
cp .env.example .env
# Edit .env with your API keys:
# - ZHIPU_API_KEY (required for AutoGLM)
# - ANTHROPIC_API_KEY (required for Claude Agent SDK)
```

### 4. Download scrcpy-server

```bash
# Download scrcpy-server.jar from scrcpy releases
# https://github.com/Genymobile/scrcpy/releases
# Place in project root
```

### 5. Connect Android Device

```bash
adb devices  # Verify device is connected
```

### 6. Run

```bash
# Run Agent (interactive CLI)
uv run python agent_sdk.py

# Run WebSocket bridge (for iOS app)
uv run python scrcpy_ws_bridge.py
```

## iOS Remote Control App

### Start Bridge Service

```bash
# 1. Connect Android phone
adb devices

# 2. Start WebSocket bridge
uv run python scrcpy_ws_bridge.py
# Server runs on ws://0.0.0.0:8765
```

### Build iOS App

```bash
# Open project in Xcode
open MobileGLM-iOS/MobileGLM.xcodeproj

# Run on device/simulator
# Enter the IP address of the computer running the bridge
```

### Bridge Protocol

**Video**: Binary H.264 NAL units (each WebSocket message = one complete NAL)

**Control Commands** (JSON):
```json
{"type": "touch", "action": "down|up|move", "x": 0.5, "y": 0.5}
{"type": "home"}
{"type": "back"}
{"type": "recents"}
```

## Architecture

```
User Command
    │
    ▼
┌─────────────────────────────────────┐
│ Claude Agent SDK                    │
│ - Task understanding & decomposition│
│ - Tool calling (phone_task, etc.)   │
│ - Stuck recovery & retry            │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│ phone_tool.py                       │
│ - Step limits (max_steps)           │
│ - Action/app allowlists             │
│ - 7 stuck detectors                 │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│ AutoGLM-Phone-9B (via Open-AutoGLM) │
│ - Screenshot analysis               │
│ - UI element recognition            │
│ - Action execution                  │
└─────────────────────────────────────┘
    │
    ▼
Android Device (ADB)
```

## File Structure

```
mobile-glm/
├── agent_sdk.py          # Claude Agent SDK orchestrator
├── phone_tool.py         # AutoGLM execution layer
├── preference_tool.py    # User preference tool
├── scrcpy_ws_bridge.py   # WebSocket H.264 bridge
├── MobileGLM-iOS/        # iOS remote control app
└── Open-AutoGLM/         # AutoGLM SDK (clone separately)
```

## License

MIT
