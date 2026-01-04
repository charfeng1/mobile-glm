"""
Phone automation agent using Claude Agent SDK.

Alternative to agent.py (which uses DeepAgents). This version uses the official
Claude Agent SDK for the orchestrator, while still delegating phone UI execution
to AutoGLM via the phone_task tool.

Usage:
    from agent_sdk import TelemetryAgentSDK

    agent = TelemetryAgentSDK()
    agent.set_step_callback(lambda step_type, content, metadata: print(f"{step_type}: {content}"))
    result = await agent.invoke("Turn on airplane mode")
"""

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from claude_agent_sdk import (
    tool,
    create_sdk_mcp_server,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    AssistantMessage,
    UserMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    ToolResultBlock,
    ToolPermissionContext,
    PermissionResultAllow,
    HookMatcher,
)

from phone_tool import phone_task as _phone_task_impl
from preference_tool import preference_tool


# ============================================================================
# ENVIRONMENT SETUP
# ============================================================================

def load_env():
    """Load environment variables from .env file."""
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip())


load_env()




# ============================================================================
# SYSTEM PROMPT (Chinese only for contest)
# ============================================================================

SYSTEM_PROMPT = """你是一个手机自动化助手。你通过调用手机自动化执行器来帮助用户完成 Android 手机上的任务。

## 沟通风格

**务必先确认用户的请求。** 在执行任何操作之前，简要确认你即将做什么，让用户知道你理解了他们的需求。

示例：
- 用户："帮我点个外卖"
- 你："好的，我来帮你点外卖。让我打开美团看看有什么好吃的。"
- [然后调用 phone_task]

- 用户："今天天气怎么样？"
- 你："我来帮你查一下天气。"
- [然后调用 phone_task]

不要默默开始执行——务必先简短确认。

## 偏好存储

使用 `preference` 工具记住用户的习惯和喜好：
- `preference(action="set", category, key, value)` - 保存偏好
- `preference(action="get", category, key)` - 查询偏好
- `preference(action="list", category?)` - 列出偏好
- `preference(action="delete", category, key)` - 删除偏好

建议的分类：`food`(饮食)、`apps`(应用)、`habits`(习惯)、`contacts`(联系人)
但你可以根据需要创建新分类。

**主动记录用户提到的偏好**，例如：
- "我喜欢吃辣" → `preference(action="set", category="food", key="spice", value="喜欢辣")`
- "用美团点外卖" → `preference(action="set", category="apps", key="food_delivery", value="美团")`
- "我对花生过敏" → `preference(action="set", category="food", key="allergy", value="花生")`

执行任务前，查询相关偏好以提供更好的服务：
- 点外卖前查询 `preference(action="get", category="food", key="allergy")`
- 打开应用前查询 `preference(action="get", category="apps", key="...")`

## 你的工作空间 (File Tools)

你可以访问一个持久化的工作空间来读写文件，用于：
- **笔记和记忆**: 跨对话记住复杂信息
- **任务历史**: 记录你做过的事情

可用文件工具：`Read`, `Write`, `Edit`, `Glob`, `Grep`

**重要：** 始终使用相对路径如 `notes/diet.md`。不要使用绝对路径——工作空间是沙盒化的，绝对路径会失败。

对于简单偏好（常用应用、饮食限制），使用 `preference` 工具。对于复杂笔记或长文本，使用文件工具。

## 你的主要工具：phone_task

使用 `phone_task` 与手机交互。它通过专门针对移动端 UI 自动化微调的视觉语言模型来控制 Android 设备。

**执行器特点：**
- 擅长导航 UI、点击按钮、输入文字、跟随视觉线索
- 适合清晰、独立的目标任务
- 在敏感场景（登录、支付、个人信息）会变得谨慎——可能会停下来请求接管，这是正确的行为
- 如果卡住了，你会通过 `stuck` 状态知道

**步数限制 (max_steps) - 重要：**
手机执行器能力强但不擅长长时间运行的任务。你必须根据任务复杂度设置合适的 `max_steps`：
- **5 步 (默认)**: 简单任务 - 打开应用、点击按钮、返回主页
- **8-10 步**: 中等任务 - 打开应用 + 导航到特定页面
- **12-15 步**: 复杂任务 - 搜索 + 浏览结果 + 提取信息
- **20+ 步**: 仅用于无法拆分的多阶段任务

**始终优先选择小步数限制和多次调用，而不是一个长任务。** 如果执行器卡住，可以通过 guidance 恢复，而不是让它空转。

## 控制执行器：allowed_actions 和 allowed_apps

**allowed_actions** - 控制可用的 UI 操作：
- 默认：所有操作可用 (Launch, Tap, Type, Swipe, Back, Home 等)
- 只读任务用 `allowed_actions=["Tap", "Swipe", "Back", "Home", "Wait"]`
- **Launch 是打开应用最高效的方式**——直接启动应用而不需要在主屏幕导航

**allowed_apps** - 控制可以启动哪些应用：
- 默认：所有应用都可以启动
- 用 `allowed_apps=["美团"]` 将任务限制在美团
- 用 `allowed_apps=["淘宝", "京东"]` 进行多应用比价任务

## 何时进行多次调用

**单次 phone_task**（让执行器处理）：
- 直接导航："打开设置，开启深色模式"
- 简单搜索："在当前应用搜索'意大利餐厅'"

**多次 phone_task 调用**（你来编排）：
- 开放式探索："看看有什么热门的" → 先浏览，评估，再探索
- 用户选择流程："点外卖" → 浏览选项，展示给用户，执行选择
- 多应用任务："比较价格" → 每个应用单独调用，然后综合
- 研究任务："找最划算的" → 迭代搜索和比较

**重要：跨应用限制**
手机执行器不擅长处理跨应用任务。如果任务涉及多个应用或切换应用，必须拆分成单独的 phone_task 调用——每个应用一次调用。
- 错误："在淘宝和京东上比较 iPhone 价格"（单次调用）
- 正确：先调用"在淘宝搜索 iPhone 并记录价格"，再调用"在京东搜索 iPhone 并记录价格"，然后你来综合

## 编写好的指令

- **具体**："打开美团，进入外卖页面" 而不是 "打开外卖应用"
- **有边界**："给我看看餐厅选项" 而不是 "点外卖"（除非用户已经选好了）
- **完整**：如果不明显，说明成功是什么样子

## 处理响应

- `completed`: 成功。评估结果并继续你的计划。
- `stuck`: 执行器尝试了但无法继续。查看 `last_actions`，然后：
  - **使用 z.ai Vision 分析截图** - 调用 z.ai 视觉工具查看屏幕内容，理解执行器卡住的原因
  - 如果看到明确的路径，用 `guidance` + `session_id` 恢复
  - 尝试完全不同的方法
  - 如果多次尝试仍然卡住，在回复中向用户寻求帮助
- `needs_takeover`: 敏感操作——通知用户并询问如何继续（登录、支付）
- `needs_interaction`: 执行器需要信息。从上下文回答或询问用户。
- `error`: 技术故障。报告给用户，考虑重试。

## 手机截图与 z.ai 视觉

每次 phone_task 调用后，最新截图会保存到工作区的 `screenshots/latest_phone_screen.jpg`。你也可以随时使用 `take_screenshot` 工具来捕获当前手机屏幕。

**在以下情况使用 z.ai 视觉工具分析截图：**
- 手机执行器返回 `stuck` 状态时 - 分析当前屏幕了解出了什么问题
- 需要确认手机当前显示的内容
- 执行器请求帮助或返回 `needs_interaction`
- 想在提供指导之前了解 UI 状态

调用 z.ai 视觉工具并传入截图路径，获取屏幕内容的详细分析，包括文字、UI 元素和建议的下一步操作。

## 使用 TodoWrite 进行任务规划

对于复杂的多步骤任务，使用 `TodoWrite` 工具来跟踪进度：
- 开始复杂任务时创建待办列表
- 开始处理某项时标记为 `in_progress`
- 完成后标记为 `completed`
- 这有助于你保持条理，并向用户展示进度

## 使用 Task 工具处理专注子任务

对于不涉及手机自动化的复杂研究或探索任务，使用 `Task` 工具生成专注的子代理：
- 用于代码库探索、文件分析或研究任务
- 子代理自主工作并将结果返回给你
- 手机自动化保留在主对话中使用 phone_task

## 寻求帮助

当需要用户输入时（需要登录、多次尝试后仍卡住、需要澄清），直接在回复文本中询问。用户会回复你的问题。

示例："淘宝需要登录。你想现在登录，还是我换一个应用试试？"
"""


# ============================================================================
# TOOL DEFINITIONS (using Claude Agent SDK @tool decorator)
# ============================================================================

@tool(
    "phone_task",
    "Execute a phone automation task using AutoGLM vision-language model. Only 'goal' is required - other params are optional.",
    {
        "type": "object",
        "properties": {
            "goal": {"type": "string", "description": "What to accomplish on the phone"},
            "max_steps": {"type": "integer", "description": "Maximum steps. Default 5. Increase based on complexity: 5 (simple), 8-10 (medium), 12-15 (complex). See system prompt.", "default": 5},
            "guidance": {"type": "string", "description": "Additional guidance if resuming a stuck task"},
            "session_id": {"type": "string", "description": "Session ID to resume a previous task"},
            "allowed_actions": {"type": "array", "items": {"type": "string"}, "description": "Restrict to specific actions like ['Tap', 'Swipe', 'Back']"},
            "allowed_apps": {"type": "array", "items": {"type": "string"}, "description": "Restrict to specific apps like ['TikTok', 'Notes']"},
        },
        "required": ["goal"],
    }
)
async def phone_task_tool(args: dict[str, Any]) -> dict[str, Any]:
    """
    Execute a phone automation task.

    Wraps the synchronous phone_task function from phone_tool.py.
    """
    goal = args.get("goal", "")
    max_steps = args.get("max_steps", 5)
    guidance = args.get("guidance")
    session_id = args.get("session_id")

    # Handle allowed_actions - model sometimes passes string instead of list
    allowed_actions = args.get("allowed_actions")
    if isinstance(allowed_actions, str):
        # Parse string like "Launch, Tap" or '["Launch"]' into list
        if allowed_actions.startswith("["):
            try:
                allowed_actions = json.loads(allowed_actions)
            except json.JSONDecodeError:
                allowed_actions = [a.strip() for a in allowed_actions.strip("[]").split(",")]
        else:
            allowed_actions = [a.strip() for a in allowed_actions.split(",")]

    # Handle allowed_apps - same issue
    allowed_apps = args.get("allowed_apps")
    if isinstance(allowed_apps, str):
        if allowed_apps.startswith("["):
            try:
                allowed_apps = json.loads(allowed_apps)
            except json.JSONDecodeError:
                allowed_apps = [a.strip() for a in allowed_apps.strip("[]").split(",")]
        else:
            allowed_apps = [a.strip() for a in allowed_apps.split(",")]

    # Run synchronous phone_task in thread pool
    loop = asyncio.get_running_loop()
    result_json = await loop.run_in_executor(
        None,
        lambda: _phone_task_impl(
            goal=goal,
            max_steps=max_steps,
            guidance=guidance,
            session_id=session_id,
            allowed_actions=allowed_actions,
            allowed_apps=allowed_apps,
        )
    )

    return {
        "content": [{"type": "text", "text": result_json}]
    }


@tool(
    "take_screenshot",
    "Take a screenshot of the phone screen via ADB. Saves to workspace and returns the file path for viewing with z.ai Vision.",
    {
        "type": "object",
        "properties": {},
        "required": [],
    }
)
async def take_screenshot_tool(args: dict[str, Any]) -> dict[str, Any]:
    """
    Take a screenshot of the phone screen via ADB.

    Saves the screenshot to the agent workspace so it can be analyzed
    with z.ai Vision tool.
    """
    import base64
    import subprocess
    from pathlib import Path

    workspace_dir = Path(__file__).parent / "agent_workspace" / "screenshots"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = workspace_dir / "latest_phone_screen.jpg"

    try:
        # Take screenshot via ADB and get PNG data
        result = subprocess.run(
            ["adb", "exec-out", "screencap", "-p"],
            capture_output=True,
            timeout=10
        )

        if result.returncode != 0:
            return {
                "content": [{"type": "text", "text": f"Error: ADB screenshot failed: {result.stderr.decode()}"}]
            }

        png_data = result.stdout

        # Convert PNG to JPEG for smaller size
        from PIL import Image
        import io
        from security import preprocess_screenshot

        img = Image.open(io.BytesIO(png_data))
        img = img.convert("RGB")  # Remove alpha channel for JPEG
        # Apply security filter to remove hidden injection text
        img = preprocess_screenshot(img)
        img.save(screenshot_path, "JPEG", quality=85)

        return {
            "content": [{"type": "text", "text": f"Screenshot saved to screenshots/latest_phone_screen.jpg. Use z.ai Vision tool to analyze it."}]
        }

    except subprocess.TimeoutExpired:
        return {
            "content": [{"type": "text", "text": "Error: ADB screenshot timed out. Is the device connected?"}]
        }
    except Exception as e:
        return {
            "content": [{"type": "text", "text": f"Error taking screenshot: {e}"}]
        }


# ============================================================================
# TELEMETRY DATA STRUCTURES (same as agent.py for compatibility)
# ============================================================================

@dataclass
class StepTelemetry:
    """Telemetry for a single orchestrator step."""
    step_number: int
    step_type: str  # "model_call", "tool_call", "tool_result"
    tool_name: str | None = None
    start_time: float = 0.0
    end_time: float = 0.0
    latency_ms: float = 0.0
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None


@dataclass
class SessionTelemetry:
    """Telemetry for an entire agent session."""
    session_id: str
    model_name: str
    start_time: float = 0.0
    end_time: float = 0.0
    total_latency_ms: float = 0.0
    steps: list[StepTelemetry] = field(default_factory=list)
    total_model_calls: int = 0
    total_tool_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0

    def add_step(self, step: StepTelemetry):
        self.steps.append(step)
        if step.step_type == "model_call":
            self.total_model_calls += 1
            if step.input_tokens:
                self.total_input_tokens += step.input_tokens
            if step.output_tokens:
                self.total_output_tokens += step.output_tokens
        elif step.step_type == "tool_call":
            self.total_tool_calls += 1

    def print_summary(self):
        print("\n" + "=" * 60)
        print("ORCHESTRATOR TELEMETRY (Claude Agent SDK)")
        print("=" * 60)
        print(f"Model: {self.model_name}")
        print(f"Total Duration: {self.total_latency_ms:.0f}ms ({self.total_latency_ms/1000:.2f}s)")
        print(f"Model Calls: {self.total_model_calls}")
        print(f"Tool Calls: {self.total_tool_calls}")
        print(f"Total Tokens: {self.total_input_tokens} in / {self.total_output_tokens} out")
        print("-" * 60)
        print("Step Breakdown:")
        for step in self.steps:
            icon = "[Model]" if step.step_type == "model_call" else "[Tool]"
            name = step.tool_name or "thinking"
            print(f"  {icon} Step {step.step_number}: {name} - {step.latency_ms:.0f}ms")
        print("=" * 60 + "\n")


# ============================================================================
# TELEMETRY AGENT (Claude Agent SDK version)
# ============================================================================

class TelemetryAgentSDK:
    """
    Claude Agent SDK wrapper with telemetry and streaming callbacks.

    Drop-in replacement for TelemetryAgent from agent.py, but uses
    Claude Agent SDK instead of DeepAgents.
    """

    def __init__(self, model: str | None = None):
        # Use environment variable or default to glm-4.7 (via z.ai Anthropic-compatible API)
        if model is None:
            model = os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "glm-4.7")
        self.model = model

        self.sessions: list[SessionTelemetry] = []
        self._step_callback: Callable[[str, str, dict], None] | None = None
        self._active_client: ClaudeSDKClient | None = None  # For interrupt support

        # SDK session ID tracking for conversation continuity
        # Maps our session_id -> SDK's internal session_id
        self._sdk_session_ids: dict[str, str] = {}

        # Create workspace directory
        self.workspace_dir = Path(__file__).parent / "agent_workspace"
        self.workspace_dir.mkdir(exist_ok=True)

        # Create MCP server with our tools
        self._mcp_server = create_sdk_mcp_server(
            name="phone-agent",
            version="1.0.0",
            tools=[phone_task_tool, take_screenshot_tool, preference_tool]
        )

    def set_step_callback(self, callback: Callable[[str, str, dict], None]):
        """
        Set a callback to receive intermediate steps.

        Callback signature: callback(step_type: str, content: str, metadata: dict)
        step_type: "thinking", "tool_call", "tool_result", "response"
        """
        self._step_callback = callback

    def clear_session(self, session_id: str = "default"):
        """
        Clear a session to start fresh conversation.

        This removes the saved SDK session ID, so the next invoke() will
        start a new conversation without prior context.

        Args:
            session_id: The session ID to clear (default: "default")
        """
        if session_id in self._sdk_session_ids:
            del self._sdk_session_ids[session_id]
            print(f"[TelemetryAgentSDK] Cleared session: {session_id}")

    def _emit_step(self, step_type: str, content: str, metadata: dict | None = None):
        """Emit a step to the callback if set."""
        if self._step_callback:
            try:
                self._step_callback(step_type, content, metadata or {})
            except Exception as e:
                print(f"[TelemetryAgentSDK] Callback error: {e}")

    async def interrupt(self):
        """
        Interrupt the currently running agent operation.

        This properly stops the Claude SDK client, not just the streaming.
        Safe to call even if no operation is running.
        """
        if self._active_client:
            try:
                await self._active_client.interrupt()
            except Exception as e:
                print(f"[TelemetryAgentSDK] Interrupt error: {e}")

    def interrupt_sync(self):
        """Synchronous wrapper for interrupt()."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self.interrupt())
            return

        # Already in async context
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(asyncio.run, self.interrupt())
            future.result(timeout=5)

    async def invoke_async(
        self,
        user_message: str,
        session_id: str = "default",
        verbose: bool = True,
    ) -> dict:
        """
        Invoke the agent asynchronously with telemetry and step streaming.

        Args:
            user_message: The user's input message
            session_id: Session ID for conversation continuity
            verbose: Whether to print telemetry summary

        Returns:
            Dict with final response and metadata
        """
        start_time = time.time()

        # Workspace sandbox hook - blocks file access outside workspace
        workspace_resolved = str(self.workspace_dir.resolve())

        async def sandbox_file_tools(input_data, tool_use_id, context):
            """PreToolUse hook to sandbox file operations to workspace."""
            tool_input = input_data.get("tool_input", {})

            # Get file path from various possible keys
            file_path = (
                tool_input.get("file_path", "") or
                tool_input.get("path", "") or
                tool_input.get("pattern", "")  # for Glob
            )

            if file_path:
                # Handle relative paths: resolve from workspace, not cwd
                path_obj = Path(file_path)
                if path_obj.is_absolute():
                    resolved = str(path_obj.resolve())
                else:
                    # Relative paths are allowed (they resolve within workspace via cwd setting)
                    return {}

                if not resolved.startswith(workspace_resolved):
                    return {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "deny",
                            "permissionDecisionReason": f"Access denied: {file_path} is outside workspace"
                        }
                    }
            return {}

        async def sandbox_bash(input_data, tool_use_id, context):
            """PreToolUse hook to sandbox Bash commands."""
            tool_input = input_data.get("tool_input", {})
            command = tool_input.get("command", "")

            # Find absolute paths in command
            paths_in_cmd = re.findall(r'(?:^|[\s\'\"=])(/[^\s\'\"]+)', command)
            for path in paths_in_cmd:
                # Block access to sensitive paths outside workspace
                if path.startswith(("/etc", "/home", "/Users", "/var", "/tmp")):
                    resolved = str(Path(path).resolve())
                    if not resolved.startswith(workspace_resolved):
                        return {
                            "hookSpecificOutput": {
                                "hookEventName": "PreToolUse",
                                "permissionDecision": "deny",
                                "permissionDecisionReason": f"Bash blocked: {path} is outside workspace"
                            }
                        }
            return {}

        # Build env vars to pass to the CLI subprocess
        env_vars = {}
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if api_key:
            env_vars["ANTHROPIC_API_KEY"] = api_key
        auth_token = os.getenv("ANTHROPIC_AUTH_TOKEN")
        if auth_token:
            env_vars["ANTHROPIC_AUTH_TOKEN"] = auth_token
        base_url = os.getenv("ANTHROPIC_BASE_URL")
        if base_url:
            env_vars["ANTHROPIC_BASE_URL"] = base_url
        # Pass model overrides
        for key in ["ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL"]:
            val = os.getenv(key)
            if val:
                env_vars[key] = val

        # Build MCP servers dict
        mcp_servers = {"phone": self._mcp_server}

        # Add z.ai MCP server for vision capabilities if API key is available
        z_ai_api_key = os.getenv("Z_AI_API_KEY")
        if z_ai_api_key:
            mcp_servers["zai"] = {
                "type": "stdio",
                "command": "npx",
                "args": ["-y", "@z_ai/mcp-server"],
                "env": {
                    "Z_AI_API_KEY": z_ai_api_key,
                    "Z_AI_MODE": "ZHIPU"
                }
            }

        # Build allowed tools list
        allowed_tools = [
            "mcp__phone__phone_task",
            "mcp__phone__take_screenshot",
            "mcp__phone__preference",  # Store/retrieve user preferences
            "Read",
            "Write",
            "Edit",
            "Glob",
            "Grep",
            # Built-in Agent SDK tools for task management
            "TodoWrite",  # Track multi-step task progress
            "Task",       # Spawn focused subagents for subtasks
            "Skill",      # Use skills from .claude/skills/
        ]
        # Add z.ai vision tools if available
        if z_ai_api_key:
            allowed_tools.append("mcp__zai__*")  # Allow all z.ai tools

        # Check if we have a saved SDK session to resume
        sdk_session_to_resume = self._sdk_session_ids.get(session_id)
        if sdk_session_to_resume:
            print(f"[TelemetryAgentSDK] Will resume SDK session: {sdk_session_to_resume}")

        options = ClaudeAgentOptions(
            model=self.model,
            system_prompt=SYSTEM_PROMPT,
            cwd=self.workspace_dir,  # Working directory for relative paths
            add_dirs=[str(self.workspace_dir)],  # Additional allowed directories
            setting_sources=["project"],  # Load skills from workspace/.claude/skills/
            mcp_servers=mcp_servers,
            allowed_tools=allowed_tools,
            permission_mode="bypassPermissions",  # Auto-approve (hooks handle sandboxing)
            hooks={
                "PreToolUse": [
                    HookMatcher(matcher="Read", hooks=[sandbox_file_tools]),
                    HookMatcher(matcher="Write", hooks=[sandbox_file_tools]),
                    HookMatcher(matcher="Edit", hooks=[sandbox_file_tools]),
                    HookMatcher(matcher="Glob", hooks=[sandbox_file_tools]),
                    HookMatcher(matcher="Grep", hooks=[sandbox_file_tools]),
                    HookMatcher(matcher="Bash", hooks=[sandbox_bash]),
                ],
            },
            include_partial_messages=True,  # Enable streaming
            env=env_vars,  # Pass env vars to CLI subprocess
            resume=sdk_session_to_resume,  # Resume previous conversation if exists
        )

        all_messages = []
        final_response = ""
        step_count = 0
        tool_id_to_name: dict[str, str] = {}  # Map tool_use_id -> tool_name for result matching

        try:
            async with ClaudeSDKClient(options=options) as client:
                self._active_client = client  # Store for interrupt support
                await client.query(user_message)

                async for message in client.receive_response():
                    all_messages.append(message)

                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                content = block.text
                                final_response = content
                                # Emit as response (no tool calls following)
                                self._emit_step("response", content, {})

                            elif isinstance(block, ThinkingBlock):
                                # Extended thinking
                                self._emit_step("thinking", block.thinking, {})

                            elif isinstance(block, ToolUseBlock):
                                step_count += 1
                                tool_name = block.name
                                tool_input = block.input

                                # Track tool_use_id -> name for matching results later
                                tool_id_to_name[block.id] = tool_name

                                # Only show user-facing tool calls in UI (Chinese only)
                                if "phone_task" in tool_name:
                                    self._emit_step(
                                        "tool_call",
                                        "正在执行手机任务...",
                                        {"tool": tool_name, "args": tool_input}
                                    )
                                elif "take_screenshot" in tool_name:
                                    self._emit_step(
                                        "tool_call",
                                        "正在截取屏幕...",
                                        {"tool": tool_name}
                                    )
                                elif "zai" in tool_name.lower() or "vision" in tool_name.lower():
                                    self._emit_step(
                                        "tool_call",
                                        "正在分析屏幕内容...",
                                        {"tool": tool_name}
                                    )
                                elif "preference" in tool_name:
                                    action = tool_input.get("action", "")
                                    if action == "get":
                                        self._emit_step(
                                            "tool_call",
                                            "正在查询偏好设置...",
                                            {"tool": tool_name, "args": tool_input}
                                        )
                                    elif action == "set":
                                        self._emit_step(
                                            "tool_call",
                                            "正在保存偏好设置...",
                                            {"tool": tool_name, "args": tool_input}
                                        )
                                    elif action == "list":
                                        self._emit_step(
                                            "tool_call",
                                            "正在列出偏好设置...",
                                            {"tool": tool_name, "args": tool_input}
                                        )
                                    elif action == "delete":
                                        self._emit_step(
                                            "tool_call",
                                            "正在删除偏好设置...",
                                            {"tool": tool_name, "args": tool_input}
                                        )
                                elif tool_name == "Skill":
                                    skill_name = tool_input.get("skill", "")
                                    self._emit_step(
                                        "tool_call",
                                        f"正在使用技能: {skill_name}...",
                                        {"tool": tool_name, "args": tool_input}
                                    )
                                # Skip emitting internal tool calls (Read, Write, Glob, Grep, etc.)

                    elif isinstance(message, SystemMessage):
                        # SystemMessage contains metadata but not session_id
                        pass

                    elif isinstance(message, UserMessage):
                        # Tool results come back as UserMessage with ToolResultBlock
                        if isinstance(message.content, list):
                            for block in message.content:
                                if isinstance(block, ToolResultBlock):
                                    # Extract text from content structure
                                    # block.content can be: str, list of dicts, or dict
                                    raw_content = block.content
                                    if isinstance(raw_content, list):
                                        # Format: [{'type': 'text', 'text': '...'}]
                                        texts = [
                                            item.get("text", "")
                                            for item in raw_content
                                            if isinstance(item, dict) and item.get("type") == "text"
                                        ]
                                        content_text = "\n".join(texts)
                                    elif isinstance(raw_content, str):
                                        content_text = raw_content
                                    else:
                                        content_text = str(raw_content)

                                    # Don't emit tool results to UI - the agent will summarize for the user
                                    # This keeps the chat clean and avoids showing raw JSON/technical details
                                    pass

                    elif isinstance(message, ResultMessage):
                        # Final result with usage info - capture session_id for resume
                        if message.session_id:
                            self._sdk_session_ids[session_id] = message.session_id
                            print(f"[TelemetryAgentSDK] Saved SDK session: {message.session_id}")
                        if message.usage:
                            pass  # Could extract token counts here

        except Exception as e:
            self._emit_step("response", f"Error: {e}", {"error": str(e)})
            return {"error": str(e), "messages": all_messages}
        finally:
            self._active_client = None  # Clear client reference

        end_time = time.time()

        # Build telemetry
        telemetry = SessionTelemetry(
            session_id=f"sdk-{int(start_time)}",
            model_name=self.model,
            start_time=start_time,
            end_time=end_time,
            total_latency_ms=(end_time - start_time) * 1000,
        )
        telemetry.total_tool_calls = step_count
        telemetry.total_model_calls = 1  # Approximate

        self.sessions.append(telemetry)

        if verbose:
            telemetry.print_summary()

        return {
            "messages": all_messages,
            "final_response": final_response,
            "telemetry": telemetry,
        }

    def invoke(
        self,
        user_message: str,
        thread_id: str = "default",  # Maps to session_id for conversation continuity
        verbose: bool = True,
    ) -> dict:
        """
        Synchronous wrapper for invoke_async.

        Matches the TelemetryAgent.invoke() signature from agent.py.
        Handles both sync and async contexts (won't fail if called from running event loop).
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running event loop - safe to use asyncio.run()
            return asyncio.run(self.invoke_async(user_message, session_id=thread_id, verbose=verbose))

        # Already in async context - create a new thread to run the coroutine
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(
                asyncio.run,
                self.invoke_async(user_message, session_id=thread_id, verbose=verbose)
            )
            return future.result()

    def get_total_stats(self) -> dict:
        """Get aggregate stats across all sessions."""
        total_duration = sum(s.total_latency_ms for s in self.sessions)
        total_model_calls = sum(s.total_model_calls for s in self.sessions)
        total_tool_calls = sum(s.total_tool_calls for s in self.sessions)
        total_input = sum(s.total_input_tokens for s in self.sessions)
        total_output = sum(s.total_output_tokens for s in self.sessions)

        return {
            "sessions": len(self.sessions),
            "total_duration_ms": total_duration,
            "total_model_calls": total_model_calls,
            "total_tool_calls": total_tool_calls,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "avg_duration_per_session_ms": total_duration / len(self.sessions) if self.sessions else 0,
        }


# ============================================================================
# CLI FOR TESTING
# ============================================================================

async def main_async():
    """Interactive CLI for testing the SDK agent."""
    print("Phone Agent (Claude Agent SDK)")
    print("=" * 40)
    print("Type a task or 'quit' to exit.")
    print()

    agent = TelemetryAgentSDK()

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not user_input:
            continue

        if user_input.lower() in ("quit", "exit", "q"):
            print("Goodbye!")
            break

        try:
            result = await agent.invoke_async(user_input)

            if "error" in result:
                print(f"\nError: {result['error']}\n")
            else:
                print(f"\nAgent: {result.get('final_response', '(no response)')}\n")

        except Exception as e:
            print(f"\nError: {e}\n")


def main():
    """Entry point for CLI."""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
