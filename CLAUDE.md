# MobileGLM - Phone Automation

## Quick Start

### Run the Agent (Interactive CLI)
```bash
uv run python agent_sdk.py
```

### Run the WebSocket Bridge (for iOS app)
```bash
uv run python scrcpy_ws_bridge.py
```

### Environment Setup
```bash
# Install dependencies
uv sync

# Required .env file
ZHIPU_API_KEY=your_key_here
ANTHROPIC_API_KEY=your_key_here
```

### Connect Android Device
```bash
adb devices
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        User                                      │
│         (iOS App for viewing / CLI for commands)                 │
└─────────────────┬───────────────────────┬───────────────────────┘
                  │                       │
    ┌─────────────▼─────────────┐   ┌─────▼─────────────────────┐
    │   iOS Viewer App          │   │   Agent CLI               │
    │   (VideoToolbox + Touch)  │   │   (agent_sdk.py)          │
    └─────────────┬─────────────┘   └─────────────┬─────────────┘
                  │                               │
    ┌─────────────▼─────────────┐   ┌─────────────▼─────────────┐
    │   WebSocket Bridge        │   │   phone_task() tool       │
    │   (scrcpy_ws_bridge.py)   │   │   (phone_tool.py)         │
    └─────────────┬─────────────┘   └─────────────┬─────────────┘
                  │                               │
                  └───────────────┬───────────────┘
                                  │
                    ┌─────────────▼─────────────┐
                    │   Android Device (ADB)    │
                    └───────────────────────────┘
```

## Key Files

| File | Purpose |
|------|---------|
| `agent_sdk.py` | Meta-orchestrator using Claude Agent SDK |
| `phone_tool.py` | AutoGLM wrapper with stuck detection |
| `preference_tool.py` | User preference storage |
| `scrcpy_ws_bridge.py` | WebSocket server streaming H.264 to iOS |
| `MobileGLM-iOS/` | Native iOS app with VideoToolbox decoder |

## phone_task() Usage

```python
from phone_tool import phone_task

# Start new task
result = phone_task(goal="Open Settings")

# Resume with guidance
result = phone_task(
    goal="Open Settings",
    guidance="Try searching for 'airplane' in Settings",
    session_id="abc123"
)
```

### Return Statuses
- `completed` - Task finished successfully
- `stuck` - Agent needs guidance (auto-detected)
- `needs_takeover` - Sensitive screen (login, payment)
- `error` - Something went wrong

## Project Structure

```
mobile-glm/
├── agent_sdk.py          # Claude Agent SDK orchestrator
├── phone_tool.py         # AutoGLM execution layer
├── preference_tool.py    # User preference tool
├── scrcpy_ws_bridge.py   # WebSocket H.264 bridge
├── MobileGLM-iOS/        # iOS native app
└── Open-AutoGLM/         # AutoGLM SDK (clone separately)
```
