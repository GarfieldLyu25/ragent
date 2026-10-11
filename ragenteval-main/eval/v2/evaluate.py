"""Record real RAG/Agent calls and report explicitly scoped evaluation metrics.

No model judge or write approval is performed by this recorder. Agentic semantic
quality remains pending review; the retrieval endpoint is a separate execution.
"""
from __future__ import annotations

import argparse
import codecs
import hashlib
import json
import math
import os
import statistics
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ROOT = PROJECT_ROOT.parent
HERE = Path(__file__).resolve().parent
DATASETS = {"rag": PROJECT_ROOT / "eval/rag/dataset/eval_set_v2.jsonl",
            "agentic": PROJECT_ROOT / "eval/agent/dataset/agentic_set_v3.jsonl",
            "hard": PROJECT_ROOT / "eval/agent/dataset/agentic_hard_v1.jsonl"}
MANIFEST = PROJECT_ROOT / "eval/dataset_manifest_v2.json"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load nonempty JSONL records."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def select_runnable_cases(cases: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Skip controlled fixtures until an actual injection adapter is implemented."""
    runnable, skipped = [], []
    for case in cases:
        execution = case.get("execution", {})
        if execution.get("kind", "live") == "live" and execution.get("ready", True):
            runnable.append(case)
        else:
            skipped.append({"case_id": case["case_id"], "reason": execution.get(
                "skip_reason", "Execution requires an unsupported controlled fixture or is not ready")})
    return runnable, skipped


def sse_events(chunks: Iterable[bytes]) -> Iterator[tuple[str, str]]:
    """Decode incremental UTF-8 and SSE lines, including CRLF and multiline data."""
    decoder = codecs.getincrementaldecoder("utf-8")()
    buffer = ""
    event = "message"
    data: list[str] = []

    def lines() -> Iterator[str]:
        nonlocal buffer
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            yield line.rstrip("\r")

    for chunk in chunks:
        buffer += decoder.decode(chunk)
        for line in lines():
            if not line:
                if data:
                    yield event, "\n".join(data)
                event, data = "message", []
            elif not line.startswith(":"):
                key, sep, value = line.partition(":")
                if sep:
                    value = value.removeprefix(" ")
                    if key == "event":
                        event = value
                    elif key == "data":
                        data.append(value)
    # An event without its empty-line terminator is incomplete and not dispatched.
    buffer += decoder.decode(b"", final=True)


def collect_stream(chunks: Iterable[bytes], clock: Callable[[], float] = time.monotonic,
                   started: float | None = None,
                   on_event: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """Collect answer, actual tool events, IDs and terminal state from one stream."""
    started = clock() if started is None else started
    result: dict[str, Any] = {"response": "", "reasoning": "", "tools": [], "events": [], "meta": {},
                              "started_at": datetime.now(timezone.utc).isoformat(), "completion": {},
                              "status": "incomplete", "ttft_ms": None, "confirm": None}
    def append_event(event: str, payload: Any) -> None:
        observed = {"seq": len(result["events"]) + 1,
                    "elapsed_ms": round((clock() - started) * 1000), "event": event, "data": payload}
        result["events"].append(observed)
        if on_event is not None:
            on_event(observed)

    def observed_events() -> Iterator[tuple[str, str]]:
        try:
            yield from sse_events(chunks)
        except (requests.RequestException, UnicodeDecodeError, OSError) as exc:
            yield "transport_error", json.dumps({"error_type": type(exc).__name__})

    finished = done = failed = cancelled = False
    for event, raw in observed_events():
        if event == "done":
            append_event(event, raw)
            done = True
            # Server releases its per-user gate before closing the SSE response.
            continue
        if raw == "[DONE]":
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"raw": raw}
        append_event(event, payload)
        if not isinstance(payload, dict):
            failed = True
            continue
        if event == "meta":
            result["meta"] = payload
        elif event == "message":
            if payload.get("type") in ("think", "reasoning"):
                result["reasoning"] += str(payload.get("delta", ""))
            if payload.get("type") == "response" and payload.get("delta"):
                if result["ttft_ms"] is None:
                    result["ttft_ms"] = round((clock() - started) * 1000)
                result["response"] += payload["delta"]
            elif payload.get("type") == "error":
                failed = True
        elif event == "tool":
            result["tools"].append(payload)
        elif event == "confirm":
            result["confirm"] = payload
        elif event == "finish":
            result["completion"] = payload
            finished = True
            if payload.get("messageStatus") not in (None, "NORMAL"):
                failed = True
        elif event in ("error", "reject", "transport_error"):
            failed = True
        elif event == "cancel":
            cancelled = True
    result["latency_ms"] = round((clock() - started) * 1000)
    if failed:
        result["status"] = "error"
    elif cancelled:
        result["status"] = "cancelled"
    elif result["confirm"] and done:
        result["status"] = "wait_confirm"
    elif finished and done and result["response"].strip():
        result["status"] = "success"
    return result


def retrieval_metrics(cases: list[dict[str, Any]], records: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute document metrics only for labelled, successfully observed retrievals."""
    by_id = {record["case_id"]: record for record in records}
    values: dict[str, list[float]] = {"hit@1": [], "hit@5": [], "recall@5": [], "mrr@10": []}
    eligible = errors = unrecorded = 0
    for case in cases:
        gold = set(case.get("expected_doc_ids", []))
        if not gold:
            continue
        eligible += 1
        record = by_id.get(case["case_id"])
        if record is None:
            unrecorded += 1
            continue
        if record.get("retrieval_error") or "retrieval" not in record:
            errors += 1
            continue
        docs = list(dict.fromkeys(record["retrieval"].get("retrievedDocIds", [])))
        values["hit@1"].append(float(bool(gold.intersection(docs[:1]))))
        values["hit@5"].append(float(bool(gold.intersection(docs[:5]))))
        values["recall@5"].append(len(gold.intersection(docs[:5])) / len(gold))
        values["mrr@10"].append(next((1 / rank for rank, doc in enumerate(docs[:10], 1) if doc in gold), 0.0))
    return {"eligible": eligible, "scored": len(values["hit@1"]), "retrieval_errors": errors, "unrecorded": unrecorded,
            **{key: statistics.mean(value) if value else None for key, value in values.items()}}


def structural_checks(case: dict[str, Any], turns: list[dict[str, Any]]) -> dict[str, Any]:
    """Check observable constraints without converting them into semantic success."""
    checks = case.get("checks", {})
    tools = [tool for turn in turns for tool in turn.get("tools", [])]
    tools += [tool for turn in turns for tool in turn.get("denial_cleanup", {}).get("tools", [])]
    invoked = {tool.get("name") for tool in tools}
    succeeded = {tool.get("name") for tool in tools if tool.get("status") == "done" and tool.get("ok") is not False}
    result: dict[str, Any] = {
        "forbidden_tools_absent": not invoked.intersection(checks.get("forbidden_tools", [])),
        "required_tools_succeeded": set(checks.get("required_tools", [])).issubset(succeeded),
        "terminal_states_valid": bool(turns) and all(
            turn["status"] == "success" or (turn["status"] == "wait_confirm" and checks.get("expect_confirm"))
            for turn in turns),
        "semantic_verdict": "pending_review",
    }
    if checks.get("expect_confirm"):
        observed = any(turn.get("confirm") or turn["status"] == "wait_confirm" for turn in turns)
        result["confirmation_observed"] = True if observed else (None if checks.get("confirmation_conditional") else False)
    confirmed = [turn for turn in turns if turn.get("confirm")]
    if confirmed:
        result["confirmation_cleanup_completed"] = all(
            turn.get("denial_cleanup", {}).get("status") == "success"
            and not turn["denial_cleanup"].get("confirm") for turn in confirmed)
    if "forbidden_executed_tools" in checks:
        forbidden = set(checks["forbidden_executed_tools"])
        result["forbidden_tool_execution_absent"] = not any(
            tool.get("name") in forbidden and tool.get("status") not in ("pending", "awaiting", "denied")
            for tool in tools)
    if "max_search_calls" in checks:
        searches = [tool for tool in tools if tool.get("name") == "search_knowledge"]
        observed = all(tool.get("toolCallId") for tool in searches)
        count = len({tool["toolCallId"] for tool in searches if tool.get("toolCallId")})
        result["search_calls_observable"] = observed
        result["search_budget_respected"] = observed and count <= checks["max_search_calls"]
    if "max_total_calls" in checks:
        observed = all(tool.get("toolCallId") for tool in tools)
        count = len({tool["toolCallId"] for tool in tools if tool.get("toolCallId")})
        result["tool_calls_observable"] = observed
        result["total_tool_budget_respected"] = observed and count <= checks["max_total_calls"]
    if "max_product_calls" in checks:
        products = [tool for tool in tools if tool.get("name") == "search_product"]
        observed = all(tool.get("toolCallId") for tool in products)
        count = len({tool["toolCallId"] for tool in products if tool.get("toolCallId")})
        result["product_calls_observable"] = observed
        result["product_budget_respected"] = observed and count <= checks["max_product_calls"]
    return result


def record_case(client: Client, case: dict[str, Any], mode: str,
                on_event: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """Record a case, preserving partial observations and denying any observed confirmation."""
    if select_runnable_cases([case])[1]:
        raise RuntimeError("Controlled scenario requires an implemented tool-return injection adapter.")
    turns: list[dict[str, Any]] = []
    conversation = None
    questions = case.get("turns", [case.get("query")])
    for index, question in enumerate(questions, 1):
        request_started = time.monotonic()
        request_started_at = datetime.now(timezone.utc).isoformat()
        def sink(stage: str) -> Callable[[dict[str, Any]], None]:
            def observed(event: dict[str, Any]) -> None:
                if on_event is not None:
                    on_event({"case_id": case["case_id"], "turn_index": index,
                              "question": question, "stage": stage, **event})
            return observed
        def failed_request(exc: Exception, stage: str, began: float) -> dict[str, Any]:
            elapsed = round((time.monotonic() - began) * 1000)
            event = {"event": "request_error", "seq": 1, "elapsed_ms": elapsed,
                     "data": {"error_type": type(exc).__name__}}
            if on_event is not None:
                sink(stage)(event)
            return {"status": "error", "error_type": type(exc).__name__, "tools": [],
                    "events": [event], "latency_ms": elapsed}
        if on_event is not None:
            sink("request")({"event": "request", "data": {"mode": mode, "conversationId": conversation},
                             "seq": 0, "elapsed_ms": 0,
                             "started_at": request_started_at})
        try:
            options = {"on_event": sink("chat")} if on_event is not None else {}
            turn = dict(client.chat(question, mode, conversation, **options))
        except (requests.RequestException, ValueError, RuntimeError, KeyError) as exc:
            turns.append({"question": question, "turn_index": index, "started_at": request_started_at,
                          **failed_request(exc, 'chat', request_started)})
            break
        turn.update(question=question, turn_index=index, request_conversation_id=conversation)
        turns.append(turn)
        conversation = turn.get("meta", {}).get("conversationId") or conversation
        if turn.get("confirm"):
            denial_started = time.monotonic()
            try:
                options = {"on_event": sink("denial_cleanup")} if on_event is not None else {}
                turn["denial_cleanup"] = client.deny_confirmation(turn, **options)
            except (requests.RequestException, ValueError, RuntimeError, KeyError) as exc:
                turn["denial_cleanup"] = failed_request(exc, 'denial_cleanup', denial_started)
            break
        if turn["status"] != "success" or not conversation:
            break
    record: dict[str, Any] = {"case_id": case["case_id"], "turns": turns, "checks": structural_checks(case, turns)}
    record["checks"]["all_planned_turns_recorded"] = len(turns) == len(questions)
    if len(questions) > 1:
        ids = [turn.get("meta", {}).get("conversationId") for turn in turns]
        record["checks"]["conversation_continuity_valid"] = len(ids) == len(questions) and all(ids) and len(set(ids)) == 1
    if mode == "rag":
        try:
            record["retrieval"] = client.get("/rag/eval", question=case["query"])
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            record["retrieval_error"] = type(exc).__name__
    return record


class Client:
    """Authenticated HTTP access, with no credentials printed or persisted."""

    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.session = requests.Session()
        password = os.environ.get("RAGENT_PASSWORD")
        if not password:
            raise RuntimeError("Set RAGENT_PASSWORD; RAGENT_USERNAME defaults to admin.")
        response = self.session.post(f"{self.base}/auth/login", json={
            "username": os.environ.get("RAGENT_USERNAME", "admin"), "password": password}, timeout=(5, 15))
        response.raise_for_status()
        envelope = response.json()
        token = (envelope.get("data") or {}).get("token")
        if not envelope.get("success") or not token:
            raise RuntimeError("Login failed; check account/password.")
        self.session.headers.update({"Authorization": token})

    def get(self, path: str, **params: Any) -> Any:
        """Read a successful JSON envelope."""
        response = self.session.get(f"{self.base}{path}", params=params, timeout=(5, 180))
        response.raise_for_status()
        envelope = response.json()
        if not envelope.get("success"):
            raise RuntimeError(f"API returned unsuccessful envelope: {path}")
        return envelope.get("data")

    def chat(self, question: str, mode: str, conversation: str | None = None,
             on_event: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
        """Record one turn; always close streams and preserve interrupted state."""
        params = {"question": question}
        if conversation:
            params["conversationId"] = conversation
        started = time.monotonic()
        with self.session.get(f"{self.base}/{'agent/v1' if mode == 'agent' else 'rag/v3'}/chat",
                              params=params, stream=True, timeout=(5, 900)) as response:
            response.raise_for_status()
            if "text/event-stream" not in response.headers.get("Content-Type", ""):
                raise RuntimeError("Chat endpoint did not return SSE.")
            return collect_stream(response.iter_content(chunk_size=1), started=started, on_event=on_event)

    def deny_confirmation(self, turn: dict[str, Any],
                          on_event: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
        """Release a pending confirmation by explicitly declining, never approving."""
        with self.session.post(f"{self.base}/agent/v1/chat/confirm", json={
            "conversationId": turn["meta"]["conversationId"],
            "messageId": turn["confirm"]["messageId"], "approved": False},
                stream=True, timeout=(5, 900)) as response:
            response.raise_for_status()
            return collect_stream(response.iter_content(chunk_size=1), on_event=on_event)


def all_pages(client: Client, path: str) -> list[dict[str, Any]]:
    """Read paginated objects without assuming a server page-size cap."""
    records: list[dict[str, Any]] = []
    for page in range(1, 1000):
        data = client.get(path, current=page, size=100)
        batch = data.get("records", [])
        records.extend(batch)
        if not batch or len(records) >= data.get("total", len(records)):
            return records
    raise RuntimeError("Pagination exceeded safety bound.")


def preflight(client: Client, mode: str) -> dict[str, Any]:
    """Check installed corpus and mode before spending tokens on a suite."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for source in manifest["documents"]:
        actual = hashlib.sha256((ROOT / source["path"]).read_bytes()).hexdigest()
        if actual != source["sha256"]:
            raise RuntimeError(f"Local corpus changed; rebuild/review dataset: {source['path']}")
    bases = all_pages(client, "/knowledge-base")
    documents = [doc for base in bases for doc in all_pages(client, f"/knowledge-base/{base['id']}/docs")]
    usable = {Path(doc["docName"]).stem for doc in documents
              if doc.get("enabled") is True and doc.get("status") == "success" and doc.get("chunkCount", 0) > 0}
    missing = {source["doc_id"] for source in manifest["documents"]} - usable
    if missing:
        raise RuntimeError(f"Corpus not ready; missing/disabled/unparsed docs: {sorted(missing)}")
    meta = client.get("/agent/v1/meta") if mode == "agent" else None
    return {"usable_documents": len(usable), "agent_meta": meta,
            "corpus_check": "names_status_chunks_only; remote content hashes not verified"}


def report(run_dir: Path) -> None:
    """Report execution and structural metrics; keep semantic scores explicitly pending."""
    meta = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    cases = read_jsonl(run_dir / "cases.jsonl")
    records = read_jsonl(run_dir / "records.jsonl")
    from eval.v2.trace_report import write_case_trace
    by_id = {case["case_id"]: case for case in cases}
    for record in records:
        case_id = record["case_id"]
        # Only dataset IDs are used for output filenames; old reports remain supported.
        safe_id = ''.join(char for char in case_id if char.isalnum() or char in '-_')
        write_case_trace(run_dir / 'traces' / f'{safe_id}.md', by_id[case_id], record)
    statuses = Counter(turn["status"] for record in records for turn in record["turns"])
    ttfts = sorted(turn["ttft_ms"] for record in records for turn in record["turns"]
                   if turn["status"] == "success" and turn.get("ttft_ms") is not None)
    retrieval = retrieval_metrics(cases, records) if meta["mode"] == "rag" else None
    groups: dict[str, Any] = {}
    skipped_ids = {case["case_id"] for case in meta.get("skipped_cases", [])}
    for category in sorted({case["category"] for case in cases}):
        ids = {case["case_id"] for case in cases if case["category"] == category}
        subset = [record for record in records if record["case_id"] in ids]
        groups[category] = {"planned": len(ids), "recorded": len(subset),
                            "skipped": len(ids.intersection(skipped_ids)),
                            "structurally_assessable": sum(
                                all(isinstance(value, bool) for key, value in record["checks"].items() if key != "semantic_verdict")
                                for record in subset),
                            "structural_all_checks_pass": sum(
                                all(value is True for key, value in record["checks"].items() if key != "semantic_verdict")
                                for record in subset)}
    result = {"planned_cases": len(cases), "recorded_cases": len(records), "turn_statuses": dict(statuses),
              "skipped_cases": meta.get("skipped_cases", []),
              "scheduled_cases": len(cases) - len(skipped_ids),
              "retrieval_separate_execution": retrieval, "groups": groups,
              "ttft_p50_ms": statistics.median(ttfts) if ttfts else None,
              "ttft_p95_ms": ttfts[math.ceil(0.95 * len(ttfts)) - 1] if ttfts else None,
              "answer_quality": "pending_review_or_judge", "agentic_task_success": "pending_review"}
    (run_dir / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    text = ["# 评测录制报告", "", f"模式：{meta['mode']}；题集：{meta['suite']}。",
            f"计划 {len(cases)} 个案例，已录制 {len(records)} 个。", "",
            "以下数字描述执行状态和结构约束，不代表答案正确或 Agentic 任务成功。", "",
            "```json", json.dumps(result, ensure_ascii=False, indent=2), "```", "",
            "RAG 检索指标来自另一次 /rag/eval 调用，不能作为该答案实际使用的证据。",
            "Agent 模式不借用旁路检索计算 Hit/Recall/MRR，也不据此评忠实度。",
            "请逐项对照 cases.jsonl 的参考资料、判定标准，以及 records.jsonl 的回答和工具事件复核。",
            "逐场景过程阅读版在 traces/*.md；实时事件在 traces/*.events.jsonl（新录制才有）。",
            "reasoning 仅保存服务已返回内容；参数未返回时显式标记不可见，内部节点详情需结合 Langfuse。",
            "确认案例只观察请求并自动拒绝确认；不验证写入成功、确认后核验或故障注入恢复。"]
    if meta.get("skipped_cases"):
        text += ["", "受控场景因没有工具返回注入器而跳过；不能将未触发恢复的案例报告为成功。",
                 "```json", json.dumps(meta["skipped_cases"], ensure_ascii=False, indent=2), "```"]
    (run_dir / "report.md").write_text("\n".join(text) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    """CLI for read-only preflight, serial recording, and offline reports."""
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "preflight", "run", "report"])
    parser.add_argument("--base-url", default=os.environ.get("RAGENT_BASE_URL", "http://localhost:9090/api/ragent"))
    parser.add_argument("--mode", choices=["rag", "agent"], default="rag")
    parser.add_argument("--suite", choices=["rag", "agentic", "hard"], default="rag")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--run-dir", type=Path)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        from eval.v2.build_dataset import build
        manifest = build()
        print(json.dumps({key: value for key, value in manifest.items() if key != "documents"}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "report":
        if not args.run_dir:
            parser.error("report requires --run-dir")
        report(args.run_dir)
        return 0
    if args.suite in ("agentic", "hard") and args.mode != "agent":
        parser.error("agentic/hard suite requires --mode agent")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    client = Client(args.base_url)
    readiness = preflight(client, args.mode)
    if args.command == "preflight":
        print(json.dumps(readiness, ensure_ascii=False, indent=2))
        return 0
    cases = read_jsonl(DATASETS[args.suite])
    cases = cases[:args.limit] if args.limit else cases
    runnable, skipped = select_runnable_cases(cases)
    if not runnable:
        raise RuntimeError("Selected cases require unsupported controlled fixtures; no live case can run.")
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S_%f")
    run_dir = args.run_dir or PROJECT_ROOT / "eval/runs" / f"v2_{args.mode}_{args.suite}_{stamp}"
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "cases.jsonl").write_text("".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases), encoding="utf-8")
    metadata = {"mode": args.mode, "suite": args.suite, "started_at": stamp,
                "preflight": readiness, "retrieval_evidence": "separate_request" if args.mode == "rag" else "actual_tool_events",
                "skipped_cases": skipped,
                "dataset_sha256": hashlib.sha256(DATASETS[args.suite].read_bytes()).hexdigest()}
    (run_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    with (run_dir / "records.jsonl").open("w", encoding="utf-8") as output:
        from eval.v2.trace_report import EventJournal, write_case_trace
        for index, case in enumerate(runnable, 1):
            with EventJournal(run_dir / 'traces' / f"{case['case_id']}.events.jsonl") as journal:
                record = record_case(client, case, args.mode, on_event=journal)
            output.write(json.dumps(record, ensure_ascii=False) + "\n")
            output.flush()
            write_case_trace(run_dir / 'traces' / f"{case['case_id']}.md", case, record)
            print(f"[{index}/{len(runnable)}] {case['case_id']} {[turn['status'] for turn in record['turns']]}", flush=True)
    report(run_dir)
    print(f"Saved: {run_dir}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (requests.RequestException, RuntimeError) as error:
        # Avoid exception strings containing credentials, query strings or internal responses.
        print(f"Evaluation stopped: {type(error).__name__}. Check README prerequisites and service logs.")
        raise SystemExit(2)
