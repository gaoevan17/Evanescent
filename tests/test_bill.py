"""账单分析测试：导入去重、分类、异常候选、可追溯。"""
from app.services import bill
from app.models import Account


def test_import_dedup(db):
    acct = db.query(Account).filter(Account.user_id == 1).first()
    items = [{"merchant": "超市", "amount_cents": 1000, "memo": "购物",
              "category": "日用", "occurred_at": "2026-10-15T10:00:00+00:00"}]
    r1 = bill.import_transactions(db, account_id=acct.id, items=items,
                                  idempotency_prefix="t1")
    r2 = bill.import_transactions(db, account_id=acct.id, items=items,
                                  idempotency_prefix="t2")
    assert r1["imported"] == 1
    assert r2["imported"] == 0  # 重复
    assert r2["duplicated"] == 1


def test_classify_report_total(db):
    acct = db.query(Account).filter(Account.user_id == 2).first()
    r = bill.classify_and_report(db, account_id=acct.id, year=2026)
    # bob 有 6800+6800+4500
    assert r["total_cents"] >= 6800 * 2


def test_report_traceable(db):
    """汇总可追溯到基础交易。"""
    acct = db.query(Account).filter(Account.user_id == 2).first()
    r = bill.classify_and_report(db, account_id=acct.id, year=2026)
    detail_total = sum(t["amount_cents"] for t in r["transactions"])
    assert detail_total == r["total_cents"]


def test_detect_anomaly_duplicate(db):
    """两笔同额扣费 → 疑似重复候选。"""
    acct = db.query(Account).filter(Account.user_id == 2).first()
    cards = bill.detect_anomalies(db, account_id=acct.id)
    kinds = {c["resolution"] for c in cards}
    assert any("duplicate" in k for k in kinds)


def test_detect_anomaly_unknown_merchant(db):
    """陌生商户 → 待核验。"""
    acct = db.query(Account).filter(Account.user_id == 1).first()
    cards = bill.detect_anomalies(db, account_id=acct.id, known_merchants={"菜市场"})
    kinds = {c["resolution"] for c in cards}
    assert any("unknown_merchant" in k for k in kinds)


def test_anomaly_status_pending_not_fraud(db):
    """异常候选保持待核验，不直接认定盗刷。"""
    acct = db.query(Account).filter(Account.user_id == 1).first()
    cards = bill.detect_anomalies(db, account_id=acct.id)
    assert all(c["status"] == "pending" for c in cards)


def test_evidence_card_fields(db):
    """证据卡关联交易号、商户别名、金额、时间、反馈。"""
    acct = db.query(Account).filter(Account.user_id == 2).first()
    cards = bill.detect_anomalies(db, account_id=acct.id)
    assert all("card_id" in c and "merchant_alias" in c and "amount_cents" in c for c in cards)
