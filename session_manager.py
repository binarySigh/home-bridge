import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import websockets
from websockets.asyncio.server import ServerConnection

logger = logging.getLogger(__name__)


class DeviceState(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"


@dataclass
class DeviceSession:
    device_id: str
    websocket: ServerConnection
    device_name: str = ""
    firmware_version: str = ""
    state: DeviceState = DeviceState.IDLE
    last_seen: float = field(default_factory=time.time)
    audio_buffer: bytearray = field(default_factory=bytearray)
    current_task: Optional[asyncio.Task] = None


class SessionManager:
    def __init__(self, heartbeat_timeout: int = 120, max_audio_buffer: int = 160_000):
        self._devices: dict[str, DeviceSession] = {}
        self._lock = asyncio.Lock()
        self._heartbeat_timeout = heartbeat_timeout
        self._max_audio_buffer = max_audio_buffer

    async def register(self, device_id: str, websocket: ServerConnection,
                       device_name: str = "", firmware_version: str = "") -> DeviceSession:
        async with self._lock:
            if device_id in self._devices:
                old = self._devices[device_id]
                logger.warning("device_id=%s 已存在，踢掉旧连接", device_id)
                try:
                    await old.websocket.send(json.dumps({
                        "type": "kicked",
                        "reason": "device_replaced"
                    }))
                    await old.websocket.close()
                except Exception:
                    pass

            session = DeviceSession(
                device_id=device_id,
                websocket=websocket,
                device_name=device_name,
                firmware_version=firmware_version,
            )
            self._devices[device_id] = session
            logger.info("设备注册成功: device_id=%s name=%s", device_id, device_name)
            return session

    async def unregister(self, device_id: str) -> Optional[DeviceSession]:
        async with self._lock:
            session = self._devices.pop(device_id, None)
            if session:
                if session.current_task and not session.current_task.done():
                    session.current_task.cancel()
                logger.info("设备已注销: device_id=%s", device_id)
            return session

    async def get(self, device_id: str) -> Optional[DeviceSession]:
        async with self._lock:
            return self._devices.get(device_id)

    async def set_state(self, device_id: str, state: DeviceState) -> bool:
        async with self._lock:
            session = self._devices.get(device_id)
            if session:
                old_state = session.state
                session.state = state
                session.last_seen = time.time()
                logger.debug("device_id=%s 状态变更: %s → %s", device_id, old_state.value, state.value)
                return True
            return False

    async def get_state(self, device_id: str) -> Optional[DeviceState]:
        async with self._lock:
            session = self._devices.get(device_id)
            return session.state if session else None

    async def append_audio(self, device_id: str, data: bytes):
        async with self._lock:
            session = self._devices.get(device_id)
            if session:
                if len(session.audio_buffer) + len(data) <= self._max_audio_buffer:
                    session.audio_buffer.extend(data)
                else:
                    logger.warning("device_id=%s 音频缓冲已满，丢弃旧数据", device_id)
                    overflow = len(session.audio_buffer) + len(data) - self._max_audio_buffer
                    session.audio_buffer = session.audio_buffer[overflow:]
                    session.audio_buffer.extend(data)

    async def get_audio(self, device_id: str) -> bytes:
        async with self._lock:
            session = self._devices.get(device_id)
            if session:
                data = bytes(session.audio_buffer)
                session.audio_buffer.clear()
                return data
            return b""

    async def send_text(self, device_id: str, message: dict) -> bool:
        async with self._lock:
            session = self._devices.get(device_id)
            if session:
                try:
                    await session.websocket.send(json.dumps(message))
                    return True
                except websockets.ConnectionClosed:
                    logger.warning("发送文本帧失败，设备已断开: device_id=%s", device_id)
                    return False
            return False

    async def send_audio(self, device_id: str, pcm_data: bytes) -> bool:
        async with self._lock:
            session = self._devices.get(device_id)
            if session:
                try:
                    await session.websocket.send(pcm_data)
                    return True
                except websockets.ConnectionClosed:
                    logger.warning("发送音频帧失败，设备已断开: device_id=%s", device_id)
                    return False
            return False

    async def heartbeat(self, device_id: str):
        async with self._lock:
            session = self._devices.get(device_id)
            if session:
                session.last_seen = time.time()

    async def cleanup_stale(self) -> list[str]:
        now = time.time()
        stale = []
        async with self._lock:
            for device_id, session in list(self._devices.items()):
                if now - session.last_seen > self._heartbeat_timeout:
                    stale.append(device_id)
                    if session.current_task and not session.current_task.done():
                        session.current_task.cancel()
                    try:
                        await session.websocket.close()
                    except Exception:
                        pass
            for device_id in stale:
                self._devices.pop(device_id, None)
                logger.warning("设备心跳超时，已注销: device_id=%s", device_id)
        return stale

    def get_device_list(self) -> list[dict]:
        return [
            {
                "device_id": d.device_id,
                "name": d.device_name,
                "state": d.state.value,
                "firmware_version": d.firmware_version,
                "last_seen": d.last_seen,
            }
            for d in self._devices.values()
        ]

    @property
    def device_count(self) -> int:
        return len(self._devices)


def match_wake_word(text: str, wake_words: list[str]) -> Optional[str]:
    text_lower = text.lower().strip()
    for ww in wake_words:
        ww_lower = ww.lower()
        if text_lower.startswith(ww_lower):
            cmd = text[len(ww):].strip()
            return cmd if cmd else None
        if text_lower.endswith(ww_lower):
            cmd = text[:-len(ww)].strip()
            return cmd if cmd else None
    return None
