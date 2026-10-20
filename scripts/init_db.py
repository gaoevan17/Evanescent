"""初始化数据库：创建三个隔离演示账号、共同空间与合成演示数据。

所有账户、交易、产品均为模拟/合成数据，仅用于参赛演示与测试。
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.auth import hash_password  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import (  # noqa: E402
    Account, Card, Contact, ExpenseItem, RuleVersion, RuleConfirmation,
    SharedSpace, SpaceMember, Subscription, Product, Transaction, User,
)
from app.services import ledger  # noqa: E402


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def reset_all() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def seed(db) -> None:
    _pwd_hash = hash_password("demo123456")  # 复用，加速测试
    # ---- 用户 ----
    users = {
        "alice": User(username="alice", display_name="王淑芬", phone="13800000001",
                      password_hash=_pwd_hash, is_demo=True),
        "bob": User(username="bob", display_name="李伟", phone="13800000002",
                    password_hash=_pwd_hash, is_demo=True),
        "carol": User(username="carol", display_name="陈晨", phone="13800000003",
                      password_hash=_pwd_hash, is_demo=True),
    }
    for u in users.values():
        db.add(u)
    db.commit()
    alice, bob, carol = users["alice"].id, users["bob"].id, users["carol"].id

    # ---- 账户（余额，分）----
    db.add(Account(user_id=alice, balance_cents=500_000))
    db.add(Account(user_id=bob, balance_cents=300_000))
    db.add(Account(user_id=carol, balance_cents=200_000))

    # ---- 共同空间 ----
    space = SharedSpace(name="幸福之家", created_by=alice)
    db.add(space)
    db.flush()
    for uid, role in [(alice, "owner"), (bob, "member"), (carol, "member")]:
        db.add(SpaceMember(space_id=space.id, user_id=uid, role=role))

    # ---- 联系人（含同名候选，用于同名澄清测试）----
    for c in [
        Contact(owner_user_id=alice, name="李伟", phone="13800000002",
                memo="室友", is_member=True, member_user_id=bob),
        Contact(owner_user_id=alice, name="陈晨", phone="13800000003",
                memo="室友", is_member=True, member_user_id=carol),
        Contact(owner_user_id=bob, name="张伟", phone="13900001111",
                memo="房东", is_member=False),
        Contact(owner_user_id=bob, name="张伟", phone="13900002222",
                memo="同事", is_member=False),
        Contact(owner_user_id=bob, name="王淑芬", phone="13800000001",
                memo="妈妈", is_member=True, member_user_id=alice),
    ]:
        db.add(c)

    # ---- 费用项目：电费 + 宽带（验收案例 P-07）----
    electric = ExpenseItem(space_id=space.id, name="本月电费", category="电费",
                           total_cents=24_000, split_method="days",
                           status="pending", created_by=alice)
    broadband = ExpenseItem(space_id=space.id, name="本月宽带", category="宽带",
                            total_cents=9_000, split_method="equal",
                            status="pending", created_by=alice)
    db.add_all([electric, broadband])
    db.flush()

    # 电费规则版本 v1：入住天数 30/20/10
    rv_elec = RuleVersion(
        expense_item_id=electric.id, version=1,
        split_config={"method": "days", "weights": {str(alice): 30, str(bob): 20, str(carol): 10}},
        created_by=alice, reason="按入住天数分摊", status="confirmed")
    rv_bb = RuleVersion(
        expense_item_id=broadband.id, version=1,
        split_config={"method": "equal", "weights": {str(alice): 1, str(bob): 1, str(carol): 1}},
        created_by=alice, reason="三人均分", status="confirmed")
    db.add_all([rv_elec, rv_bb])
    db.flush()
    for rv, uids in [(rv_elec, [alice, bob, carol]), (rv_bb, [alice, bob, carol])]:
        for u in uids:
            db.add(RuleConfirmation(rule_version_id=rv.id, user_id=u,
                                    confirmed=True,
                                    confirmed_at=_utcnow()))

    # ---- 订阅（含两笔 68 元会员，验收案例 P-09）----
    subs = [
        Subscription(user_id=alice, merchant="XX视频会员", alias="视频会员",
                     amount_cents=3_000, cycle="monthly", status="active",
                     authorized=True, next_charge_at=_utcnow() + dt.timedelta(days=7)),
        Subscription(user_id=bob, merchant="XX音乐会员A", alias="音乐会员（大号）",
                     amount_cents=6_800, cycle="monthly", status="active",
                     authorized=True, next_charge_at=_utcnow() + dt.timedelta(days=5)),
        Subscription(user_id=bob, merchant="XX音乐会员B", alias="音乐会员（小号）",
                     amount_cents=6_800, cycle="monthly", status="active",
                     authorized=True, next_charge_at=_utcnow() + dt.timedelta(days=6)),
        Subscription(user_id=carol, merchant="XX云盘会员", alias="云盘会员",
                     amount_cents=1_500, cycle="monthly", status="active",
                     authorized=True, next_charge_at=_utcnow() + dt.timedelta(days=10)),
    ]
    db.add_all(subs)

    # ---- 合成理财产品 ----
    products = [
        Product(name="稳盈货币A", risk_level="R1", min_cents=100, term_days=1,
                fee_rate=0, redemption_rule="T+0 快速到账", is_synthetic=True),
        Product(name="安享债券B", risk_level="R2", min_cents=1_000, term_days=30,
                fee_rate=15, redemption_rule="T+1 到账", is_synthetic=True),
        Product(name="均衡混合C", risk_level="R3", min_cents=1_000, term_days=90,
                fee_rate=30, redemption_rule="T+2 到账", is_synthetic=True),
        Product(name="进取股票D", risk_level="R4", min_cents=1_000, term_days=180,
                fee_rate=50, redemption_rule="T+2 到账", is_synthetic=True),
    ]
    db.add_all(products)

    # ---- 卡片 ----
    db.add(Card(user_id=alice, full_number="6258000000000001", masked_number="6258 **** **** 0001",
                status="active", limit_cents=200_000))
    db.add(Card(user_id=bob, full_number="6258000000000002", masked_number="6258 **** **** 0002",
                status="active", limit_cents=100_000))

    # ---- 交易流水（合成）----
    def _tx(user_id, amount, merchant, memo, days_ago, tx_type="debit", category="其他"):
        acct = db.query(Account).filter(Account.user_id == user_id).first()
        return Transaction(
            account_id=acct.id, tx_type=tx_type, amount_cents=amount,
            merchant=merchant, memo=memo, category=category, status="success",
            idempotency_key=f"seed-{user_id}-{merchant}-{amount}-{days_ago}",
            created_at=_utcnow() - dt.timedelta(days=days_ago),
        )

    txns = [
        # 妈妈 Alice 的陌生扣费（异常候选）
        _tx(alice, 9_900, "XX健身俱乐部", "月度扣费", 3, category="健身"),
        _tx(alice, 2_000, "菜市场", "买菜", 5, category="餐饮"),
        # Bob 的两笔 68 元会员扣费（P-09）
        _tx(bob, 6_800, "XX音乐会员A", "月度会员", 2, category="订阅"),
        _tx(bob, 6_800, "XX音乐会员B", "月度会员", 1, category="订阅"),
        _tx(bob, 4_500, "外卖平台", "午餐", 4, category="餐饮"),
        _tx(bob, 4_500, "外卖平台", "午餐", 9, category="餐饮"),
        # Carol 的流水
        _tx(carol, 5_000, "超市", "生活用品", 3, category="日用"),
        _tx(carol, 2_500, "公交地铁", "交通", 6, category="交通"),
    ]
    db.add_all(txns)

    db.commit()
    print("初始化完成：")
    print("  账号：alice / bob / carol（密码 demo123456）")
    print("  共同空间：幸福之家 (id=%d)" % space.id)
    print("  电费 240 元按 30/20/10 分摊；宽带 90 元三人均分")
    print("  分摊结果（分）：", ledger.member_due_summary(db, space.id))


if __name__ == "__main__":
    do_reset = "--reset" in sys.argv
    if do_reset:
        reset_all()
        print("已重置数据库")
    db = SessionLocal()
    try:
        if do_reset or db.query(User).count() == 0:
            if not do_reset:
                Base.metadata.create_all(bind=engine)
            seed(db)
        else:
            print("数据库已有数据；如需重置请加 --reset")
    finally:
        db.close()
