"""理财操作测试：风险适配、申购、赎回不到账、赎回到账后可用。"""
import pytest

from app.services import money, wealth
from tests.conftest import login


def test_submit_risk_profile(db):
    r = wealth.submit_risk_profile(db, user_id=1, answers=[1, 1, 1, 1, 1])
    assert r["risk_level"] in ("保守", "稳健", "平衡", "进取", "激进")


def test_adaptation_requires_profile(db):
    r = wealth.check_adaptation(db, 3, 1)
    assert r["adapted"] is False


def test_adaptation_pass(db):
    wealth.submit_risk_profile(db, user_id=1, answers=[4, 4, 4, 4, 4])  # 进取
    r = wealth.check_adaptation(db, 1, 2)  # R2 债券
    assert r["adapted"] is True


def test_adaptation_reject_high_risk(db):
    wealth.submit_risk_profile(db, user_id=1, answers=[1, 1, 1, 1, 1])  # 保守
    r = wealth.check_adaptation(db, 1, 4)  # R4 股票
    assert r["adapted"] is False


def test_subscribe_updates_holding_and_balance(db):
    wealth.submit_risk_profile(db, user_id=1, answers=[4, 4, 4, 4, 4])
    before = money.balance(db, 1)
    r = wealth.subscribe_product(db, user_id=1, product_id=2, amount_cents=50000)
    assert r["status"] == "settled"
    assert money.balance(db, 1) == before - 50000
    holdings = wealth.list_holdings(db, 1)
    assert any(h["product_id"] == 2 and h["amount_cents"] == 50000 for h in holdings)


def test_subscribe_rejected_when_not_adapted(db):
    wealth.submit_risk_profile(db, user_id=1, answers=[1, 1, 1, 1, 1])
    with pytest.raises(ValueError):
        wealth.subscribe_product(db, user_id=1, product_id=4, amount_cents=50000)


def test_redeem_not_credited_immediately(db):
    """赎回申请成功≠到账。"""
    wealth.submit_risk_profile(db, user_id=1, answers=[4, 4, 4, 4, 4])
    wealth.subscribe_product(db, user_id=1, product_id=2, amount_cents=50000)
    before = money.balance(db, 1)
    h = wealth.list_holdings(db, 1)[0]
    r = wealth.redeem_product(db, user_id=1, holding_id=h["id"], amount_cents=50000)
    assert r["status"] == "processing"
    assert money.balance(db, 1) == before  # 资金在途，未到账


def test_redeem_credited_after_settle(db):
    wealth.submit_risk_profile(db, user_id=1, answers=[4, 4, 4, 4, 4])
    wealth.subscribe_product(db, user_id=1, product_id=2, amount_cents=50000)
    before = money.balance(db, 1)
    h = wealth.list_holdings(db, 1)[0]
    r = wealth.redeem_product(db, user_id=1, holding_id=h["id"], amount_cents=50000)
    wealth.settle_redemption(db, order_id=r["order_id"], user_id=1)
    assert money.balance(db, 1) == before + 50000


def test_redeem_over_holding_rejected(db):
    wealth.submit_risk_profile(db, user_id=1, answers=[4, 4, 4, 4, 4])
    wealth.subscribe_product(db, user_id=1, product_id=2, amount_cents=50000)
    h = wealth.list_holdings(db, 1)[0]
    with pytest.raises(ValueError):
        wealth.redeem_product(db, user_id=1, holding_id=h["id"], amount_cents=999999)


def test_products_are_synthetic(db):
    prods = wealth.list_products(db)
    assert all(p["is_synthetic"] for p in prods)


def test_compare_products(db):
    r = wealth.compare_products(db, [1, 2, 3])
    assert len(r) == 3
    assert all("risk_level" in p for p in r)
