# VISAR ESP32 桥接服务 (Home Bridge)

VISAR 是运行在 Hermes 上的 AI 助手。本项目将 VISAR 通过 WebSocket 桥接到 ESP32 硬件终端，让 ESP32 设备能像智能音箱一样与 VISAR 对话。

## 整体架构

```
┌──────────────┐          ┌───────────────────────────┐          ┌──────────────┐
│  ESP32-S3 #1 │──┐       │                           │       ┌──│   Whisper     │
│  (麦克风+喇叭)  │  │  WebSocket  │    home-bridge             │  HTTP  │   (STT)       │
└──────────────┘  │  PCM 音频  │    (Python)              │──┤   └──────────────┘
                  ├──────────▶│                           │  │
┌──────────────┐  │           │  WebSocket Server :8899  │  │   ┌──────────────┐
│  ESP32-S3 #2 │──┘           │  + 系统监控  GET /status  │──┼──▶│   Kokoro     │
│  (麦克风+喇叭)  │             │                           │  │   │   (TTS)       │
└──────────────┘             │                           │  │   └──────────────┘
                              │                           │  │
┌──────────────┐             │                           │  │   ┌──────────────┐
│  ESP32-S3 #N │────────────▶│                           │──┘   │   Hermes     │
│  (麦克风+喇叭)  │             └───────────────────────────┘      │   (LLM)      │
└──────────────┘                                          └──────────────┘
                              ▲
                              │ HTTP (只读 GET)
                              │
                    ┌─────────┴──────────┐
                    │  NAS 宿主机         │
                    │  /proc /sys /dev   │
                    │  (只读挂载 :ro)     │
                    └────────────────────┘
```

**一条完整的对话流程：**

1. ESP32 通过 WebSocket 连接桥接服务，发送 `register` 注册设备
2. 用户对着 ESP32 说话，ESP32 采集 PCM 音频帧，通过 WebSocket 二进制帧实时发送
3. 用户说完后，ESP32 发送 `audio_end` 消息
4. 桥接服务将 PCM 裸音频打包为 WAV → 调用 Whisper STT 服务 → 得到文本
5. 文本匹配唤醒词（如 "visar"、"维萨"），剥离唤醒词得到指令
6. 指令发送给 Hermes API → 得到 AI 回复文本
7. 回复文本调用 Kokoro TTS 服务 → 得到 PCM 音频
8. PCM 音频帧通过 WebSocket 流式回传给 ESP32，ESP32 播放

**关键设计决策：**

| 决策 | 选择 | 理由 |
|------|------|------|
| 唤醒词 | 服务端文本匹配 | ESP32 固件零改动，灵活可配 |
| 音频格式 | PCM 裸数据 (16kHz/16bit/mono) | 零编解码开销，10ms 帧长低延迟 |
| 外部接口 | OpenAI 兼容 API | STT/TTS/LLM 可自由替换，降低耦合 |
| 多设备 | 独立会话 + 独立状态机 | 每设备独立对话历史，互不干扰 |
| 打断 | asyncio Task 取消 | 轻量级，无需额外消息队列 |

## 项目结构

```
home-bridge/
├── main.py              # 入口：参数解析、配置加载、服务启动
├── server.py            # WebSocket 核心：连接管理、音频处理流水线
├── config.py            # 配置管理：dataclass + 环境变量 + JSON 文件
├── session_manager.py   # 多设备会话管理、状态机、唤醒词匹配
├── audio_handler.py     # PCM 音频工具：帧分割、WAV 包装、时长计算
├── stt.py               # STT 客户端：OpenAI 兼容接口（Whisper）
├── tts.py               # TTS 客户端：OpenAI 兼容接口（Kokoro）
├── hermes_client.py     # Hermes 客户端：对话历史管理、流式/非流式
├── requirements.txt     # Python 依赖
├── config.example.json  # 配置文件示例
├── monitor.py           # 系统监控模块：读取宿主机 /proc，暴露 GET /status
└── Dockerfile           # Docker 镜像构建
```

## 模块职责

### server.py — 核心编排器

管理所有 ESP32 的 WebSocket 连接，每个连接独立处理。核心方法：

- `_handle_connection()` — 接受连接，等待注册，循环接收消息
- `_handle_text_message()` — 处理 JSON 控制消息（audio_start/audio_end/interrupt/ping）
- `_handle_binary_message()` — 接收 PCM 音频帧，追加到设备音频缓冲
- `_process_audio()` — 核心流水线：STT → 唤醒词 → Hermes → TTS → 回传
- `_heartbeat_loop()` / `_cleanup_loop()` — 后台心跳检测和超时清理

### session_manager.py — 设备状态机

每个 ESP32 设备拥有独立的状态机：

```
IDLE ──(audio_start)──▶ LISTENING ──(audio_end)──▶ PROCESSING
  ▲                                                    │
  │                                                    ▼
  │         ┌──────────── SPEAKING ◀──────────────────┘
  │         │                 │
  └──(interrupt 打断)─────────┘
```

- `DeviceSession` — 设备会话数据类，包含 websocket、状态、音频缓冲、当前任务
- `SessionManager` — 线程安全的设备注册/注销/状态管理
- `match_wake_word()` — 前缀/结尾匹配唤醒词，支持多唤醒词

### stt.py / tts.py — 语音服务客户端

- `STTEngine` 调用 Whisper 的 OpenAI 兼容 `/v1/audio/transcriptions` 接口
- `TTSEngine` 调用 Kokoro 的 OpenAI 兼容 `/v1/audio/speech` 接口
- 两者都通过 `AudioHandler` 处理 PCM ↔ WAV 转换
- 独立设计，可替换为火山引擎 ASR、Edge TTS 等服务

### hermes_client.py — AI 对话客户端

- 调用 Hermes API Server 的 `/v1/chat/completions` 接口
- 每个设备独立维护对话历史（最多 20 轮）
- 支持非流式 (`chat()`) 和流式 (`chat_stream()`) 两种模式

### audio_handler.py — PCM 工具

- 仅处理裸 PCM 数据，不做编解码
- 帧大小：320 bytes = 10ms @ 16kHz/16bit/mono
- 提供 WAV 包装/解包、帧分割、时长计算等功能

### config.py — 配置管理

三级优先级：**环境变量 > JSON 配置文件 > 内置默认值**

所有配置项通过 `BridgeConfig` dataclass 统一管理，支持 30+ 个环境变量（`BRIDGE_*` 前缀）覆盖。

### monitor.py — 系统监控（独立功能模块）

只读挂载宿主机 `/proc:ro`、`/sys:ro`，通过 HTTP GET `/status` 接口返回 NAS 系统状态数据。**纯只读，无任何写入能力。**

**安全设计：**

| 措施 | 说明 |
|:----|:------|
| 只读挂载 | 挂载宿主机目录时使用 `:ro`，容器无法写入 |
| 非 root 运行 | Docker 容器以普通用户 `1000:1000` 运行 |
| 只读接口 | 仅暴露 GET 接口，无 POST/PUT/DELETE |
| 代码可控 | 模块代码不足百行，可逐行审计 |

**暴露的接口：**

```
GET /status → {
  "cpu":    {"usage": 12.5, "temp": 52.3, "freq": 2400},
  "memory": {"total": 17179869184, "used": 8804000000},
  "disk": [
    {"name": "sda", "total": 4000752599040, "used": 2254857830400, "temp": 41},
    {"name": "sdb", "total": 4000752599040, "used": 1610612736000, "temp": 39}
  ],
  "network": {"rx": 1250000, "tx": 500000},
  "uptime": 86400
}
```

**部署方式（Docker Compose 添加 volume 挂载）：**

```yaml
services:
  bridge:
    volumes:
      - /proc:/host/proc:ro
      - /sys:/host/sys:ro
      - ./config.json:/app/config.json:ro
    user: "1000:1000"          # 非 root 运行
```

**使用方式：**

VISAR 或其他客户端直接通过 HTTP 获取系统状态：

```bash
curl http://nas-ip:8899/status
```

**数据来源说明：**

| 监控项 | 数据来源 | 命令/文件 |
|:-------|:---------|:----------|
| CPU 使用率 | `/host/proc/stat` | 计算 idle 差值 |
| CPU 温度 | `/host/sys/class/thermal/thermal_zone0/temp` | 读取整数除以 1000 |
| 内存 | `/host/proc/meminfo` | MemTotal / MemAvailable |
| 磁盘 | `/host/proc/diskstats` + `df` | IO 统计 + 容量 |
| 磁盘温度 | `smartctl`（需安装） | 可选，需额外挂载 |
| 网络 | `/host/proc/net/dev` | 各网卡收发字节数 |
| 运行时间 | `/host/proc/uptime` | 秒数 |

## 快速开始

### 前置依赖

- Python 3.10+
- 三个外部服务（可以是本地或远程）：
  - Whisper STT 服务（OpenAI 兼容接口）
  - Kokoro TTS 服务（OpenAI 兼容接口）
  - Hermes API Server

### 安装

```bash
cd /opt/data/workspace/home-bridge
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 配置

三种方式任选其一：

**方式一：环境变量（开发调试推荐）**

```bash
export BRIDGE_HERMES_API_KEY="your-hermes-key"
export BRIDGE_STT_API_KEY="your-stt-key"
export BRIDGE_LOG_LEVEL="DEBUG"
```

**方式二：JSON 配置文件**

```bash
cp config.example.json config.json
# 编辑 config.json 填写实际 API 地址和密钥
```

**方式三：混合使用**

配置文件放通用配置，敏感信息用环境变量覆盖。

### 启动

```bash
python main.py --config config.json
```

或纯环境变量模式：

```bash
python main.py
```

## Docker Compose 部署

完整的 `docker-compose.yml` 包含：桥接服务 + STT + TTS + Hermes API Server。

### 1. 创建 Dockerfile

在项目根目录创建 `Dockerfile`：

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py .

EXPOSE 8899

CMD ["python", "main.py"]
```

### 2. 创建 docker-compose.yml

```yaml
version: "3.9"

services:
  # ====== Hermes API Server ======
  hermes:
    image: ghcr.io/nousresearch/hermes:latest
    container_name: hermes-server
    restart: unless-stopped
    ports:
      - "8642:8642"
    volumes:
      - ./hermes-data:/opt/data
    environment:
      - HERMES_API_KEY=${HERMES_API_KEY:-}
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8642/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  # ====== Whisper STT 服务 ======
  whisper:
    image: onerahmet/openai-whisper-asr-webservice:latest
    container_name: whisper-stt
    restart: unless-stopped
    ports:
      - "8000:9000"
    environment:
      - ASR_MODEL=base
      - ASR_ENGINE=openai_whisper
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/docs"]
      interval: 30s
      timeout: 10s
      retries: 3

  # ====== Kokoro TTS 服务 ======
  kokoro:
    image: ghcr.io/remsky/kokoro-fastapi:latest
    container_name: kokoro-tts
    restart: unless-stopped
    ports:
      - "8880:8880"
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8880/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  # ====== 桥接服务 ======
  bridge:
    build: .
    container_name: home-bridge
    restart: unless-stopped
    ports:
      - "8899:8899"
    volumes:
      - ./config.json:/app/config.json:ro
    environment:
      - BRIDGE_CONFIG_FILE=/app/config.json
      - BRIDGE_HERMES_API_KEY=${HERMES_API_KEY:-}
      - BRIDGE_STT_API_KEY=${STT_API_KEY:-}
      - BRIDGE_TTS_API_KEY=${TTS_API_KEY:-}
    depends_on:
      hermes:
        condition: service_healthy
      whisper:
        condition: service_healthy
      kokoro:
        condition: service_healthy
    networks:
      - bridge-net

networks:
  bridge-net:
    driver: bridge
```

### 3. 准备 config.json

```json
{
  "ws_host": "0.0.0.0",
  "ws_port": 8899,
  "wake_words": ["visar", "薇萨", "维萨"],
  "wake_response_text": "我在",

  "stt_api_url": "http://whisper:9000/v1/audio/transcriptions",
  "stt_api_key": "",
  "stt_model": "whisper-1",
  "stt_language": "zh",

  "tts_api_url": "http://kokoro:8880/v1/audio/speech",
  "tts_api_key": "",
  "tts_model": "kokoro",
  "tts_voice": "af_heart",

  "hermes_api_url": "http://hermes:8642/v1/chat/completions",
  "hermes_api_key": "",
  "hermes_model": "deepseek-v4-pro",

  "heartbeat_interval": 30,
  "heartbeat_timeout": 120,
  "log_level": "INFO"
}
```

> **注意：** Docker Compose 内部通过服务名互访，所以 API URL 中用 `whisper`、`kokoro`、`hermes` 而不是 `localhost`。

### 4. 启动

```bash
# 创建 .env 文件存放密钥（可选）
echo "HERMES_API_KEY=your-key" > .env

# 启动所有服务
docker compose up -d

# 查看日志
docker compose logs -f bridge

# 停止
docker compose down
```

### 5. 无 GPU 的轻量部署

如果没有 GPU 或只需要轻量方案，可以用 CPU 版本的镜像：

```yaml
  whisper:
    image: onerahmet/openai-whisper-asr-webservice:latest
    environment:
      - ASR_MODEL=tiny        # CPU 用 tiny/base 模型
      - ASR_ENGINE=openai_whisper
    # 去掉 deploy.resources 部分即可

  kokoro:
    image: ghcr.io/remsky/kokoro-fastapi:cpu
    # CPU 专用镜像
```

## WebSocket 协议

ESP32 与桥接服务之间通过 WebSocket 通信，协议如下：

### 控制消息（JSON 文本帧）

| 类型 | 方向 | 说明 |
|------|------|------|
| `register` | ESP32 → 服务 | 注册设备，携带 `device_id` |
| `register_ack` | 服务 → ESP32 | 注册成功确认 |
| `audio_start` | ESP32 → 服务 | 开始说话，进入 LISTENING 状态 |
| `audio_end` | ESP32 → 服务 | 说话结束，触发语音处理 |
| `interrupt` | ESP32 → 服务 | 打断当前播放/处理 |
| `tts_start` | 服务 → ESP32 | 即将发送 TTS 音频 |
| `tts_end` | 服务 → ESP32 | TTS 音频发送完毕 |
| `ping` / `pong` | 双向 | 心跳保活 |
| `kicked` | 服务 → ESP32 | 被踢下线（同 device_id 新连接） |
| `error` | 服务 → ESP32 | 错误信息 |

### 音频消息（二进制帧）

- **上传（ESP32 → 服务）：** 10ms PCM 帧，320 bytes/帧，16kHz 16bit mono
- **下发（服务 → ESP32）：** 同上格式，流式逐帧发送

## 配置参考

完整的环境变量列表：

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `BRIDGE_WS_PORT` | 8899 | WebSocket 监听端口 |
| `BRIDGE_WAKE_WORDS` | visar,薇萨,维萨 | 逗号分隔的唤醒词列表 |
| `BRIDGE_WAKE_RESPONSE_TEXT` | "" | 仅唤醒词时的回复文本 |
| `BRIDGE_STT_API_URL` | http://localhost:8000/v1/audio/transcriptions | STT 服务地址 |
| `BRIDGE_STT_API_KEY` | "" | STT API 密钥 |
| `BRIDGE_STT_MODEL` | whisper-1 | STT 模型名 |
| `BRIDGE_STT_LANGUAGE` | zh | 识别语言 |
| `BRIDGE_TTS_API_URL` | http://localhost:8880/v1/audio/speech | TTS 服务地址 |
| `BRIDGE_TTS_API_KEY` | "" | TTS API 密钥 |
| `BRIDGE_TTS_VOICE` | af_heart | TTS 语音 |
| `BRIDGE_HERMES_API_URL` | http://localhost:8642/v1/chat/completions | Hermes API 地址 |
| `BRIDGE_HERMES_API_KEY` | "" | Hermes API 密钥 |
| `BRIDGE_HERMES_MODEL` | deepseek-v4-pro | 使用的模型 |
| `BRIDGE_HERMES_MAX_HISTORY` | 20 | 对话历史最大轮数 |
| `BRIDGE_LOG_LEVEL` | INFO | 日志级别 |

## 技术栈

- **Python 3.10+**，纯异步（asyncio）
- **websockets** — WebSocket 服务端
- **httpx** — 异步 HTTP 客户端
- **STT** — OpenAI 兼容 Whisper 服务
- **TTS** — OpenAI 兼容 Kokoro 服务
- **LLM** — Hermes API Server（OpenAI 兼容接口）
