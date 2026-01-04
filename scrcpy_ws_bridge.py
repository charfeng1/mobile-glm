"""
Scrcpy WebSocket Bridge - Streams H.264 video from Android to WebSocket clients.

This bridge:
1. Connects to scrcpy-server on an Android device (via ADB)
2. Receives raw H.264 NAL units
3. Forwards them to WebSocket clients for hardware decoding
4. Handles agent chat messages from iOS and forwards to DeepAgent

For iOS clients: Use VideoToolbox to decode H.264 in hardware
For web clients: Use WebCodecs API (if available) or Broadway.js fallback

Usage:
    uv run python scrcpy_ws_bridge.py [device_serial]
"""

import asyncio
import json
import logging
import os
import queue
import re
import socket
import struct
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from time import sleep, time
from typing import Optional, Set, Dict

# WebSocket server
try:
    import websockets
    from websockets.server import serve
except ImportError:
    print("Installing websockets...")
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "websockets"], check=True)
    import websockets
    from websockets.server import serve

# ADB utilities
try:
    from adbutils import AdbDevice, AdbConnection, AdbError, Network, adb
except ImportError:
    print("Installing adbutils...")
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "adbutils"], check=True)
    from adbutils import AdbDevice, AdbConnection, AdbError, Network, adb


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


@dataclass
class DeviceInfo:
    """Information about the connected device."""
    name: str
    width: int
    height: int
    serial: str


@dataclass
class TouchState:
    """Per-client touch tracking state."""
    start_pos: Optional[tuple[float, float]] = None
    start_time: float = 0.0


class ScrcpyBridge:
    """
    Bridge between scrcpy-server and WebSocket clients.

    Receives raw H.264 from scrcpy and broadcasts to all connected WebSocket clients.
    """

    SCRCPY_SERVER_VERSION = "2.4"

    # Configurable constants
    SWIPE_DISTANCE_THRESHOLD = 0.03  # 3% of screen distance minimum for swipe
    SWIPE_VELOCITY_THRESHOLD = 0.3  # Normalized units per second for swipe
    SWIPE_MAX_TAP_DURATION = 0.4  # Max seconds for a tap (longer = drag intent)
    TOUCH_TIMEOUT = 5.0  # Seconds before stale touch state is cleared
    VIDEO_POLL_INTERVAL = 0.001  # 1ms polling for low latency

    def __init__(
        self,
        device: Optional[AdbDevice] = None,
        max_width: int = 1080,
        bitrate: int = 4_000_000,  # 4 Mbps - good for mobile
        max_fps: int = 60,
        ws_host: str = "0.0.0.0",
        ws_port: int = 8765,
    ):
        self.device = device or self._get_device()
        self.max_width = max_width
        self.bitrate = bitrate
        self.max_fps = max_fps
        self.ws_host = ws_host
        self.ws_port = ws_port

        self.device_info: Optional[DeviceInfo] = None
        self.clients: Set[websockets.WebSocketServerProtocol] = set()
        self.alive = False

        # Scrcpy connections
        self._server_stream: Optional[AdbConnection] = None
        self._video_socket: Optional[socket.socket] = None
        self._control_socket: Optional[socket.socket] = None

        # H.264 parameter sets (needed by decoders)
        self._sps: Optional[bytes] = None  # Sequence Parameter Set (type 7)
        self._pps: Optional[bytes] = None  # Picture Parameter Set (type 8)
        # Cache first keyframe (IDR) for new clients
        self._last_keyframe: Optional[bytes] = None
        # Buffer for accumulating incomplete NAL units
        self._nal_buffer = bytearray()
        # Physical screen dimensions (queried once from ADB)
        self._physical_width: int = 0
        self._physical_height: int = 0
        # Per-client touch tracking for swipe detection
        self._touch_states: Dict[websockets.WebSocketServerProtocol, TouchState] = {}

        # Agent for chat - lazy initialized
        self._agent = None
        self._agent_thread_id = "ios-session"
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._chat_lock = threading.Lock()  # Prevent concurrent agent invocations
        self._chat_cancelled = threading.Event()  # Flag to cancel current chat

    def _get_agent(self):
        """Lazy-load the TelemetryAgentSDK to avoid import at module level."""
        if self._agent is None:
            try:
                # Use Claude Agent SDK version instead of DeepAgents
                from agent_sdk import TelemetryAgentSDK
                self._agent = TelemetryAgentSDK()
                logger.info("Agent SDK initialized")
            except Exception as e:
                logger.error(f"Failed to initialize agent: {e}")
                raise
        return self._agent

    def _get_device(self) -> AdbDevice:
        """Get first available ADB device."""
        devices = adb.device_list()
        if not devices:
            raise RuntimeError("No ADB devices found. Connect a device or start an emulator.")
        return devices[0]

    def _get_scrcpy_server_jar(self) -> str:
        """Get path to scrcpy-server.jar, downloading if needed."""
        # First check if py-scrcpy-client is installed
        try:
            import scrcpy
            jar_path = os.path.join(os.path.dirname(scrcpy.__file__), "scrcpy-server.jar")
            if os.path.exists(jar_path):
                return jar_path
        except ImportError:
            pass

        # Check local directory
        local_jar = Path(__file__).parent / "scrcpy-server.jar"
        if local_jar.exists():
            return str(local_jar)

        # Download from scrcpy releases
        logger.info("Downloading scrcpy-server.jar...")
        import urllib.request
        url = f"https://github.com/Genymobile/scrcpy/releases/download/v{self.SCRCPY_SERVER_VERSION}/scrcpy-server-v{self.SCRCPY_SERVER_VERSION}"
        urllib.request.urlretrieve(url, local_jar)
        return str(local_jar)

    def _deploy_server(self) -> None:
        """Deploy scrcpy-server to the Android device."""
        jar_path = self._get_scrcpy_server_jar()
        jar_name = "scrcpy-server.jar"

        logger.info(f"Pushing scrcpy-server to device {self.device.serial}...")
        self.device.sync.push(jar_path, f"/data/local/tmp/{jar_name}")

        # Build server command
        commands = [
            f"CLASSPATH=/data/local/tmp/{jar_name}",
            "app_process",
            "/",
            "com.genymobile.scrcpy.Server",
            self.SCRCPY_SERVER_VERSION,
            "log_level=info",
            f"max_size={self.max_width}",
            f"max_fps={self.max_fps}",
            f"video_bit_rate={self.bitrate}",
            "video_encoder=OMX.google.h264.encoder",
            "video_codec=h264",
            "tunnel_forward=true",
            "send_frame_meta=false",  # Raw H.264 without scrcpy headers
            "control=true",
            "audio=false",
            "show_touches=false",
            "stay_awake=true",
            "power_off_on_close=false",
            "clipboard_autosync=false",
        ]

        logger.info("Starting scrcpy-server...")
        self._server_stream = self.device.shell(commands, stream=True)

        # Wait for server startup
        self._server_stream.read(10)
        logger.info("scrcpy-server started")

    def _connect_to_server(self) -> None:
        """Connect to the scrcpy-server sockets."""
        logger.info("Connecting to scrcpy-server...")

        # Connect video socket with retry
        for i in range(30):  # 3 second timeout
            try:
                self._video_socket = self.device.create_connection(
                    Network.LOCAL_ABSTRACT, "scrcpy"
                )
                break
            except AdbError:
                sleep(0.1)
        else:
            raise ConnectionError("Failed to connect to scrcpy-server")

        # Receive dummy byte
        dummy = self._video_socket.recv(1)
        if dummy != b"\x00":
            raise ConnectionError(f"Expected dummy byte 0x00, got {dummy!r}")

        # Connect control socket
        self._control_socket = self.device.create_connection(
            Network.LOCAL_ABSTRACT, "scrcpy"
        )

        # Receive device name (64 bytes, null-terminated)
        device_name = self._video_socket.recv(64).decode("utf-8").rstrip("\x00")

        # scrcpy 2.x protocol: codec info + initial video size
        # First 4 bytes: codec ID (0x68323634 = "h264")
        codec_data = self._video_socket.recv(4)
        logger.info(f"Codec data: {codec_data.hex()}")

        # Next 4 bytes: initial width (u32 BE)
        # Next 4 bytes: initial height (u32 BE)
        res_data = self._video_socket.recv(8)
        width, height = struct.unpack(">II", res_data)

        self.device_info = DeviceInfo(
            name=device_name,
            width=width,
            height=height,
            serial=self.device.serial,
        )

        logger.info(f"Connected to {device_name} ({width}x{height})")

        # Set non-blocking for async reads
        self._video_socket.setblocking(False)

    async def _handle_client(self, websocket: websockets.WebSocketServerProtocol) -> None:
        """Handle a new WebSocket client connection."""
        self.clients.add(websocket)
        client_addr = websocket.remote_address
        logger.info(f"Client connected: {client_addr}")

        try:
            # Send device info to client
            if self.device_info:
                await websocket.send(json.dumps({
                    "type": "device_info",
                    "name": self.device_info.name,
                    "width": self.device_info.width,
                    "height": self.device_info.height,
                    "serial": self.device_info.serial,
                }))

            # Send SPS and PPS if we have them (needed for decoder initialization)
            if self._sps:
                await websocket.send(self._sps)
                logger.info(f"Sent cached SPS to new client: {len(self._sps)} bytes")
            if self._pps:
                await websocket.send(self._pps)
                logger.info(f"Sent cached PPS to new client: {len(self._pps)} bytes")

            # Send last keyframe to prevent initial corruption
            if self._last_keyframe:
                await websocket.send(self._last_keyframe)
                logger.info(f"Sent cached keyframe to new client: {len(self._last_keyframe)} bytes")

            # Handle incoming messages (touch events, chat, etc.)
            async for message in websocket:
                await self._handle_control_message(websocket, message)

        except websockets.ConnectionClosed:
            logger.info(f"Client disconnected: {client_addr}")
        finally:
            self.clients.discard(websocket)
            # Clean up per-client touch state
            self._touch_states.pop(websocket, None)

    async def _handle_control_message(
        self,
        client: websockets.WebSocketServerProtocol,
        message: str,
    ) -> None:
        """Handle control messages from clients (touch, keys, chat, etc.)."""
        try:
            data = json.loads(message)
            msg_type = data.get("type")
            if msg_type != "touch":  # Don't spam touch logs
                logger.info(f"Received control message: type={msg_type}")

            if msg_type == "touch":
                # Touch event: {type: "touch", action: "down"|"up"|"move", x: 0-1, y: 0-1}
                logger.info(f"Touch: {data['action']} at ({data['x']:.3f}, {data['y']:.3f})")
                await self._send_touch(
                    client=client,
                    action=data["action"],
                    x=data["x"],
                    y=data["y"],
                )
            elif msg_type == "key":
                # Key event: {type: "key", keycode: int, action: "down"|"up"}
                await self._send_key(
                    keycode=data["keycode"],
                    action=data["action"],
                )
            elif msg_type == "back":
                logger.info("Back button pressed")
                await self._send_key(keycode=4, action="press")  # KEYCODE_BACK
            elif msg_type == "home":
                logger.info("Home button pressed")
                await self._send_key(keycode=3, action="press")  # KEYCODE_HOME
            elif msg_type == "recents":
                logger.info("Recents button pressed")
                await self._send_key(keycode=187, action="press")  # KEYCODE_APP_SWITCH
            elif msg_type == "chat":
                # Chat message: {type: "chat", message: "user text"}
                user_message = data.get("message", "")
                logger.info(f"Chat message received: {user_message[:50]}...")
                # Run as background task so we can still receive cancel messages
                asyncio.create_task(self._handle_chat_message(user_message, client))
            elif msg_type == "cancel":
                # Cancel current agent task
                logger.info("Cancel requested by user")
                self._chat_cancelled.set()
                # Properly interrupt the agent via SDK
                agent = self._get_agent()
                agent.interrupt_sync()
            elif msg_type == "new_session":
                # Start a new conversation (clear session history)
                logger.info("New session requested by user")
                agent = self._get_agent()
                agent.clear_session(self._agent_thread_id)
                # Send confirmation to client
                await client.send(json.dumps({
                    "type": "session_cleared",
                    "message": "新对话已开始",
                }))
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Invalid control message: {e}")

    async def _handle_chat_message(
        self, user_message: str, websocket: websockets.WebSocketServerProtocol
    ) -> None:
        """Handle a chat message by invoking the Claude Agent SDK and streaming response."""
        # Validate input
        if not user_message or not user_message.strip():
            await websocket.send(json.dumps({
                "type": "agent_error",
                "error": "Empty message",
            }))
            return

        # Clear cancel flag at start of new chat
        self._chat_cancelled.clear()

        loop = asyncio.get_running_loop()

        # Send "thinking" status
        await websocket.send(json.dumps({
            "type": "agent_status",
            "status": "thinking",
        }))

        # Queue for streaming steps from agent thread to async handler
        step_queue = queue.Queue()

        def step_callback(step_type: str, content: str, metadata: dict):
            """Called by agent for each step - puts into queue for async sending."""
            step_queue.put((step_type, content, metadata))

        def invoke_agent():
            """Run agent in thread pool to avoid blocking event loop."""
            with self._chat_lock:
                try:
                    agent = self._get_agent()
                    # Set up streaming callback
                    agent.set_step_callback(step_callback)
                    result = agent.invoke(
                        user_message,
                        thread_id=self._agent_thread_id,
                        verbose=True,
                    )
                    # Signal completion
                    step_queue.put(None)
                    return result
                except Exception as e:
                    logger.exception(f"Agent error: {e}")
                    step_queue.put(None)
                    return {"error": str(e)}

        # Start agent in background
        agent_future = loop.run_in_executor(self._executor, invoke_agent)

        # Stream steps as they arrive
        try:
            while True:
                # Check for cancellation
                if self._chat_cancelled.is_set():
                    logger.info("Chat cancelled by user")
                    # Wait briefly for agent thread to finish cleanup after interrupt
                    try:
                        await asyncio.wait_for(agent_future, timeout=2.0)
                    except asyncio.TimeoutError:
                        logger.warning("Agent thread did not finish within timeout after interrupt")
                    except Exception as e:
                        logger.warning(f"Agent thread cleanup error: {e}")
                    # Agent was interrupted via SDK interrupt() call
                    await websocket.send(json.dumps({
                        "type": "agent_response",
                        "content": "",
                        "done": True,
                    }))
                    return

                # Check for steps with timeout to allow checking if agent is done
                try:
                    step = await asyncio.wait_for(
                        loop.run_in_executor(None, lambda: step_queue.get(timeout=0.1)),
                        timeout=0.5
                    )
                except (asyncio.TimeoutError, queue.Empty):
                    # Check if agent is done
                    if agent_future.done():
                        # Drain remaining steps
                        while not step_queue.empty():
                            step = step_queue.get_nowait()
                            if step:
                                await self._send_agent_step(websocket, *step)
                        break
                    continue

                if step is None:
                    # Agent finished
                    break

                step_type, content, metadata = step
                await self._send_agent_step(websocket, step_type, content, metadata)

            # Get final result
            result = await agent_future

            if "error" in result:
                await websocket.send(json.dumps({
                    "type": "agent_error",
                    "error": result["error"],
                }))
                return

            # Send completion signal
            await websocket.send(json.dumps({
                "type": "agent_response",
                "content": "",
                "done": True,
            }))

            logger.info("Agent completed")

        except Exception as e:
            logger.error(f"Chat handler error: {e}")
            await websocket.send(json.dumps({
                "type": "agent_error",
                "error": str(e),
            }))

    async def _send_agent_step(
        self,
        websocket: websockets.WebSocketServerProtocol,
        step_type: str,
        content: str,
        metadata: dict,
    ) -> None:
        """Send an agent step to the iOS client."""
        await websocket.send(json.dumps({
            "type": "agent_step",
            "step_type": step_type,  # "thinking", "tool_call", "tool_result", "response"
            "content": content,
            "metadata": metadata,
        }))
        logger.info(f"Agent step: {step_type} - {content[:100]}..." if len(content) > 100 else f"Agent step: {step_type} - {content}")

    def _query_physical_screen_size(self) -> tuple[int, int]:
        """Query physical screen size from ADB (not scrcpy video dimensions)."""
        try:
            output = self.device.shell("wm size")
            # Output: "Physical size: 1080x2340" or "Override size: ..."
            match = re.search(r'(\d+)x(\d+)', output)
            if match:
                return int(match.group(1)), int(match.group(2))
        except Exception as e:
            logger.warning(f"Failed to query screen size: {e}")
        return 1080, 2340  # Fallback

    async def _send_touch(
        self,
        client: websockets.WebSocketServerProtocol,
        action: str,
        x: float,
        y: float,
    ) -> None:
        """Send touch event to device via ADB. Detects swipes vs taps.

        Args:
            client: The WebSocket client sending this touch event
            action: Touch action - "down", "move", or "up"
            x: Normalized X coordinate (0.0 to 1.0)
            y: Normalized Y coordinate (0.0 to 1.0)
        """
        if not self.device_info:
            return

        # Validate coordinates
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            logger.warning(f"Invalid touch coordinates: ({x}, {y}) - must be in range [0.0, 1.0]")
            return

        # Get physical screen size (cache it)
        if self._physical_width == 0:
            self._physical_width, self._physical_height = self._query_physical_screen_size()
            logger.info(f"Physical screen size: {self._physical_width}x{self._physical_height}")

        # Get or create touch state for this client
        if client not in self._touch_states:
            self._touch_states[client] = TouchState()
        touch_state = self._touch_states[client]

        # Clean up stale touch state (timeout protection)
        # This handles edge case where user starts touch, waits >5s, then releases
        if touch_state.start_pos is not None:
            elapsed = time() - touch_state.start_time
            if elapsed > self.TOUCH_TIMEOUT:
                logger.warning(
                    f"Touch cancelled: held for {elapsed:.1f}s (>{self.TOUCH_TIMEOUT}s timeout). "
                    "Start a new touch to continue."
                )
                touch_state.start_pos = None
                # Don't process this action since state was stale
                return

        if action == "down":
            # Record touch start position and time
            touch_state.start_pos = (x, y)
            touch_state.start_time = time()
            return

        if action == "move":
            # Update position for potential swipe, but don't execute yet
            # This allows for smoother swipe detection
            return

        if action == "up":
            if touch_state.start_pos is None:
                return

            start_x, start_y = touch_state.start_pos
            start_time = touch_state.start_time
            touch_state.start_pos = None

            # Calculate distance and duration
            dx = x - start_x
            dy = y - start_y
            distance = (dx * dx + dy * dy) ** 0.5
            duration = time() - start_time
            velocity = distance / duration if duration > 0 else 0

            # Convert to absolute coordinates
            abs_start_x = int(start_x * self._physical_width)
            abs_start_y = int(start_y * self._physical_height)
            abs_end_x = int(x * self._physical_width)
            abs_end_y = int(y * self._physical_height)

            # Swipe detection using velocity AND distance:
            # - Fast movement (high velocity) = swipe even if short distance
            # - Slow movement over threshold distance AND held long = intentional drag/swipe
            # - Short distance + short duration = tap (even with some drift)
            is_swipe = (
                velocity > self.SWIPE_VELOCITY_THRESHOLD or
                (distance > self.SWIPE_DISTANCE_THRESHOLD and duration > self.SWIPE_MAX_TAP_DURATION)
            )

            try:
                if is_swipe:
                    # Swipe gesture - use 200ms duration for smooth swipe
                    self.device.shell(
                        f"input swipe {abs_start_x} {abs_start_y} {abs_end_x} {abs_end_y} 200",
                        timeout=2
                    )
                    logger.info(f"Sent swipe (v={velocity:.2f}, d={distance:.3f}, t={duration:.2f}s)")
                else:
                    # Tap gesture
                    self.device.shell(f"input tap {abs_end_x} {abs_end_y}", timeout=1)
                    logger.info(f"Sent tap at ({abs_end_x}, {abs_end_y}) (d={distance:.3f}, t={duration:.2f}s)")
            except Exception as e:
                logger.warning(f"Failed to send touch: {e}")

    async def _send_key(self, keycode: int, action: str) -> None:
        """Send key event to device via ADB (more reliable than scrcpy control)."""
        try:
            # Use ADB input command - more reliable
            self.device.shell(f"input keyevent {keycode}", timeout=1)
            logger.info(f"Sent key {keycode} via ADB")
        except Exception as e:
            logger.warning(f"Failed to send key: {e}")

    def _get_nal_types(self, data: bytes) -> set:
        """Get all NAL unit types in data."""
        # NAL unit types in H.264:
        # 5 = IDR (keyframe)
        # 7 = SPS (Sequence Parameter Set)
        # 8 = PPS (Picture Parameter Set)
        nal_types = set()
        if len(data) < 5:
            return nal_types

        # Look for NAL start codes and check unit types
        i = 0
        while i < len(data) - 4:
            # Check for 3-byte or 4-byte start code
            if data[i:i+3] == b'\x00\x00\x01' or data[i:i+4] == b'\x00\x00\x00\x01':
                start_len = 3 if data[i:i+3] == b'\x00\x00\x01' else 4
                if i + start_len < len(data):
                    nal_type = data[i + start_len] & 0x1F
                    nal_types.add(nal_type)
                i += start_len
            else:
                i += 1
        return nal_types

    def _get_nal_type(self, data: bytes) -> int:
        """Get NAL unit type from data (assumes single NAL with start code)."""
        if len(data) < 5:
            return -1
        # Check for 4-byte start code
        if data[0:4] == b'\x00\x00\x00\x01':
            return data[4] & 0x1F
        # Check for 3-byte start code
        if data[0:3] == b'\x00\x00\x01':
            return data[3] & 0x1F
        return -1

    def _is_sps(self, data: bytes) -> bool:
        """Check if data is SPS NAL unit (type 7)."""
        return self._get_nal_type(data) == 7

    def _is_pps(self, data: bytes) -> bool:
        """Check if data is PPS NAL unit (type 8)."""
        return self._get_nal_type(data) == 8

    def _is_keyframe(self, data: bytes) -> bool:
        """Check if data is IDR (keyframe) NAL unit (type 5)."""
        return self._get_nal_type(data) == 5

    def _extract_nal_units(self) -> list[bytes]:
        """
        Extract complete NAL units from the buffer.

        Returns list of complete NAL units (each with its start code).
        Incomplete data remains in buffer for next call.
        """
        nal_units = []
        buf = self._nal_buffer

        # Find all start code positions
        start_positions = []
        i = 0
        while i < len(buf) - 3:
            # Check for 4-byte start code: 0x00000001
            if buf[i:i+4] == b'\x00\x00\x00\x01':
                start_positions.append((i, 4))
                i += 4
            # Check for 3-byte start code: 0x000001
            elif buf[i:i+3] == b'\x00\x00\x01':
                start_positions.append((i, 3))
                i += 3
            else:
                i += 1

        if len(start_positions) < 2:
            # Not enough NAL units yet - need at least 2 start codes
            # to know where the first NAL unit ends
            return []

        # Extract complete NAL units (all but the last incomplete one)
        for idx in range(len(start_positions) - 1):
            start_pos, start_len = start_positions[idx]
            next_start_pos, _ = start_positions[idx + 1]

            # Extract NAL unit WITH its start code (iOS parser expects this)
            nal_unit = bytes(buf[start_pos:next_start_pos])
            nal_units.append(nal_unit)

        # Keep the last incomplete NAL in the buffer
        last_start_pos, _ = start_positions[-1]
        self._nal_buffer = bytearray(buf[last_start_pos:])

        return nal_units

    async def _video_stream_loop(self) -> None:
        """Read H.264 data from scrcpy and broadcast to WebSocket clients."""
        logger.info("Starting video stream loop...")

        recv_count = 0
        nal_count = 0
        while self.alive:
            try:
                # Read H.264 data (non-blocking)
                try:
                    data = self._video_socket.recv(0x10000)  # 64KB buffer
                    recv_count += 1
                except BlockingIOError:
                    await asyncio.sleep(0.001)  # 1ms sleep - lower latency
                    continue
                except OSError as e:
                    if e.errno == 35:  # EAGAIN on macOS
                        await asyncio.sleep(0.001)
                        continue
                    raise

                if not data:
                    logger.warning("Video stream disconnected")
                    break

                # Accumulate data in NAL buffer
                self._nal_buffer.extend(data)

                # Extract complete NAL units
                nal_units = self._extract_nal_units()

                if not nal_units:
                    continue

                # Process each complete NAL unit
                for nal_unit in nal_units:
                    nal_count += 1
                    if nal_count <= 10 or nal_count % 100 == 0:
                        nal_type = (nal_unit[4] & 0x1F) if len(nal_unit) > 4 else -1
                        logger.info(f"NAL #{nal_count}: {len(nal_unit)} bytes, type={nal_type}")

                    # Store SPS for new clients
                    if self._is_sps(nal_unit):
                        self._sps = nal_unit
                        logger.info(f"Cached SPS: {len(nal_unit)} bytes")

                    # Store PPS for new clients
                    if self._is_pps(nal_unit):
                        self._pps = nal_unit
                        logger.info(f"Cached PPS: {len(nal_unit)} bytes")

                    # Cache keyframes for new clients
                    if self._is_keyframe(nal_unit):
                        self._last_keyframe = nal_unit

                    # Broadcast single NAL unit to all clients
                    if self.clients:
                        results = await asyncio.gather(
                            *[client.send(nal_unit) for client in self.clients],
                            return_exceptions=True
                        )
                        for result in results:
                            if isinstance(result, Exception):
                                logger.warning(f"Failed to send to client: {result}")

            except Exception as e:
                logger.error(f"Error in video stream: {e}")
                await asyncio.sleep(0.1)

        logger.info("Video stream loop ended")

    async def start(self) -> None:
        """Start the bridge - deploy server, connect, and start streaming."""
        try:
            self._deploy_server()
            self._connect_to_server()
            self.alive = True

            # Start WebSocket server
            logger.info(f"Starting WebSocket server on ws://{self.ws_host}:{self.ws_port}")

            async with serve(
                self._handle_client,
                self.ws_host,
                self.ws_port,
                compression=None,  # Disable compression for lower latency
                max_size=2**20,    # 1MB max message size
            ):
                # Run video streaming in parallel
                await self._video_stream_loop()

        except Exception as e:
            logger.error(f"Bridge error: {e}")
            raise
        finally:
            self.stop()

    def stop(self) -> None:
        """Stop the bridge and cleanup."""
        logger.info("Stopping bridge...")
        self.alive = False

        # Shutdown executor
        if self._executor:
            self._executor.shutdown(wait=False)

        if self._server_stream:
            try:
                self._server_stream.close()
            except:
                pass

        if self._video_socket:
            try:
                self._video_socket.close()
            except:
                pass

        if self._control_socket:
            try:
                self._control_socket.close()
            except:
                pass


async def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Scrcpy WebSocket Bridge")
    parser.add_argument("device", nargs="?", help="ADB device serial (optional)")
    parser.add_argument("--width", type=int, default=1080, help="Max video width")
    parser.add_argument("--bitrate", type=int, default=4_000_000, help="Video bitrate")
    parser.add_argument("--fps", type=int, default=60, help="Max FPS")
    parser.add_argument("--host", default="0.0.0.0", help="WebSocket host")
    parser.add_argument("--port", type=int, default=8765, help="WebSocket port")
    args = parser.parse_args()

    # Get device
    device = None
    if args.device:
        device = adb.device(serial=args.device)

    bridge = ScrcpyBridge(
        device=device,
        max_width=args.width,
        bitrate=args.bitrate,
        max_fps=args.fps,
        ws_host=args.host,
        ws_port=args.port,
    )

    try:
        await bridge.start()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        bridge.stop()


if __name__ == "__main__":
    asyncio.run(main())
