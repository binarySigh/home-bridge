import json
import logging
from typing import AsyncIterator, Optional

import httpx

logger = logging.getLogger(__name__)


class HermesClient:
    def __init__(self, *,
                 api_url: str,
                 api_key: str = "",
                 model: str = "default",
                 timeout: int = 120,
                 connect_timeout: float = 10.0,
                 max_history: int = 20):
        self.api_url = api_url
        self.api_key = api_key
        self.model = model
        self.max_history = max_history
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=connect_timeout),
            headers=self._build_headers(),
        )
        self._conversation_history: dict[str, list[dict]] = {}

    def _build_headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def close(self):
        await self._http.aclose()

    def _get_history(self, device_id: str) -> list[dict]:
        if device_id not in self._conversation_history:
            self._conversation_history[device_id] = []
        return self._conversation_history[device_id]

    def clear_history(self, device_id: str):
        self._conversation_history.pop(device_id, None)

    async def chat(self, device_id: str, text: str) -> Optional[str]:
        if not text or not text.strip():
            return None

        history = self._get_history(device_id)
        history.append({"role": "user", "content": text})

        if len(history) > self.max_history:
            history = history[-self.max_history:]

        payload = {"model": self.model, "messages": history, "stream": False}

        try:
            response = await self._http.post(self.api_url, json=payload)
            if response.status_code != 200:
                logger.error("Hermes API 返回错误: status=%d body=%s",
                           response.status_code, response.text[:500])
                history.pop()
                return None

            result = response.json()
            reply = self._parse_response(result)

            if reply:
                history.append({"role": "assistant", "content": reply})
                self._conversation_history[device_id] = history
                logger.info("Hermes 回复: device=%s reply=%s", device_id, reply[:100])
            else:
                history.pop()

            return reply

        except httpx.TimeoutException:
            logger.error("Hermes API 请求超时")
            history.pop()
            return None
        except httpx.ConnectError:
            logger.error("无法连接 Hermes API Server: %s", self.api_url)
            history.pop()
            return None
        except Exception as e:
            logger.error("Hermes API 调用异常: %s", e)
            history.pop()
            return None

    async def chat_stream(self, device_id: str, text: str) -> AsyncIterator[str]:
        if not text or not text.strip():
            return

        history = self._get_history(device_id)
        history.append({"role": "user", "content": text})

        if len(history) > self.max_history:
            history = history[-self.max_history:]

        payload = {"model": self.model, "messages": history, "stream": True}
        full_reply = ""

        try:
            async with self._http.stream("POST", self.api_url, json=payload) as response:
                if response.status_code != 200:
                    logger.error("Hermes API 流式错误: status=%d", response.status_code)
                    history.pop()
                    return

                async for line in response.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        choices = chunk.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                full_reply += content
                                yield content
                    except json.JSONDecodeError:
                        continue

            if full_reply:
                history.append({"role": "assistant", "content": full_reply})
                self._conversation_history[device_id] = history
                logger.info("Hermes 流式回复: device=%s reply=%s", device_id, full_reply[:100])
            else:
                history.pop()

        except httpx.TimeoutException:
            logger.error("Hermes API 流式请求超时")
            history.pop()
        except httpx.ConnectError:
            logger.error("无法连接 Hermes API Server: %s", self.api_url)
            history.pop()
        except Exception as e:
            logger.error("Hermes API 流式调用异常: %s", e)
            history.pop()

    def _parse_response(self, result: dict) -> Optional[str]:
        try:
            choices = result.get("choices", [])
            if choices:
                content = choices[0].get("message", {}).get("content", "")
                if content:
                    return content.strip()
        except Exception as e:
            logger.error("解析 Hermes 响应失败: %s", e)
        return None
