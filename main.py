import argparse
import asyncio
import logging
import sys

from config import load_config
from server import BridgeServer


def setup_logging(level: str = "INFO"):
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=fmt,
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    for lib in ("httpx", "websockets", "edge_tts"):
        logging.getLogger(lib).setLevel(logging.WARNING)


async def main():
    parser = argparse.ArgumentParser(description="VISAR ESP32 桥接服务")
    parser.add_argument("--config", "-c", help="JSON 配置文件路径")
    args = parser.parse_args()

    config = load_config(config_file=args.config)
    setup_logging(config.log_level)

    logger = logging.getLogger("main")
    logger.info("=" * 60)
    logger.info("VISAR ESP32 桥接服务 v%s", config.server_version)
    logger.info("=" * 60)

    if not config.stt_api_key:
        logger.warning("STT API Key 未配置 — 语音识别功能不可用")
    if not config.hermes_api_key:
        logger.warning("Hermes API Key 未配置 — 将尝试无认证连接")

    server = BridgeServer(config)
    try:
        await server.start()
    except KeyboardInterrupt:
        logger.info("收到中断信号，正在关闭...")
    except Exception as e:
        logger.error("服务异常退出: %s", e)
        raise


if __name__ == "__main__":
    asyncio.run(main())
