"""Create reproducible source-derived evaluation drafts for the current corpus.

This is not a set of real user logs or an independently annotated holdout set.
References are local snapshot evidence, not claims about current Apple products.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from eval.v2.agent_scenarios import build_agent_scenarios

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ROOT = PROJECT_ROOT.parent
HERE = Path(__file__).resolve().parent
CORPUS = ROOT / "resources/initializer/bit-selection/docs/knowledge"


def sections(text: str) -> list[tuple[str, str]]:
    """Return nonempty leaf sections with their exact evidence text."""
    parts = re.split(r"(?m)^#{2,3} (.+)\r?\n", text)
    result = []
    for index in range(1, len(parts), 2):
        title, body = parts[index], parts[index + 1].strip()
        if len(body) > 20 and not any(label in title for label in ("相关文档", "怎么用这份文档", "排查总原则")):
            result.append((title, body))
    return result


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    """Write deterministic UTF-8 dataset records."""
    path.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")


def build() -> dict[str, Any]:
    """Build unchanged 180 RAG cases and 40 Agent decision scenarios."""
    output = PROJECT_ROOT / "eval"
    rag_path = output / "rag/dataset/eval_set_v2.jsonl"
    agent_path = output / "agent/dataset/agentic_set_v3.jsonl"
    rag_path.parent.mkdir(parents=True, exist_ok=True)
    agent_path.parent.mkdir(parents=True, exist_ok=True)
    docs = {path.stem: path for path in sorted(CORPUS.rglob("*.md"))}
    if len(docs) != 69:
        raise ValueError(f"Expected 69 unique corpus documents, found {len(docs)}")
    source_manifest = [{"doc_id": doc_id, "path": path.relative_to(ROOT).as_posix(),
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                       for doc_id, path in docs.items()]

    def span(doc_id: str, heading: str, excerpt: str) -> dict[str, Any]:
        path = docs[doc_id]
        return {"doc_id": doc_id, "path": path.relative_to(ROOT).as_posix(), "heading": heading,
                "excerpt": excerpt, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    cases: list[dict[str, Any]] = []
    for doc_id, path in docs.items():
        text = path.read_text(encoding="utf-8")
        title = text.splitlines()[0].lstrip("# ").removesuffix(" 商品详情")
        candidates = sections(text)
        faq = []
        for heading, body in candidates:
            if "常见问答" in heading:
                for line in body.splitlines():
                    cells = [cell.strip() for cell in line.strip("|").split("|")]
                    if len(cells) == 2 and cells[0].endswith(("？", "?")):
                        faq.append((cells[0], cells[1], heading))
        # Every document contributes two distinct factual tasks, not synonym repeats.
        chosen = [item for item in candidates if item[0] in ("芯片", "显示屏", "规格", "怎么选", "二、怎么选", "四、要注意的")]
        chosen += [item for item in candidates if item not in chosen and "商品概览" not in item[0]]
        if len(faq) < 2 and len(chosen) < 2:
            raise ValueError(f"Not enough evidence sections: {doc_id}")
        for number in range(2):
            if len(faq) >= 2:
                question, reference, heading = faq[number]
                query = f"关于比特严选的{title}：{question}"
            else:
                heading, reference = chosen[number]
                label = re.sub(r"^[一二三四五六七八九十]+、|^\d+(?:\.\d+)*\s*", "", heading)
                query = f"根据比特严选知识库的资料快照，{title}的“{label}”有哪些具体说明？"
            cases.append({"case_id": f"DOC-{len(cases) + 1:03d}", "suite": "rag",
                          "category": path.relative_to(CORPUS).parts[0], "difficulty": "easy",
                          "query": query, "expected_doc_ids": [doc_id], "reference": reference,
                          "source_spans": [span(doc_id, heading, reference)],
                          "gold_status": "source_derived_draft", "checks": {}})

    cross = [
        ("签收第五天，手机只拆了外包装，赠品找不到了。按本店政策如何判断无理由退货，赠品和运费怎么处理？", ["7天无理由退换政策", "退换货条件"]),
        ("手机签收第十天出现性能故障，质量退换的条件是什么，具体从哪里申请？", ["7天无理由退换政策", "退换货流程与申请方式"]),
        ("手机签收超过十五天后故障，免费保修有哪些边界，送修前要准备哪些凭证？", ["保修政策总则", "保修凭证要求"]),
        ("iPhone摔过后屏幕出现绿线，该继续排查吗？这类情况能否走免费保修？", ["iPhone常见故障排查", "保修政策总则"]),
        ("iPhone电池耗电异常应该先看哪里？在保修期内蓄电量低于80%时保修怎么判断？", ["iPhone常见故障排查", "各品类保修期与保修范围"]),
        ("手机刚更新系统后耗电快，先如何判断，更新失败有哪些恢复办法？", ["iPhone常见故障排查", "系统更新与版本管理"]),
        ("设备提示无法加入网络和已连接但无互联网分别是什么方向的问题？WiFi排查有哪些步骤？", ["常见错误提示与处理", "连接WiFi与网络异常排查"]),
        ("准备将手机寄回售后，账号、查找和数据备份该如何处理？申请入口在哪里？", ["退换货流程与申请方式", "Apple账户与iCloud基础"]),
        ("过保Mac维修如何申请，报价怎样确定？", ["过保维修政策与流程", "维修服务报价表"]),
        ("本店保修总则与各品类保修范围分别怎么规定电池及人为损坏？", ["保修政策总则", "各品类保修期与保修范围"]),
        ("商品降价该申请价保还是无理由退货？两个政策的条件分别是什么？", ["价保政策", "7天无理由退换政策"]),
        ("退货商品包装或赠品缺失如何处理？已经开了发票，退款申请还需要注意什么？", ["退换货条件", "发票开具规则"]),
        ("优惠券使用和退货时优惠分摊各有什么规则？", ["促销活动与优惠券规则", "退换货条件"]),
        ("会员积分如何获取和过期？不同会员等级有哪些权益？", ["会员积分规则与到期提醒", "会员权益体系"]),
        ("本店一般什么时候发货，配送范围与配送服务有什么要求？", ["发货时效与配送范围", "配送服务"]),
        ("物流长时间不更新或签收破损如何处理？寄回退换货走什么流程？", ["物流异常处理", "退换货流程与申请方式"]),
        ("新iPhone如何迁移数据，迁移前Apple账户和iCloud要注意什么？", ["新设备开机设置与数据迁移", "Apple账户与iCloud基础"]),
        ("iPad如何分屏？选iPad配件时怎样确认兼容性？", ["平板用户手册", "iPad配件选购指南"]),
        ("AirPods使用中连接或充电异常如何排查，日常如何清洁保养？", ["AirPods与AppleWatch常见故障排查", "日常保养与耗材更换"]),
        ("根据资料快照分别说明iPhone 17和iPhone 16的芯片与显示屏，不查询实时价格。", ["IPHONE-17", "IPHONE-16"]),
        ("根据资料快照，MacBook Air和MacBook Pro的芯片和屏幕分别有哪些说明？", ["MACBOOK-AIR", "MACBOOK-PRO"]),
        ("根据资料快照，HomePod与HomePod mini分别有哪些规格及设备联动使用说明？", ["HOMEPOD", "HOMEPOD-MINI", "设备联动"]),
    ]
    for index, (query, ids) in enumerate(cross, 1):
        evidence = []
        for doc_id in ids:
            if doc_id not in docs:
                raise ValueError(f"Missing source document: {doc_id}")
            # Full source retained so a reviewer can verify cross-document coverage.
            evidence.append(span(doc_id, "全文", docs[doc_id].read_text(encoding="utf-8")))
        cases.append({"case_id": f"CROSS-{index:02d}", "suite": "rag", "category": "cross_document",
                      "difficulty": "hard", "query": query, "expected_doc_ids": ids,
                      "reference": "\n\n".join(item["excerpt"] for item in evidence), "source_spans": evidence,
                      "gold_status": "evidence_bundle_pending_minimal_gold_review", "checks": {}})

    controls = {
        "unanswerable": ["本店iPhone 17在2026年10月每个批次的返修率是多少？", "本店全部商品昨天每小时的库存历史是多少？",
                         "本店下一代未发布手机的完整参数是什么？", "iPhone 17在本店实验室的跌落测试原始记录是什么？",
                         "本店未来三个月的进货采购合同内容是什么？", "本店专用优惠券SECRET-UNLISTED-999的使用规则是什么？",
                         "本店维修检测中心的内部故障数据库统计是什么？", "本店新品发布会的未公开排期是什么？"],
        "out_of_scope": ["给我推荐一台本店的小米扫地机器人。", "比较本店华为和三星手机，哪个好？", "本店有石头G10的维修手册吗？",
                         "查询京东上我昨天的订单。", "告诉我小米空气净化器的滤芯更换周期。", "帮我查询另一家店的赠品政策。",
                         "给我本店没有经营的牙刷型号做购买推荐。", "帮我评价某只股票是否值得购买。"],
        "chat": ["你好。", "谢谢你，暂时不用了。", "你是谁，能帮助我做什么？", "把‘感谢您的耐心等待’改成更亲切的一句话。"],
    }
    for category, questions in controls.items():
        for index, query in enumerate(questions, 1):
            reference = ("直接完成问候、自我介绍或文字改写，无需检索。" if category == "chat" else
                         "如实说明本店不经营或当前无法查询，不能编造商品、订单或规则。" if category == "out_of_scope" else
                         "缺少可核实依据时明确说明无法确定，不编造统计、未发布信息或内部数据。")
            cases.append({"case_id": f"{category.upper()}-{index:02d}", "suite": "rag", "category": category,
                          "difficulty": "medium", "query": query, "expected_doc_ids": [], "reference": reference,
                          "source_spans": [], "gold_status": "behavior_rubric_pending_review", "checks": {}})

    scenarios = build_agent_scenarios(cases, docs, ROOT)
    assert len(cases) == 180 and len(scenarios) == 40
    ids = [case["case_id"] for case in cases + scenarios]
    assert len(set(ids)) == len(ids)
    assert len({case["query"] for case in cases}) == len(cases)
    for case in cases:
        for item in case["source_spans"]:
            assert item["excerpt"] in (ROOT / item["path"]).read_text(encoding="utf-8")
    write_jsonl(rag_path, cases)
    write_jsonl(agent_path, scenarios)
    from eval.v2.hard_scenarios import build_hard_scenarios, write_hard_guide
    hard = build_hard_scenarios(ROOT)
    write_jsonl(output / 'agent/dataset/agentic_hard_v1.jsonl', hard)
    write_hard_guide(output.parent / 'docs/agent-hard-cases.md', hard)
    manifest = {"version": "v2-draft", "corpus": "bit-selection-69-local-snapshot",
                "origin": "source-derived synthetic questions; no real user logs; no external benchmark",
                "rag_cases": len(cases), "agentic_scenarios": len(scenarios),
                "agentic_turns": sum(len(case["turns"]) for case in scenarios),
                "agentic_version": "v3-draft",
                "hard_scenarios": len(hard), "hard_turns": sum(len(case['turns']) for case in hard),
                "agentic_live_scenarios": sum(case["execution"]["kind"] == "live" for case in scenarios),
                "agentic_controlled_scenarios": sum(case["execution"]["kind"] == "controlled" for case in scenarios),
                "agentic_live_turns": sum(len(case["turns"]) for case in scenarios if case["execution"]["kind"] == "live"),
                "rag_categories": dict(Counter(case["category"] for case in cases)),
                "agentic_categories": dict(Counter(case["category"] for case in scenarios)),
                "documents": source_manifest}
    (output / "dataset_manifest_v2.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    result = build()
    print(json.dumps({key: value for key, value in result.items() if key != "documents"}, ensure_ascii=False, indent=2))
