# 连续协作与恢复验收（2026-10-18）

本次验收只证明协作与状态管线。没有新增官网请求、真实算力消费、原生来源资格或因子经济结论。

| 验收 | 观察结果 | 适用范围 |
| --- | --- | --- |
| 三轮脚本演练 | 6任务、57动作、4次合成启动、6次跨线审查、2次执行前停放 | 确定性协议及恢复回归；脚本不是研究 agent |
| 真实角色邮箱交接 | 3轮、2任务、25动作（23条角色交接、2条执行器动作）、1次合成启动 | 主宿主担任 lead，三个现有 Codex agent 分别担任 market/fundamental/skeptic |
| 同任务补证 | 两类演练各完成1次原任务补证再审 | 保留原定义、Run、尝试、分母、旧 manifest 和未解决资格 |
| 两处注入中断 | 回复落盘后、任务落板前；registry提交后、任务关闭前 | 重开两个数据库并恢复原动作，无重复登记或启动 |
| 实际进程恢复 | 私有状态展示文件在 Windows 并发读取时发生共享锁，恢复后沿用原 session/待办 | 展示文件重试；没有重置业务状态或重新询问已答角色 |
| A/B 真实缓存适配 | A为10组×70日期，B为10组×60日期；组表、IC/RankIC和完整日志可识别 | 报告交付检查；来源仍pending，经济未评估 |

真实角色不是依次重复“同意”。skeptic 指出 SyntheticClient 不计算公式、排名或收益，作者据此收窄能力主张。两个研究员分别提出价量机械暴露、共同经营消息、资产分母变化及期间错配等竞争解释，lead 在冻结前显式处置。

fundamental 将精确来源标识改成说明句，超出合成 grant 的来源集合，lead 在派发前停放该任务。没有为凑执行数而扩大 grant、改写原请求或新增变体。其后两轮两个研究员均独立选择 no_candidate；归档补证没有被解释为新的原生或经济信息。角色包和职责说明已补充精确来源范围及机器标识规则。

回归还覆盖：既有 Run 优先恢复、同方向 ready 槽位释放、模糊创建响应不重派、非法嵌套回复可退回原角色纠正、旧资格不可因漏填消失、新快照可用于重审、合成环境不可改标、源修复与正负经济结论进入下轮科学记忆。正面结论也不会允许改名重复登记或自动入池。

从源码复现确定性验收：

```text
python -m pip install -r requirements-panda-alpha.txt
python -m panda_alpha.team host-demo --output research_runs/team_host_demo
python -m pytest tests/test_team_lifecycle.py tests/test_team_workflow.py tests/test_registry.py tests/test_evolution.py -q
python -B scripts/verify_public_research.py
python -m pytest tests -q
```

真实角色复现使用 [协作协议](team_workflow.md#有限持续调度) 的文件邮箱或显式命令桥，需由宿主启动实际角色。没有默认模型服务；单协调写入者约定、外部 work_id 去重及来源判断仍是部署边界。长期检索、多进程主节点切换和统计研究质量不由上述验收证明。

原权威账本保持研究轮累计27次/58点、0未结算，剩余授权46点；科学策略总数625不变，旧夜间自动化仍暂停。A的原生时钟/共同支持集/worker绑定及B的期间/PIT问题未获证，本次不启动新官网回测。
