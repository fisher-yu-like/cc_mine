# cc_mine

> 一个从零手写的 AI 编程助手，对标 Claude Code。你说话，它干活。

**cc_mine** 就像一个驻扎在你项目里的程序员——你跟它说要做什么，它自己规划、派子代理去执行、检查结果、然后向你汇报。整个过程你只需要看着就行。

它跑在 OpenAI 兼容的模型上（DeepSeek、OpenAI 等都行），目前有 **38 个内置工具**，能读代码、写文件、跑命令、搜网页、管理任务看板、甚至自己派小弟干活。

---

## 跑分

```
综合评分: 100/100  |  59/59 全部通过
```

| 评测维度 | 权重 | 得分 | 什么意思 |
|----------|------|------|----------|
| 工具完整性 | 20% | 100% | 38 个工具全部正常调用 |
| 代码理解 | 20% | 100% | 搜索、阅读、结构分析准确 |
| 代码修改 | 20% | 100% | 编辑精准，diff 清晰，不乱改 |
| 规划与执行 | 15% | 100% | 计划→审批→执行流程完整 |
| 上下文与记忆 | 10% | 100% | 记忆增删查改、压缩不丢信息 |
| 安全与权限 | 10% | 100% | 危险命令拦截、敏感文件保护 |
| 性能 | 5% | 100% | 缓存命中、token 预估准确 |

```bash
python eval/benchmark.py              # 跑全部 59 项测试
python eval/benchmark.py --suite tool # 只跑某一类
python eval/benchmark.py --list       # 列出所有测试项
```

---

## 它到底能做什么？

### 核心能力——像一个真正的程序员

- **对话式交互**：你说需求，它来执行。支持多轮对话，上下文自动压缩不会爆。
- **先计划再动手**：可以进入"规划模式"只读不写，出方案等你审批通过再执行。
- **两种模式切换**：`auto`（自动执行不废话）/ `ask`（每次写文件跑命令前问你），随时可以切。
- **崩溃也不怕**：自动存档，下次 `--resume` 接着干。API 挂了会自动重试换模型。

### 派小弟干活——自己不碰代码

cc_mine 的设计哲学是"你是指挥官，不是士兵"。它**自己不碰文件**，而是派子代理去干活：

- **一次性子代理**：派一个去做具体任务，做完汇报结果，然后"消亡"。
- **常驻队友**：派一个在后台长期运行，随时发消息分配任务。
- **工作流引擎**：多个任务并行跑，支持 DAG 依赖编排。
- **任务看板**：持久化任务卡片，有依赖关系（A 做完 B 才能开始）。
- **代理间通信**：基于文件的信箱系统，代理之间可以互发消息。

### 记忆系统——该记住的一个不丢

- **四层压缩防线**：消息多了自动压缩——先卸大文件→再截中间→再冻旧结果→最后 AI 摘要，确保上下文不爆。
- **分类记忆**：用户偏好存 `user/`，Agent 决策存 `agent/`，跨 worktree 共享存 `shared/`。
- **自动去重**：相似的记忆不会重复存。
- **Skill 持久化**：加载的技能存在系统提示词里，上下文压缩也丢不了。
- **CC_MINE.md**：在项目根目录写下你的习惯和要求，每次启动自动注入。

### 安全——不会乱来

- **硬拒绝名单**：13 种命令直接拦（fork bomb、`mkfs`、`chmod 777 /` 等）。
- **危险确认**：15 种操作会问你（`rm`、`chown`、`curl | bash`、`eval` 等）。
- **Git 保护**：8 种破坏性 git 操作（`push --force`、`reset --hard`）需要确认。
- **敏感路径保护**：禁止直接写 `.env`、`.ssh/`、`credentials` 等文件。
- **频率限制**：每回合最多调用 bash 的次数可以配置。
- **权限文件**：`permissions.json` 支持热加载，改完规则立马生效。

### 界面体验——看着舒服

- **Rich 终端渲染**：Markdown、语法高亮、Panel 卡片、Table 表格全支持。
- **动画 Spinner**：等 LLM 回复时转圈圈，自动适配 GBK/UTF-8 终端。
- **Aider 风格 Diff**：改文件只显示差异，绿增红删，绝不 dump 整个文件。
- **状态栏**：顶部显示当前模型、模式、plan 状态。
- **结果预览**：Bash 输出只显示前 3000 字符，完整内容存文件，想看随时展开。

---

## 快速开始

```bash
# 1. 克隆（注意：项目名叫 cc_mine）
git clone https://github.com/fisher-yu-like/cc_mine.git
cd cc_mine

# 2. 装依赖（就 5 个包）
pip install openai python-dotenv pyyaml requests rich

# 3. 配置密钥
cp .env.example .env
# 编辑 .env，填入你的 API key

# 4. 跑起来
python main.py

# 指定工作目录和模型
python main.py --workdir /path/to/project --model deepseek-v4-pro

# 恢复上次会话
python main.py --resume
```

---

## 内置命令

在对话中输入 `/` 开头的命令：

| 命令 | 作用 |
|------|------|
| `/help` | 查看所有可用命令 |
| `/mode auto\|ask` | 切换模式：自动执行 / 每步确认 |
| `/plan` | 进入规划模式（只读探索） |
| `/plan-approve` | 批准当前计划 |
| `/plan-reject 意见` | 驳回计划并给出修改意见 |
| `/model 模型名` | 切换云端模型 |
| `/ollama 模型名` | 切换到本地 Ollama 模型 |
| `/cache` | 查看提示词缓存命中率 |
| `/ccmine` | 查看 CC_MINE.md 中的偏好设置 |
| `/usage` | 查看 token 用量 |
| `/skills` | 列出可用技能 |
| `/skill-install url` | 从网址安装技能 |
| `/skill-clear` | 清除已加载的技能 |
| `/memory` | 查看所有记忆 |
| `/memory-add 标题 \| 内容` | 添加一条记忆 |
| `/memory-search 关键词` | 搜索记忆 |
| `/tasks` | 查看任务看板 |
| `/crons` | 查看定时任务 |
| `/worktrees` | 查看隔离工作区 |
| `/debug-status` | 查看 debug 失败次数 |
| `/compact` | 手动压缩上下文 |
| `/sessions` | 查看历史会话 |
| `/resume 会话ID` | 恢复某个会话 |
| `/exit` | 退出 |

---

## 项目结构

```
cc_mine/
├── main.py               # 主循环 + REPL 界面
├── config.py              # 配置 + 系统提示词 + 目录初始化
├── call_llm.py            # LLM 调用封装 + 三层提示词缓存
├── executor.py            # 工具路由（38 个处理函数）
├── tool_registry.py       # 38 个工具定义（OpenAI 格式）
├── hooks.py               # 权限 + 日志 + 安全钩子
├── memory.py              # 四层压缩 + 记忆增删查改
├── planning.py            # 规划模式状态机
├── subagent.py            # 一次性子代理
├── AutonomousAgent.py     # 常驻后台队友
├── workflow.py            # DAG 并行任务编排
├── task.py                # 任务看板（支持依赖）
├── session.py             # 会话存档与恢复
├── MessageBus.py          # 代理间消息通信
├── CronScheduler.py       # 定时任务调度
├── mcp.py                 # MCP 外部工具集成
├── worktree.py            # Git worktree 隔离
├── terminal_renderer.py   # Rich 统一渲染层
├── repl_ui.py             # REPL 界面渲染
├── spinner.py             # 加载动画
├── mode_manager.py        # auto/ask 模式
├── query_queue.py         # 并发查询队列
├── skill_context.py       # 技能持久化存储
├── skill_installer.py     # 技能安装器
├── skill_load.py          # 技能加载器
├── debug_tracker.py       # Debug 失败追踪
├── multimodal.py          # 图片/PDF 附件
├── cli_commands.py        # 25 个斜杠命令
├── CC_MINE.md             # 你的偏好设置
├── skills/                # 预装技能
├── eval/
│   ├── benchmark.py       # 7 维度 59 项评测
│   ├── suites/            # 各维度 YAML 测试定义
│   └── results/           # 评测报告
└── tools/
    ├── bash.py            # 执行命令
    ├── file_ops.py        # 文件读写编辑搜索
    ├── git.py             # Git 操作
    ├── grep.py            # 纯 Python 实现的 ripgrep
    ├── todo_write.py      # 待办事项展示
    ├── web.py             # 网页搜索与抓取
    └── result_renderer.py # 命令结果格式化
```

---

## License

MIT

---

## English Summary

cc_mine is a self-built Claude Code clone. It's an AI coding agent that plans, delegates to subagents, executes tools, and reports results — all through a terminal REPL. 38 built-in tools, planning mode, DAG workflow engine, 4-layer context compaction, safety hooks, Rich terminal UI, and a 59-task benchmark suite scoring **100/100**.

```bash
git clone https://github.com/fisher-yu-like/cc_mine.git
cd cc_mine && pip install openai python-dotenv pyyaml requests rich
cp .env.example .env   # add your API key
python main.py
```
