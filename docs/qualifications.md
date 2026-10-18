# 固定待核项与证据替代

`panda_alpha/qualifications.py` 是任务板、结论和科学记忆共用的投影。事项包含固定 `id`、当前文字 `text`、`status`、内容版本 `revision`、来源事件和 `required`。状态为 `pending`、`resolved` 或 `superseded`。任务包的 `qualifications.head` 绑定整组当前事项；旧自由文本首次迁移时按任务范围与完整原文确定编号，不用关键词、前缀或相似度猜测等价关系。

## 新审查怎样销项

`result_review` 的 `assessment.pending/resolved` 保留为可读说明。关闭、重开、合并或拆分旧事项需要额外提交：

```json
{
  "qualification_changes": {
    "base_head": "复制当前 qualifications.head",
    "replacements": [{
      "supersedes": [{"id": "复制待替代编号", "revision": "复制当前版本"}],
      "items": [{"id": "保留原编号或使用新的窄范围编号", "text": "证据支持的当前判断", "status": "resolved"}],
      "reason": "说明新证据改变了旧判断的哪一部分",
      "evidence": [{"path": "已绑定的本地证据路径", "sha256": "证据哈希"}]
    }],
    "additions": [{
      "id": "new-source-obligation",
      "text": "本次证据新发现的待核事项",
      "status": "pending",
      "reason": "解释为什么这是新增事项",
      "evidence": [{"path": "已绑定的本地证据路径", "sha256": "证据哈希"}]
    }]
  }
}
```

协调器校验当前编号、版本、证据哈希和与本次审查的绑定，生成快照；模型不能直接提交快照。一个旧项可拆成多个新项，多个重复描述可合并。已经存在的编号只能通过精确替代来更新，不能覆盖其他事项。冻结请求和交付契约的门槛保留原编号；普通迁移不能将它们从待核改为已证。未来真正来源通过仍须独立审查、原 Run 绑定和独立来源证据，不因缓存报告成功而通过。

已有编号的评审中，`assessment.pending/resolved` 只作说明，“编号＋解释”或改写文字不会另建事项。新发现的资格必须通过 `additions` 给出新编号、证据和理由；新编号不能覆盖已有或已替代编号。没有事项变化时，提交当前 `base_head` 与空的 `replacements`、`additions` 即可。未显式选择变更方式却出现新说明文字时，系统拒绝并要求明确处理。首次尚未编号的旧式评审仍可由文字初始化编号。

历史 `pending` 没有写在新回复中仍然保留。只在 `resolved` 换一种说法不会消除原编号。过期的旧文字也不能自动复活已被替代的事项。独立初审不接收历史事项及结论；综合审查和负责人得到最新固定编号后再处理替代关系。

## 旧记录迁移

先保持研究停止，读取既有权威账本。下面命令不启动模型、联网或官网执行器。

```powershell
python -m panda_alpha.team qualifications --ledger research_runs/experiments.sqlite3 --registry research_runs/research_trials.sqlite3 | Out-File -Encoding utf8 migration-inventory.json
```

清单列出所有仍待核的科学记录及当前任务，包括原事件 ID/哈希、任务身份与版本、任务板游标、迁移前事项。为清单选择唯一 `operation_id`，只编辑需要更正的 `changes` 与绑定的 `artifacts`，保存为 `migration-reviewed.json`。不需要改变判断的记录保持 `changes: null`。没有结构化文字的历史来源记录得到一个仍待核的占位编号，保留原事件内容，不推断其已经解决。

```powershell
python -m panda_alpha.team qualifications --ledger research_runs/experiments.sqlite3 --registry research_runs/research_trials.sqlite3 --input migration-reviewed.json
python -m panda_alpha.team qualifications --ledger research_runs/experiments.sqlite3 --registry research_runs/research_trials.sqlite3 --input migration-reviewed.json --execute
```

第一条只读预览。应用会重新检查所有基线、任务停止状态、替代关系和证据，再在科学登记的一个事务中追加修正及恢复回执，随后在任务板的一个事务中追加对应维护事件。注册定义、分母、任务版本/状态、预算和授权均不改变。

若在两个数据库提交之间中断，使用**同一份清单与同一 operation_id** 重试；登记回执驱动任务板补写，不重复追加科学事件。原清单不能更换内容。若账本或任务在首次应用前已变化，重新生成预览；恢复阶段发生冲突时保留已有回执，先核对新事实，不能清空或重建账本。

## 本次真实迁移与验收

2026-10-19 凌晨先在权威账本的离线副本演练，再应用原账本；27 组记录（26 组科学来源记录，另有尚未官网运行的 A 对照任务），追加 27 条科学事件及 3 条任务板维护事件，原 1029 条科学事件全部保留，登记总数仍为 625。重复应用新增事件为 0。

A 的 28 条旧累计描述整理为 6 项：原生输入范围/暖机、原生报告日期、输入时钟/PIT、共同有效支持集、实际部署绑定、历史成员及成本/经济验证。旧“缓存日志缺失”销项；跨仓库版本等价的错误前提与实际部署未知分开。B 保留原 10 条待核项和报告期/PIT门槛；未运行的对照没有被当作已有回测。全部研究停止，维护期间模型与官网调用为 0，剩余预算仍为 46 点/2 次。

私有迁移清单、证据、前后审计和恢复备份存于工作空间 `work/qualification_reconciliation_20261018/`，不提交 Git。离线回归覆盖变更措辞、拆分混合命题、跨任务编号、错误/过期版本、重复目标、证据变动、迁移清单变化、重复提交及跨库提交中断。三轮演练也使用固定编号销项，不把合成回归当作真实来源验证。

随后在独立副本完成了四个真实角色的 6 个动作。初审互不读取同行及历史结论；综合审查提交了证据绑定的替代关系，负责人识别出说明文字被再次登记的问题：显式变更留下 8 项，旧投影却生成了 16 项。因此这一轮记录为发现缺陷，不能记作整体通过。修复后，同一份原始模型回复在不调用模型的重放中准确保留 8 项；新事项须显式 `additions`，说明不再增生编号。原始回复、失败副本和旧历史保留，权威 A/B、账单及 625 个登记定义未受影响。修复后的新一轮真实模型复验仍待完成。
