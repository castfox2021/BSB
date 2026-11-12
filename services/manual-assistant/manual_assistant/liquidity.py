from decimal import Decimal

from .ledger import decimal_math, decimal_text

D = Decimal


@decimal_math
def assess(book, budget, max_slippage):
    if not book or not book.get("asks") or not book.get("bids"):
        return False, "상세 호가 대기", None
    ask, bid = D(book["asks"][0][0]), D(book["bids"][0][0])
    if ask <= bid or (ask - bid) / bid > D(".003"):
        return False, "비정상 스프레드", None
    if min(sum(D(p) * D(q) for p, q in book[side]) for side in ("asks", "bids")) < D(
        budget
    ):
        return False, "호가 잔량 부족", None
    left = D(budget)
    qty = D(0)
    spent = D(0)
    for p, q in book["asks"]:
        p, q = D(p), D(q)
        take = min(q, left / p)
        qty += take
        spent += take * p
        left -= take * p
        if left <= D("1e-18"):
            break
    if left > D("1e-18") or qty <= 0:
        return False, "호가 잔량 부족", None
    slip = spent / qty / ask - 1
    if slip > D(max_slippage) + D("1e-24"):
        return False, "예상 슬리피지 초과", decimal_text(slip)
    return True, "정상", decimal_text(slip)
