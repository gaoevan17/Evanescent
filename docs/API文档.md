# API 文档

所有接口（除登录/健康检查外）需在请求头携带 `Authorization: Bearer <token>`。

## 认证

### POST /api/auth/login
请求：`{"username": "alice", "password": "demo123456"}`
响应：`{"token": "...", "user": {...}, "note": "参赛模拟环境"}`

### POST /api/auth/logout
无请求体，携带 token 即登出。

## 当前用户

### GET /api/me
返回本人信息、余额（分）、所属空间、是否离线模式。

## 对话与规划

### POST /api/chat
请求：`{"message": "帮我们结算本月共同开销", "space_id": 1}`
响应：
- 有澄清：`{"clarification": {"question": "...", "options": [...]}}`
- 有计划：`{"reply": "...", "plan": {"plan_id", "plan_status", "nodes", "waits"}}`

### GET /api/plans/{plan_id}
返回计划快照（节点状态）。

### POST /api/plans/{plan_id}/continue
请求：`{"node_key": "t1", "confirmed": true}` 或
`{"node_key": "n1", "challenge_id": 1, "challenge_code": "123456", "operation_id": "op-...", "authorization_id": 1}`

### POST /api/plans/{plan_id}/pause | /resume | /cancel

### POST /api/plans/{plan_id}/nodes/{node_key}/modify
请求：`{"new_params": {...}}`（改口，旧授权失效）

## 受控工具调用（经权限门）

### POST /api/tools/run
请求：
```json
{
  "tool_name": "transfer",
  "params": {"amount_cents": 80000, "payee_contact_id": 1},
  "confirmed": true,
  "challenge_id": null, "challenge_code": null,
  "operation_id": null, "authorization_id": null
}
```
响应（按权限等级）：
- 绿色：`{"status": "success", "result": {...}}`
- 黄色未确认：`{"status": "waiting_confirm", "detail": {...}}`
- 红色未验证：`{"status": "waiting_auth", "challenge": {...}, "authorization_id", "operation_id"}`

## 工具清单（白名单）

| 工具名 | 等级 | 说明 |
|--------|------|------|
| balance_query | 绿 | 余额查询 |
| transaction_list | 绿 | 交易记录 |
| bill_analysis / bill_report | 绿 | 账单分析/报告 |
| wealth_income_query / holding_query / product_list / risk_profile_query | 绿 | 理财查询 |
| card_list / list_subscriptions / list_evidence / list_rule_versions | 绿 | 列表查询 |
| space_progress_query | 绿 | 共享进度 |
| transfer | 黄/红 | 转账（按日累计） |
| subscribe_query / subscribe_cancel | 黄 | 订阅查询/取消 |
| virtual_card_apply | 黄 | 虚拟卡申请 |
| aa_request | 黄 | AA 收款 |
| update_evidence / propose_split_rule / confirm_split_rule | 黄 | 证据反馈/提出/确认分摊 |
| password_change | 红 | 密码修改 |
| card_lost / card_unlost | 红 | 挂失/解挂 |
| wealth_subscribe | 红 | 理财申购 |
| wealth_redeem / card_limit_adjust / card_restrict / card_unrestrict | 红(团队策略) | 赎回/额度/限制/解锁 |

## 共享与审计

### GET /api/space/{space_id}/progress
共享结算进度（成员应付/已付/剩余，不含私人余额）。

### GET /api/audit
本人脱敏审计记录。

### GET /api/health
健康检查。
