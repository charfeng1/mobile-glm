"""
AutoGLM phone automation tool for deepagents.

This tool wraps the AutoGLM API to execute phone UI tasks.
It handles:
- Step-by-step execution with screenshot context
- Stuck detection (repeated actions, failed launches, too many steps)
- Session management for pause/resume
- Image stripping to prevent token overflow
- Data logging for fine-tuning (screenshot + input/output pairs)
"""

import base64
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from dataclasses import dataclass, field

# Import Open-AutoGLM (installed via pip from git)
from phone_agent.model import ModelConfig
from phone_agent.model.client import ModelClient, MessageBuilder
from phone_agent.actions.handler import ActionHandler, parse_action
from phone_agent.adb.screenshot import get_screenshot
from phone_agent.device_factory import get_device_factory


# ============================================================================
# ACTION REGISTRY - All available actions with their prompt definitions
# ============================================================================

ACTION_REGISTRY: dict[str, str] = {
    "Launch": '''- do(action="Launch", app="xxx")
    Launch是启动目标app的操作，这比通过主屏幕导航更快。''',

    "Tap": '''- do(action="Tap", element=[x,y])
    Tap是点击操作，点击屏幕上的特定点。坐标系统从左上角 (0,0) 到右下角 (999,999)。''',

    "Type": '''- do(action="Type", text="xxx")
    Type是输入操作，在当前聚焦的输入框中输入文本。输入前会自动清除现有文本。''',

    "Swipe": '''- do(action="Swipe", start=[x1,y1], end=[x2,y2])
    Swipe是滑动操作，用于滚动内容或导航。坐标系统从左上角 (0,0) 到右下角 (999,999)。''',

    "Long Press": '''- do(action="Long Press", element=[x,y])
    Long Press是长按操作，用于触发上下文菜单。''',

    "Double Tap": '''- do(action="Double Tap", element=[x,y])
    Double Tap在屏幕上快速连续点按两次。''',

    "Back": '''- do(action="Back")
    导航返回到上一个屏幕或关闭当前对话框。''',

    "Home": '''- do(action="Home")
    Home是回到系统桌面的操作。''',

    "Wait": '''- do(action="Wait", duration="x seconds")
    等待页面加载，x为需要等待多少秒。''',

    "Take_over": '''- do(action="Take_over", message="xxx")
    Take_over是接管操作，表示需要用户协助（登录、验证等）。''',

    "Interact": '''- do(action="Interact")
    Interact是询问用户如何选择的交互操作。''',

    "finish": '''- finish(message="xxx")
    finish是结束任务的操作，表示任务完成。''',
}

# All action names for validation
ALL_ACTIONS = set(ACTION_REGISTRY.keys())

# Default allowed actions (all except dangerous ones)
DEFAULT_ALLOWED_ACTIONS = list(ALL_ACTIONS)


# ============================================================================
# DATA LOGGING FOR FINE-TUNING
# ============================================================================

DATA_DIR = Path(__file__).parent / "data" / "sessions"


class StepLogger:
    """Logs phone automation steps for fine-tuning data collection."""

    def __init__(self, session_id: str, goal: str, model: str = "autoglm-phone"):
        self.session_id = session_id
        self.goal = goal
        self.model = model
        self.start_time = datetime.now()

        # Create session directory
        timestamp = self.start_time.strftime("%Y-%m-%d_%H%M%S")
        self.session_dir = DATA_DIR / f"{timestamp}_{session_id}"
        self.session_dir.mkdir(parents=True, exist_ok=True)

        # Initialize metadata
        self.metadata = {
            "session_id": session_id,
            "goal": goal,
            "model": model,
            "start_time": self.start_time.isoformat(),
            "steps": [],
            "final_status": None,
            "total_steps": 0,
        }

    def log_step(
        self,
        step_number: int,
        is_first_step: bool,
        screenshot_b64: str,
        text_input: str,
        screen_info: str,
        thinking: str,
        action_raw: str,
        action_parsed: dict,
        action_result: str | None = None,
    ):
        """Log a single AutoGLM step."""
        step_name = f"step_{step_number:03d}"

        # Save screenshot as JPEG
        img_path = self.session_dir / f"{step_name}.jpg"
        img_data = base64.b64decode(screenshot_b64)
        with open(img_path, "wb") as f:
            f.write(img_data)

        # Build structured step data
        step_data = {
            "step_number": step_number,
            "timestamp": datetime.now().isoformat(),
            "is_first_step": is_first_step,
            "input": {
                "text": text_input,
                "screen_info": screen_info,
                "image_file": f"{step_name}.jpg",
            },
            "output": {
                "thinking": thinking,
                "action_raw": action_raw,
                "action_parsed": action_parsed,
            },
            "result": action_result,
        }

        # Save step JSON
        json_path = self.session_dir / f"{step_name}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(step_data, f, indent=2, ensure_ascii=False)

        # Update metadata
        self.metadata["steps"].append(step_name)
        self.metadata["total_steps"] = step_number

    def finalize(self, status: str, message: str):
        """Finalize the session with final status."""
        self.metadata["final_status"] = status
        self.metadata["final_message"] = message
        self.metadata["end_time"] = datetime.now().isoformat()

        # Save metadata
        meta_path = self.session_dir / "metadata.json"
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(self.metadata, f, indent=2, ensure_ascii=False)

        print(f"[DataLogger] Session saved: {self.session_dir}")


@dataclass
class PhoneTaskResult:
    """Result from a phone task execution."""
    status: str  # "completed", "stuck", "needs_takeover", "needs_interaction", "error"
    message: str
    steps_taken: int
    last_actions: list[str] = field(default_factory=list)
    screenshot_b64: str | None = None
    session_id: str | None = None


class PhoneExecutor:
    """Executes phone tasks using AutoGLM."""

    DEVICE_ID = os.getenv("PHONE_DEVICE_ID", "")  # Set via environment variable

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or self._load_api_key()
        if not self.api_key:
            raise ValueError("ZHIPU_API_KEY not found")

        self.client = self._create_client()
        # Note: system_prompt is now built dynamically per-task via _build_system_prompt()

        # Session storage (in-memory for now, could be persisted)
        self.sessions: dict[str, dict] = {}

    def _load_api_key(self) -> str | None:
        """Load API key from .env file or environment."""
        # Check project .env
        env_path = Path(__file__).parent / ".env"
        if env_path.exists():
            with open(env_path) as f:
                for line in f:
                    if line.startswith("ZHIPU_API_KEY="):
                        return line.split("=", 1)[1].strip()
        return os.getenv("ZHIPU_API_KEY")

    def _create_client(self) -> ModelClient:
        """Create AutoGLM model client."""
        config = ModelConfig(
            base_url="https://open.bigmodel.cn/api/paas/v4",
            model_name="autoglm-phone",
            api_key=self.api_key,
            lang="en",
        )
        return ModelClient(config)

    def _build_system_prompt(self, allowed_actions: list[str] | None = None) -> str:
        """Build system prompt dynamically based on allowed actions.

        Args:
            allowed_actions: List of action names to include in prompt.
                           If None, all actions from ACTION_REGISTRY are included.

        Returns:
            Complete system prompt with only the specified actions.
        """
        from datetime import datetime
        today = datetime.today()
        formatted_date = today.strftime("%Y年%m月%d日")

        # Use all actions if not specified
        if allowed_actions is None:
            allowed_actions = list(ACTION_REGISTRY.keys())

        # Build action documentation from registry
        action_docs = "\n".join([
            ACTION_REGISTRY[action]
            for action in allowed_actions
            if action in ACTION_REGISTRY
        ])

        return f"""今天的日期是: {formatted_date}

你是一个手机操控专家，专门负责执行手机上的操作任务。

你的职责边界：
【你负责的】手机操作相关的所有任务，包括：
- 打开应用、点击按钮、滑动屏幕、输入文字等基础操作
- 多步骤的手机操作流程（如：打开微信 → 找到联系人 → 查看消息 → 回复）
- 从屏幕上获取信息并返回（如：查看消息内容、读取通知、查看页面信息）

【你不负责的】复杂的智能决策和外部信息处理：
- 网络搜索、信息查询
- 复杂的内容分析、问题理解
- 需要外部知识的决策
- 跨应用的复杂任务编排

工作原则：
- 专注于手机操作本身，使用屏幕上可见的信息来完成任务
- 当任务是"获取信息"时，获取后立即使用 finish() 返回给主流程
- 当任务是"执行操作"时，完成操作后使用 finish() 确认完成
- 如果任务需要外部信息（如网络搜索结果），应在 finish() 中说明"需要主流程提供XX信息"

你必须严格按照要求输出以下格式：
<think>{{think}}</think>
<answer>{{action}}</answer>

其中：
- {{think}} 是对你为什么选择这个操作的简短推理说明。
- {{action}} 是本次执行的具体操作指令，必须严格遵循下方定义的指令格式。

【可用操作 - 只能使用以下操作】：
{action_docs}

必须遵循的规则：
1. 只能使用上述列出的操作，不要使用其他操作。
2. 在执行任何操作前，先检查当前app是否是目标app，如果不是且Launch可用，先执行 Launch。
3. 如果进入到了无关页面，先执行 Back。如果执行Back后页面没有变化，请点击页面左上角的返回键进行返回。
4. 如果页面未加载出内容，最多连续 Wait 三次，否则执行 Back重新进入。
5. 如果当前页面找不到目标信息，可以尝试 Swipe 滑动查找。
6. 在执行下一步操作前请一定要检查上一步的操作是否生效，如果没生效请调整位置重试。
7. 任务完成时使用 finish，并在message中描述完成情况。
8. 坐标系统从左上角 (0,0) 到右下角 (999,999)。
"""

    def _compute_screenshot_hash(self, screenshot_b64: str) -> str:
        """Compute a hash of screenshot for stagnation detection."""
        # Use first 1000 chars of base64 as proxy (faster than full decode)
        return hashlib.md5(screenshot_b64[:1000].encode()).hexdigest()[:8]

    def _detect_stuck(
        self,
        state: dict,
        action_name: str,
        result_msg: str = "",
        screenshot_b64: str | None = None,
    ) -> tuple[bool, str]:
        """
        Detect if the agent is stuck using multiple heuristics.

        Based on analysis of 59 logged sessions with 52% failure rate:
        - Primary pattern: Type action failures (50% of stuck sessions)
        - Secondary: Tap-Type-Tap loops without progress (40%)
        - Tertiary: Navigation stuck on video/media pages (15%)

        Returns:
            Tuple of (is_stuck: bool, reason: str)
        """
        # Track recent actions (keep last 15 for pattern analysis)
        recent_actions = state.get("recent_actions", [])
        recent_actions.append(action_name)
        if len(recent_actions) > 15:
            recent_actions = recent_actions[-15:]
        state["recent_actions"] = recent_actions

        # Track screenshot hashes for stagnation detection
        if screenshot_b64:
            screen_hash = self._compute_screenshot_hash(screenshot_b64)
            screen_hashes = state.get("screen_hashes", [])
            screen_hashes.append(screen_hash)
            if len(screen_hashes) > 6:
                screen_hashes = screen_hashes[-6:]
            state["screen_hashes"] = screen_hashes

        # =========================================================
        # CHECK 1: Launch failures (existing, slightly relaxed)
        # =========================================================
        if action_name == "Launch" and "not found" in result_msg.lower():
            launch_failures = state.get("launch_failures", 0) + 1
            state["launch_failures"] = launch_failures
            if launch_failures >= 2:
                return True, f"Launch failed {launch_failures} times"

        # =========================================================
        # CHECK 2: Screen stagnation (NEW)
        # Same screen 4+ consecutive times = no progress
        # =========================================================
        screen_hashes = state.get("screen_hashes", [])
        if len(screen_hashes) >= 4:
            last_4_hashes = screen_hashes[-4:]
            if len(set(last_4_hashes)) == 1:
                return True, "Screen unchanged for 4 consecutive steps"

        # =========================================================
        # CHECK 3: Tap-Type-Tap cycle detection (NEW - 50% of failures)
        # Pattern: Tap → Type → Tap repeating = likely Type failed
        # =========================================================
        if len(recent_actions) >= 6:
            last_6 = recent_actions[-6:]
            # Count Tap-Type-Tap sequences
            ttp_count = 0
            for i in range(len(last_6) - 2):
                if (last_6[i] == "Tap" and
                    last_6[i+1] == "Type" and
                    last_6[i+2] == "Tap"):
                    ttp_count += 1
            if ttp_count >= 2:
                return True, "Tap-Type-Tap cycle detected (Type likely failing)"

        # =========================================================
        # CHECK 4: Navigation oscillation (NEW)
        # Back/Home alternating with other actions without progress
        # =========================================================
        if len(recent_actions) >= 6:
            last_6 = recent_actions[-6:]
            nav_actions = [a for a in last_6 if a in ("Back", "Home")]
            if len(nav_actions) >= 4:
                return True, "Navigation stuck (4+ Back/Home in last 6 actions)"

        # =========================================================
        # CHECK 5: Low action diversity (NEW)
        # Only 1-2 unique action types in last 10 = narrow strategy
        # =========================================================
        if len(recent_actions) >= 10:
            last_10 = recent_actions[-10:]
            unique_actions = set(last_10)
            # Exclude finish-like actions from count
            unique_actions.discard("finish")
            if len(unique_actions) <= 1:
                return True, f"Low action diversity (only {unique_actions} in last 10 steps)"

        # =========================================================
        # CHECK 6: Repetitive actions (existing, tuned)
        # Same action 4+ times in last 5 steps
        # =========================================================
        last_5 = recent_actions[-5:] if len(recent_actions) >= 5 else recent_actions
        if len(last_5) >= 4:
            most_common = max(set(last_5), key=last_5.count)
            if last_5.count(most_common) >= 4:
                return True, f"Repetitive {most_common} (4+ times in last 5 steps)"

        # =========================================================
        # CHECK 7: Step limit (existing, increased to 20)
        # =========================================================
        current_step = state.get("step", 0)
        if current_step >= 20:
            return True, f"Reached step limit ({current_step} steps)"

        return False, ""

    def _run_step(self, state: dict) -> dict:
        """Run a single AutoGLM step."""
        # Take screenshot
        screenshot = get_screenshot(self.DEVICE_ID)
        if not screenshot or screenshot.is_sensitive:
            return {**state, "error": "Screenshot failed or sensitive", "finished": True}

        screenshot_b64 = screenshot.base64_data
        state["last_screenshot"] = screenshot_b64

        # Get current app info
        device_factory = get_device_factory()
        current_app = device_factory.get_current_app(self.DEVICE_ID)
        screen_info = MessageBuilder.build_screen_info(current_app)

        # Build user message
        is_first = state["step"] == 0
        if is_first:
            text_content = f"{state['instruction']}\\n\\n{screen_info}"
        else:
            text_content = f"** Screen Info **\\n\\n{screen_info}"

        user_msg = MessageBuilder.create_user_message(
            text=text_content,
            image_base64=screenshot_b64,
        )

        # Add to history
        messages = state["messages"] + [user_msg]

        # Call AutoGLM API
        model_response = self.client.request(messages)
        thinking = model_response.thinking
        action_str = model_response.action

        # Parse action
        action = parse_action(action_str)
        action_type = action.get("_metadata")
        action_name = action.get("action", "")

        # Handle Type_Name as Type for validation purposes
        validation_action_name = "Type" if action_name == "Type_Name" else action_name

        # =========================================================
        # ACTION VALIDATION - Block disallowed actions
        # =========================================================
        allowed_actions = state.get("allowed_actions", list(ACTION_REGISTRY.keys()))
        if action_type == "do" and validation_action_name not in allowed_actions:
            # Action not allowed - return blocked status to orchestrator
            return {
                **state,
                "blocked": True,
                "blocked_action": action_name,
                "blocked_message": f"Action '{action_name}' is not allowed. Allowed actions: {allowed_actions}",
                "step": state["step"] + 1,
            }

        # =========================================================
        # APP VALIDATION - Block disallowed apps for Launch action
        # =========================================================
        allowed_apps = state.get("allowed_apps")
        if action_type == "do" and action_name == "Launch" and allowed_apps is not None:
            app_name = action.get("app", "")
            if app_name not in allowed_apps:
                # App not allowed - return blocked status to orchestrator
                return {
                    **state,
                    "blocked": True,
                    "blocked_action": f"Launch({app_name})",
                    "blocked_message": f"App '{app_name}' is not allowed. Allowed apps: {allowed_apps}. Try navigating to the app from home screen instead.",
                    "step": state["step"] + 1,
                }

        # Log step for fine-tuning data collection
        if "logger" in state and state["logger"]:
            state["logger"].log_step(
                step_number=state["step"] + 1,
                is_first_step=is_first,
                screenshot_b64=screenshot_b64,
                text_input=text_content,
                screen_info=screen_info,
                thinking=thinking,
                action_raw=action_str,
                action_parsed=action,
            )

        # Strip image from user message for context management
        user_msg_no_image = MessageBuilder.remove_images_from_message(user_msg)

        # Store formatted response
        formatted_response = f"<think>{thinking}</think><answer>{action_str}</answer>"
        assistant_msg = MessageBuilder.create_assistant_message(formatted_response)

        new_messages = state["messages"] + [user_msg_no_image, assistant_msg]

        # Check for finish
        if action_type == "finish":
            return {
                **state,
                "finished": True,
                "result": action.get("message", "Task completed"),
                "step": state["step"] + 1,
                "messages": new_messages,
            }

        # Check for Take_over (login, captcha, payment)
        if action_name == "Take_over":
            return {
                **state,
                "needs_takeover": True,
                "takeover_message": action.get("message", "User intervention required"),
                "step": state["step"] + 1,
                "messages": new_messages,
            }

        # Check for Interact (clarification needed)
        if action_name == "Interact":
            return {
                **state,
                "needs_interaction": True,
                "interaction_message": action.get("message", "Clarification needed"),
                "step": state["step"] + 1,
                "messages": new_messages,
            }

        # Execute action
        width, height = screenshot.width, screenshot.height
        handler = ActionHandler(device_id=self.DEVICE_ID)
        result = handler.execute(action, width, height)
        result_msg = result.message or "OK"

        # Check if stuck (pass screenshot for stagnation detection)
        is_stuck, stuck_reason = self._detect_stuck(
            state, action_name, result_msg, screenshot_b64
        )
        if is_stuck:
            return {
                **state,
                "stuck": True,
                "stuck_message": f"{stuck_reason}. Recent actions: {state.get('recent_actions', [])[-5:]}",
                "step": state["step"] + 1,
                "messages": new_messages,
            }

        if result.should_finish:
            return {
                **state,
                "finished": True,
                "result": result.message,
                "step": state["step"] + 1,
                "messages": new_messages,
            }

        return {
            **state,
            "step": state["step"] + 1,
            "messages": new_messages,
            "last_action": action_name,
        }

    def execute_task(
        self,
        goal: str,
        max_steps: int = 20,
        guidance: str | None = None,
        session_id: str | None = None,
        allowed_actions: list[str] | None = None,
        allowed_apps: list[str] | None = None,
    ) -> PhoneTaskResult:
        """
        Execute a phone task.

        Args:
            goal: The task to accomplish (e.g., "Open DoorDash and order pad thai")
            max_steps: Maximum steps before forcing a pause
            guidance: Optional guidance to help the agent (used when resuming)
            session_id: Session ID to resume a previous task
            allowed_actions: List of allowed action names. If None, all actions allowed.
                           Actions not in this list will be blocked and reported.
                           Example: ["Tap", "Swipe", "Back", "finish"] for read-only mode.
            allowed_apps: List of allowed app names for Launch action. If None, all apps allowed.
                        If set, Launch is only allowed for apps in this list.
                        Example: ["美团", "淘宝"] to only allow launching Meituan and Taobao.

        Returns:
            PhoneTaskResult with status and details
        """
        # Normalize allowed_actions
        if allowed_actions is None:
            allowed_actions = list(ACTION_REGISTRY.keys())

        # Always allow finish and Take_over for safety
        if "finish" not in allowed_actions:
            allowed_actions.append("finish")
        if "Take_over" not in allowed_actions:
            allowed_actions.append("Take_over")

        # Resume or start new session
        if session_id and session_id in self.sessions:
            state = self.sessions[session_id]
            state["stuck"] = False
            state["needs_takeover"] = False
            state["needs_interaction"] = False
            # Update allowed_actions and allowed_apps for resumed session
            state["allowed_actions"] = allowed_actions
            state["allowed_apps"] = allowed_apps

            # Add guidance to context if provided
            if guidance:
                state["messages"].append(
                    MessageBuilder.create_user_message(f"User guidance: {guidance}")
                )
        else:
            # New session
            import uuid
            session_id = str(uuid.uuid4())[:8]

            # Build system prompt with only allowed actions
            system_prompt = self._build_system_prompt(allowed_actions)

            # Initialize logger for data collection
            logger = StepLogger(session_id=session_id, goal=goal)

            state = {
                "instruction": goal,
                "step": 0,
                "messages": [MessageBuilder.create_system_message(system_prompt)],
                "finished": False,
                "recent_actions": [],
                "logger": logger,
                "allowed_actions": allowed_actions,
                "allowed_apps": allowed_apps,
            }

        # Run steps
        while state["step"] < max_steps:
            try:
                state = self._run_step(state)
            except Exception as e:
                # Finalize logger on exception
                if state.get("logger"):
                    state["logger"].finalize("error", str(e))
                self.sessions[session_id] = state
                return PhoneTaskResult(
                    status="error",
                    message=str(e),
                    steps_taken=state["step"],
                    last_actions=state.get("recent_actions", []),
                    session_id=session_id,
                )

            # Check termination conditions
            if state.get("finished"):
                # Finalize logger
                if state.get("logger"):
                    state["logger"].finalize("completed", state.get("result", "Task completed"))
                # Clean up session
                if session_id in self.sessions:
                    del self.sessions[session_id]
                return PhoneTaskResult(
                    status="completed",
                    message=state.get("result", "Task completed"),
                    steps_taken=state["step"],
                    last_actions=state.get("recent_actions", []),
                    screenshot_b64=state.get("last_screenshot"),
                )

            if state.get("needs_takeover"):
                # Finalize logger
                if state.get("logger"):
                    state["logger"].finalize("needs_takeover", state.get("takeover_message", "User intervention required"))
                self.sessions[session_id] = state
                return PhoneTaskResult(
                    status="needs_takeover",
                    message=state.get("takeover_message", "User intervention required"),
                    steps_taken=state["step"],
                    last_actions=state.get("recent_actions", []),
                    screenshot_b64=state.get("last_screenshot"),
                    session_id=session_id,
                )

            if state.get("needs_interaction"):
                # Finalize logger
                if state.get("logger"):
                    state["logger"].finalize("needs_interaction", state.get("interaction_message", "Clarification needed"))
                self.sessions[session_id] = state
                return PhoneTaskResult(
                    status="needs_interaction",
                    message=state.get("interaction_message", "Clarification needed"),
                    steps_taken=state["step"],
                    last_actions=state.get("recent_actions", []),
                    screenshot_b64=state.get("last_screenshot"),
                    session_id=session_id,
                )

            if state.get("blocked"):
                # Action was blocked - return to orchestrator
                blocked_msg = f"Action '{state.get('blocked_action')}' blocked. {state.get('blocked_message', '')}"
                if state.get("logger"):
                    state["logger"].finalize("blocked", blocked_msg)
                self.sessions[session_id] = state
                return PhoneTaskResult(
                    status="blocked",
                    message=blocked_msg,
                    steps_taken=state["step"],
                    last_actions=state.get("recent_actions", []),
                    screenshot_b64=state.get("last_screenshot"),
                    session_id=session_id,
                )

            if state.get("stuck"):
                # Finalize logger
                if state.get("logger"):
                    state["logger"].finalize("stuck", state.get("stuck_message", "Agent appears stuck"))
                self.sessions[session_id] = state
                return PhoneTaskResult(
                    status="stuck",
                    message=state.get("stuck_message", "Agent appears stuck"),
                    steps_taken=state["step"],
                    last_actions=state.get("recent_actions", []),
                    screenshot_b64=state.get("last_screenshot"),
                    session_id=session_id,
                )

            if state.get("error"):
                # Finalize logger
                if state.get("logger"):
                    state["logger"].finalize("error", state.get("error", "Unknown error"))
                return PhoneTaskResult(
                    status="error",
                    message=state.get("error", "Unknown error"),
                    steps_taken=state["step"],
                    last_actions=state.get("recent_actions", []),
                    session_id=session_id,
                )

        # Hit max steps - finalize logger
        if state.get("logger"):
            state["logger"].finalize("stuck", f"Reached max steps ({max_steps})")
        self.sessions[session_id] = state
        return PhoneTaskResult(
            status="stuck",
            message=f"Reached max steps ({max_steps})",
            steps_taken=state["step"],
            last_actions=state.get("recent_actions", []),
            screenshot_b64=state.get("last_screenshot"),
            session_id=session_id,
        )


# Singleton executor instance
_executor: PhoneExecutor | None = None


def get_executor() -> PhoneExecutor:
    """Get or create the phone executor singleton."""
    global _executor
    if _executor is None:
        _executor = PhoneExecutor()
    return _executor


def phone_task(
    goal: str,
    max_steps: int = 10,
    guidance: str | None = None,
    session_id: str | None = None,
    allowed_actions: list[str] | None = None,
    allowed_apps: list[str] | None = None,
) -> str:
    """
    Execute a phone automation task using AutoGLM.

    This tool controls a phone (Android device) to accomplish UI tasks like:
    - Opening apps
    - Tapping buttons
    - Entering text
    - Navigating menus
    - Browsing content, ordering food, etc.

    Args:
        goal: What you want to accomplish on the phone. Be specific and focused.
            Good: "Open Meituan app" or "Search for 'noodles' in the search bar"
            Bad: "Order food" (too vague, will get stuck)
        max_steps: Maximum UI actions before returning. Choose based on task complexity:
            - 1-3: Simple tasks (open app, tap one button, read screen)
            - 5-10: Medium tasks (navigate to a section, fill a form)
            - 10-20: Complex tasks (multi-step workflows)
            Lower values give you more control; higher values let AutoGLM work longer autonomously.
        guidance: Hints to help the agent when resuming a stuck task.
            Example: "Use the search bar instead of scrolling"
        session_id: Session ID to resume a previous task (returned when task pauses)
        allowed_actions: List of allowed action names. Controls what AutoGLM can do.
            If None, all actions are allowed. Use for security/control:
            - Read-only mode: ["Tap", "Swipe", "Back", "Home", "Wait", "finish"]
            - No app switching: ["Tap", "Type", "Swipe", "Back", "Wait", "finish"]
            - Full access: None (all actions allowed)
            Available actions: Launch, Tap, Type, Swipe, Long Press, Double Tap,
                             Back, Home, Wait, Take_over, Interact, finish
        allowed_apps: List of allowed app names for the Launch action.
            If None, all apps can be launched. If set, only apps in this list can be launched.
            Use for security/scoping:
            - Single app task: ["美团"] to only allow launching Meituan
            - Multi-app task: ["淘宝", "京东"] for comparison tasks
            - No launches: Set allowed_actions without "Launch" instead

    Returns:
        JSON string with:
        - status: "completed", "stuck", "blocked", "needs_takeover", "needs_interaction", or "error"
        - message: Details about the result or what's needed
        - steps_taken: Number of UI actions performed
        - last_actions: Recent action types (for debugging)
        - session_id: ID to resume this task (if paused)

    Example usage:
        # Simple task - just open an app (1-3 steps)
        result = phone_task(goal="Open Meituan", max_steps=3)

        # Medium task - navigate somewhere (5-10 steps)
        result = phone_task(goal="Go to the food delivery section", max_steps=8)

        # Read-only task - only allow viewing, no typing or launching
        result = phone_task(
            goal="Check the current price of the first item",
            max_steps=5,
            allowed_actions=["Tap", "Swipe", "Back", "Wait"]
        )

        # Scoped to specific app - can only launch Meituan
        result = phone_task(
            goal="Search for late night food",
            max_steps=10,
            allowed_apps=["美团"]  # Can only launch Meituan
        )

        # Resume a stuck task with guidance
        result = phone_task(
            goal="Find noodle restaurants",
            guidance="Try using the search bar at the top",
            session_id="abc123",
            max_steps=5
        )
    """
    executor = get_executor()
    result = executor.execute_task(
        goal=goal,
        max_steps=max_steps,
        guidance=guidance,
        session_id=session_id,
        allowed_actions=allowed_actions,
        allowed_apps=allowed_apps,
    )

    response = {
        "status": result.status,
        "message": result.message,
        "steps_taken": result.steps_taken,
        "last_actions": result.last_actions,
        "session_id": result.session_id,
    }

    # Save latest screenshot to agent workspace for cloud agent to view
    # This avoids inflating token count while still allowing visual analysis
    if result.screenshot_b64:
        workspace_dir = Path(__file__).parent / "agent_workspace" / "screenshots"
        workspace_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = workspace_dir / "latest_phone_screen.jpg"
        try:
            import io
            from PIL import Image
            from security import preprocess_screenshot

            img_data = base64.b64decode(result.screenshot_b64)
            img = Image.open(io.BytesIO(img_data))
            img = img.convert("RGB")
            # Apply security filter to remove hidden injection text
            img = preprocess_screenshot(img)
            img.save(screenshot_path, "JPEG", quality=85)
            # Return relative path from workspace root
            response["screenshot_path"] = "screenshots/latest_phone_screen.jpg"
        except Exception as e:
            print(f"[phone_tool] Failed to save screenshot: {e}")

    return json.dumps(response, indent=2)
