# 从 Codex 对话启动研究

当前默认由操作者在 Codex 对话中发起运行。操作者可以说“启动四个 agent，继续免费核验 A/B”“暂停研究”或“查看研究状态”，Codex 按下述流程调用现有命令。无需操作者手工输入终端命令，也不依赖登录触发或定时唤醒。

## 启动与恢复

1. 查看 `readiness`、当前 host/runtime 和进程记录，说明本轮目标、剩余边界及来源阻点。状态查询本身不启动模型。
2. 有未完成且可恢复的会话时，沿用原 session、runtime、workers 和 deployment；不重置截止时间、尝试或启动次数。`attention` 必须先解决其实际原因。
3. 会话已完成或过期时，根据当前已授权任务初始化新的有限会话，复用原实验账本和科学登记。明确轮数、动作数、截止时间及输入材料；不把已完成的验收会话当作真实研究入口。只有来源阻点且没有新增材料时，报告等待原因，不重复空跑。
4. 使用 `service-prepare` 固定运行配置，默认离线。只有明确准备官网运行时才同时传 `--execute --config execution.json`；这只冻结原执行设置，不安装系统启动项、不启动模型或访问账户。不要执行 `team-service.ps1 -Action Install`。

以下命令由 Codex 在明确的启动请求后执行，配置须来自本轮准备或原会话；使用该配置绑定的 checkout 和 Python：

```powershell
python -m panda_alpha.team host-control --deployment research_runs/service-review/service.json --action start
```

此命令隐藏启动原服务入口并返回可观测状态，包含四角色及协调器的存活情况、模型进程数、轮数、动作数和原会话边界。重复启动已运行的部署不会新建第二次模型运行；完成、过期、额度 attention 或次数耗尽时只返回阻点。启动进程自行登记身份，启动回执缺失时保留 `ambiguous_manual_launch`，需核对现场而不能盲目重启。

`running` 表示宿主进程存活，四角色是否就绪须看 `components`。四角色继续使用原宿主的独占锁、超时和组件重连。整台电脑重启或最外层启动器退出后，等待下一次聊天指令恢复；不会由 Windows 自动拉起。聊天结束不会自动暂停已启动的有限运行。

如果同一 runtime 正由旧 `host-serve` 命令直接运行，返回 `runtime_outside_deployment`，保持原进程且拒绝另启一个部署。先通过原终端正常停止，再由聊天入口接管；不能把一个缺少 service 记录的活跃宿主当成已退出。

## 暂停与状态

“暂停研究”通过该部署的正常停止入口执行，并等待全部所属进程退出：

```powershell
python -m panda_alpha.team host-control --deployment research_runs/service-review/service.json --action stop
```

停止开关持久保存，后续继续须由操作者明确发起。最多等待十秒清理，若仍有所属进程，按返回的进程状态继续核对，不把“已请求停止”当作“全部已退出”。保留任务、原始回复、模型会话及审计历史，不清空运行目录。

“查看状态”使用两个只读入口，分别展示宿主和研究就绪状态，不因状态过旧而自动启动：

```powershell
python -m panda_alpha.team host-control --deployment research_runs/service-review/service.json --action status
python -m panda_alpha.team readiness --ledger research_runs/experiments.sqlite3
```

`readiness.source_queue` 区分排队、重审中、待结论和重审完成。`review_completed` 可以与 `source_status=pending` 同时成立；没有新材料且来源仍待核时，`next_action=await_new_source`，无需再空跑模型。

## 官网运行的同一入口

官网部署在准备时绑定非空 `execution_config`，值与原 session、ledger、registry 一起冻结。每次由聊天明确启动或恢复：

```powershell
python -m panda_alpha.team host-control --deployment research_runs/service-pilot/service.json --action start --execute
```

省略 `--execute` 返回 `explicit_online_start_required`，不会启动进程；不能用这个标志升级一个既有离线部署。旧部署、会话和配置不能被原地换绑。官网部署不接受登录/分钟任务或未带手动启动回执的服务直启。

`--execute` 仅选择已冻结的执行入口。新派发仍检查原任务的来源、原生预检、有效 grant、研究次数、资金来源和预留。开始与恢复不会激活、延长或扩大 grant，也不清零用量。已有 Run 可以在 grant 过期或团队暂停时只读查询并结算；宿主自身原 deadline、动作和启动上限仍然有效，过期会话须另行明确安排有界接续。

停止仍使用同一个 `host-control --action stop`。停止信号在协调动作之间、账户调用之前和创建/启动边界检查；已在途请求可能完成。Stop 关闭本机派发与轮询，不调用远端终止接口、不承诺撤销计费。返回状态的 `unsettled_jobs` 保留原 Run 或无 Run ID 的待核阶段，`remote_status=not_queried` 表示这是账本视图。查询状态不联网。

恢复已知 Run 时只查该 Run 并结算，不重新 create/start。若暂停发生在预留后、创建后，或者创建/启动响应丢失而没有 Run ID，保留 `RESERVED/CREATED/CREATING/DISPATCHING` 记录和预算占用，进入人工核对；不能猜测成功或清除预留后重发。未知状态下停止服务也不结算或退款。

当前真实研究保持停止，A/B 来源未通过前不消耗剩余 46 点，不重新激活旧授权。本轮只在隔离测试账本中使用本地模拟官网和合成角色验收官网控制链路。

## 本机迁移记录（2026-10-18）

原 `PandaAlpha-Research` 计划任务在操作者本次开机登录后仍执行状态检查，返回码为 0。旧验收会话已于当天下午完成，检查没有新增模型动作。Windows 报告的内核启动时间仍为 10 月 14 日，因此这里只确认本次登录后的自动检查可用，不将其记作完整冷重启中的任务恢复验收。

随后按操作者要求停止并卸载该计划任务，登录触发和分钟恢复一并移除。部署开关为 `enabled=false`，原配置、日志和研究历史保留。本次修改不启动新的真实模型会话或官网回测。

原隐藏启动入口已用隔离账本和合成角色实测：启动、正常暂停、沿原待办恢复均通过，原运行清单和请求快照保持不变，最终到轮数上限退出。后续将其收敛为 `host-control`，回归还覆盖重复启动、宿主丢失后接管原模型尝试，以及终态、次数和模糊启动阻断。全部使用合成角色，不注册系统任务，不消耗真实模型或官网算力。

Windows 登录服务仍保留为需要明确选择的可选部署方式，见[登录服务](login_service.md)。
