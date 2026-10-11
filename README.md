# Agentic RAG：项目介绍与本机启动

这是一个 Java 知识问答项目，提供固定 RAG 工作流和 AgentScope ReAct 两条入口。RAG 负责问题改写、意图解析、检索、重排及生成；外层 Agent 调用知识工具、MCP 业务工具和 Skills，并管理多轮对话与工具确认。

- 普通 RAG：`/api/ragent/rag/v3/chat`。
- Agent：`/api/ragent/agent/v1/chat`。
- 当前演示场景：Apple 授权经销商“比特严选”。

下面只记录这台电脑已经存在的环境和启动方法。

## 这台电脑用哪些服务

2026-10-10 已实际核对 Docker 容器、端口和 Windows 服务：**Redis 用本地服务，其余项目基础服务用 Docker**。

| 服务 | 本机已有的名称 | 项目访问地址 |
| --- | --- | --- |
| Redis | Windows 服务 `Redis` | `127.0.0.1:6379` |
| PostgreSQL/pgvector | Docker `ragent-pg` | `127.0.0.1:5433`，容器内 `5432` |
| MinIO | Docker `ragent-minio` | 文件接口 `9000`，管理界面 `9001` |
| RocketMQ NameServer | Docker `rmqnamesrv` | `9876` |
| RocketMQ Broker | Docker `rmqbroker` | `10911` 等 |
| RocketMQ 控制台 | Docker `rocketmq-dashboard` | `8082`，与 Broker 共享网络 |
| MCP 后端 | IDE `McpServerApplication` | `http://localhost:9099` |
| 主后端 | IDE `RagentApplication` | `http://localhost:9090/api/ragent` |
| 前端 | `frontend` 中运行 Vite | `http://127.0.0.1:5173` |

**MQ 要启动。ES 和 Milvus 当前不用启动**：现有配置使用 pgvector，关键词后端为 `none`。MQ 不仅用于上传文档，当前主应用启动时也会连接消息消费者。

## 每次怎么启动

### 1. 打开 Docker Desktop，启动已有容器

在 Docker Desktop 点击已有容器的启动按钮，或者在 PowerShell 执行：

```powershell
docker start ragent-pg ragent-minio rmqnamesrv
docker ps
```

等 `rmqnamesrv` 显示 `healthy`，再启动 Broker 和控制台：

```powershell
docker start rmqbroker rocketmq-dashboard
```

直接启动已有容器，保留原来的数据库和文件，不重新创建。

### 2. 确认本地 Redis 正在运行

```powershell
Get-Service Redis
```

如果状态是 `Stopped`，在管理员 PowerShell 中执行：

```powershell
Start-Service Redis
```

### 3. 在 IDE 启动两个后端

项目已经有这两份运行配置，按顺序启动：

1. **`McpServerApplication`**：`com.nageoffer.ai.ragent.mcp.McpServerApplication`，端口 `9099`。
2. **`RagentApplication`**：`com.nageoffer.ai.ragent.RagentApplication`，端口 `9090`。

配置分别来自 `mcp-server/src/main/resources/application.yml` 和 `bootstrap/src/main/resources/application.yaml`。外层 Agent 的模型选择配置在 `agent.chat`；RAG 内部聊天按 `ai.chat.tiers` 选择候选，当前 fast 首选 `qwen-flash`、standard/deep 首选 `qwen3-max`。这些配置项不代表对应 Key 已配置或模型可调用。Embedding 默认模型为百炼 `text-embedding-v4`，Rerank 默认模型为百炼 `qwen3-rerank`。


**本地直接使用 `bootstrap/src/main/resources/application.yaml`，API Key 可以明文写在这里。** 该文件已加入 `.gitignore` 并取消 Git 跟踪。本地启动方式不变，无需额外密钥文件或 profile。

Git 保存不含真实模型 Key 的备用配置 `bootstrap/src/main/resources/application.example.yaml`。以后新拉取项目时先复制一份，再填写自己的 Key：

```powershell
Copy-Item bootstrap/src/main/resources/application.example.yaml bootstrap/src/main/resources/application.yaml
```

已有本地配置时不要覆盖。需要同步公共配置变更时，同步修改备用文件，备用文件的模型 Key 保留环境变量占位符。

供应商地址与模型选择仍在 `application.yaml` 修改。百炼北京业务空间专属地址在本项目的 `ai.providers.bailian.url` 中只填 `https://<业务空间ID>.cn-beijing.maas.aliyuncs.com`，不包含 `/api/v1`：本项目会另外追加 `endpoints`。聊天和 Embedding 使用 `/compatible-mode/v1/...`。当前 Rerank 客户端实现原生协议；最新百炼文档的 `qwen3-rerank` 使用 `/compatible-api/v1/reranks` 和不同的请求/响应格式，需要适配后验证，不能仅替换路径。参考 [百炼 Base URL](https://docs.bailian.console.aliyun.com/zh/model-studio/base-url) 和 [Rerank API](https://www.alibabacloud.com/help/zh/model-studio/text-rerank-api)。

修改 Key、地址或模型后重启主后端。通过 IDE 启动时确认资源已刷新；使用 JAR 时重新编译，或用已有 `--spring.config.additional-location=file:bootstrap/src/main/resources/application.yaml` 方式加载当前本地配置。

当前主配置已设为 `ragent.engine.type=agent`、`ragent.eval.enabled=true`。启动后确认日志没有数据库、MQ、模型或 MCP 连接错误。

### 4. 启动前端

从项目根目录进入前端，执行：

```powershell
Set-Location frontend
npm run dev
```

浏览器打开 `http://127.0.0.1:5173`，用本地已有账号登录。

### 5. 启动 Langfuse 查看 Agent Trace

Langfuse 负责记录模型调用、工具执行、耗时和 Token，不提供聊天或 Embedding 模型 API。它与 OhMyGPT 等模型中转站分别配置。

从项目根目录执行：

```powershell
docker compose -f resources/docker/langfuse/langfuse-stack-v4.compose.yaml up -d
docker compose -f resources/docker/langfuse/langfuse-stack-v4.compose.yaml ps
```

这份编排启动 6 个容器，只对宿主机发布 `3000`。PostgreSQL、Redis、ClickHouse、MinIO 应显示 `healthy`，Web 和 Worker 应正常运行。查看 Web 是否就绪：

```powershell
docker compose -f resources/docker/langfuse/langfuse-stack-v4.compose.yaml logs --tail 80 langfuse-web
```

打开 `http://localhost:3000`。本项目 Compose 首次启动预建账号 `admin@ragent.dev`、密码 `admintrace`、项目 `ragent-service`。

随后在现有 `agent.trace` 配置中将 `enabled` 改为 `true`，重启 `RagentApplication`。当前 endpoint 为 `http://localhost:3000/api/public/otel/v1/traces`，public-key/secret-key 对应该 Compose 的预置项目密钥。`capture-content=true` 时记录提示词和工具出入参，设为 `false` 时保留运行结构、耗时和 Token 等信息。

通过 Agent 入口提问后，在 Langfuse 的 Tracing 页面观察新记录。它自带专用 Redis 和数据库；本项目业务 Redis 仍使用 Windows 服务。2026-10-10 已确认这 6 个容器正常运行，本地 `agent.trace.enabled` 已设为 `true` 并同步到编译输出；配置变更须重启主后端，之后的新对话才会产生记录。详细说明见 [Langfuse 本机部署](resources/docker/langfuse/README.md)。

## 当前数据情况与后续顺序

2026-10-10 已执行演示数据导入并核对：商品 695、SKU 1,420、订单 8、优惠券 7；4 个演示知识库、42 个意图、5 份 Skills、比特严选人设和 15 条示例问题已创建。默认账号、内置人设和提示词原本已经存在。

**知识文档 Embedding 已完成：69 篇目标文档全部为 `success`，生成 208 个切片和 208 条 1536 维向量，缺失向量数为 0。** 分布为商品与导购 38 篇/141 个切片、使用手册 11 篇/24 个切片、平台政策 16 篇/34 个切片、常见问题 4 篇/9 个切片。

本次已执行文档导入和只读数据库核对，日常启动无需重复执行下面的文档导入命令。原 `VerifyMain` 全局校验未通过：另有一个个人简历知识库，总知识库数为 5，超过模板要求的 4；四个演示库的文档、切片、向量和维度已单独核对通过。后来用户重新处理个人 PDF 后，其状态也已为 `success`，有 2 个切片，当前共 70 篇可用文档和 210 条向量。

```powershell
& 'C:/Users/Administrator/.jdks/ms-17.0.18/bin/java.exe' '-Dfile.encoding=UTF-8' -cp tmp/initializer-classes com.nageoffer.ai.ragent.initializer.DocumentInitMain --agent-type-dir tmp/import-20261010/bit-selection
& 'C:/Users/Administrator/.jdks/ms-17.0.18/bin/java.exe' '-Dfile.encoding=UTF-8' -cp tmp/initializer-classes com.nageoffer.ai.ragent.initializer.VerifyMain --agent-type-dir tmp/import-20261010/bit-selection
```

导入使用临时 LF 换行副本，138 个模板文件均与仓库原校验和匹配，原模板内容未修改。完整执行记录见 [本次数据导入记录](docs/local-data-import-2026-10-10.md)。

仓库提供的 [比特严选演示数据](resources/initializer/bit-selection/README.md) 包含 69 篇资料等。普通启动时不需要重建数据库或重复导入。前端在 `/admin/knowledge` 进入知识库查看文档状态与切片数；文档的“更多 → 分块日志”查看处理记录，分块管理查看切片内容。

测试项目已移到 [ragenteval-main](ragenteval-main/README.md)。固定RAG与Agent同题集为180条；当前Agent场景已更新为40场景、48轮，其中34个自然场景可录制、6个受控恢复场景默认跳过。旧24场景和已完成的历史记录保留。场景内容见 [Agent场景阅读版](ragenteval-main/docs/agent-scenarios-v3.md)。

2026-10-10 已完成 180 条固定 RAG 和 24 个 Agentic 场景的全量测试，结果见本文末尾。下一步审核答案与金标准，再针对跨文档证据覆盖、知识工具耗时和确认流程演进项目。

项目细节见 [系统设计](docs/Agentic-RAG设计架构.md)；原上游介绍保留在 [docs/README-upstream.md](docs/README-upstream.md)。

## 全量测试命令与本次结果

题目和实际导入内容先看 [数据集阅读版](docs/evaluation-dataset-v2.md)。测试运行器从 `.env` 读取地址和账号；需要主后端、MCP、模型服务和已有演示数据可用，测试不依赖前端启动。

首次准备测试依赖：

```powershell
Push-Location ragenteval-main
python -m pip install requests python-dotenv
Pop-Location
```

从本项目根目录运行。本轮实际执行两个预检和两个全量录制命令，测试串行完成：

```powershell
Push-Location ragenteval-main
$env:PYTHONUTF8 = '1'
python -m eval v2 preflight --mode rag
python -m eval v2 preflight --mode agent
python -m eval v2 run --mode rag --suite rag
python -m eval v2 run --mode agent --suite agentic
Pop-Location
```

第一组 180 条固定 RAG，第二组 24 个 Agentic 场景、30 轮。确认场景自动拒绝并清理，不批准取消订单或售后写入。未执行外层 Agent 对同一批 180 条的第三组对照。

### 2026-10-10 全量结果

| 项目 | 实测结果 |
| --- | --- |
| 固定 RAG 执行 | 180 / 180 条完成 |
| 文档级检索（160 条有指定来源） | Hit@1 97.50%；Hit@5 100%；Recall@5 96.98%；MRR@10 0.9875 |
| RAG 首字耗时 | P50 4.013 秒；P95 5.581 秒 |
| 跨文档检索 | 22 条，Recall@5 78.03%；10 条前五未覆盖全部指定来源 |
| Agentic 执行 | 24 场景 / 30 轮；27 轮正常结束、3 轮等待系统确认 |
| Agentic 自动过程约束 | 23 场景可判定且通过；AG-22 改地址的系统确认约束待复核 |
| Agentic 首字耗时 | P50 8.678 秒；P95 38.043 秒 |
| 答案质量 | 完整生成质量评分未完成；补跑 RAGAS 正确性保存 6 / 160 条后按用户要求停止，不能报告全量正确率或 Agentic 任务成功率 |

主要发现：跨文档证据覆盖低于单文档；知识与业务组合中的知识工具耗时约 30–45 秒；AG-06 第二轮调用商品查询 10 次，需要工具预算；AG-22 只要求文字确认，尚未观察到系统确认事件。原始记录支持逐条复核，自动过程约束通过不代表答案正确。

本轮题集是来源派生草案，包含标题式问题，不能与旧 20 条结果直接计算提升幅度。检索分数来自独立 `/rag/eval`，不是生成实际使用的证据；两组首字耗时也不是同题集架构对照。

详细分析与简历可用表述：[全量评测分析](docs/evaluation-full-2026-10-10.md)。本机原始报告：[RAG](ragenteval-main/eval/runs/v2_rag_rag_20261010_191926_212903/report.md)、[Agentic](ragenteval-main/eval/runs/v2_agent_agentic_20261010_200758_262563/report.md)，运行目录被 Git 忽略。

### 复用已保存回答补跑 RAGAS

在 `ragenteval-main/.env` 填评分 Key、Base URL、Judge 和评分 Embedding。`AIHUBMIX_` 是旧变量名，实际调用你填写的服务。OhMyGPT 本机地址为 `https://apic1.ohmycdn.com/v1`，不要加 `/chat/completions`；后台会追加具体接口路径。评分需要该站可用的聊天和 Embedding 模型。

本轮重试命令从项目根目录执行：

```powershell
Push-Location ragenteval-main
$env:PYTHONUTF8 = '1'
.\.venv-ragas\Scripts\python.exe -m eval.v2.score_answers --run-dir eval/runs/v2_rag_rag_20261010_191926_212903 --output-dir eval/runs/v2_rag_rag_20261010_191926_212903/answer_correctness_retry_20261010_2
Pop-Location
```

入口复用保存的回答，不重新跑 180 条问答或向量化文档。160 条有指定来源的知识题进入 `answer_correctness`，其余 20 条另按行为要求检查；只评答案与参考内容的事实、语义一致性，不评真实证据忠实度或 Agentic 任务成功率。评分逐题保存、支持断点续跑，模型或来源变化会拒绝混用。

独立环境安装和文件说明见 [评分说明](ragenteval-main/README.md#复用保存的回答进行-ragas-评分)。本机最新重试产物：[评分摘要](ragenteval-main/eval/runs/v2_rag_rag_20261010_191926_212903/answer_correctness_retry_20261010_2/summary.json)、[评分报告](ragenteval-main/eval/runs/v2_rag_rag_20261010_191926_212903/answer_correctness_retry_20261010_2/report.md)。**本次补评分已按用户要求停止，完成 6 / 160 条，154 条未完成，不提供新的全量均分。**原始 180 条 RAG 和 24 个 Agentic 场景的录制与检索报告保留。

旧 20 条对照的 RAGAS 口径已离线核实：第一轮全部 20 条均分 0.660，第二轮 18 条均分 0.758；按两轮共同可评分的 18 条重算，均分为 **0.699 → 0.758**。两轮题目、参考答案与目标文档一致；若引用该提升，应注明共同样本数 18，不能与本轮题集混算。

### 待补：验证外层 Agent 对同一批问题的作用

此前 180 条是固定 RAG 基线，24 个 Agentic 场景是另一批问题，不能用两组结果证明外层 Agent 相对固定 RAG 的收益。用户目标是验证 Agent 的作用，需要补跑外层 Agent 对相同 180 条的回答，复用原固定 RAG 记录逐题对照。

2026-10-10 补跑预检发生连接失败，已确认 `http://localhost:9090/api/ragent` 地址正确，但主应用 9090 与 MCP 9099 均未监听；尚未开始这组在线测试。先在 IDE 启动 `McpServerApplication` 和 `RagentApplication`，再从根目录执行：

```powershell
Push-Location ragenteval-main
$env:PYTHONUTF8 = '1'
python -m eval v2 preflight --mode agent
python -m eval v2 run --mode agent --suite rag
Pop-Location
```

这个入口不重新跑固定 RAG，也不执行 RAGAS。新结果写到 `eval/runs/v2_agent_rag_<时间>/`，保存题目快照、最终回答、实际工具事件、工具结果及耗时。与原基线 `v2_rag_rag_20261010_191926_212903` 按 case_id 对照：

| 检查 | 判定重点 |
| --- | --- |
| 22 条跨文档问题 | 对照参考原文审核各子问题是否答全；首次返回不足时，有没有针对缺项再次查询 |
| 8 条知识不足问题 | 是否承认资料缺失、控制补查次数，而不是补写无依据事实 |
| 8 条范围外、4 条闲聊/文字任务 | 是否选择合适路线，避免无意义检索 |
| 138 条单文档问题 | 是否保持答案正确和覆盖，避免为了 Agent 化增加无收益的调用 |
| 过程与性能 | 实际知识/业务工具调用、失败与后续动作、完整答案耗时；首字可能只是开场白 |

自动报告的过程约束通过不等于语义成功。多次查询也不能自动当作失败恢复：要检查工具输入输出是否表明针对缺项补查；记录不足时标记无法判定。Agent 入口不借用独立 `/rag/eval` 来冒充实际检索证据。资料快照、人设、模型和长期记忆条件变化须注明，必要时重新建立同条件基线。


### 已先修改 Agent 决策场景

新版 [agentic_set_v3.jsonl](ragenteval-main/eval/agent/dataset/agentic_set_v3.jsonl) 共40场景、48轮：路由4、澄清4、跨文档覆盖8、受控反馈恢复6、多轮续接4、知识与业务组合4、确认6、合理停止4。每个场景有通过条件、失败表现、证据覆盖清单或实际业务事实要求，以及工具预算。

核心判据是“首次充分可以结束；首次确有可检索缺项时应针对性补查；仍无依据时合理停止或澄清”，不是要求每题多查几次。8个跨文档问题与原固定RAG基线完全相同，可直接对照原回答。

当前34个自然场景（42轮）可由现有录制器执行；6个受控场景需要工具返回注入器，尚未实现，默认跳过且写入报告，不能当作恢复通过。场景细节与模拟返回规格见 [阅读版](ragenteval-main/docs/agent-scenarios-v3.md)。本次只修改场景与离线保护，47项离线测试通过；没有运行新版在线测试。

后端启动后，从根目录执行新版场景录制，不调用RAGAS：

```powershell
Push-Location ragenteval-main
$env:PYTHONUTF8 = '1'
python -m eval v2 preflight --mode agent
python -m eval v2 run --mode agent --suite agentic
Pop-Location
```

普通180条题集及旧24场景未改写，历史运行目录保留。新版场景结果与原24场景结果应分别报告，不能直接当成同题提升。
