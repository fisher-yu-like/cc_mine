# cc_mine 项目完整分析报告

> 生成日期: 2026-06-03 | 工作目录: `D:\agent\cc_mine`

---

## 一、项目概述

**cc_mine** 是一个从零手写的 Claude Code 克隆项目，底层逻辑与 Claude Code 基本一致，但在其基础上进行了改进。项目使用 OpenAI 兼容的 LLM API（当前适配 DeepSeek v4 Pro），实现了完整的多智能体协作系统。

| 属性 | 值 |
|------|-----|
| **语言** | Python 3.12 |
| **LLM 提供商** | OpenAI 兼容 API (DeepSeek) |
| **虚拟环境** | `.venv` (pip) |
| **许可证** | MIT |
| **测试框架** | pytest 9.0.3 |

---

## 二、架构总览

```
cc_mine/
├── main.py              # 主循环 + CLI 交互
├── config.py            # 全局配置 + 系统提示词
├── call_llm.py          # LLM API 封装 + token 估算
├── executor.py          # 38 个工具的路由分发
├── tool_registry.py     # 38 个内置工具的定义 (OpenAI Schema)
├── hooks.py             # 权限管道 + 日志 + 安全钩子
├── memory.py            # 4 层上下文压缩 + 记忆 CRUD
├── ErrorRecovery.py     # 指数退避 + 模型降级
├── planning.py          # 规划模式: 只读探索 → 审批 → 执行
├── workflow.py          # DAG 工作流引擎 (并行编排)
├── session.py           # 会话持久化 (JSONL)
├── log_setup.py         # 统一日志 (控制台 + 文件)
├── subagent.py          # 一次性子智能体 (同步/异步)
├── AutonomousAgent.py   # 持久化后台协作智能体
├── task.py              # 任务看板 + 依赖图
├── MessageBus.py        # 文件式智能体间消息总线
├── ProtocolState.py     # 关机/审批协议握手
├── CronScheduler.py     # 5 字段 Cron 调度器
├── mcp.py               # MCP 服务器集成 (mock)
├── bg_task.py           # 后台 bash 执行
├── worktree.py          # Git worktree 隔离
├── skill_load.py        # SKILL.md 加载器
├── skills/              # 4 个预置技能
│   ├── agent-builder/
│   ├── code-review/
│   ├── mcp-builder/
│   └── pdf-extractor/
├── tools/
│   ├── bash.py          # Shell 命令执行
│   ├── file_ops.py      # read/write/edit/glob
│   ├── git.py           # Git 操作
│   ├── todo_write.py    # 可视化待办追踪
│   └── web.py           # web_search + web_fetch
└── calc_bug_fix/        # 示例项目 (pytest 测试通过)
```

---

## 三、核心模块详细分析

### 3.1 主循环 (`main.py`)

**agent_loop** 是整个系统的核心，每个 turn 的工作流程：

```
┌─ 检查 turn 上限 (MAX_TURNS=100)
├─ 消费 Cron 队列 → 注入定时任务
├─ 注入后台通知 (bg_task + async subagent 结果)
├─ 每 3 turn 提醒更新 todo
├─ prepare_context (4 层压缩)
├─ assemble_tool_pool (合并 MCP 工具)
├─ call_llm → 获取 LLM 响应
│   ├─ finish_reason="length" → 提升 max_tokens 重试
│   ├─ finish_reason="tool_calls" → 执行工具
│   └─ 其他 → Stop 钩子，结束
├─ 规划模式拦截: 写工具在 PLANNING 状态被阻止
├─ 后台判断: 慢操作自动后台执行
├─ call_tool_handler → 执行工具
└─ 追加 tool 结果到 messages
```

**关键特性**:
- 多行输入支持 (空行提交)
- `--resume` 会话恢复
- `--workdir` / `--model` 参数覆盖
- 后台 cron 自动运行线程
- auto-save 每次 turn 后持久化

### 3.2 系统提示词 (`config.py`)

设计了三套提示词身份:

| 身份 | 用途 |
|------|------|
| **identity** (LEAD orchestrator) | 主控智能体: 禁止直接操作文件，只负责规划+委派 |
| **subagent_identity** (WORKER) | 子智能体: 执行单一任务，最多 30 turn |
| **tools** | 完整的工具箱说明，包含 task vs spawn_teammate 决策流程图 |

### 3.3 38 个内置工具 (`tool_registry.py` + `executor.py`)

| 类别 | 工具 | 数量 |
|------|------|------|
| **文件操作** | bash, read_file, write_file, edit_file, glob | 5 |
| **规划** | todo_write, enter_plan_mode, submit_plan, exit_plan_mode, update_plan_step | 5 |
| **委派** | task, spawn_teammate, send_message, check_inbox, request_shutdown, request_plan, review_plan | 7 |
| **任务管理** | create_task, list_tasks, get_task, claim_task, complete_task | 5 |
| **工作流** | begin_workflow, add_workflow_node, seal_workflow, execute_workflow, workflow_status, cancel_workflow | 6 |
| **基础设施** | compact, load_skill, schedule_cron, list_crons, cancel_cron, create_worktree, remove_worktree, keep_worktree, connect_mcp | 9 |
| **记忆** | add_memory, search_memory, delete_memory | 3 |
| **网络** | web_search, web_fetch | 2 |
| **其他** | structured_output | 1 |
| **总计** | | **38** |

### 3.4 上下文压缩 4 层防线 (`memory.py`)

```
第1层: tool_result_budget  — 大文本 (>200KB) 持久化到磁盘
第2层: snip_compact         — 消息数 >50 时腰斩中间段
第3层: micro_compact        — 冻结旧工具结果 (>10条)
第4层: AI summary           — 调用 LLM 生成摘要坍缩
```

**额外安全网**: `reactive_compact` 在遇到 `context_length_exceeded` 错误时触发。

**记忆系统**: Frontmatter .md 卡片，支持 CRUD + 全文搜索，存储在 `WORKDIR/.memory/`。

### 3.5 错误恢复 (`ErrorRecovery.py`)

```
指数退避: delay = min(500ms × 2^attempt, 32s) + 随机抖动
错误分类:
  ├─ RateLimitError (429)     → 退避重试
  ├─ InternalServerError (500) → 退避 + 连续2次触发模型降级
  ├─ APITimeoutError          → 退避重试
  ├─ APIConnectionError       → 退避重试
  ├─ BadRequestError (context) → 标记需压缩
  └─ 其他                     → 直接抛出
```

### 3.6 子智能体系统 (`subagent.py`)

**一次性子智能体 (task)**:
- 独立 agent loop，最多 30 turn
- 工具集: bash, read_file, write_file, edit_file, glob, todo_write
- **不能**再生成子智能体（防止递归爆炸）
- 返回文本摘要后销毁

**异步模式 (task + run_in_background=true)**:
- 后台线程执行
- 结果通过 `collect_subagent_results()` 收集
- 支持并行 fan-out

### 3.7 持久化后台智能体 (`AutonomousAgent.py`)

**spawn_teammate** 创建持久化智能体:
- 无限生命周期，直到收到 shutdown_request
- 可以认领/完成任务 (claim_task, complete_task)
- 支持 worktree 隔离
- 可以提交计划等待主控审批
- 通过 MessageBus 双向通信
- 空闲轮询: 每 5 秒检查收件箱，60 秒超时

### 3.8 工作流引擎 (`workflow.py`)

DAG 基并行任务编排:
- **patterns**: sequential, parallel (fan-out), phase (map-reduce), diamond
- 状态机: building → sealed → running → completed/failed/cancelled
- 后台引擎线程每秒 tick 调度
- 自动依赖解析: 所有前置节点完成后才派发
- `max_parallel` 控制并发度

### 3.9 规划模式 (`planning.py`)

状态机: IDLE → PLANNING → PLAN_READY → PLAN_APPROVED/PLAN_REJECTED

```
PLANNING 阶段:
  ✓ 允许: read_file, glob, web_search, web_fetch, todo_write 等 12 个只读工具
  ✗ 阻止: bash, write_file, edit_file, task 等写入/执行工具
  
用户审批:
  y/yes → 批准，开始执行
  n/no  → 拒绝，重新规划
  其他   → 反馈意见，继续修改
```

### 3.10 权限系统 (`hooks.py`)

4 层权限检查:
```
1. permissions.json (热重载) → allow/deny/ask
2. 内置 DENY_LIST: rm -rf /, sudo, shutdown, reboot, mkfs, dd if=
3. 破坏性命令交互确认: rm, > /etc/, chmod 777
4. 路径逃逸检查: safe_path 确保文件操作在 workspace 内
```

### 3.11 MCP 支持 (`mcp.py`)

- 可插拔外部工具服务器
- 当前 mock 实现: `docs` (search, get_version) + `deploy` (trigger, status)
- 工具命名: `mcp__{server}__{tool}`
- 动态合并到工具池

### 3.12 Git Worktree 隔离 (`worktree.py`)

- 为并行智能体创建隔离工作空间
- 命名规范校验 (A-Za-z0-9._- , 1-64 字符)
- 自动绑定 task ↔ worktree
- 删除前检查未提交变更

### 3.13 Cron 调度器 (`CronScheduler.py`)

- 标准 5 字段 cron (分 时 日 月 周)
- 支持: `*`, `*/N`, `逗号列表`, `范围`, `精确值`
- 一次性 / 循环任务
- 持久化到 `.scheduled_tasks.json`
- 分钟级精度 (每秒轮询)

### 3.14 Web 工具 (`tools/web.py`)

- **web_search**: DuckDuckGo Lite (无需 API key)，支持域名过滤
- **web_fetch**: HTTP GET + HTML→文本提取 (100KB 上限)，自动编码检测

### 3.15 会话持久化 (`session.py`)

- JSONL 格式存储完整消息历史
- JSON 元数据文件存储摘要信息
- `--resume` 启动时恢复
- auto-save 每个 turn 自动保存

---

## 四、技术亮点

1. **清晰的角色分离**: Lead agent (规划/委派) vs Worker (执行) — 防止 LLM 越权操作
2. **多层防御式上下文管理**: 4 层压缩 + 孤儿消息清理，避免 token 超限
3. **完整的错误恢复**: 指数退避 + 模型自动降级 + reactive compact
4. **热插拔权限系统**: permissions.json 热重载，无需重启
5. **DAG 工作流引擎**: 支持复杂依赖关系的并行任务编排
6. **双模式子智能体**: 一次性 (task) vs 持久化 (spawn_teammate)
7. **安全设计**: DENY_LIST + 路径逃逸检查 + 规划模式写保护
8. **文件式消息总线**: 通过原子 rename 操作实现无锁消息传递

---

## 五、当前状态

| 项目 | 状态 |
|------|------|
| 核心 agent loop | ✅ 完成 |
| 38 个内置工具 | ✅ 完成 |
| 子智能体系统 | ✅ 完成 |
| 权限管道 | ✅ 完成 |
| 上下文压缩 | ✅ 完成 |
| 错误恢复 | ✅ 完成 |
| 规划模式 | ✅ 完成 |
| 工作流引擎 | ✅ 完成 |
| Cron 调度器 | ✅ 完成 |
| Git worktree | ✅ 完成 |
| MCP 集成 (mock) | ✅ 完成 |
| Web 工具 | ✅ 完成 |
| 记忆系统 | ✅ 完成 |
| 会话持久化 | ✅ 完成 |
| 4 个预置技能 | ✅ 完成 |
| pytest 测试 (calc_bug_fix) | ✅ 3/3 通过 |

---

## 六、依赖项

| 包 | 版本 | 用途 |
|----|------|------|
| openai | - | LLM API 客户端 |
| python-dotenv | 0.9.9 | 环境变量加载 |
| pyyaml | - | SKILL.md frontmatter 解析 |
| requests | - | web_search / web_fetch |
| pytest | 9.0.3 | 测试框架 |
| anyio | 4.13.0 | 异步 I/O |
| httpx | 0.28.1 | HTTP 客户端 |
| mcp | - | MCP 协议支持 |

---

## 七、已知限制 & 改进方向

1. **MCP 仅为 Mock**: 需要实现真实的 MCP stdio/SSE 客户端连接
2. **单用户 CLI**: 缺乏 Web UI / API 接口
3. **无数据库**: 任务/会话/记忆均基于文件系统，大规模时会受限
4. **token 估算粗糙**: 中日韩字符 ~1 tok, ASCII ~0.25 tok, 不够精确
5. **缺少流式输出**: 未使用 streaming API, 用户体验可优化
6. **skills 目录为空壳**: 4 个技能目录只有 SKILL.md 描述，无实际工具
7. **calc_bug_fix**: 仅示例项目 (3 个简单测试)，非实际 bug 修复

---

## 八、文件统计

| 类别 | 文件数 | 代码行数 (估计) |
|------|--------|-----------------|
| 核心 Python 模块 | 22 | ~3,500 |
| 工具模块 (tools/) | 5 | ~400 |
| 技能描述 (skills/) | 4 | ~200 |
| 配置/环境 | 3 | ~30 |
| 总计 | 34 | ~4,130 |

---

> 本报告由 cc_mine WORKER subagent 自动生成。
