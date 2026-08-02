#!/usr/bin/env python3
"""
Home Bridge — 系统监控 MCP Server

通过 MCP 协议暴露宿主机系统监控数据给大模型（VISAR/Hermes）。
提供以下工具：
  - get_system_status: 获取完整系统状态
  - get_cpu_status: 获取 CPU 状态
  - get_memory_status: 获取内存状态
  - get_disk_status: 获取磁盘状态
  - get_network_status: 获取网络状态
  - get_uptime: 获取系统运行时间

用法：
  Hermes 配置中作为 MCP stdio server 运行：
    mcp_servers:
      home_monitor:
        command: "/path/to/.venv/bin/python3"
        args: ["/path/to/monitor_mcp.py"]
        env:
          BRIDGE_MONITOR_PROC_PATH: "/host/proc"   # Docker 中挂载路径
          BRIDGE_MONITOR_SYS_PATH: "/host/sys"     # Docker 中挂载路径
        timeout: 30
"""

import json
import os
import sys

# 确保能导入项目中的 monitor 模块
_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

from monitor import HostMonitor

# 读取环境变量配置
proc_path = os.environ.get("BRIDGE_MONITOR_PROC_PATH", "/proc")
sys_path = os.environ.get("BRIDGE_MONITOR_SYS_PATH", "/sys")

# 创建全局监控实例
monitor = HostMonitor(proc_path=proc_path, sys_path=sys_path)

# ── MCP Server ──────────────────────────────────────────────────

from mcp.server import MCPServer as FastMCP

mcp = FastMCP(
    name="home-monitor",
    instructions="NAS 系统监控服务 — 提供 Docker 宿主机的 CPU、内存、磁盘、网络、运行时间等实时状态数据。所有数据均为只读，从 /proc 和 /sys 获取。",
)


@mcp.tool(
    name="get_system_status",
    description="获取 Docker 宿主机完整系统状态，包括 CPU 使用率/温度、内存使用量、磁盘容量、网络流量、系统运行时间。一次性返回所有监控数据。",
)
def get_system_status() -> str:
    """获取完整系统状态。"""
    status = monitor.get_status()
    # 使用 json.dumps 确保输出结构化
    return json.dumps(status, ensure_ascii=False, indent=2)


@mcp.tool(
    name="get_cpu_status",
    description="获取 CPU 状态：使用率（百分比）、温度（摄氏度）、当前频率（MHz）。",
)
def get_cpu_status() -> str:
    """获取 CPU 状态。"""
    cpu = monitor.get_cpu()
    return json.dumps({
        "usage": cpu.usage,
        "temp": cpu.temp,
        "freq": cpu.freq,
    }, ensure_ascii=False)


@mcp.tool(
    name="get_memory_status",
    description="获取内存状态：总容量、已使用、可用、使用率百分比。单位均为字节。",
)
def get_memory_status() -> str:
    """获取内存状态。"""
    mem = monitor.get_memory()
    status = {
        "total": mem.total,
        "used": mem.used,
        "available": mem.available,
        "usage_pct": mem.usage_pct,
    }
    # 添加人类可读的格式化字段
    status["total_human"] = _format_bytes(mem.total)
    status["used_human"] = _format_bytes(mem.used)
    status["available_human"] = _format_bytes(mem.available)
    return json.dumps(status, ensure_ascii=False)


@mcp.tool(
    name="get_disk_status",
    description="获取磁盘状态：各磁盘/分区的总容量、已使用量。单位均为字节。",
)
def get_disk_status() -> str:
    """获取磁盘状态。"""
    disks = monitor.get_disks()
    result = []
    for d in disks:
        item = {
            "name": d.name,
            "total": d.total,
            "used": d.used,
            "temp": d.temp,
        }
        item["total_human"] = _format_bytes(d.total)
        item["used_human"] = _format_bytes(d.used)
        result.append(item)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool(
    name="get_network_status",
    description="获取网络状态：各网卡累计接收/发送字节数，以及当前接收/发送速率（bytes/s）。",
)
def get_network_status() -> str:
    """获取网络状态。"""
    net = monitor.get_network()
    return json.dumps({
        "rx_bytes": net.rx_bytes,
        "tx_bytes": net.tx_bytes,
        "rx_rate": net.rx_rate,
        "tx_rate": net.tx_rate,
        "rx_rate_human": _format_bytes(net.rx_rate) + "/s",
        "tx_rate_human": _format_bytes(net.tx_rate) + "/s",
        "rx_total_human": _format_bytes(net.rx_bytes),
        "tx_total_human": _format_bytes(net.tx_bytes),
    }, ensure_ascii=False)


@mcp.tool(
    name="get_uptime",
    description="获取系统运行时间（秒）。返回自上次启动以来的秒数。",
)
def get_uptime() -> str:
    """获取系统运行时间。"""
    uptime = monitor.get_uptime()
    days = int(uptime // 86400)
    hours = int((uptime % 86400) // 3600)
    minutes = int((uptime % 3600) // 60)
    return json.dumps({
        "uptime_seconds": uptime,
        "uptime_human": f"{days}天{hours}小时{minutes}分钟",
    }, ensure_ascii=False)


# ── 工具函数 ────────────────────────────────────────────────────

def _format_bytes(size: float) -> str:
    """将字节数格式化为人类可读的字符串。"""
    if size <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    i = 0
    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1
    return f"{size:.1f} {units[i]}"


# ── 入口 ────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=8898)