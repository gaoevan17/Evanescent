"""可配置大模型接入（OpenAI 兼容协议）。

模型只负责理解、澄清、生成结构化计划；金额计算、权限校验、业务执行由确定性程序负责。
模型只接收完成当前任务所必需的数据（数据最小化）。
"""
from __future__ import annotations

import json

import httpx

from ..config import settings
from ..security.policy import TOOL_WHITELIST

_SYSTEM_PROMPT = """你是银行 AI 智能体的规划器。你只负责把用户的自然语言需求转换为结构化计划（JSON）。
你只能使用以下受控工具（白名单）：
{tools}

输出格式（严格 JSON，不要输出其他文字）：
{{
  "intent": "一句话意图",
  "clarification": null,
  "nodes": [
    {{"node_key": "n1", "tool_name": "工具名", "params": {{...}}, "depends_on": []}}
  ]
}}

规则：
1. 只能使用白名单工具；params 只填该工具所需参数。
2. 金额参数 amount_cents 以「分」为整数单位。
3. 需要用户补充信息时，把 clarification 设为 {{"question": "..."}}，nodes 置空。
4. 涉及共同开销结算时，先做账单分析，再识别异常，再形成/确认分摊规则，最后各自授权付款。
5. 不要输出任何账户余额、卡号等你在上下文中看不到的数据。
"""


class LLMPlanner:
    def __init__(self) -> None:
        self.base_url = settings.LLM_BASE_URL.rstrip("/")
        self.api_key = settings.LLM_API_KEY
        self.model = settings.LLM_MODEL

    def _tools_desc(self) -> str:
        return ", ".join(TOOL_WHITELIST)

    def plan(self, text: str, *, user_id: int, space_id: int | None = None,
             context: dict | None = None) -> dict:
        if not self.api_key:
            raise RuntimeError("未配置 LLM_API_KEY，无法使用真实模型接入")
        sys_prompt = _SYSTEM_PROMPT.format(tools=self._tools_desc())
        user_msg = f"用户需求：{text}\n当前用户ID：{user_id}\n共同空间ID：{space_id}\n"
        if context:
            user_msg += f"可用上下文（仅本人）：{json.dumps(context, ensure_ascii=False)}\n"
        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model,
                  "messages": [{"role": "system", "content": sys_prompt},
                               {"role": "user", "content": user_msg}],
                  "temperature": 0.1},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        parsed = _parse_json(content)
        parsed = _sanitize_plan(parsed)
        parsed["offline_mode"] = False
        return parsed


def _parse_json(content: str) -> dict:
    content = content.strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.startswith("json"):
            content = content[4:]
    start = content.find("{")
    end = content.rfind("}")
    if start != -1 and end != -1:
        content = content[start:end + 1]
    return json.loads(content)


def _sanitize_plan(parsed: dict) -> dict:
    """安全校验：只保留白名单工具，过滤越权参数。"""
    nodes = parsed.get("nodes", [])
    clean = []
    for n in nodes:
        tool = n.get("tool_name")
        if tool not in TOOL_WHITELIST:
            continue
        clean.append({"node_key": n.get("node_key", f"n{len(clean)}"),
                      "tool_name": tool,
                      "params": n.get("params", {}),
                      "depends_on": n.get("depends_on", [])})
    parsed["nodes"] = clean
    return parsed
