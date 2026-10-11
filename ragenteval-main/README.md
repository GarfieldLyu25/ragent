# Ragent 评测项目

这个目录是本机 Ragent 的评测项目，位置为 `ragent/ragenteval-main`。当前评测针对 Apple 授权经销商“比特严选”，分别检查普通 RAG 和外层 Agent。

Ragent 的本地启动方法见 [主项目 README](../README.md)：本地 Redis，加 Docker 中的 `ragent-pg`、`ragent-minio`、`rmqnamesrv`、`rmqbroker`；再启动 IDE 中的 `McpServerApplication` 和 `RagentApplication`。

## 已经放进来的测试数据

| 文件 | 数据量 | 用途 |
| --- | ---: | --- |
| [eval/rag/dataset/eval_set_v2.jsonl](eval/rag/dataset/eval_set_v2.jsonl) | 180 条单轮题 | RAG 与 Agent 回答同一批问题 |
| [eval/agent/dataset/agentic_set_v3.jsonl](eval/agent/dataset/agentic_set_v3.jsonl) | 40 场景、48 轮 | 当前：34 自然场景可录制，6 受控恢复场景暂跳过 |
| [eval/agent/dataset/agentic_set_v2.jsonl](eval/agent/dataset/agentic_set_v2.jsonl) | 24 场景、30 轮 | 历史已测试版本，保留供复核 |
| [eval/dataset_manifest_v2.json](eval/dataset_manifest_v2.json) | 69 个来源文档 | 路径、SHA-256 和类别统计 |

180 条单轮题包括：138 条单文档题、22 条跨文档题、8 条知识不足题、8 条经营范围外题、4 条闲聊或文字任务。单文档题覆盖全部 69 篇资料，每篇两个不同问题。

这些题由当前项目资料派生，来源是 `../resources/initializer/bit-selection/docs/knowledge/`；Agentic 场景同时参考该模板的提示词、Skills 和演示业务。知识题保留参考原文及来源哈希，场景保留用户轮次和判定标准。

RAG 题集标记为 **`v2-draft`**，当前 Agent 场景标记为 **`v3-draft`**，均为合成测试草案。它们不是实际用户日志，标题式题目可能使分数偏乐观。跨文档最小必需证据、等价来源和行为判据还需复核，正式评价泛化效果应增加独立保留集。

旧的小米题集、`knowledge_base/` 和历史报告仍保留为 v1 资料，原说明见 [docs/README-v1.md](docs/README-v1.md)。当前 v2 使用自己的入口和题集，不能把新旧分数直接比较。

## 测试前要有什么

- 主后端在线：`http://localhost:9090/api/ragent`。
- `ragent.engine.type=agent`、`ragent.eval.enabled=true`。
- 当前启用“比特严选”人设，知识资料已入库且有切片。
- Agent 业务场景需要 MCP 在线和测试账号的演示订单。
- 模型、Embedding 和 Rerank 的密钥已在后端配置。

2026-10-10 已导入并核对业务数据：商品 695、SKU 1,420、订单 8、优惠券 7。4 个演示知识库、意图、Skills、人设和示例问题也已创建。69 篇演示文档均已成功处理，生成 208 个切片和 208 条 1536 维向量。另有个人 PDF 已成功生成 2 个切片，当前预检报告 70 篇可用文档，数据库共 210 条向量。日常运行评测无需重复导入。

## 怎么运行

以下命令在 **`ragenteval-main` 目录**执行。从主项目根目录先进入：

```powershell
Set-Location ragenteval-main
python -m pip install requests python-dotenv
$env:PYTHONUTF8 = '1'
```

### 1. 离线准备与自测

不调用后端或模型：

```powershell
python -m eval v2 prepare
python -m unittest discover -s eval/v2 -p 'test_*.py'
```

`prepare` 重新生成题集并检查数量、唯一 ID、唯一问题和引用原文。资料变化后应重新审核金标准。离线自测覆盖事件解码、真实终止协议、异常、确认清理、检索分母、多轮状态和 CLI，不代表在线效果已通过。

### 2. 登录配置和预检

自动加载当前目录的 `.env`，使用其中的 `RAGENT_BASE_URL`、`RAGENT_USERNAME` 和 `RAGENT_PASSWORD`。也可在 PowerShell 中设置，这些环境变量优先：

```powershell
$env:RAGENT_BASE_URL = 'http://localhost:9090/api/ragent'
$env:RAGENT_USERNAME = 'admin'
$env:RAGENT_PASSWORD = 'admin'  # 默认演示账号；已有环境填写实际密码
python -m eval v2 preflight --mode agent
```

账号密码不写入录制结果。预检检查本地来源哈希与远端文档名称、启用状态、成功状态和切片数；远端正文哈希尚未核对。还要确认人设、业务夹具和账号一致。

### 3. 先跑小样本

```powershell
python -m eval v2 run --mode rag --suite rag --limit 5
python -m eval v2 run --mode agent --suite agentic --limit 4
```

确认服务、协议、真实工具名和结果正常后扩大。

### 4. 全量录制三组

```powershell
# 固定RAG工作流，180条
python -m eval v2 run --mode rag --suite rag

# 外层Agent回答同一批180条
python -m eval v2 run --mode agent --suite rag

# 当前Agent场景：40个计划场景，默认录制34个自然场景、42轮；6个受控场景明确跳过
python -m eval v2 run --mode agent --suite agentic
```

串行运行，避免同一账号的运行占位影响结果。每个案例独立会话，多轮案例沿用本案例的会话。确认场景只观察并自动拒绝确认，不批准取消、支付或其他业务写入；状态不满足办理条件时，确认约束标记待复核。

## 结果在哪里

每轮保存到 `eval/runs/v2_<模式>_<题集>_<时间>/`：

- `metadata.json`：模式、题集哈希和预检。
- `cases.jsonl`：本轮题目快照。
- `records.jsonl`：回答、真实 SSE/工具事件、会话标识、确认清理与执行状态。
- `summary.json` / `report.md`：执行、结构约束及独立检索统计。

重新计算报告，不重复调用模型：

```powershell
python -m eval v2 report --run-dir eval/runs/<实际运行目录>
```

## RAG 指标与 Agentic 检查怎么区分

| 层次 | 自动记录或计算 | 结论边界 |
| --- | --- | --- |
| 普通 RAG 检索 | Doc Hit@1/5、Recall@5、MRR@10 | 独立 `/rag/eval` 文档级结果；有效分母、执行错误与未录制样本分开 |
| 在线执行 | 完成、异常、不完整、等待确认，正式回答首字耗时 | 完成不等于答案正确；没有首字时不以总耗时代替 |
| Agent 过程 | 实际工具成功、禁用工具、调用次数、确认和清理、多轮会话 | 结构约束通过不等于任务成功 |
| 生成质量 | 回答、参考资料、实际工具结果供复核 | v2 尚未接 RAGAS，明确保留待人工/独立 Judge 评分 |
| Agentic 语义 | 按场景判据复核澄清、续接和缺项处理 | `semantic_verdict=pending_review`，不虚报任务成功率 |

普通 RAG 的生成与 `/rag/eval` 是两次独立检索，旁路 contexts 不能当作生成实际使用的证据。Agent 模式不借旁路计算检索分数或忠实度；知识工具返回目前是合成文本，也不等于原始检索片段。

本版尚未覆盖可控工具故障注入、长上下文恢复、确认后写入与业务核验。

## 2026-10-10 全量实测

本轮实际运行固定 RAG 180 条和 Agentic 24 场景（30 轮），没有运行上述可选的“Agent 回答同一批 180 条”对照组。

- RAG：180 条执行完成；160 条独立检索计分，Doc Hit@1 97.50%、Hit@5 100%、Recall@5 96.98%、MRR@10 0.9875；首字 P50 / P95 为 4.013 / 5.581 秒。
- 跨文档：22 条 Recall@5 为 78.03%，10 条前五未覆盖全部指定来源；需审核等价证据并改进分支覆盖。
- Agentic：27 轮正常结束、3 轮系统确认并拒绝清理；23 场景自动过程约束可判定且通过，AG-22 的系统确认未判定。首字 P50 / P95 为 8.678 / 38.043 秒。
- 完整答案质量与 Agentic 语义仍待评分；后续补跑 RAGAS 正确性保存 6 / 160 条后按用户要求停止，不能据此报告全量答案质量或任务成功率。

[题集与导入数据阅读版](../docs/evaluation-dataset-v2.md) · [详细分析](../docs/evaluation-full-2026-10-10.md)

本机报告：[RAG](eval/runs/v2_rag_rag_20261010_191926_212903/report.md)、[Agentic](eval/runs/v2_agent_agentic_20261010_200758_262563/report.md)。完整记录在相同目录 `records.jsonl`；运行文件被 Git 忽略。

## 复用保存的回答进行 RAGAS 评分

新版入口只评 `answer_correctness`，不会重新调用问答后端，也不会重新向量化知识库。指标使用 Judge 判断事实一致性，并用 Embedding 比较答案语义；不使用独立 `/rag/eval` 片段评忠实度，不代表 Agentic 任务成功率。

评分入口直接读取本目录 `.env` 的四项设置，不使用其他环境变量中的模型 Key。`AIHUBMIX_` 是历史变量名，支持你填写的 OpenAI 兼容服务。填写 Base URL，客户端自动追加 `/chat/completions`、`/embeddings`；不要填完整请求地址。例如本机使用：

```dotenv
AIHUBMIX_BASE_URL=https://apic1.ohmycdn.com/v1
AIHUBMIX_API_KEY=填写这个服务的实际密钥
JUDGE_MODEL=deepseek-flash
EMBEDDING_MODEL=text-embedding-3-large
```

配置文件被 Git 忽略；上述只是填法，不包含实际 Key。模型名必须在你使用的服务和账号下可调用。

首次安装独立环境，以下命令在本目录执行：

```powershell
python -m venv .venv-ragas
python -m pip --python .venv-ragas/Scripts/python.exe install -r requirements-ragas.txt
```

本机 Anaconda 的 `ensurepip` 曾失败，但环境内 Python 已创建；随后使用第二条命令安装成功。已有环境不需要再次安装。

本轮实际重试命令：

```powershell
$env:PYTHONUTF8 = '1'
.\.venv-ragas\Scripts\python.exe -m eval.v2.score_answers --run-dir eval/runs/v2_rag_rag_20261010_191926_212903 --output-dir eval/runs/v2_rag_rag_20261010_191926_212903/answer_correctness_retry_20261010_2
```

加 `--prepare-only` 只检查输入，不调用模型；加 `--limit 1` 只评分一条。重复同一命令会复用成功分数、重试失败项；题目、原始回答、地址或模型设置变化会拒绝混用，需指定新输出目录。首个实际尝试失败时停止，避免错误配置影响整个题集。

本轮选择 160 条有指定来源的知识题；20 条其他类型不进入该指标，需按行为要求另评。输出目录保存 `inputs.jsonl`、`signature.json`、`scores.jsonl`、`summary.json`、`report.md`；分数逐题原子保存，失败及重试历史保留，摘要按每题最新结果计数。原来的 `cases.jsonl` 和 `records.jsonl` 保持原样。

均分是模型评分，不是答对题目的比例。实际答案正确性与参考草案质量仍需人工抽查；新题集和新 Judge 不能直接与旧版 20 条结果计算提升幅度。

2026-10-10 本次补评分已按用户要求停止：160 条中保存 6 条分数，154 条未完成；未执行并发评分，不发布新的全量均分。所有成功分数和失败尝试保留，可用相同命令续跑。

旧版两轮的题目、参考答案、目标文档一致。第一轮全部 20 条答案评分均分 0.660；两轮共同可评的 18 条重算后为 **0.699 → 0.758**。旧检索对照为 Hit@1 77.8% → 100%、Recall@5 93.5% → 95.4%、MRR@10 0.889 → 1.000；应分别注明录制题数与指标分母。


## Agent 场景已改为 v3

当前入口 `--mode agent --suite agentic` 读取新版40场景。[逐场景阅读版](docs/agent-scenarios-v3.md) 列出用户轮次、通过条件、失败表现、每个子问题的来源以及受控工具返回规格。

新增8个跨文档场景与原RAG基线问题完全一致，允许首次证据充分时直接回答；首次确有可检索缺项时检查针对性补查。6个受控恢复场景分别是部分证据、首次空结果、错误机型、工具超时、证据冲突、连续无进展。当前没有注入器，因此不会执行这些场景，报告明确记录跳过原因；未触发不能声称恢复成功。

语义判定不能只靠调用次数；工具输入输出不可观察时不能猜测查询改写或补查目标。自动约束增加全部工具调用预算，按 toolCallId 去重，商品查询等同样受限，确认清理的调用也纳入统计。

本次只完成场景和离线保护，47项离线测试通过；没有跑新版在线效果测试。已验证180条RAG题与旧24场景和原运行快照的哈希一致，历史结果仍有效。需主后端9090及MCP9099在线后录制新场景。
