import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class BridgeConfig:
    ws_host: str = "0.0.0.0"
    ws_port: int = 8899
    server_version: str = "1.0.0"

    sample_rate: int = 16000
    channels: int = 1
    bits_per_sample: int = 16
    frame_ms: int = 10

    wake_words: list = field(default_factory=lambda: ["visar", "薇萨", "维萨"])
    wake_response_text: str = ""

    stt_api_key: str = ""
    stt_api_url: str = "http://localhost:8000/v1/audio/transcriptions"
    stt_model: str = "whisper-1"
    stt_language: str = "zh"
    stt_timeout: float = 30.0

    tts_api_url: str = "http://localhost:8880/v1/audio/speech"
    tts_api_key: str = ""
    tts_model: str = "kokoro"
    tts_voice: str = "af_heart"
    tts_response_format: str = "wav"
    tts_timeout: float = 30.0

    hermes_api_url: str = "http://localhost:8642/v1/chat/completions"
    hermes_api_key: str = ""
    hermes_model: str = "deepseek-v4-pro"
    hermes_timeout: int = 120
    hermes_connect_timeout: float = 10.0
    hermes_max_history: int = 20

    heartbeat_interval: int = 30
    heartbeat_timeout: int = 120

    max_audio_buffer_bytes: int = 160_000

    register_timeout: float = 10.0

    log_level: str = "INFO"

    @property
    def frame_bytes(self) -> int:
        return int(self.sample_rate * (self.bits_per_sample / 8)
                   * self.channels * (self.frame_ms / 1000))


_ENV_MAP = {
    "ws_host": ("BRIDGE_WS_HOST", str),
    "ws_port": ("BRIDGE_WS_PORT", int),
    "server_version": ("BRIDGE_SERVER_VERSION", str),
    "sample_rate": ("BRIDGE_SAMPLE_RATE", int),
    "channels": ("BRIDGE_CHANNELS", int),
    "bits_per_sample": ("BRIDGE_BITS_PER_SAMPLE", int),
    "frame_ms": ("BRIDGE_FRAME_MS", int),
    "wake_words": ("BRIDGE_WAKE_WORDS", "csv"),
    "wake_response_text": ("BRIDGE_WAKE_RESPONSE_TEXT", str),
    "stt_api_url": ("BRIDGE_STT_API_URL", str),
    "stt_api_key": ("BRIDGE_STT_API_KEY", str),
    "stt_model": ("BRIDGE_STT_MODEL", str),
    "stt_language": ("BRIDGE_STT_LANGUAGE", str),
    "stt_timeout": ("BRIDGE_STT_TIMEOUT", float),
    "tts_api_url": ("BRIDGE_TTS_API_URL", str),
    "tts_api_key": ("BRIDGE_TTS_API_KEY", str),
    "tts_model": ("BRIDGE_TTS_MODEL", str),
    "tts_voice": ("BRIDGE_TTS_VOICE", str),
    "tts_response_format": ("BRIDGE_TTS_FORMAT", str),
    "tts_timeout": ("BRIDGE_TTS_TIMEOUT", float),
    "hermes_api_url": ("BRIDGE_HERMES_API_URL", str),
    "hermes_api_key": ("BRIDGE_HERMES_API_KEY", str),
    "hermes_model": ("BRIDGE_HERMES_MODEL", str),
    "hermes_timeout": ("BRIDGE_HERMES_TIMEOUT", int),
    "hermes_connect_timeout": ("BRIDGE_HERMES_CONNECT_TIMEOUT", float),
    "hermes_max_history": ("BRIDGE_HERMES_MAX_HISTORY", int),
    "heartbeat_interval": ("BRIDGE_HEARTBEAT_INTERVAL", int),
    "heartbeat_timeout": ("BRIDGE_HEARTBEAT_TIMEOUT", int),
    "max_audio_buffer_bytes": ("BRIDGE_MAX_AUDIO_BUFFER", int),
    "register_timeout": ("BRIDGE_REGISTER_TIMEOUT", float),
    "log_level": ("BRIDGE_LOG_LEVEL", str),
}


def load_config(config_file: Optional[str] = None) -> BridgeConfig:
    defaults = BridgeConfig()
    kwargs = {f.name: getattr(defaults, f.name)
              for f in defaults.__dataclass_fields__.values()}

    if config_file is None:
        config_file = os.getenv("BRIDGE_CONFIG_FILE", "")
    if config_file:
        file_path = Path(config_file)
        if file_path.exists():
            try:
                with open(file_path) as f:
                    file_data = json.load(f)
                for key, value in file_data.items():
                    if key in kwargs:
                        kwargs[key] = value
            except (json.JSONDecodeError, IOError) as e:
                import logging
                logging.getLogger("config").warning("配置文件加载失败: %s", e)

    for field_name, (env_name, converter) in _ENV_MAP.items():
        raw = os.getenv(env_name)
        if raw is not None:
            if converter == "csv":
                parsed = [w.strip() for w in raw.split(",") if w.strip()]
                if parsed:
                    kwargs[field_name] = parsed
            else:
                try:
                    kwargs[field_name] = converter(raw)
                except (ValueError, TypeError):
                    pass

    return BridgeConfig(**kwargs)
