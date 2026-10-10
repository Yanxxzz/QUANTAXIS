# QAResourceManager 使用说明

本说明对应 [QAResourceManager.py](QAResourceManager.py) 的现有实现。
该模块提供 MongoDB、RabbitMQ、ClickHouse、Redis 的连接生命周期管理及
按类型共享的 `QAResourcePool`。导入路径为：

```python
from QUANTAXIS.QAUtil.QAResourceManager import (
    QAMongoResourceManager,
    QARabbitMQResourceManager,
    QAClickHouseResourceManager,
    QARedisResourceManager,
    QAResourcePool,
)
```

## 依赖与连接时机

| 管理器 | 所需客户端 | 常用获取方法 |
| --- | --- | --- |
| `QAMongoResourceManager` | `pymongo` 和 `motor` | `get_database(name)`、`get_client()` |
| `QARabbitMQResourceManager` | `pika` | `get_channel()`、`get_connection()` |
| `QAClickHouseResourceManager` | `clickhouse-driver` | `execute(sql)`、`query_dataframe(sql)`、`get_client()` |
| `QARedisResourceManager` | `redis` | `get_client()`、`pipeline()`、`set()`、`get()` |

构造器保存连接参数；调用 `connect()`、进入 `with` 或第一次获取客户端时连接。
构造相应管理器时，缺失客户端依赖会产生 `ImportError`。MongoDB 当前将
`pymongo` 与 `motor` 放在同一导入检查中，因此同步模式也需要两者均已安装。

所有管理器均提供 `connect()`、`close()`、`is_connected()` 和 `reconnect()`。
`reconnect()` 先关闭再连接。访问方法发现连接不可用时会再次调用 `connect()`；
当前实现没有后台重试任务或自动重放失败业务操作。

## MongoDB 同步示例

下面代码需要可连接的 MongoDB 服务。显式传入 URI 可避免依赖默认设置：

```python
with QAMongoResourceManager(
    uri="mongodb://localhost:27017/quantaxis",
    max_pool_size=100,
    server_selection_timeout_ms=5000,
) as mongo:
    db = mongo.get_database("quantaxis")
    row = db.stock_day.find_one({"code": "000001"})
    print(row)
```

进入 `with` 会建立连接并执行 `ping`；退出时关闭客户端。基类 `__exit__`
返回 `False`，业务异常会继续传播。手动使用时将 `close()` 放在 `finally` 中：

```python
mongo = QAMongoResourceManager(uri="mongodb://localhost:27017/quantaxis")
try:
    mongo.connect()
    db = mongo.get_database("quantaxis")
finally:
    mongo.close()
```

未提供 URI 时先读取 `QA_Setting().mongo_uri`；设置读取失败时回退到
`MONGODB` 主机环境变量和端口 27017，主机默认 `localhost`。

`async_mode=True` 会创建 Motor 客户端。现有管理器只实现同步的
`__enter__` / `__exit__`，没有 `__aenter__` / `__aexit__`，因此不能对它使用
`async with`。异步查询应在事件循环内手动获取数据库，并在 `finally` 中调用
同步 `close()`；该模式的连接标志也不代表已完成服务器 `ping`。
现有示例文件中的 `example3_mongodb_async` 使用了未实现的异步上下文协议，
不能作为已通过验证的用法。

## RabbitMQ、ClickHouse 与 Redis

RabbitMQ 可显式传入 `host`、`port`、`username`、`password` 和 `vhost`。
进入 `with` 创建 `pika.BlockingConnection` 和通道，退出时先关闭通道，再关闭
连接。没有传入主机时尝试使用 `QAPubSub.setting.qapubsub_ip`，否则使用
`localhost`。凭据应从应用配置传入。

ClickHouse 使用 native protocol，默认端口 9000、数据库 `quantaxis`。
`execute(sql)` 返回驱动查询结果；需要 DataFrame 时使用
`query_dataframe(sql)`。未传入主机时尝试读取 `qaenv.clickhouse_ip`，
否则使用 `localhost`。

Redis 默认使用 `localhost:6379`、数据库编号 0。连接时创建连接池并执行
`ping`，关闭时断开池中的连接。`set()`、`get()`、`delete()`、`exists()`
委托给 Redis 客户端；`pipeline()` 返回驱动管道，需要自行调用 `execute()`。

## 共享资源池

```python
pool = QAResourcePool.get_instance()
try:
    mongo = pool.get_mongo(uri="mongodb://localhost:27017/quantaxis")
    db = mongo.get_database("quantaxis")
    print(pool.health_check())
finally:
    pool.close_all()
```

池按 `mongo`、`rabbitmq`、`clickhouse`、`redis` 四个键缓存管理器；同一类型
后续调用返回已创建的管理器，新的连接参数不会覆盖首次参数。需要更换连接时，
先调用 `close_resource("mongo")`，再调用 `get_mongo(...)`。

获取池中的管理器本身不会连接服务，实际连接仍由获取数据库/通道等操作触发。
`health_check()` 只检查已注册资源，不启动周期性后台检查；尚未连接的资源
可能返回 `False`。池注册了 `atexit` 清理，同时支持显式 `close_all()`。
关闭共享管理器会影响同一进程中的其他使用者，应由应用协调生命周期。

## 便捷上下文函数与验证

`get_mongo_resource()`、`get_rabbitmq_resource()`、
`get_clickhouse_resource()`、`get_redis_resource()` 创建独立管理器，并在退出
上下文时关闭。进入这些便捷函数本身不执行连接；获取数据库/通道等操作时连接。

[使用示例](../../examples/resource_manager_example.py) 包含服务查询和写入，
不是无需服务的测试。源码检查入口为：

```bash
python scripts/verify_compatibility.py
```

检查范围及运行时验证限制见
[兼容性状态](../../doc/migration/COMPATIBILITY_STATUS.md)。文档与源码存在性检查
不能证明服务连接、异步操作或业务结果正确。
