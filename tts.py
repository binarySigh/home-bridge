import asyncio
import logging
from typing import AsyncIterator

import httpx

from audio_handler import AudioHandler

logger = logging.getLogger(__name__)


class TTSEngine:
    def __init__(self, *,
                 api_url: str,
                 api_key: str = "",
                 model: str = "kokoro",
                 voice: str = "af_heart",
                 response_format: str = "wav",
                 timeout: float = 30.0,
                 audio_handler: AudioHandler = None):
        self.api_url = api_url.rstrip("/")
        self.model = model
        self.voice = voice
        self.response_format = response_format
        self.audio = audio_handler or AudioHandler()
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(timeout), headers=headers)

    async def close(self):
        await self._http.aclose()

    async def synthesize(self, text: str) -> bytes:
        if not text or not text.strip():
            return b""

        try:
            payload = {
                "model": self.model,
                "input": text,
                "voice": self.voice,
                "response_format": self.response_format,
            }

            response = await self._http.post(self.api_url, json=payload)

            if response.status_code != 200:
                logger.error("TTS API 返回错误: status=%d body=%s",
                           response.status_code, response.text[:500])
                return b""

            audio_data = response.content
            if not audio_data:
                logger.warning("TTS 返回空音频")
                return b""

            pcm_data = self._extract_pcm(audio_data)
            logger.info("TTS 合成完成: text_len=%d pcm_len=%d duration=%.1fs",
                        len(text), len(pcm_data),
                        len(pcm_data) / self.audio.bytes_per_second)
            return pcm_data

        except httpx.TimeoutException:
            logger.error("TTS API 请求超时")
            return b""
        except httpx.ConnectError:
            logger.error("无法连接 TTS 服务: %s", self.api_url)
            return b""
        except Exception as e:
            logger.error("TTS 合成失败: %s", e)
            return b""

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        pcm_data = await self.synthesize(text)
        if not pcm_data:
            return

        frame_size = self.audio.frame_bytes
        for i in range(0, len(pcm_data), frame_size):
            frame = pcm_data[i:i + frame_size]
            if len(frame) == frame_size:
                yield frame
            elif len(frame) > 0:
                yield frame + b"\x00" * (frame_size - len(frame))

    def _extract_pcm(self, data: bytes) -> bytes:
        if len(data) > 44 and data[:4] == b"RIFF" and data[8:12] == b"WAVE":
            offset = 12
            while offset < len(data) - 8:
                chunk_id = data[offset:offset + 4]
                chunk_size = int.from_bytes(data[offset + 4:offset + 8], "little")
                if chunk_id == b"data":
                    return data[offset + 8:offset + 8 + chunk_size]
                offset += 8 + chunk_size
            return data[44:]
        return data
