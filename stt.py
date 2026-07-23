import json
import logging
from typing import Optional

import httpx

from audio_handler import AudioHandler

logger = logging.getLogger(__name__)


class STTEngine:
    def __init__(self, *,
                 api_url: str,
                 api_key: str = "",
                 model: str = "whisper-1",
                 language: str = "zh",
                 timeout: float = 30.0,
                 audio_handler: Optional[AudioHandler] = None):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.language = language
        self.audio = audio_handler or AudioHandler()
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(timeout), headers=headers)

    async def close(self):
        await self._http.aclose()

    async def recognize(self, pcm_data: bytes) -> Optional[str]:
        if not pcm_data or len(pcm_data) < self.audio.frame_bytes * 10:
            logger.warning("音频数据太短，跳过识别: %d bytes", len(pcm_data))
            return None

        wav_data = self.audio.wrap_wav(pcm_data)

        try:
            response = await self._http.post(
                self.api_url,
                files={"file": ("audio.wav", wav_data, "audio/wav")},
                data={
                    "model": self.model,
                    "language": self.language,
                    "response_format": "json",
                },
            )

            if response.status_code != 200:
                logger.error("STT API 返回错误: status=%d body=%s",
                           response.status_code, response.text[:500])
                return None

            result = response.json()
            text = result.get("text", "").strip()
            if text:
                logger.info("STT 识别结果: %s", text)
            else:
                logger.info("STT 未识别到内容")
            return text or None

        except httpx.TimeoutException:
            logger.error("STT API 请求超时")
            return None
        except httpx.ConnectError:
            logger.error("无法连接 STT 服务: %s", self.api_url)
            return None
        except Exception as e:
            logger.error("STT 识别异常: %s", e)
            return None
