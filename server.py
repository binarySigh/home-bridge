import asyncio
import json
import logging
import signal
from typing import Optional

import websockets
from websockets.asyncio.server import ServerConnection, serve

from config import BridgeConfig
from session_manager import SessionManager, DeviceState, match_wake_word
from audio_handler import AudioHandler
from stt import STTEngine
from tts import TTSEngine
from hermes_client import HermesClient

logger = logging.getLogger(__name__)


class BridgeServer:
    def __init__(self, config: BridgeConfig):
        self.config = config
        self.audio = AudioHandler(
            sample_rate=config.sample_rate,
            channels=config.channels,
            bits_per_sample=config.bits_per_sample,
            frame_ms=config.frame_ms,
        )
        self.session_manager = SessionManager(
            heartbeat_timeout=config.heartbeat_timeout,
            max_audio_buffer=config.max_audio_buffer_bytes,
        )
        self.stt = STTEngine(
            api_key=config.stt_api_key,
            api_url=config.stt_api_url,
            model=config.stt_model,
            language=config.stt_language,
            timeout=config.stt_timeout,
            audio_handler=self.audio,
        )
        self.tts = TTSEngine(
            api_url=config.tts_api_url,
            api_key=config.tts_api_key,
            model=config.tts_model,
            voice=config.tts_voice,
            response_format=config.tts_response_format,
            timeout=config.tts_timeout,
            audio_handler=self.audio,
        )
        self.hermes = HermesClient(
            api_url=config.hermes_api_url,
            api_key=config.hermes_api_key,
            model=config.hermes_model,
            timeout=config.hermes_timeout,
            connect_timeout=config.hermes_connect_timeout,
            max_history=config.hermes_max_history,
        )
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._cleanup_task: Optional[asyncio.Task] = None

    async def start(self):
        logger.info("VISAR 桥接服务启动中...")
        logger.info("WebSocket 监听: ws://%s:%d", self.config.ws_host, self.config.ws_port)
        logger.info("音频参数: %dHz %dbit %dch, 帧长=%dms(%dbytes)",
                    self.config.sample_rate, self.config.bits_per_sample,
                    self.config.channels, self.config.frame_ms, self.config.frame_bytes)
        logger.info("唤醒词: %s", self.config.wake_words)
        logger.info("Hermes API: %s (model=%s)", self.config.hermes_api_url, self.config.hermes_model)
        logger.info("STT API: %s", self.config.stt_api_url)
        logger.info("TTS API: %s (voice=%s)", self.config.tts_api_url, self.config.tts_voice)

        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

        async with serve(
            self._handle_connection,
            self.config.ws_host,
            self.config.ws_port,
        ):
            logger.info("✓ 桥接服务已就绪，等待 ESP32 连接...")
            stop = asyncio.get_event_loop().create_future()
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    asyncio.get_event_loop().add_signal_handler(
                        sig, lambda: stop.set_result(None))
                except NotImplementedError:
                    pass
            await stop

        logger.info("桥接服务正在关闭...")
        await self.shutdown()

    async def shutdown(self):
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._cleanup_task:
            self._cleanup_task.cancel()
        await self.stt.close()
        await self.hermes.close()
        logger.info("桥接服务已关闭")

    async def _handle_connection(self, websocket: ServerConnection):
        device_id = None
        remote = websocket.remote_address
        logger.info("新连接: %s", remote)

        try:
            first_msg = await asyncio.wait_for(
                websocket.recv(), timeout=self.config.register_timeout)
            device_id = await self._handle_register(websocket, first_msg)
            if not device_id:
                logger.warning("注册失败，关闭连接: %s", remote)
                return

            async for message in websocket:
                if isinstance(message, str):
                    await self._handle_text_message(device_id, message)
                elif isinstance(message, bytes):
                    await self._handle_binary_message(device_id, message)

        except asyncio.TimeoutError:
            logger.warning("连接超时未注册: %s", remote)
        except websockets.ConnectionClosed as e:
            logger.info("连接关闭: device_id=%s reason=%s", device_id, e.reason)
        except Exception as e:
            logger.error("连接异常: device_id=%s error=%s", device_id, e)
        finally:
            if device_id:
                await self.session_manager.unregister(device_id)

    async def _handle_register(self, websocket: ServerConnection, message: str) -> Optional[str]:
        try:
            data = json.loads(message)
            if data.get("type") != "register":
                await websocket.send(json.dumps({
                    "type": "error", "message": "第一条消息必须是 register"}))
                return None

            device_id = data.get("device_id", "")
            if not device_id:
                await websocket.send(json.dumps({
                    "type": "error", "message": "缺少 device_id"}))
                return None

            await self.session_manager.register(
                device_id, websocket,
                data.get("device_name", device_id),
                data.get("firmware_version", ""),
            )

            await websocket.send(json.dumps({
                "type": "register_ack",
                "device_id": device_id,
                "status": "ok",
                "server_version": self.config.server_version,
            }))
            return device_id

        except json.JSONDecodeError:
            await websocket.send(json.dumps({
                "type": "error", "message": "无效的 JSON 格式"}))
            return None

    async def _handle_text_message(self, device_id: str, message: str):
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            logger.warning("device_id=%s 收到无效 JSON", device_id)
            return

        msg_type = data.get("type", "")

        if msg_type == "ping":
            await self.session_manager.heartbeat(device_id)
            await self.session_manager.send_text(device_id, {"type": "pong"})

        elif msg_type == "audio_start":
            await self.session_manager.set_state(device_id, DeviceState.LISTENING)
            logger.info("device_id=%s 开始说话", device_id)

        elif msg_type == "audio_end":
            state = await self.session_manager.get_state(device_id)
            if state == DeviceState.LISTENING:
                await self.session_manager.set_state(device_id, DeviceState.PROCESSING)
                task = asyncio.create_task(self._process_audio(device_id))
                session = await self.session_manager.get(device_id)
                if session:
                    session.current_task = task

        elif msg_type == "interrupt":
            logger.info("device_id=%s 收到打断指令", device_id)
            await self._handle_interrupt(device_id)

        else:
            logger.debug("device_id=%s 未知消息类型: %s", device_id, msg_type)

    async def _handle_binary_message(self, device_id: str, data: bytes):
        state = await self.session_manager.get_state(device_id)
        if state == DeviceState.LISTENING:
            await self.session_manager.append_audio(device_id, data)

    async def _process_audio(self, device_id: str):
        try:
            pcm_data = await self.session_manager.get_audio(device_id)
            if not pcm_data:
                logger.info("device_id=%s 无音频数据", device_id)
                await self.session_manager.set_state(device_id, DeviceState.IDLE)
                return

            audio_duration = self.audio.duration_ms(pcm_data) / 1000
            logger.info("device_id=%s 收到音频: %d bytes (%.1fs)",
                       device_id, len(pcm_data), audio_duration)

            text = await self.stt.recognize(pcm_data)
            if not text:
                await self.session_manager.set_state(device_id, DeviceState.IDLE)
                return

            command = match_wake_word(text, self.config.wake_words)
            if command is None:
                logger.info("device_id=%s 未匹配唤醒词，忽略: %s", device_id, text)
                await self.session_manager.set_state(device_id, DeviceState.IDLE)
                return

            if not command:
                logger.info("device_id=%s 仅唤醒词，无指令", device_id)
                await self.session_manager.set_state(device_id, DeviceState.IDLE)
                if self.config.wake_response_text:
                    await self._speak_reply(device_id, self.config.wake_response_text)
                return

            logger.info("device_id=%s 唤醒词命中，指令: %s", device_id, command)

            reply = await self.hermes.chat(device_id, command)
            if not reply:
                await self.session_manager.set_state(device_id, DeviceState.IDLE)
                return

            await self.session_manager.set_state(device_id, DeviceState.SPEAKING)
            await self._speak_reply(device_id, reply)
            await self.session_manager.set_state(device_id, DeviceState.IDLE)

        except asyncio.CancelledError:
            logger.info("device_id=%s 处理被取消（打断）", device_id)
            await self.session_manager.set_state(device_id, DeviceState.IDLE)
            raise
        except Exception as e:
            logger.error("device_id=%s 处理异常: %s", device_id, e)
            await self.session_manager.set_state(device_id, DeviceState.IDLE)

    async def _speak_reply(self, device_id: str, text: str):
        try:
            await self.session_manager.send_text(device_id, {"type": "tts_start"})
            frame_count = 0
            async for frame in self.tts.synthesize_stream(text):
                state = await self.session_manager.get_state(device_id)
                if state != DeviceState.SPEAKING:
                    logger.info("device_id=%s 播放被中断，停止发送", device_id)
                    break
                await self.session_manager.send_audio(device_id, frame)
                frame_count += 1
                if frame_count % 10 == 0:
                    await asyncio.sleep(0.01)
            await self.session_manager.send_text(device_id, {"type": "tts_end"})
            logger.info("device_id=%s 播放完成: %d 帧", device_id, frame_count)

        except Exception as e:
            logger.error("device_id=%s 播放异常: %s", device_id, e)
            try:
                await self.session_manager.send_text(device_id, {"type": "tts_end"})
            except Exception:
                pass

    async def _handle_interrupt(self, device_id: str):
        session = await self.session_manager.get(device_id)
        if session:
            if session.current_task and not session.current_task.done():
                session.current_task.cancel()
                session.current_task = None
            session.audio_buffer.clear()
            await self.session_manager.set_state(device_id, DeviceState.IDLE)

    async def _heartbeat_loop(self):
        while True:
            try:
                await asyncio.sleep(self.config.heartbeat_interval)
                for device in self.session_manager.get_device_list():
                    await self.session_manager.send_text(
                        device["device_id"], {"type": "ping"})
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("心跳循环异常: %s", e)

    async def _cleanup_loop(self):
        while True:
            try:
                await asyncio.sleep(self.config.heartbeat_timeout // 2)
                stale = await self.session_manager.cleanup_stale()
                if stale:
                    logger.info("清理了 %d 个超时设备: %s", len(stale), stale)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("清理循环异常: %s", e)
