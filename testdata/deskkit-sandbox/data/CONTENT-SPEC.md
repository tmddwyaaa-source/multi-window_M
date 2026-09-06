# 词库批次约定（ROUND-002）

窗号是 **C1**。产出文件名是 **CB1.json**。二者不是同一个东西。

- 路径：`data/words-batch/CB1.json`
- JSON 字段 `"window"`：写文件标记 `CB1`（给脚本认）
- JSON 字段 `"owner_window"`：写真实窗号 `C1`
- 对 M1 汇报只说「C1 已完成」，不要说「CB1 已完成」
