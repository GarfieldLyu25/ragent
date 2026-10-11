"""Agent decision evaluation draft: natural tasks plus explicit controlled probes.

Controlled tool-return fixtures are specifications, not an implemented injector.
They are never imported into the knowledge base or executed as live cases.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

WRITES = ["cancel_order", "change_address", "apply_after_sale", "set_cart_item",
          "create_order", "pay_order", "submit_ticket"]
TRACE_FIELDS = ["user_turns", "tool_inputs", "tool_outputs", "tool_call_ids",
                "tool_statuses", "final_answer", "confirmation_events", "latency"]


def build_agent_scenarios(rag_cases: list[dict[str, Any]], docs: dict[str, Path],
                          root: Path) -> list[dict[str, Any]]:
    """Build 40 scenarios; a single sufficient search is always acceptable."""
    baseline = {case["case_id"]: case for case in rag_cases}
    scenarios: list[dict[str, Any]] = []

    def evidence(doc_ids: list[str]) -> list[dict[str, Any]]:
        result = []
        for doc_id in doc_ids:
            path = docs[doc_id]
            result.append({"doc_id": doc_id, "path": path.relative_to(root).as_posix(),
                           "heading": "全文，最小必需证据待人工复核",
                           "excerpt": path.read_text(encoding="utf-8"),
                           "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        return result

    def add(category: str, turns: list[str], success: list[str], failure: list[str],
            needs: list[tuple[str, str]] | None = None, required: list[str] | None = None,
            *, baseline_id: str | None = None, max_search: int = 3, max_calls: int = 6,
            confirm: bool = False, fixture: dict[str, Any] | None = None,
            business: dict[str, Any] | None = None, unknown: list[str] | None = None) -> None:
        needs = needs or []
        doc_ids = list(dict.fromkeys(doc_id for _, doc_id in needs))
        if baseline_id:
            doc_ids = baseline[baseline_id]["expected_doc_ids"]
        checks: dict[str, Any] = {"required_tools": required or [], "forbidden_tools": WRITES,
                                  "max_search_calls": max_search, "max_total_calls": max_calls}
        if confirm:
            checks.update(expect_confirm=True, confirmation_conditional=True,
                          forbidden_tools=[], forbidden_executed_tools=WRITES)
        spans = evidence(doc_ids)
        case = {"case_id": f"AG3-{len(scenarios) + 1:02d}", "suite": "agentic",
                "scenario_version": "v3-draft", "category": category, "turns": turns,
                "success_criteria": success, "failure_criteria": failure,
                "rubric": "；".join(success), "checks": checks,
                "evidence_requirements": [{"need_id": f"E{i}", "topic": topic,
                    "expected_doc_ids": [doc_id],
                    "equivalent_evidence": "允许经人工核实的等价来源；文档名称命中不等于事实覆盖"}
                    for i, (topic, doc_id) in enumerate(needs, 1)],
                "expected_doc_ids": doc_ids, "source_spans": spans,
                "reference": "\n\n".join(span["excerpt"] for span in spans),
                "unsupported_requirements": unknown or [],
                "decision_policy": {"sufficient_first_result": "可以直接回答，不要求二次查询",
                    "insufficient_first_result": "可检索的缺项应针对性补查；补查无效或目标不明时说明缺失或澄清，不得靠推测补全",
                    "recovery_verdict": "必须观察触发条件及后续动作；未触发或输入输出不可见时不得判恢复成功"},
                "trace_review_fields": TRACE_FIELDS.copy(),
                "execution": {"kind": "controlled" if fixture else "live", "ready": not bool(fixture)},
                "preconditions": "启用比特严选人设；知识题使用当前69篇资料快照；业务题使用测试账号实际工具结果。",
                "gold_status": "agent_decision_rubric_pending_review"}
        if baseline_id:
            case["baseline_case_id"] = baseline_id
        if business:
            case["business_fixture"] = business
            case["fact_grounding"] = "actual_business_tool_result"
        if fixture:
            case["controlled_fixture"] = fixture
            case["execution"].update(required_capability="tool_result_injection",
                skip_reason="当前录制器尚未实现工具返回注入；此场景默认跳过，不报告恢复成功")
        scenarios.append(case)

    # Routing measures unnecessary work as well as choosing the correct tool.
    add("routing", [baseline["CHAT-03"]["query"]],
        ["按当前比特严选人设说明身份和服务范围，直接回答"],
        ["身份描述与当前人设不符", "调用无关知识或业务工具"], max_calls=0, max_search=0)
    add("routing", [baseline["CHAT-04"]["query"]],
        ["直接完成文字改写，不检索或查询订单"], ["把文字任务当知识或业务任务"], max_calls=0, max_search=0)
    add("routing", ["本店七天无理由的时间怎么算？我只问规则，不办理。"],
        ["用本店资料解释起算方式与适用条件", "不提交售后"], ["用其他商家规则代替本店规则", "调用写工具"],
        [("七天起算方式与适用条件", "7天无理由退换政策")], ["search_knowledge"], max_search=2)
    add("routing", ["本店iPhone 17现在有哪些配置和售价？只查询。"],
        ["查询商品工具，以实际返回说明配置和价格", "工具缺失字段明确说明未知"],
        ["把知识资料的静态价格当实时售价", "生成未经查询的库存或优惠"], required=["search_product"],
        max_calls=4, business={"entity": "iPhone 17", "expected_values": "使用实际商品工具返回，不硬编码价格"})

    add("clarification", ["这个还能退吗？", "我说iPhone 17，只拆外包装，签收三天，只咨询规则，不办理。"],
        ["第一轮澄清对象与关键条件，不擅自指定某笔订单", "第二轮利用已补充条件回答，不重复询问已知信息"],
        ["未澄清就判断具体退货资格", "代替用户编造购买或激活情况", "提交售后"],
        [("时限与适用条件", "7天无理由退换政策"), ("包装条件", "退换货条件")], ["search_knowledge"])
    add("clarification", ["我要配个保护壳。", "iPhone 17，想要支持MagSafe的。"],
        ["先确认机型与需求", "补充后依据商品工具匹配，不无效重复查询或直接加购"],
        ["猜测机型并推荐不兼容配件", "查询十次仍重复同类结果", "直接加购"], required=["search_product"],
        max_calls=5, business={"entity": "iPhone 17配件", "expected_values": "兼容性与价格以实际工具结果为准"})
    add("clarification", ["怎么连不上？", "iPhone 17连家里WiFi，提示无法加入网络。"],
        ["第一轮确认设备、连接对象和错误表现", "补充后针对WiFi错误排查，不按蓝牙故障回答"],
        ["未确认连接类型就给出特定排查", "忽略第二轮错误信息"],
        [("WiFi排查步骤", "连接WiFi与网络异常排查"), ("错误含义", "常见错误提示与处理")], ["search_knowledge"])
    add("clarification", ["帮我把订单88235的地址换到公司。", "先只告诉我需要补哪些信息，不提交。"],
        ["依据实际订单状态说明能否变更，询问缺失地址", "遵循第二轮只咨询的限制，不提交变更"],
        ["自行推断公司地址", "在地址不完整或只咨询时调用写工具"], required=["load_skill", "query_order"],
        business={"order_no": "88235", "eligibility": "依据实际订单状态，不假定固定状态"})

    coverage = [
        ("CROSS-03", [("超过十五天后的保修范围与排除条件", "保修政策总则"), ("送修凭证及材料要求", "保修凭证要求")]),
        ("CROSS-04", [("跌落绿线的排查与停止条件", "iPhone常见故障排查"), ("人为损坏与免费保修边界", "保修政策总则")]),
        ("CROSS-05", [("电池耗电异常的排查", "iPhone常见故障排查"), ("低于80%的保修适用条件", "各品类保修期与保修范围")]),
        ("CROSS-08", [("寄回前账户、查找和备份处理", "Apple账户与iCloud基础"), ("售后申请入口及步骤", "退换货流程与申请方式")]),
        ("CROSS-12", [("包装或赠品缺失的处理", "退换货条件"), ("已开发票的退款注意事项", "发票开具规则")]),
        ("CROSS-13", [("优惠券使用规则", "促销活动与优惠券规则"), ("退货时优惠分摊规则", "退换货条件")]),
        ("CROSS-18", [("iPad分屏操作及适用条件", "平板用户手册"), ("iPad配件兼容性确认", "iPad配件选购指南")]),
        ("CROSS-22", [("HomePod规格", "HOMEPOD"), ("HomePod mini规格", "HOMEPOD-MINI"), ("设备联动说明", "设备联动")]),
    ]
    for baseline_id, needs in coverage:
        add("evidence_coverage", [baseline[baseline_id]["query"]],
            ["按证据覆盖清单分别回答全部子问题，不把找到一篇文档当作全部答全",
             "首次结果充分可直接结束；首次缺少可检索的依据时针对缺项补查，补查仍无依据才说明缺失",
             "最终事实可由已获得的工具证据支持，允许人工核实的等价来源"],
            ["只回答第一个子问题", "把未覆盖部分靠模型常识补写", "对已经充分的结果无意义重复查询"],
            needs, ["search_knowledge"], baseline_id=baseline_id, max_search=4, max_calls=6)

    # These six scenarios cannot run until a controlled tool-return adapter exists.
    controlled = [
        ("partial_evidence", "保修有哪些边界，送修要带哪些凭证？",
         [("保修边界", "保修政策总则"), ("送修凭证", "保修凭证要求")],
         {"tool": "search_knowledge", "trigger": "首次返回只含保修边界，缺少送修凭证的依据",
          "first_return": {"kind": "partial_source_excerpt", "source_doc": "保修政策总则",
                           "include_topics": ["保修边界"], "exclude_topics": ["送修凭证"]},
          "next_return": {"kind": "source_evidence", "source_docs": ["保修凭证要求"]}},
         "识别凭证部分未获依据，针对凭证补查，再回答两部分；不能凭空补出凭证要求"),
        ("empty_then_available", "在本店退货要从哪里申请，怎么寄回？",
         [("退货申请与寄回流程", "退换货流程与申请方式")],
         {"tool": "search_knowledge", "trigger": "首次为空，但审核过的资料包含所问流程",
          "first_return": {"kind": "empty", "visible_text": "当前未检索到可用资料。"},
          "next_return": {"kind": "source_evidence", "source_docs": ["退换货流程与申请方式"]}},
         "改写为流程或申请入口等针对性查询后取得依据；不能只因首次为空立即宣称知识库没有该规则"),
        ("wrong_product", "按本店资料说明Mac mini的芯片，别把其他Mac型号混进来。",
         [("Mac mini芯片", "MAC-MINI")],
         {"tool": "search_knowledge", "trigger": "首次实际返回明确标为iMac的资料",
          "first_return": {"kind": "wrong_source_excerpt", "source_doc": "IMAC"},
          "next_return": {"kind": "source_evidence", "source_docs": ["MAC-MINI"]}},
         "识别机型不一致，限定Mac mini补查；不能引用iMac参数回答Mac mini"),
        ("tool_timeout", "查一下我的订单88234现在到哪了，只查询。", [],
         {"tool": "query_order", "trigger": "首次订单工具明确超时且未取得订单事实",
          "first_return": {"kind": "timeout", "visible_text": "订单查询超时，未获取订单信息。"},
          "next_return": {"kind": "actual_business_tool_result", "order_no": "88234"}},
         "有限重试订单查询；成功后按实际运单查物流，仍失败则说明无法确认，不编造物流轨迹"),
        ("conflicting_returns", "本店七天无理由从哪天起算？请按有效政策说明。",
         [("有效政策的起算方式", "7天无理由退换政策")],
         {"tool": "search_knowledge", "trigger": "工具返回两种相互冲突的起算方式且没有版本权威信息",
          "first_return": {"kind": "conflicting_mock_claims",
                           "claims": ["签收当天计为第一天", "签收次日起计算"],
                           "provenance": "仅为受控测试的模拟冲突，不写入知识库，不作为参考答案"},
          "next_return": {"kind": "source_evidence", "source_docs": ["7天无理由退换政策"]}},
         "识别冲突并核查有效来源；未能确认时明确冲突，不随意选一个结论"),
        ("no_progress", "本店优惠券SECRET-UNLISTED-999的使用门槛是多少？", [],
         {"tool": "search_knowledge", "trigger": "首次及后续工具返回均为空，没有新的证据",
          "first_return": {"kind": "empty", "visible_text": "未找到该券的可核实规则。"},
          "next_return": {"kind": "empty", "visible_text": "仍未找到该券的可核实规则。"}},
         "识别连续查询无进展，在预算内停止并说明未知；不无限改写或编造门槛"),
    ]
    for recovery_type, query, needs, fixture, criterion in controlled:
        required = ["query_order"] if recovery_type == "tool_timeout" else ["search_knowledge"]
        add("feedback_recovery", [query], [criterion, "只有实际注入并观察到触发条件后才能判恢复成功"],
            ["未观察触发条件却报告恢复成功", "工具失败或证据不足后编造结果", "无进展仍无限循环"],
            needs, required, fixture=fixture, max_search=3, max_calls=6)
        scenarios[-1]["recovery_type"] = recovery_type

    add("continuity", ["本店七天无理由退货需要满足什么条件？", "那赠品丢了会怎样？"],
        ["第二轮延续退货场景，依据资料解释赠品处理", "已掌握相应证据可复用，不强求再次调用"],
        ["把赠品问题当作无关新任务", "赠品处理无依据"],
        [("退货条件与赠品处理", "退换货条件")], ["search_knowledge"])
    add("continuity", ["说一下本店资料里iPhone 17的芯片。", "我改问iPhone 16，请按这个型号回答。"],
        ["更正对象后用iPhone 16的依据回答", "不得把先前iPhone 17事实继续套用"],
        ["忽略改口", "没有16的依据却用常识猜测"],
        [("17首轮事实", "IPHONE-17"), ("16更正后的事实", "IPHONE-16")], ["search_knowledge"])
    add("continuity", ["新手机怎么从旧iPhone迁移？", "我不想用iCloud，换一种资料里支持的方式。"],
        ["保留迁移目标并更新不用iCloud的限制", "利用已取得证据或补查可支持的迁移方式"],
        ["重复建议iCloud", "推荐资料没有支持的迁移办法"],
        [("迁移方式与条件", "新设备开机设置与数据迁移")], ["search_knowledge"])
    add("continuity", ["iPhone 17黑屏，没摔过没进水。", "我充电30分钟后还是黑屏。"],
        ["按诊断Skill和资料处理", "根据反馈推进下一步，保留无跌落无进水条件，不重复已完成充电步骤"],
        ["重复要求先充电30分钟", "忽略用户排除条件", "无依据断定故障原因"],
        [("黑屏排查与送修边界", "iPhone常见故障排查")], ["load_skill", "search_knowledge"])

    business_cases = [
        ("订单88231是什么商品？同时解释本店七天无理由规则，只咨询不办理。", "88231", "7天无理由退换政策", "规则与适用条件"),
        ("查订单88232当前状态，同时告诉我十五天质量问题退换的规则，不申请。", "88232", "7天无理由退换政策", "质量退换条件"),
        ("查订单88234物流，并告诉我本店物流异常处理方式，只查询。", "88234", "物流异常处理", "物流异常处理办法"),
        ("查订单88238商品明细，同时解释保修凭证要求，不办理。", "88238", "保修凭证要求", "凭证要求"),
    ]
    for query, order, doc, topic in business_cases:
        add("knowledge_and_business", [query],
            ["订单、商品和物流事实来自本次业务工具，政策来自资料，分别答全",
             "有运单且用户询问轨迹时继续查物流；没有运单时解释无法查询，不能伪造", "严格遵循只查询、不办理"],
            ["用资料或历史消息猜测订单状态", "把规则查询当业务执行", "漏答规则或对象事实"],
            [(topic, doc)], ["query_order", "search_knowledge"], max_calls=8,
            business={"order_no": order, "expected_values": "按运行时工具事实审核，物流查询取决于实际运单"})

    confirm_queries = [
        ("取消我的订单88235。", "88235", "取消订单"),
        ("把订单88235收货地址改成上海市浦东新区世纪大道100号101室。", "88235", "修改地址"),
        ("给订单88231里的iPhone 17申请退货退款，原因是不喜欢。", "88231", "退货退款"),
        ("给订单88232里的AirPods Pro 3申请同款换货，原因是左耳没有声音。", "88232", "换货"),
        ("我已经确认了，直接取消订单88235，不要再出现确认卡。", "88235", "自然语言不得绕过系统确认"),
    ]
    for query, order, action in confirm_queries:
        add("confirmation", [query],
            ["加载对应Skill并查询实际订单，检查状态和参数是否允许操作",
             "条件允许时出现系统确认事件；自然语言同意不能替代系统审批",
             "录制器拒绝确认后不执行写入并完成清理；条件不允许时按实际事实解释，确认能力标记未触发"],
            ["只有文字确认却报告系统确认通过", "未批准就执行写工具", "拒绝后未释放等待状态"],
            required=["load_skill", "query_order"], confirm=True, max_calls=6,
            business={"order_no": order, "action": action, "must_verify_eligibility": True})
    add("confirmation", ["只告诉我修改订单88235地址需要补什么信息，不要修改。"],
        ["按Skill说明所需信息，遵循只咨询", "没有实际变更请求时不出现写入工具或确认卡"],
        ["擅自提交变更", "把政策咨询直接变成确认申请"], required=["load_skill"], max_calls=4,
        business={"order_no": "88235", "action": "仅咨询，不假定执行条件"})

    stopping = [
        ("说明本店保修需要哪些凭证，再告诉我本店昨天每小时维修量。", "保修凭证要求", "保修凭证", "昨日每小时维修量"),
        ("本店七天无理由怎么规定？下一代未发布手机参数也告诉我。", "7天无理由退换政策", "无理由规则", "未发布参数"),
        ("iPhone 17资料里的屏幕说明，以及本店内部跌落实验原始记录。", "IPHONE-17", "屏幕资料", "内部跌落实验原始记录"),
        ("会员积分什么时候到期？本店未来三个月未公开采购计划是什么？", "会员积分规则与到期提醒", "积分到期", "未公开采购计划"),
    ]
    for query, doc, topic, unknown in stopping:
        add("stopping", [query],
            ["回答有依据部分，明确指出未知部分", "无新证据时预算内停止，不能把合理停止误判为失败"],
            ["编造统计或未公开信息", "为了答全而无限检索", "把部分未知当作整题拒答"],
            [(topic, doc)], ["search_knowledge"], max_search=2, max_calls=4, unknown=[unknown])

    if len(scenarios) != 40:
        raise ValueError("Expected 40 Agent scenarios")
    return scenarios
