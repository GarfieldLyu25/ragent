# LangFuse 链路追踪（Agent 模式）

本目录提供 LangFuse v4 的本机自托管编排，用于观测 Agent 模式的执行现场：模型每一轮实际看到的完整上下文、ReAct 轮次、每次模型调用的 token 与耗时、每批工具的批区间，以及批内**每个工具各自的**起止、耗时、状态与入参出参。

## 启动

以下命令从项目根目录执行：

```powershell
docker compose -f resources/docker/langfuse/langfuse-stack-v4.compose.yaml up -d
docker compose -f resources/docker/langfuse/langfuse-stack-v4.compose.yaml ps
```

PostgreSQL、Redis、ClickHouse、MinIO 四个依赖容器应显示 healthy；Web 和 Worker 没有配置 healthcheck，只需确认正常运行并等待 Web 就绪。可查看启动日志：

```powershell
docker compose -f resources/docker/langfuse/langfuse-stack-v4.compose.yaml logs --tail 80 langfuse-web
```

浏览器打开 <http://localhost:3000>。这份 Compose 首次启动预建 `admin@ragent.dev` / `admintrace` 和项目 `ragent-service`，这是本项目的初始化配置。

## 组件与端口

| 容器 | 内存上限 | 宿主机端口 | 说明 |
|------|---------|-----------|------|
| langfuse-web | 1536 MB | **3000** | UI 与 OTLP 摄取入口 |
| langfuse-worker | 1 GB | 无 | 异步消费摄取队列写入 ClickHouse |
| langfuse-clickhouse | 3 GB | 无 | 观测数据主库，这套里最吃内存的一个 |
| langfuse-postgres | 256 MB | 无 | 项目、用户、密钥等事务数据 |
| langfuse-redis | 128 MB | 无 | BullMQ 队列，内部数据上限 96 MB |
| langfuse-minio | 256 MB | 无 | 摄取事件与媒体的对象存储 |

**只发布 3000 一个端口。** 上游编排会把 minio 映到宿主机 9090、ClickHouse 映到 9000/8123、Postgres 映到 5432、Redis 映到 6379，这几个在本项目里全部撞车（9090 是应用自身、9000/9001 是 milvus 栈的 rustfs、5432/6379 是应用的库）。这里把这些 `ports` 整段删掉了，容器之间走 `langfuse-net` 内部 DNS，不需要任何宿主机端口。

注意「绑 `127.0.0.1` 就不算占用」是错的，`127.0.0.1:5432:5432` 照样会和应用的 Postgres 抢端口，所以是删而不是改绑定。

起完确认一下：

```powershell
docker compose -f resources/docker/langfuse/langfuse-stack-v4.compose.yaml ps
```

编排只发布 3000，不占用本项目的 9090、9099、5433、6379、9000 或 9001。

## 应用侧接入

`application.yaml` 里的追踪总开关默认关闭、内容采集默认开启，密钥已经和编排里的 `LANGFUSE_INIT_*` 对齐。起栈后只需打开追踪：

在 IDE 的 `RagentApplication` 运行配置里设置 `AGENT_TRACE_ENABLED=true` 并重启。也可在 `bootstrap/src/main/resources/application.yaml` 的现有 `agent.trace` 下将 `enabled` 改为 `true`。当前引擎已配置为 `agent`。

此时 Input / Output 会随 trace 一起上报，无需再额外配置内容开关。环境变量在 Spring 的松散绑定里优先于配置文件，IDE Run Configuration 也遵循同一规则。

只想看骨架（轮次、批区间、逐工具耗时与状态、token）时显式关闭内容采集：

在同一 IDE 运行配置里设置 `AGENT_TRACE_CAPTURE_CONTENT=false`，或在现有 `agent.trace` 下将 `capture-content` 改为 `false`，然后重启主后端。

四层节点仍会成树，只是 Input / Output 不记录业务内容。

问一个会调工具的问题，Trace 立刻出现在 LangFuse 的 Tracing 页，树形是这样：

```
invoke_agent ragent          根节点带本轮问题与最终回答
├─ reasoning                 ragent.react.round_index=1
│  └─ chat                   模型输入输出
├─ tool_batch                按 callIndex 排序的批次清单与结果
│  ├─ execute_tool A         A 自己的入参、结果、状态与真实耗时
│  └─ execute_tool B         B 自己的那一份
└─ reasoning                 ragent.react.round_index=2
   └─ chat
```

轮次、callId、batchId 都在属性里而不在名字里，所以 `reasoning` / `tool_batch` 这两个名字是固定的，可以直接按名字聚合耗时。同名工具并行时靠 `gen_ai.tool.call.id` 与 `ragent.tool.call_index` 区分。按 `langfuse.session.id` 过滤即按会话过滤，它就是库里的 conversationId。

内容开关只对**新产生的** trace 生效，改完不会回填已有的那些。

## 六个容器一个都砍不掉

ClickHouse 是观测数据主库、对象存储是摄取事件的落盘位置，都是 LangFuse v4 的硬依赖。

复用应用自己的 Postgres 和 Redis 也不行：worker 用 BullMQ，要求 Redis 的 `maxmemory-policy` 为 `noeviction`，和应用 Redis 的淘汰策略一冲突就是**静默丢摄取任务**——追踪数据缺一块比没有追踪更难排查。

## 已知取舍

- **多模态附件在 UI 里预览不可用**。`LANGFUSE_S3_MEDIA_UPLOAD_ENDPOINT` 上游是 `http://localhost:9090`，靠浏览器直连 minio 取文件；我们不发布 minio 端口，只能给内部地址 `http://minio:9000`。本项目的 trace 是纯文本，不受影响。
- **批量导出走不通**，同上原因，`LANGFUSE_S3_BATCH_EXPORT_ENABLED` 保持关闭。
- **`TELEMETRY_ENABLED` 关掉了**，不往上游回传使用统计。

## minio 镜像拉不动时

`cgr.dev/chainguard/minio` 是上游用的镜像，chainguard 免费档只提供 `latest`，是这套里唯一没钉版本的一个，国内网络也可能拉不动。换成官方镜像只改一行：

```yaml
  minio:
    image: minio/minio:RELEASE.2024-12-18T13-15-44Z
```

`entrypoint`、`command`、healthcheck 都不用动——官方镜像里 `/usr/bin/mc` 和 `/usr/bin/curl` 都在，`mc ready local` 照样能跑（已实测过这个 tag）。

## 清理

```powershell
docker compose -f resources/docker/langfuse/langfuse-stack-v4.compose.yaml down  # 停容器，追踪数据保留
```

添加 `-v` 会删除数据卷和追踪记录，普通停止时不需要这个参数。
