# Home Bridge — VISAR ESP32 桥接服务

连接 ESP32 硬件终端与 Hermes/VISAR AI 的 WebSocket 桥接服务。

## 功能

- 接收 ESP32 的 PCM 音频流
- 通过本地 Whisper STT 转文字
- 唤醒词检测
- 调用 Hermes API 获取 AI 回复
- 通过本地 Kokoro TTS 合成语音回传

## 快速启动

```bash
pip install -r requirements.txt
python main.py --config config.json
```
