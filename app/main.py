"""FastAPI 应用入口。

身份来自服务端会话（Bearer token），不信任请求体自报的 user_id。
所有查询按当前身份过滤（对象级权限），不提供跨用户查询端点。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session

from . import llm
from .auth import create_session, resolve_session, revoke_session, verify_password
from .db import Base, engine, get_db
from .models import User
from .services import audit, dag, ledger, money
from .services import transfer as transfer_svc
from .tools import run_guarded

app = FastAPI(title="共识护航 AI Banking Agent", version="1.0.0")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


# ---------------------------------------------------------------------------
# 依赖：当前用户
# ---------------------------------------------------------------------------
def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "未登录")
    token = auth[len("Bearer "):]
    user = resolve_session(db, token)
    if user is None:
        raise HTTPException(401, "会话无效或已过期")
    return user


# ---------------------------------------------------------------------------
# 请求体
# ---------------------------------------------------------------------------
class LoginReq(BaseModel):
    username: str
    password: str


class ChatReq(BaseModel):
    message: str
    space_id: int | None = None


class ContinueReq(BaseModel):
    node_key: str
    confirmed: bool = False
    challenge_id: int | None = None
    challenge_code: str | None = None
    operation_id: str | None = None
    authorization_id: int | None = None


class ToolRunReq(BaseModel):
    tool_name: str
    params: dict = {}
    confirmed: bool = False
    challenge_id: int | None = None
    challenge_code: str | None = None
    operation_id: str | None = None
    authorization_id: int | None = None
    rule_version_id: int | None = None
    plan_version: str | None = None


class ModifyNodeReq(BaseModel):
    new_params: dict


# ---------------------------------------------------------------------------
# 认证
# ---------------------------------------------------------------------------
@app.post("/api/auth/login")
def login(req: LoginReq, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == req.username).first()
    if user is None or not verify_password(req.password, user.password_hash):
        raise HTTPException(401, "用户名或密码错误")
    token = create_session(db, user.id)
    return {"token": token, "user": _user_dict(user),
            "note": "参赛模拟环境，所有账户/交易均为模拟数据"}


@app.post("/api/auth/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        revoke_session(db, auth[len("Bearer "):])
    return {"status": "ok"}


def _user_dict(u: User) -> dict:
    return {"id": u.id, "username": u.username, "display_name": u.display_name,
            "phone": u.phone}


@app.get("/api/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    acct = money.get_account(db, user.id)
    spaces = _my_spaces(db, user.id)
    return {"user": _user_dict(user),
            "balance_cents": acct.balance_cents if acct else 0,
            "spaces": spaces, "offline_mode": llm.is_offline_mode()}


def _my_spaces(db: Session, user_id: int) -> list[dict]:
    from .models import SharedSpace, SpaceMember
    rows = (db.query(SharedSpace, SpaceMember)
            .join(SpaceMember, SpaceMember.space_id == SharedSpace.id)
            .filter(SpaceMember.user_id == user_id, SpaceMember.active.is_(True)).all())
    return [{"id": s.id, "name": s.name, "role": m.role} for s, m in rows]


# ---------------------------------------------------------------------------
# 对话：自然语言 → 计划 → DAG
# ---------------------------------------------------------------------------
@app.post("/api/chat")
def chat(req: ChatReq, user: User = Depends(current_user),
         db: Session = Depends(get_db)):
    planner = llm.get_planner(db)
    plan_result = planner.plan(req.message, user_id=user.id, space_id=req.space_id)

    if plan_result.get("clarification"):
        return {"reply": plan_result["clarification"]["question"],
                "clarification": plan_result["clarification"],
                "offline_mode": plan_result.get("offline_mode", False)}

    nodes = plan_result.get("nodes", [])
    if not nodes:
        return {"reply": "没有可执行的计划。", "plan": None,
                "offline_mode": plan_result.get("offline_mode", False)}

    plan = dag.create_plan(db, user_id=user.id, space_id=req.space_id,
                           intent_summary=plan_result.get("intent", ""),
                           original_text=req.message, nodes=nodes)
    audit.record(db, user_id=user.id, action="plan_created",
                 intent_summary=plan.intent_summary, plan_version=plan.plan_version,
                 tool_params={"original_text": req.message}, success=True)

    result = dag.run_plan(db, plan.id, user.id)
    reply = _compose_reply(plan_result, result)
    return {"reply": reply, "plan": result, "clarification": None,
            "offline_mode": plan_result.get("offline_mode", False),
            "hint": plan_result.get("hint")}


def _compose_reply(plan_result: dict, result: dict) -> str:
    if result.get("plan_status") == "waiting":
        waits = result.get("waits", [])
        kinds = {w["type"] for w in waits}
        if "auth" in kinds:
            return "计划已生成，部分操作需要强验证，请在下方完成验证后继续。"
        if "confirm" in kinds:
            return "计划已生成，部分操作需要您确认，请核对下方详情后确认。"
        return "计划执行中，存在等待项。"
    if result.get("plan_status") == "completed":
        return "计划已完成。"
    if result.get("plan_status") == "failed":
        return "计划执行失败，请查看任务详情。"
    return f"意图：{plan_result.get('intent', '')}，计划已创建。"


# ---------------------------------------------------------------------------
# DAG 控制
# ---------------------------------------------------------------------------
@app.get("/api/plans/{plan_id}")
def get_plan(plan_id: int, user: User = Depends(current_user),
             db: Session = Depends(get_db)):
    plan = dag.get_plan(db, plan_id)
    if plan is None or plan.user_id != user.id:
        raise HTTPException(404, "计划不存在")
    return dag.plan_snapshot(db, plan_id)


@app.post("/api/plans/{plan_id}/continue")
def continue_plan(plan_id: int, req: ContinueReq, user: User = Depends(current_user),
                  db: Session = Depends(get_db)):
    plan = dag.get_plan(db, plan_id)
    if plan is None or plan.user_id != user.id:
        raise HTTPException(404, "计划不存在")
    result = dag.continue_node(db, plan_id=plan_id, node_key=req.node_key, user_id=user.id,
                               confirmed=req.confirmed, challenge_id=req.challenge_id,
                               challenge_code=req.challenge_code,
                               operation_id=req.operation_id,
                               authorization_id=req.authorization_id)
    return result


@app.post("/api/plans/{plan_id}/pause")
def pause_plan(plan_id: int, user: User = Depends(current_user),
               db: Session = Depends(get_db)):
    plan = dag.get_plan(db, plan_id)
    if plan is None or plan.user_id != user.id:
        raise HTTPException(404, "计划不存在")
    return dag.pause_plan(db, plan_id)


@app.post("/api/plans/{plan_id}/resume")
def resume_plan(plan_id: int, user: User = Depends(current_user),
                db: Session = Depends(get_db)):
    plan = dag.get_plan(db, plan_id)
    if plan is None or plan.user_id != user.id:
        raise HTTPException(404, "计划不存在")
    return dag.resume_plan(db, plan_id, user.id)


@app.post("/api/plans/{plan_id}/cancel")
def cancel_plan(plan_id: int, user: User = Depends(current_user),
                db: Session = Depends(get_db)):
    plan = dag.get_plan(db, plan_id)
    if plan is None or plan.user_id != user.id:
        raise HTTPException(404, "计划不存在")
    return dag.cancel_plan(db, plan_id)


@app.post("/api/plans/{plan_id}/nodes/{node_key}/modify")
def modify_node(plan_id: int, node_key: str, req: ModifyNodeReq,
                user: User = Depends(current_user), db: Session = Depends(get_db)):
    plan = dag.get_plan(db, plan_id)
    if plan is None or plan.user_id != user.id:
        raise HTTPException(404, "计划不存在")
    try:
        return dag.modify_node(db, plan_id=plan_id, node_key=node_key,
                               new_params=req.new_params, user_id=user.id)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------------------------------------------------------------------------
# 工具直接调用（受权限门，供演示与测试）
# ---------------------------------------------------------------------------
@app.post("/api/tools/run")
def tool_run(req: ToolRunReq, user: User = Depends(current_user),
             db: Session = Depends(get_db)):
    try:
        return run_guarded(db, tool_name=req.tool_name, user_id=user.id,
                           params=req.params, confirmed=req.confirmed,
                           challenge_id=req.challenge_id,
                           challenge_code=req.challenge_code,
                           operation_id=req.operation_id,
                           authorization_id=req.authorization_id,
                           rule_version_id=req.rule_version_id,
                           plan_version=req.plan_version)
    except (ValueError, PermissionError) as e:
        raise HTTPException(400, str(e))


# ---------------------------------------------------------------------------
# 共同空间与审计
# ---------------------------------------------------------------------------
@app.get("/api/space/{space_id}/progress")
def space_progress(space_id: int, user: User = Depends(current_user),
                   db: Session = Depends(get_db)):
    if not ledger.is_space_member(db, space_id, user.id):
        raise HTTPException(403, "非共同空间成员，无权查看")
    return ledger.member_due_summary(db, space_id)


@app.get("/api/audit")
def my_audit(user: User = Depends(current_user), db: Session = Depends(get_db)):
    from .models import AuditLog
    logs = db.query(AuditLog).filter(AuditLog.user_id == user.id).order_by(
        AuditLog.id.desc()).limit(100).all()
    return {"audit": [{"id": l.id, "action": l.action, "intent_summary": l.intent_summary,
                       "decision_reason": l.decision_reason, "auth_result": l.auth_result,
                       "success": l.success, "created_at": l.created_at.isoformat()}
                      for l in logs]}


@app.get("/api/health")
def health():
    return {"status": "ok", "offline_mode": llm.is_offline_mode(),
            "note": "参赛模拟环境"}


# ---------------------------------------------------------------------------
# 静态前端
# ---------------------------------------------------------------------------
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


@app.on_event("startup")
def _startup():
    Base.metadata.create_all(bind=engine)


@app.exception_handler(Exception)
def _unhandled(request: Request, exc: Exception):  # noqa: ANN001
    return JSONResponse(status_code=500, content={"detail": f"内部错误：{type(exc).__name__}"})
