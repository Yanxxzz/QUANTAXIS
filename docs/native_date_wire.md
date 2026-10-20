# 原生日线日期表示

2026-10-20 的免费下游回放还确认，固定公开版本的

[`MarketDataReader._chunk_date_range`](https://github.com/PandaAI-Tech/panda_factor/blob/a783e69732da1f9ffc93844dc522375a1f67c507/panda_data/panda_data/market_data/market_data_reader.py)

要求 `YYYYMMDD`。新日线实现可以在内部用 Timestamp 计算，输出时序列化为八位日期；

预检只在解析后日期、证券、行顺序与完整输入逐键相同时接纳该表示，不排序、不补键、

不截断时间，也不修复含糊日期。构建入口与分析日期消费者分别检查。

这项公开接口兼容证据不认证生产部署，也不证明某次官网 `date` 错误具有同一原因。



原输入、日志与预检约定见[panda_alpha_native_contract.md](panda_alpha_native_contract.md)。

准备新预检时，将可能继续编辑的说明文档复制为研究目录内的固定快照并绑定 SHA256。后续文档维护不改变旧凭证；代码或运行参数发生变化时仍须重新核验，不能修改旧回执的通过标志。
