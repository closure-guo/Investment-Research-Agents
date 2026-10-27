# Delta for factor-research-monitor

## ADDED Requirements

### Requirement: 会话产物保留窗清理
The system MUST 在每次研发会话结束后，将 `RD-Agent_workspace` 下最新文件改动时间早于保留窗（默认 48h，可经 `FACTOR_WORKSPACE_RETENTION_H` 覆盖）的任务目录删除，且删除行为 MUST NOT 影响正在运行或保留窗内的会话目录。

#### Scenario: 会话结束后清理过期目录
- GIVEN workspace 下存在任务目录 A（最新文件 mtime 为 72h 前）与 B（最新文件 mtime 为 10h 前）
- WHEN 一次会话结束、看护进入下一节拍
- THEN 目录 A 被删除、目录 B 保留
- AND 清理结果（删除数 / 失败数）写入看护日志

#### Scenario: 清理失败不致崩
- GIVEN 某过期目录因 I/O 错误无法删除
- WHEN 保留窗清理执行
- THEN 该目录被跳过并记录失败明细
- AND 看护主循环继续正常运行

### Requirement: 磁盘水位采集与只读派生
The collector MUST 在每节拍采集研发 distro 的 guest ext4 空闲字节、宿主工作盘空闲字节与 workspace 目录总占用，并 MUST 在只读端点以 `disk` 块派生只读视图；宿主卷或 guest 任一侧空闲低于阈值（guest 默认 30GB、宿主默认 20GB）时派生 `low_watermark=true`。

#### Scenario: 水位读数入快照
- GIVEN 采集器按节拍运行
- WHEN 快照组装
- THEN 快照含 `disk.guest_free_bytes` / `disk.host_free_bytes` / `disk.workspace_bytes`
- AND 任一读数采集失败时该字段如实置空并记录失败原因，MUST NOT 编造数字

#### Scenario: 低水位判定
- GIVEN guest ext4 空闲为 25GB（< 30GB 阈值）
- WHEN 端点派生 `disk` 块
- THEN `low_watermark=true` 且 `low_watermark_reason` 指明 guest 侧

### Requirement: 低水位停发保护
The supervisor MUST 在低水位成立时暂停拉起新的研发会话，并在状态中如实披露暂停原因；已在运行的会话 MUST NOT 被低水位保护中断。

#### Scenario: 低水位时暂停新会话
- GIVEN `disk.low_watermark=true`
- WHEN 看护到达下一节拍且无会话在运行
- THEN 看护不拉起新会话
- AND 状态记录 `paused_reason=disk_low_watermark`

#### Scenario: 运行中会话不受影响
- GIVEN 低水位成立且当前有会话在运行
- WHEN 看护节拍
- THEN 该会话继续运行至自然结束
- AND 结束后进入停发状态

### Requirement: distro 探活覆盖页缓存假阴性
The distro 探活 MUST 在 exec 探针之外增加对 distro 文件系统的真实读写验证（创建并删除一个临时文件）；exec 成功而读写失败时探活结果 MUST 为不可用。

#### Scenario: 页缓存假阴性被识破
- GIVEN distro 内 `/bin/true` 可执行（页缓存命中）但文件系统大面积 I/O 错误
- WHEN 探活执行
- THEN exec 探针成功、读写探针失败
- AND 探活结果为不可用（`ok=false`），失败原因区分 `fs_rw_failed`
