# QUANTAXIS 2.1.0-alpha2 兼容性检查范围

更新日期：2026-10-05。

`scripts/verify_compatibility.py` 对当前仓库进行源码检查，不需要安装
QUANTAXIS 或连接数据库。检查通过表示下面列出的定义、导出文本、维护文件和
依赖约束存在。参数签名、返回值、运行时导入、服务连通性及业务行为仍需分别验证。

## 本地复现与修复

原检查共 26 项，其中 22 项通过，4 项失败。失败项都是仓库缺失的文档：
`BACKWARD_COMPATIBILITY_REPORT.md`、`COMPATIBILITY_SUMMARY.md`、
`FINAL_SUMMARY.md` 和 `QUANTAXIS/QAUtil/RESOURCE_MANAGER_README.md`。
三个历史报告的检查现合并到本文档；资源管理器使用说明已补齐。
文档检查仍要求维护文件存在且非空。

在仓库根目录运行：

```bash
python scripts/verify_compatibility.py
```

修复后的结果为 **24 项通过，0 项失败**：

| 类别 | 检查数 | 检查内容 |
| --- | ---: | --- |
| 版本 | 1 | `QUANTAXIS/__init__.py` 中的版本为 `2.1.0.alpha2` |
| 旧 API 定义 | 7 | 下表列出的函数或类定义存在 |
| 新功能及导出 | 8 | `base_ps` 关闭/上下文方法，资源管理器类及主模块导出文本 |
| 维护文件 | 4 | 本文档、资源管理器说明、运行时测试脚本、使用示例 |
| 依赖约束 | 4 | `requirements.txt` 中 pymongo、pika、pandas、pytdx 的指定下界 |

脚本通过正则表达式和文本读取完成检查；它不会执行这些 API，也不会安装依赖。
例如“主模块导出通过”仅表示导出名称出现在源码中，不能保证实际导入成功。
它不自动比较历史提交 `c1e609d` 的参数签名或语义，因此不能据此声明任意旧代码
均可直接升级。

## 被检查的旧 API

| API | 源码位置 |
| --- | --- |
| `QA_util_sql_mongo_setting` | `QUANTAXIS/QAUtil/QASql.py` |
| `base_ps` | `QUANTAXIS/QAPubSub/base.py` |
| `QA_Order` | `QUANTAXIS/QAMarket/QAOrder.py` |
| `QA_Position` | `QUANTAXIS/QAMarket/QAPosition.py` |
| `MARKET_PRESET` | `QUANTAXIS/QAMarket/market_preset.py` |
| `QIFI_Account` | `QUANTAXIS/QIFI/QifiAccount.py` |
| `QA_fetch_get_stock_list` | `QUANTAXIS/QAFetch/__init__.py` |

`base_ps` 构造时连接 RabbitMQ，`close()` 依次关闭通道和连接，
`__exit__()` 调用 `close()` 并保留业务异常。
资源管理器的接口、连接时机和限制见
[QAResourceManager 使用说明](../../QUANTAXIS/QAUtil/RESOURCE_MANAGER_README.md)。

## 运行时验证

源码检查可直接在 Python 3.11 运行。完整运行时验证需要按照当前仓库的安装配置
安装依赖，并配置对应的 MongoDB、RabbitMQ 等服务。仅用源码检查成功不能证明
依赖解析、安装或包导入成功；应针对实际业务调用继续验证。

旧运行时测试入口为：

```bash
python scripts/test_backward_compatibility.py
```

该脚本会导入 QUANTAXIS 并尝试数据库、消息队列和数据获取操作。其中部分测试
捕获服务不可用、数据库未配置或可选导入失败后仍返回，因此退出码 0 也不足以
证明全部服务正常。应检查实际日志，并在业务测试中明确断言连接成功、预期数据
和返回结果。本次文档修复只执行源码检查，没有执行这个服务测试或使用示例。

Panda Alpha 的独立研究测试通过 `python -m pytest tests -q` 运行，
其结果与上述历史 API/外部服务验证分别记录。

## 维护入口

- [源码检查脚本](../../scripts/verify_compatibility.py)
- [运行时测试脚本](../../scripts/test_backward_compatibility.py)
- [资源管理器示例](../../examples/resource_manager_example.py)
- [资源管理器使用说明](../../QUANTAXIS/QAUtil/RESOURCE_MANAGER_README.md)
- [2.0 到 2.1 迁移说明](v2.0-to-v2.1.md)

更新 API 或依赖时，应同时更新相应检查和实际运行测试；版本号、文件存在性或
源码检查成功不构成业务兼容性或性能保证。
