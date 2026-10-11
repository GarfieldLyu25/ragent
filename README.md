# Agentic RAG：项目介绍与本机启动

这是一个 Java 知识问答项目，提供固定 RAG 工作流和 AgentScope ReAct 两条入口。RAG 负责问题改写、意图解析、检索、重排及生成；外层 Agent 根据问题和工具反馈选择直接回答、澄清、调用知识工具、MCP 业务工具或 Skills，并管理多轮对话与工具确认。当前知识工具内部仍主要采用预先编排的 RAG 流程，外层 Agent 的证据补查与失败恢复效果还需要专项验证。

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
| Elasticsearch / IK | Docker `elasticsearch`，ES 与 IK 均为 `8.17.0` | `http://127.0.0.1:9200` |
| MinIO | Docker `ragent-minio` | 文件接口 `9000`，管理界面 `9001` |
| RocketMQ NameServer | Docker `rmqnamesrv` | `9876` |
| RocketMQ Broker | Docker `rmqbroker` | `10911` 等 |
| RocketMQ 控制台 | Docker `rocketmq-dashboard` | `8082`，与 Broker 共享网络 |
| Langfuse | Docker Compose `langfuse-stack`，6 个容器 | `http://localhost:3000` |
| MCP 后端 | IDE `McpServerApplication` | `http://localhost:9099` |
| 主后端 | IDE `RagentApplication` | `http://localhost:9090/api/ragent` |
| 前端 | `frontend` 中运行 Vite | `http://127.0.0.1:5173` |

**MQ 与 ES 要启动，Milvus 当前不用启动**：向量存储使用 pgvector；本地主配置已开启 ES 关键词通道，重启主应用后与向量通道共同召回。MQ 不仅用于上传文档，当前主应用启动时也会连接消息消费者。

## 每次怎么启动

**每天按这个顺序启动：基础容器 → 本地 Redis → Langfuse → MCP 后端 → 主后端 → 前端。** 当前 `agent.trace.enabled=true`，Langfuse 也要作为日常启动步骤检查；开关为 `true` 不代表它的容器已启动。

### 1. 打开 Docker Desktop，启动已有容器

在 Docker Desktop 点击已有容器的启动按钮，或者在 PowerShell 执行：

```powershell
docker start ragent-pg ragent-minio rmqnamesrv elasticsearch
docker ps
```

等 `rmqnamesrv` 显示 `healthy`，再启动 Broker 和控制台：

```powershell
docker start rmqbroker rocketmq-dashboard
```

直接启动已有容器，保留原来的数据库和文件，不重新创建。


实际导入 **70 篇文档、210 个切片**，索引计数为 **210**，Embedding 调用数 **0**。ES 只存切片文本、知识库集合、文档 ID 和切片序号；原文件继续在 MinIO，向量继续在 pgvector。回填按原切片 ID 幂等写入，可重跑以补齐/更新有效切片，不负责清理历史多余记录。结果摘要在 `tmp/es-keyword-configure.json` 和 `tmp/es-keyword-backfill.json`，不含密钥或文档正文。

### 2. 确认本地 Redis 正在运行

```powershell
Get-Service Redis
```

如果状态是 `Stopped`，在管理员 PowerShell 中执行：

```powershell
Start-Service Redis
```

### 3. 启动 Langfuse，再启动后端

Langfuse 负责记录模型调用、工具执行、耗时和 Token。它使用独立的服务地址，与模型 API 地址分别配置。

从项目根目录执行；已有容器运行时可以再次执行此命令：

```powershell
docker compose -f resources/docker/langfuse/langfuse-stack-v4.compose.yaml up -d
docker compose -f resources/docker/langfuse/langfuse-stack-v4.compose.yaml ps
```

这份编排启动 6 个容器，对宿主机发布 `3000`。PostgreSQL、Redis、ClickHouse、MinIO 应显示 `healthy`，Web 和 Worker 应正常运行。需要排查 Web 启动状态时执行：

```powershell
docker compose -f resources/docker/langfuse/langfuse-stack-v4.compose.yaml logs --tail 80 langfuse-web
```

打开 `http://localhost:3000`。本项目 Compose 首次启动预建账号 `admin@ragent.dev`、密码 `admintrace`、项目 `ragent-service`。

本地配置 `bootstrap/src/main/resources/application.yaml` 的 `agent.trace.enabled` 已设为 `true`。Trace endpoint 为 `http://localhost:3000/api/public/otel/v1/traces`，public-key/secret-key 对应该 Compose 的预置项目密钥。`capture-content=true` 时记录提示词和工具出入参，设为 `false` 时保留运行结构、耗时和 Token 等信息。

Langfuse 启动后，再按下一步启动主后端。若主后端已运行，先确认它实际加载了当前 Trace 配置；修改开关、地址或密钥后需要重启。通过 Agent 入口发起新对话，在 Langfuse 的 Tracing 页面查看新记录；之前没有采集的对话不会因为启动 Langfuse 自动补录。

ES 客户端显式复用 Spring 中的 `OpenTelemetry` 实例，使项目追踪 SDK 先于 ES transport 初始化；没有追踪实例时使用 `NoopInstrumentation`，不读取全局追踪实例。若旧代码启动时报 `GlobalOpenTelemetry 已被占用`，已确认 ES 8.17 默认 instrumentation 会提前读取全局实例并初始化为空实现，随后阻止 Langfuse SDK 注册。更新源码、重新加载 Maven 并重启，保留 `agent.trace.enabled=true`；不通过清空全局实例或关闭 Langfuse 绕过该冲突。

Langfuse 自带专用 Redis 和数据库，本项目业务 Redis 仍使用 Windows 服务。2026-10-11 只读核对时，6 个容器均在运行，4 个基础服务为 `healthy`；该快照不代表后续每次启动都已就绪。详细说明见 [Langfuse 本机部署](resources/docker/langfuse/README.md)。

### 4. 在 IDE 启动两个后端

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

### 5. 启动前端

从项目根目录进入前端，执行：

```powershell
Set-Location frontend
npm run dev
```

浏览器打开 `http://127.0.0.1:5173`，用本地已有账号登录。

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

2026-10-10 已完成 180 条固定 RAG 和旧版 24 个 Agentic 场景的全量测试，结果见下文。后续重点是验证外层 Agent 的补查作用、缩短澄清与知识工具耗时，以及补齐后台售后审核，具体范围见“当前问题与后续演进”。

项目细节见 [系统设计](docs/Agentic-RAG设计架构.md)；原上游介绍保留在 [docs/README-upstream.md](docs/README-upstream.md)。

## 全量测试命令与本次结果

### 只测高难度 Agent 场景并保存过程

当前新增一套独立的 **8 场景、22 轮** 小测试集，不替换原 180 条或 40 场景。覆盖赠品/发票退货、保护壳需求续接与机型纠正、二手保修、迁移条件变化、HomePod 联动、及时澄清和未知统计的合理停止。题目及逐轮判据见 [难题阅读版](ragenteval-main/docs/agent-hard-cases.md)，数据文件见 [agentic_hard_v1.jsonl](ragenteval-main/eval/agent/dataset/agentic_hard_v1.jsonl)。

主后端、MCP、模型与演示数据可用后，从根目录执行：

```powershell
Push-Location ragenteval-main
$env:PYTHONUTF8 = '1'
python -m eval v2 preflight --mode agent
python -m eval v2 run --mode agent --suite hard
Pop-Location
```

仅试第一个场景可在 run 命令末尾加 `--limit 1`。每个场景新建会话，场景内复用会话。题目都只咨询，遇到意外操作确认会自动拒绝并结束该场景；工具预算用于评测判定，不是服务端运行时拦截。

结果在 `ragenteval-main/eval/runs/v2_agent_hard_<时间>/`：`cases.jsonl` 保存题目与来源，`records.jsonl` 保存已录制轮次；`traces/HARD-01.md` 等文件可直接阅读每轮输入、已返回的 reasoning、工具事件与结果、完整回答、调用次数和耗时；`traces/*.events.jsonl` 在请求进行中逐事件写入，中断后仍保留已观察部分。

这是完整的**可见事件过程**，不是隐藏模型状态。当前普通工具参数未通过 SSE 返回，报告会标记不可见；完整工具入参及 RAG 内部节点需结合 [Langfuse](http://localhost:3000) 的相应时间和会话 Trace。参数或首次缺项不可见时，不能宣称已验证主动补查。运行记录可能含测试账号数据，运行目录被 Git 忽略。

本次已生成测试集并做离线验证，尚未运行这 8 场景的在线测试。以下保留历史全量测试命令和结果。

题目和实际导入内容先看 [数据集阅读版](docs/evaluation-dataset-v2.md)。测试运行器从 `.env` 读取地址和账号；需要主后端、MCP、模型服务和已有演示数据可用，测试不依赖前端启动。

首次准备测试依赖：

```powershell
Push-Location ragenteval-main
python -m pip install requests python-dotenv
Pop-Location
```


历史运行第一组为 180 条固定 RAG，第二组为旧版 24 个 Agentic 场景、30 轮。当前 Agentic 默认题集有 40 场景、48 轮，其中录制 34 个自然场景、42 轮，跳过 6 个尚无注入器的受控场景。确认场景自动拒绝并清理，不批准取消订单或售后写入

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
| 答案与任务完成情况 | 尚未完成全量语义复核，不能报告答案正确率或 Agentic 任务成功率 |

主要发现：跨文档证据覆盖低于单文档；知识与业务组合中的知识工具耗时约 30–45 秒；AG-06 第二轮调用商品查询 10 次，需要工具预算；AG-22 只要求文字确认，尚未观察到系统确认事件。原始记录支持逐条复核，自动过程约束通过不代表答案正确。

本轮题集是来源派生草案，包含标题式问题，不能与旧 20 条结果直接计算提升幅度。检索分数来自独立 `/rag/eval`，不是生成实际使用的证据；两组首字耗时也不是同题集架构对照。

详细分析与简历可用表述：[全量评测分析](docs/evaluation-full-2026-10-10.md)。本机原始报告：[RAG](ragenteval-main/eval/runs/v2_rag_rag_20261010_191926_212903/report.md)、[Agentic](ragenteval-main/eval/runs/v2_agent_agentic_20261010_200758_262563/report.md)，运行目录被 Git 忽略。

### 已先修改 Agent 决策场景

新版 [agentic_set_v3.jsonl](ragenteval-main/eval/agent/dataset/agentic_set_v3.jsonl) 共40场景、48轮：路由4、澄清4、跨文档覆盖8、受控反馈恢复6、多轮续接4、知识与业务组合4、确认6、合理停止4。每个场景有通过条件、失败表现、证据覆盖清单或实际业务事实要求，以及工具预算。

核心判据是“首次充分可以结束；首次确有可检索缺项时应针对性补查；仍无依据时合理停止或澄清”，不是要求每题多查几次。8个跨文档问题与原固定RAG基线完全相同，可直接对照原回答。

当前34个自然场景（42轮）可由现有录制器执行；6个受控场景需要工具返回注入器，尚未实现，默认跳过且写入报告，不能当作恢复通过。场景细节与模拟返回规格见 [阅读版](ragenteval-main/docs/agent-scenarios-v3.md)。本次只修改场景与离线保护，47项离线测试通过；没有运行新版在线测试。

后端启动后，从根目录执行新版场景录制：

普通180条题集及旧24场景未改写，历史运行目录保留。新版场景结果与原24场景结果应分别报告，不能直接当成同题提升。

## 当前问题与后续演进

以下记录截至 2026-10-11 的已知缺口和后续目标，不代表已经实现或通过验证。

### 1. 补齐后台售后审核

当前已实现用户确认、MCP 售后申请落库和售后进度查询。用户点击确认只是授权 Agent 提交申请，不等于商家审核通过。`apply_after_sale` 向业务库 `ragent_bit.t_after_sale` 写入状态为“待处理”的售后单；当前没有后台售后管理页面、审核通过/驳回接口，也没有寄回、验收、退款等后续状态推进。

工具描述和回执中的“进入人工审核”“审核通过后退款”是预期流程文案，尚未接上实际审核系统；管理看板的“人工确认”统计的是用户工具授权。

后续需要售后列表与详情、管理员审核权限、通过/驳回及原因、审核人和时间记录，并按售后类型推进后续状态。重复审核应避免重复推进；演示退款与真实支付退款需要明确区分。

验收目标：用户提交后，管理员能看到待处理售后单；管理员审核后，用户通过 `query_after_sale` 查到相应状态和结果。审核通过不能直接等同于退款完成。

### 2. 让歧义澄清更早发生

在“这个还能退吗？”的单次对话记录中，Agent 第一轮已发现商品不明确，但先查询订单和知识库，再询问用户是哪一件；知识工具耗时约 30 秒，最终澄清回复约 40 秒才出现。这是单次观察，不是全量耗时统计。

后续应先利用已有上下文定位商品；仍不明确时及时澄清，必要时只查询最近订单辅助选择。确认具体商品后，再查询签收时间、激活/使用情况、退货原因和适用规则，区分取消订单、运输中处理与签收后售后。

验收目标：指代不明时不擅自选择订单、不提前提交操作；补充条件后的下一轮能承接原对话，不重复询问已提供的信息。

### 3. 降低知识工具耗时，并改进证据返回

历史知识与业务组合场景中，知识工具曾耗时约 30–45 秒。当前 `KnowledgeSearchFacade` 会在检索后先调用 LLM 合成答案，再交给外层 Agent 继续回答，存在两层生成的耗时；具体瓶颈仍需按 Trace 分解。

后续分别记录问题改写、意图识别、检索、重排、内部生成和外层生成的耗时，并评估让知识工具返回带来源的证据片段、覆盖情况和缺失项，由外层 Agent 组织最终回答。性能优化应同时检查答案覆盖和依据，不能只缩短首字时间。

验收目标：外层 Agent 能看到每个子问题已有和缺失的证据；报告完整回答耗时及工具耗时，首字指标注明是否仅为开场白。

### 4. 完善过程记录和工具预算

历史 AG-06 第二轮曾调用商品查询 10 次；AG-22 改地址只观察到文字确认，未观察到系统确认事件，需要检查当时工具选择与配置。新版评测已增加总工具调用预算，但评测约束不等于运行时已有相同限制。

后续补充可复核的工具输入、输出、调用 ID、失败原因和后续动作；针对重复参数、没有新增信息的查询设置运行时停止规则，并核对写操作是否触发真实确认卡片。记录不足的场景应标记无法判定。

验收目标：能解释每次补查针对什么缺项；无进展时不会持续重复调用；用户拒绝后不执行原写操作。
