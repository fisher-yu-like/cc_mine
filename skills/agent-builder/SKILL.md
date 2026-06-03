---
name: "agent-builder"
description: "设计与实现具备明确边界控制、结构化、有状态的多智能体（Multi-Agent）或单智能体系统。"
---
# 智能体构建者技能 (Agent Builder Skill)

本技能为构建高可靠性的 LLM Agent 提供设计模式与模版，核心专注于确定性的状态转移、清晰 brains 系统边界以及精准的工具路由控制。

## 核心设计原则
1. **单一职责原则 (Separation of Concerns)**：每个 Agent 必须拥有清晰且聚焦的业务边界，绝不膨胀职责。
2. **显式状态管理 (State Management)**：Agent 的当前状态（如：进行中、已挂起、已完成）必须通过共享的数据结构或运行时文件进行显式追踪，切勿完全依赖 LLM 的上下文记忆。
3. **结构化交接 (Structured Handoff)**：智能体之间的任务路由与流转，必须由明确的条件网关或编排层（Orchestration Layer）来触发。

## 代码实现模式
在创建新的 Agent 系统时，请遵循以下基础结构模式：

```python
class AgentSession:
    def __init__(self, task_description: str):
        self.state = "pending"
        self.context = {"task": task_description, "history": []}
        
    def step(self, client, model) -> str:
        # 在此处实现 Agent 的 Thought-Action-Observation 思考循环
        pass