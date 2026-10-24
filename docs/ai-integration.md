# 接入自己的 AI 或 Agent

回测平台、研究模型和开发工具分别选择。换模型不需要换实验账本；换回测平台也不需要重建角色调度。QuantFactra 提供规划接口与任务消息接口两种接入方式。

## 作为编程助手维护项目

可让 Codex、Claude Code、Cursor 或其他编程工具读取根目录 [AGENTS.md](../AGENTS.md)。先定位[架构地图](architecture.md)，再按任务阅读一个模块及对应测试；原历史、账本和来源证据继续保留。

这不代表所有工具都已经通过运行验收，也不要求安装全部开发工具。先用下面的离线命令验证环境：

```bash
python -m quantfactra demo --output research_runs/ai_onboarding
python -B scripts/verify_public_research.py
```

## 用模型生成与反思研究假设

公开协议为 `quantfactra.ai.PlanningBackend`：

```python
class MyPlanner:
    def complete_json(self, stage, context):
        # 在这里调用你的模型，返回符合当前 stage 契约的 JSON 对象。
        # 模型返回的是假设或反思，不是执行权限或收益认证。
        ...
```

现有 `OpenAICompatibleBackend` 适配明确提供的 completion callable：

```python
from quantfactra.ai import OpenAICompatibleBackend

planner = OpenAICompatibleBackend(my_completion_callable, model="your-model-id")
```

callable 接收 `model`、`messages`、`response_format`，返回 JSON 对象、JSON 文本或兼容的 Chat Completions 返回值。需要额外认证、不同请求结构或不同输出封装的模型，应在 callable 内转换；不能假定任意厂商 SDK 都能直接替换。

CLI 的 `llm` 配置沿现有 HTTP Chat Completions 路径：

```json
{
  "enabled": false,
  "base_url": "https://your-compatible-endpoint.example/v1",
  "model": "your-model-id",
  "api_key_env": "QUANTFACTRA_LLM_KEY"
}
```

默认关闭。启用前由使用者确认模型、权限、费用和允许发送的上下文；该路径需要模型支持 JSON 对象输出。配置兼容接口不等于已认证该供应商或模型。

## 让自己的 Agent 接管角色消息

公开入口为 `quantfactra.workflow.CommandAgents` 和 `MailboxAgents`，它们复用现有 `Host`。

### 命令行桥接

为 `lead`、`market`、`fundamental`、`skeptic` 四种角色提供明确的 argv 数组。它们可以指向同一个自定义适配程序，并通过参数选择角色。程序从 stdin 读取一个完整 JSON packet，stdout 只返回 JSON 信封：

```json
{
  "work_id": "原 packet 的 work_id",
  "output": {"当前 action 要求的结构化输出": "具体内容"}
}
```

`output` 结构由原 packet 的 action 契约决定；上面的键只是信封结构示意。运行时保留原 `work_id`、任务身份、会话、截止及累计尝试，不让模型直接创建官网 Run 或改账本。

命令适配程序负责调用自己的模型、缓存 `work_id` 并处理超时。重复传输不等于可以再次执行有副作用的操作。参考 `CommandAgents` 和 [工作流合同测试](../tests/test_team_workflow.py)。

### 文件消息桥接

`MailboxAgents(directory)` 将原 packet 写入：

```text
<directory>/<role>/requests/<work_id>.json
```

你的已有 Agent 读取请求，并原子写入同名响应：

```text
<directory>/<role>/responses/<work_id>.json
```

没有响应时工作流明确等待，不虚构角色回答，也不隐式购买模型调用。CLI 的 `team host-run --mailbox ...` 可使用此传输；先运行 `team host-run --help` 查看原账本、会话和输出绑定参数。

## 三个边界保持清楚

- **模型**提出假设、实现方案与审查意见，不授予资金或正式入池权限。
- **工作流**维护角色、固定定义、历史、截止与证据，不把传输 ACK 当成理解或事实认证。
- **平台适配器**执行已授权的精确定义、读取原 Run 和回执，不替代来源/PIT与经济判断。

实际部署仍使用[聊天控制与恢复](chat_start.md)。具体模型或聊天工具需要自己的传输适配与验收；新增 AI 可以从以上公开协议接入，不需要照搬现有模型品牌或修改历史研究。本次通用接口不宣称所有厂商已开箱即用。
