---
name: "mcp-builder"
description: "构建符合模型上下文协议（Model Context Protocol）规范的 MCP 服务器，为大模型扩展本地/远程工具及资源。"
---
# MCP 服务器开发技能 (Model Context Protocol Builder)

本指南用于指导如何使用官方 Python 或 TypeScript SDK 开发符合 MCP 标准规范的扩展服务，从而无缝增强 Agent 的外接能力。

## 结构化规范要求
1. **工具配置声明**：每个注册到 MCP 服务的工具，必须具备极其精准的函数名称、清晰的功能描述（Description）以及完全符合 JSON Schema 规范的确定性输入参数定义。
2. **传输机制选择**：必须实现高健壮性的通信管道处理，支持基于命令行标准输入输出的 `stdio` 传输，或基于网络长连接的 `SSE`（Server-Sent Events）传输。

## 核心代码模式 (Python FastMCP 示例)
在 MCP 架构中定义工具时，请遵循以下标准写法：

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Specialized Tool Server")

@mcp.tool()
def calculate_metrics(data_points: list[float]) -> str:
    """计算统计数据的方差与标准差。"""
    # 此处实现具体的工具执行逻辑
    return json.dumps({"status": "success"})