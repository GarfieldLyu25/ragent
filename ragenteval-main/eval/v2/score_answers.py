"""Score saved v2 RAG answers with RAGAS; never invoke the application backend.

Only answer_correctness is evaluated. Independent retrieval contexts are not
used as generation evidence. Results are checkpointed and resumable.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import logging
import math
import os
from collections import defaultdict
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import urlsplit, urlunsplit

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def unique_by_id(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed = {item["case_id"]: item for item in items}
    if len(indexed) != len(items):
        raise ValueError("Duplicate case identifiers")
    return indexed


def prepare_inputs(cases: list[dict[str, Any]], records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Select source-labelled single-turn cases, preserving all skip reasons."""
    unique_by_id(cases)
    by_id = unique_by_id(records)
    inputs, skipped = [], []
    for case in cases:
        if not case.get("expected_doc_ids"):
            continue
        record = by_id.get(case["case_id"])
        reason = None
        turns = record.get("turns", []) if record else []
        if record is None:
            reason = "missing_record"
        elif len(turns) != 1:
            reason = "not_single_turn"
        elif turns[0].get("status") != "success":
            reason = "not_success"
        elif not turns[0].get("response", "").strip():
            reason = "empty_response"
        elif not case.get("reference", "").strip():
            reason = "empty_reference"
        if reason:
            skipped.append({"case_id": case["case_id"], "reason": reason})
            continue
        inputs.append({"case_id": case["case_id"], "category": case["category"],
                       "user_input": case["query"], "reference": case["reference"],
                       "response": turns[0]["response"]})
    return inputs, skipped


def validate_resume(expected: dict[str, Any], saved: dict[str, Any]) -> None:
    if expected != saved:
        raise ValueError("Source files or judge configuration changed; use a new output directory")


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def write_checkpoint(path: Path, rows: list[dict[str, Any]]) -> None:
    """Replace only a complete checkpoint; interruption leaves the old file valid."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n"
                                 for row in rows), encoding="utf-8")
    temporary.replace(path)


def scoring_settings(path: Path) -> dict[str, str]:
    """Use the user's evaluation file explicitly, never another inherited key."""
    from dotenv import dotenv_values
    values = dotenv_values(path, interpolate=False)
    names = ("AIHUBMIX_API_KEY", "AIHUBMIX_BASE_URL", "JUDGE_MODEL", "EMBEDDING_MODEL")
    normalized = {name: str(values.get(name) or "").strip() for name in names}
    if any(not normalized[name] for name in names):
        raise RuntimeError("Configure all four scoring settings in the evaluation .env")
    return normalized


def failure_details(error: Exception) -> dict[str, Any]:
    """Keep diagnostic status/endpoint, excluding provider response and keys."""
    details: dict[str, Any] = {"error_type": type(error).__name__}
    status = getattr(error, "status_code", None)
    if isinstance(status, int) and 100 <= status <= 599:
        details["http_status"] = status
    url = getattr(getattr(error, "request", None), "url", None)
    path = getattr(url, "path", "")
    for endpoint in ("embeddings", "chat/completions", "responses"):
        if isinstance(path, str) and path.endswith("/" + endpoint):
            details["endpoint"] = endpoint
    return details


def score_pending(items: list[dict[str, Any]], previous: dict[str, dict[str, Any]],
                  scorer: Callable[[dict[str, Any]], float]) -> Iterator[dict[str, Any]]:
    """Score pending cases serially; caller saves each result before continuing."""
    pending = [item for item in items if previous.get(item['case_id'], {}).get('status') != 'scored']
    if not pending:
        return

    def attempt(item: dict[str, Any]) -> dict[str, Any]:
        row = {'case_id': item['case_id'], 'category': item['category'],
               'scored_at': datetime.now(timezone.utc).isoformat()}
        try:
            row.update(status='scored', answer_correctness=scorer(item))
        except Exception as error:
            row.update(status='failed', answer_correctness=None, **failure_details(error))
        return row

    first = attempt(pending[0])
    yield first
    if first['status'] == 'failed':
        return
    for item in pending[1:]:
        yield attempt(item)


def score_one(item: dict[str, Any], api_key: str, base_url: str, judge_model: str, embedding_model: str) -> float:
    """Use only question, saved answer and reference for answer correctness."""
    from datasets import Dataset
    from ragas import evaluate
    from ragas.metrics import answer_correctness
    from ragas.run_config import RunConfig
    from eval.rag.metrics.ragas_judge import _build_judges

    judge, embeddings = _build_judges(api_key, base_url, judge_model, embedding_model, timeout=180)
    data = Dataset.from_dict({key: [item[key]] for key in ("user_input", "response", "reference")})
    result = evaluate(dataset=data, metrics=[copy.deepcopy(answer_correctness)], llm=judge,
                      embeddings=embeddings, show_progress=False, raise_exceptions=True,
                      run_config=RunConfig(timeout=180, max_retries=1, max_workers=1))
    score = float(result.to_pandas().iloc[0]["answer_correctness"])
    if not math.isfinite(score) or not 0 <= score <= 1:
        raise ValueError("Invalid judge score")
    return score


def save_report(output: Path, items: list[dict[str, Any]], results: dict[str, dict[str, Any]],
                excluded: int, skipped: list[dict[str, str]], signature: dict[str, Any]) -> dict[str, Any]:
    selected = {item["case_id"] for item in items}
    rows = [value for key, value in results.items() if key in selected]
    valid = [row for row in rows if row["status"] == "scored"]
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in valid:
        grouped[row["category"]].append(row["answer_correctness"])
    summary = {"metric": "answer_correctness", "requested": len(items), "scored": len(valid),
               "failed": sum(row["status"] == "failed" for row in rows),
               "pending": len(items) - len(rows), "unlabelled_excluded": excluded,
               "input_skipped": skipped,
               "overall": sum(row["answer_correctness"] for row in valid) / len(valid) if valid else None,
               "groups": {key: {"scored": len(values), "mean": sum(values) / len(values)}
                          for key, values in sorted(grouped.items())}, "signature": signature,
               "reference_status": "source_derived_draft",
               "interpretation": "Mean judge score, not percentage of correct answers; no faithfulness or Agentic task success score"}
    write_json(output / "summary.json", summary)
    report = ["# 保存回答的 RAGAS 正确性评分", "",
              "复用题目快照和原始回答，没有重新调用主后端。只评 answer_correctness，不使用旁路检索片段评忠实度。",
              "参考答案是来源派生草案，均分不等于答对题目的百分比，也不等于 Agentic 任务成功率。", "",
              "```json", json.dumps(summary, ensure_ascii=False, indent=2), "```"]
    (output / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    metadata = json.loads((args.run_dir / "metadata.json").read_text(encoding="utf-8"))
    if metadata.get("mode") != "rag" or metadata.get("suite") != "rag":
        raise ValueError("This scorer supports the single-turn RAG run only")
    cases_path, records_path = args.run_dir / "cases.jsonl", args.run_dir / "records.jsonl"
    cases, records = read_jsonl(cases_path), read_jsonl(records_path)
    inputs, skipped = prepare_inputs(cases, records)
    if not inputs:
        raise ValueError("No scoreable saved answers")
    excluded = sum(not case.get("expected_doc_ids") for case in cases)
    output = args.output_dir or args.run_dir / "answer_correctness"
    print(f"Saved answers: {len(inputs)}; unlabelled excluded: {excluded}; input skips: {len(skipped)}", flush=True)
    if args.prepare_only:
        output.mkdir(parents=True, exist_ok=True)
        write_json(output / "preparation.json", {"eligible": len(inputs), "excluded": excluded, "skipped": skipped})
        return 0

    settings = scoring_settings(PROJECT_ROOT / ".env")
    # Score only through the configured judge; avoid unrelated tracing exports.
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["RAGAS_DO_NOT_TRACK"] = "true"
    for logger_name in ("ragas", "openai", "httpx", "langchain", "langsmith"):
        logging.getLogger(logger_name).setLevel(logging.CRITICAL)
    api_key, base_url = settings["AIHUBMIX_API_KEY"], settings["AIHUBMIX_BASE_URL"]
    judge_model, embedding_model = settings["JUDGE_MODEL"], settings["EMBEDDING_MODEL"]
    url = urlsplit(base_url)
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError("Use a base URL without credentials, query or fragment")
    signature = {"cases_sha256": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
                 "records_sha256": hashlib.sha256(records_path.read_bytes()).hexdigest(),
                 "base_url": urlunsplit(url), "judge_model": judge_model,
                 "embedding_model": embedding_model, "ragas_version": version("ragas"),
                 "metric": "answer_correctness"}
    signature_path, scores_path = output / "signature.json", output / "scores.jsonl"
    if signature_path.exists():
        validate_resume(signature, json.loads(signature_path.read_text(encoding="utf-8")))
    elif scores_path.exists():
        raise ValueError("Scores exist without their source signature")
    else:
        output.mkdir(parents=True, exist_ok=True)
        write_json(signature_path, signature)
    write_checkpoint(output / "inputs.jsonl", inputs)
    history = read_jsonl(scores_path) if scores_path.exists() else []
    results = {row["case_id"]: row for row in history}
    items = inputs[:args.limit] if args.limit else inputs
    save_report(output, items, results, excluded, skipped, signature)
    print(f"Judge: {judge_model}; embedding: {embedding_model}; metric: answer_correctness", flush=True)
    reused = sum(results.get(item['case_id'], {}).get('status') == 'scored' for item in items)
    print(f"Reusing {reused} scores; serial scoring", flush=True)
    def scorer(item: dict[str, Any]) -> float:
        return score_one(item, api_key, base_url, judge_model, embedding_model)
    for attempted, row in enumerate(score_pending(items, results, scorer), 1):
        history.append(row)
        write_checkpoint(scores_path, history)
        results[row["case_id"]] = row
        save_report(output, items, results, excluded, skipped, signature)
        print(f"[{reused + attempted}/{len(items)}] {row['case_id']} {row['status']} {row['answer_correctness']}", flush=True)
        if row['status'] == 'failed':
            print(f"Failure: {row['error_type']}; HTTP={row.get('http_status')}; endpoint={row.get('endpoint')}", flush=True)
        if attempted == 1 and row["status"] == "failed":
            print("First sample failed; stop before spending on the full set. Check scoring service configuration.", flush=True)
            return 2
    summary = save_report(output, items, results, excluded, skipped, signature)
    print(f"Saved: {output}; scored={summary['scored']}; failed={summary['failed']}; mean={summary['overall']}", flush=True)
    return 0 if summary["scored"] == len(items) else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Scoring stopped: {type(error).__name__}. Check input files, dependencies and scoring configuration.", flush=True)
        raise SystemExit(2)
