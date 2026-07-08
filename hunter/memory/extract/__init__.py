"""extract 子包：S3 提取记忆。

`secret_scan` 落库前扫密钥，`chat_memories` 管 chat_memories 表和提取游标的存取，`extractor`
（第 2 次做）是提取编排。对外函数由门面 hunter.memory 从各叶子模块 re-export。
"""
