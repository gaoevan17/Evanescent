"""离线测试模式：确定性意图解析与计划生成器。

⚠️ 本模块是「离线测试模式」的实现，用于在无大模型 API Key 时复现业务逻辑。
它基于规则 + 槽位抽取 + 计划模板，**不宣称是真实大模型接入**。
真实模型接入见 provider.LLMPlanner（OpenAI 兼容协议）。
"""
from __future__ import annotations

import re
import datetime as dt

from sqlalchemy.orm import Session

_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6,
           "七": 7, "八": 8, "九": 9, "十": 10}


def _amount_cents(text: str) -> int | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*元", text)
    if m:
        return int(round(float(m.group(1)) * 100))
    m = re.search(r"(\d+(?:\.\d+)?)\s*块", text)
    if m:
        return int(round(float(m.group(1)) * 100))
    m = re.search(r"(\d+(?:\.\d+)?)\s*分", text)
    if m:
        return int(float(m.group(1)))
    return None


def _extract_days(text: str) -> dict | None:
    # 入住天数 30/20/10 或 30、20、10
    m = re.findall(r"(\d+)\s*天", text)
    if m:
        return [int(x) for x in m]
    m = re.search(r"(\d+)\s*/\s*(\d+)\s*/\s*(\d+)", text)
    if m:
        return [int(m.group(i)) for i in (1, 2, 3)]
    return None


class OfflinePlanner:
    """离线计划生成器。context 提供必要的补全信息。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    def plan(self, text: str, *, user_id: int, space_id: int | None = None) -> dict:
        text = text.strip()
        nodes: list[dict] = []
        clarification = None

        # 结算/共同开销 主场景
        if any(k in text for k in ["结算", "共同开销", "分摊", "一起算", "AA", "合租"]):
            return self._plan_settlement(text, user_id, space_id)

        # 转账
        if any(k in text for k in ["转账", "转给", "付款", "支付", "打钱", "汇"]) and \
           "结算" not in text:
            return self._plan_transfer(text, user_id)

        # 账单
        if any(k in text for k in ["账单", "流水", "花费", "开销", "消费", "支出"]):
            return self._plan_bill(text, user_id)

        # 理财
        if any(k in text for k in ["理财", "申购", "赎回", "投资", "余钱", "基金", "收益"]):
            return self._plan_wealth(text, user_id)

        # 卡片
        if any(k in text for k in ["卡", "挂失", "解挂", "额度", "虚拟卡"]):
            return self._plan_card(text, user_id)

        # 订阅
        if any(k in text for k in ["会员", "订阅", "扣费", "续费", "自动扣", "代扣"]):
            return self._plan_subscription(text, user_id)

        # 余额/查询
        if any(k in text for k in ["余额", "多少钱", "查询", "看一下", "查一下"]):
            nodes = [{"node_key": "q_balance", "tool_name": "balance_query",
                      "params": {}, "depends_on": []}]

        if nodes:
            return {"intent": "query", "nodes": nodes, "clarification": None,
                    "offline_mode": True}
        return {"intent": "unknown", "nodes": [], "offline_mode": True,
                "clarification": {"question": "我没有完全理解您的意图，请说明您想办理的业务（转账、账单、理财、卡片、订阅或共同开销结算）。"}}

    # ------------------------------------------------------------------
    def _plan_settlement(self, text: str, user_id: int, space_id: int | None) -> dict:
        nodes = [
            {"node_key": "s1_analyze", "tool_name": "bill_analysis",
             "params": {"year": dt.datetime.now().year}, "depends_on": []},
            {"node_key": "s2_anomaly", "tool_name": "list_evidence",
             "params": {}, "depends_on": ["s1_analyze"]},
            {"node_key": "s3_report", "tool_name": "bill_report",
             "params": {"year": dt.datetime.now().year}, "depends_on": ["s1_analyze"]},
        ]
        if space_id is not None:
            nodes.append({"node_key": "s4_progress", "tool_name": "space_progress_query",
                          "params": {"space_id": space_id}, "depends_on": ["s1_analyze"]})
        return {"intent": "settlement", "nodes": nodes, "clarification": None,
                "offline_mode": True,
                "hint": "已生成共同开销结算计划：先核验账单→识别异常→生成月报，并展示共享结算进度。"}

    def _plan_transfer(self, text: str, user_id: int) -> dict:
        amount = _amount_cents(text)
        from ..services import transfer as transfer_svc
        # 收款人检索（同名澄清）
        m = re.search(r"(?:转给|给|付给)\s*([\u4e00-\u9fa5]{2,4})", text)
        payee_name = m.group(1) if m else None
        clarification = None
        contact_id = None
        if payee_name:
            cands = transfer_svc.search_contacts(self.db, user_id, payee_name)
            if len(cands) > 1:
                clarification = {"question": f"收款人「{payee_name}」有多个同名候选，请选择：",
                                 "options": [{"contact_id": c["contact_id"],
                                              "label": f"{c['name']}（{c.get('phone') or c.get('memo') or '无备注'}）"}
                                             for c in cands]}
                return {"intent": "transfer", "nodes": [], "clarification": clarification,
                        "offline_mode": True}
            elif len(cands) == 1:
                contact_id = cands[0]["contact_id"]
            else:
                clarification = {"question": f"未找到收款人「{payee_name}」，请补充手机号或完整姓名。"}
                return {"intent": "transfer", "nodes": [], "clarification": clarification,
                        "offline_mode": True}
        if amount is None:
            clarification = {"question": "请问转账金额是多少？（请用「元」说明，例如 800 元）"}
            return {"intent": "transfer", "nodes": [], "clarification": clarification,
                    "offline_mode": True}
        params = {"amount_cents": amount}
        if contact_id is not None:
            params["payee_contact_id"] = contact_id
        elif payee_name:
            params["payee_external"] = payee_name
        else:
            clarification = {"question": "请问转给谁？（姓名/手机号/备注）"}
            return {"intent": "transfer", "nodes": [], "clarification": clarification,
                    "offline_mode": True}
        return {"intent": "transfer",
                "nodes": [{"node_key": "t1", "tool_name": "transfer",
                           "params": params, "depends_on": []}],
                "clarification": None, "offline_mode": True}

    def _plan_bill(self, text: str, user_id: int) -> dict:
        year = dt.datetime.now().year
        month = None
        m = re.search(r"(\d{1,2})\s*月", text)
        if m:
            month = int(m.group(1))
        params = {"year": year}
        if month:
            params["month"] = month
        nodes = [{"node_key": "b1", "tool_name": "bill_analysis", "params": params, "depends_on": []},
                 {"node_key": "b2", "tool_name": "bill_report", "params": params, "depends_on": ["b1"]}]
        return {"intent": "bill", "nodes": nodes, "clarification": None, "offline_mode": True}

    def _plan_wealth(self, text: str, user_id: int) -> dict:
        amount = _amount_cents(text)
        nodes = [{"node_key": "w1", "tool_name": "risk_profile_query", "params": {}, "depends_on": []},
                 {"node_key": "w2", "tool_name": "product_list", "params": {}, "depends_on": []},
                 {"node_key": "w3", "tool_name": "holding_query", "params": {}, "depends_on": []}]
        clarification = None
        if "赎回" in text:
            clarification = {"question": "请提供要赎回的持仓编号（可先查看持仓）。"}
            return {"intent": "wealth_redeem", "nodes": nodes, "clarification": clarification,
                    "offline_mode": True}
        if "申购" in text or "买" in text or "投资" in text:
            if amount is None:
                clarification = {"question": "请问申购金额是多少元？"}
                return {"intent": "wealth_subscribe", "nodes": nodes,
                        "clarification": clarification, "offline_mode": True}
            clarification = {"question": "请提供要申购的产品编号（可先查看产品列表）。"}
            return {"intent": "wealth_subscribe", "nodes": nodes,
                    "clarification": clarification, "offline_mode": True}
        return {"intent": "wealth", "nodes": nodes, "clarification": None, "offline_mode": True}

    def _plan_card(self, text: str, user_id: int) -> dict:
        nodes = [{"node_key": "c1", "tool_name": "card_list", "params": {}, "depends_on": []}]
        clarification = None
        if "挂失" in text:
            clarification = {"question": "请提供要挂失的卡号编号。"}
            return {"intent": "card_lost", "nodes": nodes, "clarification": clarification,
                    "offline_mode": True}
        if "申请" in text or "虚拟卡" in text:
            nodes.insert(0, {"node_key": "c0", "tool_name": "virtual_card_apply",
                             "params": {}, "depends_on": []})
        if "额度" in text:
            clarification = {"question": "请提供卡片编号和目标额度。"}
            return {"intent": "card_limit", "nodes": nodes, "clarification": clarification,
                    "offline_mode": True}
        return {"intent": "card", "nodes": nodes, "clarification": None, "offline_mode": True}

    def _plan_subscription(self, text: str, user_id: int) -> dict:
        from ..services import subscription as sub_svc
        subs = sub_svc.list_subscriptions(self.db, user_id)
        # 识别候选订阅
        if "取消" in text or "停" in text or "关" in text:
            if len(subs) == 1:
                nodes = [{"node_key": "sub1", "tool_name": "subscribe_query",
                          "params": {"sub_id": subs[0]["id"]}, "depends_on": []},
                         {"node_key": "sub2", "tool_name": "subscribe_cancel",
                          "params": {"sub_id": subs[0]["id"]}, "depends_on": ["sub1"]}]
                return {"intent": "subscribe_cancel", "nodes": nodes, "clarification": None,
                        "offline_mode": True}
            if len(subs) > 1:
                return {"intent": "subscribe_cancel", "nodes": [], "offline_mode": True,
                        "clarification": {"question": "您名下有多个订阅，请指定要取消哪一个：",
                                          "options": [{"sub_id": s["id"],
                                                       "label": f"{s['merchant']}（{s['amount_cents']/100:.2f}元/{s['cycle']}）"}
                                                      for s in subs]}}
            return {"intent": "subscribe_cancel", "nodes": [], "offline_mode": True,
                    "clarification": {"question": "未发现可取消的订阅。"}}
        # 查询订阅
        nodes = [{"node_key": "sub_q", "tool_name": "subscribe_query",
                  "params": {"sub_id": s["id"]}, "depends_on": []} for s in subs]
        if not nodes:
            return {"intent": "subscribe", "nodes": [], "offline_mode": True,
                    "clarification": {"question": "未发现订阅记录。"}}
        return {"intent": "subscribe_query", "nodes": nodes, "clarification": None,
                "offline_mode": True}
