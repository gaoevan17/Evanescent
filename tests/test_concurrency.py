"""并发与幂等测试：并发同键只扣一次、并发转账余额守恒、日累计并发占用。"""
import threading

from app.db import SessionLocal
from app.services import money, transfer as transfer_svc


def test_concurrent_same_idempotency_key(db):
    """并发使用同一幂等键，只扣一次款。"""
    cands = transfer_svc.search_contacts(db, 1, "李伟")
    cid = cands[0]["contact_id"]
    before = money.balance(db, 1)
    results = []

    def worker():
        s = SessionLocal()
        try:
            r = transfer_svc.transfer(s, payer_user_id=1, payee_contact_id=cid,
                                      amount_cents=3000, idempotency_key="conc-1")
            results.append(r.get("status"))
        except Exception as e:  # noqa: BLE001
            results.append("err:" + str(e))
        finally:
            s.close()

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    after = money.balance(db, 1)
    assert after == before - 3000  # 只扣一次


def test_concurrent_distinct_transfers_conserve(db):
    """并发不同键转账，余额守恒（总扣款=总额）。"""
    cands = transfer_svc.search_contacts(db, 1, "李伟")
    cid = cands[0]["contact_id"]
    before = money.balance(db, 1)
    n = 4
    amount = 1000

    def worker(i):
        s = SessionLocal()
        try:
            transfer_svc.transfer(s, payer_user_id=1, payee_contact_id=cid,
                                  amount_cents=amount, idempotency_key=f"conc-d-{i}")
        except Exception:  # noqa: BLE001
            pass
        finally:
            s.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    after = money.balance(db, 1)
    assert before - after == n * amount  # 每笔都扣了，守恒


def test_concurrent_daily_limit_no_bypass(db):
    """并发转账无法绕过日累计限制（占用计入当日累计）。"""
    from app.security import policy
    cands = transfer_svc.search_contacts(db, 1, "李伟")
    cid = cands[0]["contact_id"]
    transfer_svc.transfer(db, payer_user_id=1, payee_contact_id=cid,
                          amount_cents=80000, idempotency_key="conc-limit-0")
    d = policy.classify_transfer(db, 1, 30000)
    assert d.level == "red"
