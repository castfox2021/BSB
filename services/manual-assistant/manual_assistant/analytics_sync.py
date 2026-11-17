"""Read-only Bithumb historical sync & CSV/XLSX importer for Task 18."""

import asyncio
import csv
import hashlib
import io
import json
import logging
import re
import uuid
import zipfile
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree as ET

from .private_auth import CredentialError, load_credentials
from .private_client import PrivateClient

D = Decimal
KST = timezone(timedelta(hours=9))
logger = logging.getLogger("bsb.manual.analytics")
MAX_IMPORT_BYTES = 10 * 1024 * 1024
MAX_IMPORT_ROWS = 50000
MAX_XLSX_UNCOMPRESSED = 50 * 1024 * 1024

COLUMN_ALIASES = {
    "filled_at": ("체결일시", "거래일시", "일시", "time", "filled_at", "created_at", "date"),
    "market": ("마켓", "종목", "자산", "코인", "market", "symbol", "asset", "pair"),
    "side": ("구분", "유형", "매매구분", "거래유형", "side", "type"),
    "price": ("체결가격", "체결단가", "단가", "가격", "price", "fill_price"),
    "quantity": ("체결수량", "수량", "거래수량", "quantity", "qty", "volume", "fill_quantity"),
    "amount": ("체결금액", "거래금액", "금액", "정산금액", "amount", "total", "fill_amount"),
    "fee": ("수수료", "fee", "fee_amount", "paid_fee"),
    "fee_currency": ("수수료통화", "fee_currency", "fee_asset"),
    "exchange_fill_id": ("체결id", "체결번호", "trade_id", "exchange_fill_id", "fill_id"),
    "exchange_order_id": ("주문id", "주문번호", "order_id", "exchange_order_id", "uuid"),
}


def _clean_num(val) -> str:
    s = str(val or "").strip().replace(",", "")
    if s.startswith(("=", "+", "@", "\t", "\r")):
        s = s.lstrip("=+@\t\r")
    try:
        d = D(s)
        if not d.is_finite():
            return "0"
        return format(d, "f")
    except (InvalidOperation, ValueError):
        return "0"


def _norm_side(val: str) -> str:
    s = str(val or "").strip().upper()
    if any(k in s for k in ("BUY", "BID", "매수")):
        return "BUY"
    if any(k in s for k in ("SELL", "ASK", "매도")):
        return "SELL"
    if any(k in s for k in ("DEPOSIT", "입금")):
        return "DEPOSIT"
    if any(k in s for k in ("WITHDRAW", "출금")):
        return "WITHDRAW"
    return "UNKNOWN"


def _norm_market(val: str) -> str:
    s = str(val or "").strip().upper()
    if not s:
        return ""
    if s.startswith("KRW-"):
        return s
    if s.endswith("-KRW"):
        return "KRW-" + s[:-4]
    if "/" in s:
        a, b = s.split("/", 1)
        return f"KRW-{a}" if b == "KRW" else f"{a}-{b}"
    return f"KRW-{s}"


def _norm_dt(val: str) -> str:
    s = str(val or "").strip().replace(".", "-").replace("/", "-")
    s = re.sub(r"\s+", " ", s)
    if s.endswith("Z"):
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC).isoformat()
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(s, fmt)
                break
            except ValueError:
                dt = None
        if dt is None:
            raise ValueError(f"날짜 형식을 인식할 수 없습니다: {val}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt.astimezone(UTC).isoformat()


def _parse_xlsx_rows(raw_bytes: bytes) -> list[list[str]]:
    with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
        total_uncomp = sum(info.file_size for info in zf.infolist())
        if total_uncomp > MAX_XLSX_UNCOMPRESSED:
            raise ValueError("XLSX 압축 해제 용량이 허용 한도를 초과했습니다.")
        shared: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root:
                texts = [t.text or "" for t in si.iter() if t.tag.endswith("}t") or t.tag == "t"]
                shared.append("".join(texts))
        sheet_names = sorted(
            n for n in zf.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n)
        )
        if not sheet_names:
            raise ValueError("XLSX 시트를 찾을 수 없습니다.")
        sroot = ET.fromstring(zf.read(sheet_names[0]))
        rows: list[list[str]] = []
        for row_el in sroot.iter():
            if not (row_el.tag.endswith("}row") or row_el.tag == "row"):
                continue
            vals: list[str] = []
            for c in row_el:
                if not (c.tag.endswith("}c") or c.tag == "c"):
                    continue
                ctype = c.attrib.get("t", "")
                val_text = ""
                for child in c:
                    if child.tag.endswith("}v") or child.tag == "v":
                        raw_v = child.text or ""
                        if ctype == "s" and raw_v.isdigit() and int(raw_v) < len(shared):
                            val_text = shared[int(raw_v)]
                        else:
                            val_text = raw_v
                    elif child.tag.endswith("}is") or child.tag == "is":
                        val_text = "".join(
                            t.text or "" for t in child.iter() if t.tag.endswith("}t") or t.tag == "t"
                        )
                vals.append(val_text)
            if any(v.strip() for v in vals):
                rows.append(vals)
                if len(rows) > MAX_IMPORT_ROWS + 1:
                    raise ValueError(f"최대 허용 행 수({MAX_IMPORT_ROWS}행)를 초과했습니다.")
        return rows


def parse_import_file(filename: str, raw_bytes: bytes) -> tuple[str, list[dict], list[str]]:
    if len(raw_bytes) > MAX_IMPORT_BYTES:
        raise ValueError("파일 크기는 10MB를 초과할 수 없습니다.")
    lower = filename.lower()
    errors: list[str] = []
    if lower.endswith(".xlsx"):
        fmt = "IMPORTED_XLSX"
        grid = _parse_xlsx_rows(raw_bytes)
    elif lower.endswith(".csv"):
        fmt = "IMPORTED_CSV"
        decoded = None
        for enc in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
            try:
                decoded = raw_bytes.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if decoded is None:
            raise ValueError("CSV 인코딩을 해석할 수 없습니다 (UTF-8/CP949 지원).")
        reader = csv.reader(io.StringIO(decoded))
        grid = [row for row in reader if any(c.strip() for c in row)]
    else:
        raise ValueError("CSV 또는 XLSX 파일만 업로드할 수 있습니다.")

    if len(grid) < 2:
        raise ValueError("헤더와 최소 1개 이상의 데이터 행이 필요합니다.")
    if len(grid) - 1 > MAX_IMPORT_ROWS:
        raise ValueError(f"최대 허용 행 수({MAX_IMPORT_ROWS}행)를 초과했습니다.")

    raw_headers = [h.strip().lower().replace(" ", "") for h in grid[0]]
    col_map: dict[str, int] = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for idx, h in enumerate(raw_headers):
            if any(a.lower().replace(" ", "") in h for a in aliases):
                col_map.setdefault(canonical, idx)
                break

    for req in ("filled_at", "market", "side", "quantity"):
        if req not in col_map:
            raise ValueError(f"필수 컬럼을 찾을 수 없습니다: {req}")

    parsed: list[dict] = []
    for row_num, row in enumerate(grid[1:], start=2):
        try:
            def cell(key: str) -> str:
                idx = col_map.get(key)
                return row[idx].strip() if idx is not None and idx < len(row) else ""

            filled_at = _norm_dt(cell("filled_at"))
            market = _norm_market(cell("market"))
            side = _norm_side(cell("side"))
            qty = _clean_num(cell("quantity"))
            price = _clean_num(cell("price")) if "price" in col_map else "0"
            amount = _clean_num(cell("amount")) if "amount" in col_map else "0"
            if D(amount) <= 0 and D(price) > 0 and D(qty) > 0:
                amount = format(D(price) * D(qty), "f")
            if D(price) <= 0 and D(amount) > 0 and D(qty) > 0:
                price = format(D(amount) / D(qty), "f")
            fee = _clean_num(cell("fee")) if "fee" in col_map else "0"
            fee_cur = (cell("fee_currency") or "KRW").upper()
            if D(qty) <= 0 or side == "UNKNOWN" or not market:
                errors.append(f"{row_num}행: 수량·구분·종목 값이 올바르지 않습니다.")
                continue
            parsed.append(
                {
                    "row_num": row_num,
                    "filled_at": filled_at,
                    "market": market,
                    "side": side,
                    "fill_price": price,
                    "fill_quantity": qty,
                    "fill_amount": amount,
                    "fee_amount": fee,
                    "fee_currency": fee_cur,
                    "exchange_fill_id": cell("exchange_fill_id"),
                    "exchange_order_id": cell("exchange_order_id"),
                }
            )
        except Exception as err:
            errors.append(f"{row_num}행: {err}")
    return fmt, parsed, errors


class AnalyticsSyncService:
    """Read-only Bithumb historical & incremental sync + file import."""

    def __init__(self, store, private_sync=None, env_path: Path | None = None):
        self.store = store
        self.private_sync = private_sync
        self.env_path = env_path
        self.analytics_client: PrivateClient | None = None
        self.sync_lock = asyncio.Lock()
        self.last_success_at: str | None = None
        self.last_error: str | None = None
        self.running = False
        if env_path and env_path.is_file():
            try:
                creds, enabled, code = load_credentials(env_path)
                if creds and enabled and code == "CONFIGURED":
                    self.analytics_client = PrivateClient(creds)
            except (CredentialError, OSError):
                self.analytics_client = None

    def _client(self) -> tuple[PrivateClient | None, str]:
        if self.analytics_client is not None:
            return self.analytics_client, getattr(
                self.analytics_client.auth, "account_id", "analytics"
            )
        if self.private_sync and getattr(self.private_sync, "client", None):
            return self.private_sync.client, getattr(
                self.private_sync, "account", None
            ) or "default"
        return None, "default"

    def _bot_client_order_ids(self, conn) -> set[str]:
        ids: set[str] = set()
        for row in conn.execute(
            "SELECT details_json FROM system_events WHERE type='auto_buy_submitted'"
        ):
            try:
                d = json.loads(row[0])
                if d.get("uuid"):
                    ids.add(str(d["uuid"]))
            except Exception:
                pass
        return ids

    def ingest_existing_private_tables(self, conn) -> dict:
        """Unify records already synced by PrivateSync into exchange_orders & exchange_fills."""
        with self.store.connect() as sconn:
            bot_uuids = self._bot_client_order_ids(sconn)
            last_ord_row = conn.execute(
                "SELECT max(updated_at) AS mx FROM exchange_orders WHERE data_source='BITHUMB_API'"
            ).fetchone()
            last_ord_at = last_ord_row["mx"] if last_ord_row else None
            ord_rows = (
                sconn.execute("SELECT * FROM private_orders WHERE updated_at >= ?", (str(last_ord_at),)).fetchall()
                if last_ord_at
                else sconn.execute("SELECT * FROM private_orders").fetchall()
            )
            last_fill_row = conn.execute(
                "SELECT max(filled_at) AS mx FROM exchange_fills WHERE data_source='BITHUMB_API'"
            ).fetchone()
            last_fill_at = last_fill_row["mx"] if last_fill_row else None
            fill_rows = (
                sconn.execute("SELECT * FROM private_fills WHERE filled_at >= ?", (str(last_fill_at),)).fetchall()
                if last_fill_at
                else sconn.execute("SELECT * FROM private_fills").fetchall()
            )
        inserted_orders = 0
        inserted_fills = 0
        for o in ord_rows:
            od = dict(o)
            origin = "BOT" if od["order_id"] in bot_uuids else "MANUAL"
            conn.execute(
                """INSERT OR IGNORE INTO exchange_orders
                (id,exchange,account_id,exchange_order_id,client_order_id,market,side,order_type,order_price,order_quantity,executed_quantity,remaining_quantity,executed_amount,status,execution_origin,data_source,ordered_at,updated_at,raw_payload)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    uuid.uuid4().hex,
                    "bithumb",
                    od["account_id"],
                    od["order_id"],
                    None,
                    od["market"],
                    "BUY" if str(od["side"]).lower() in ("bid", "buy") else "SELL",
                    "UNKNOWN",
                    None,
                    od["executed_quantity"],
                    od["executed_quantity"],
                    "0",
                    "0",
                    od["state"],
                    origin,
                    "BITHUMB_API",
                    od["created_at"],
                    od["updated_at"],
                    json.dumps({"order_id": od["order_id"], "state": od["state"]}),
                ),
            )
            inserted_orders += conn.execute("SELECT changes()").fetchone()[0]

        for f in fill_rows:
            fd = dict(f)
            origin = "BOT" if fd["order_id"] in bot_uuids else "MANUAL"
            qty = D(str(fd["quantity"]))
            price = D(str(fd["price"]))
            fee = D(str(fd.get("fee_krw") or "0"))
            conn.execute(
                """INSERT OR IGNORE INTO exchange_fills
                (id,exchange,account_id,exchange_fill_id,exchange_order_id,client_order_id,market,side,fill_price,fill_quantity,fill_amount,fee_amount,fee_currency,fee_krw_equiv,fee_eval_note,execution_origin,data_source,source_hash,filled_at,raw_payload)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    uuid.uuid4().hex,
                    "bithumb",
                    fd["account_id"],
                    fd["trade_id"],
                    fd["order_id"],
                    None,
                    fd["symbol"],
                    "BUY" if str(fd["side"]).lower() in ("bid", "buy") else "SELL",
                    str(price),
                    str(qty),
                    format(qty * price, "f"),
                    str(fee),
                    "KRW",
                    str(fee),
                    "recorded_krw",
                    origin,
                    "BITHUMB_API",
                    None,
                    fd["filled_at"],
                    json.dumps({"trade_id": fd["trade_id"], "order_id": fd["order_id"]}),
                ),
            )
            inserted_fills += conn.execute("SELECT changes()").fetchone()[0]
        return {"orders": inserted_orders, "fills": inserted_fills}

    async def sync_history(
        self, start_iso: str | None = None, end_iso: str | None = None
    ) -> dict:
        if self.sync_lock.locked():
            return {"status": "ALREADY_RUNNING"}
        async with self.sync_lock:
            job_id = uuid.uuid4().hex
            now_dt = datetime.now(UTC)
            end_dt = (
                datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
                if end_iso
                else now_dt
            )
            start_dt = (
                datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
                if start_iso
                else end_dt - timedelta(days=1)
            )
            client, account_id = self._client()

            with self.store.identity_connect() as conn:
                conn.execute(
                    "INSERT INTO analytics_sync_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        job_id,
                        "BITHUMB_API",
                        account_id,
                        start_dt.isoformat(),
                        end_dt.isoformat(),
                        None,
                        "RUNNING",
                        0,
                        0,
                        0,
                        None,
                        now_dt.isoformat(),
                        None,
                    ),
                )
                local_res = self.ingest_existing_private_tables(conn)

            fetched = 0
            inserted = local_res["fills"]
            updated = local_res["orders"]
            error_msg = None

            if client is not None:
                try:
                    cur_start = start_dt
                    while cur_start < end_dt:
                        cur_end = min(end_dt, cur_start + timedelta(days=7))
                        for state in ("done", "cancel"):
                            page = 1
                            while True:
                                orders = await client.get(
                                    "/v1/orders",
                                    {
                                        "states[]": state,
                                        "page": page,
                                        "limit": 100,
                                        "order_by": "desc",
                                    },
                                )
                                if not isinstance(orders, list) or not orders:
                                    break
                                fetched += len(orders)
                                for o in orders:
                                    ex_oid = str(o.get("uuid") or "")
                                    if not ex_oid:
                                        continue
                                    exec_vol = D(str(o.get("executed_volume") or "0"))
                                    if exec_vol <= 0:
                                        continue
                                    detail = await client.get(
                                        "/v1/order", {"uuid": ex_oid}
                                    )
                                    trades = detail.get("trades") or []
                                    with self.store.identity_connect() as conn:
                                        with self.store.connect() as sconn:
                                            bot_uuids = self._bot_client_order_ids(sconn)
                                        coid = detail.get("client_order_id")
                                        origin = (
                                            "BOT"
                                            if (
                                                ex_oid in bot_uuids
                                                or (coid and str(coid).startswith("bsb-ab-"))
                                            )
                                            else "MANUAL"
                                        )
                                        side = (
                                            "BUY"
                                            if str(detail.get("side", "")).lower()
                                            in ("bid", "buy")
                                            else "SELL"
                                        )
                                        conn.execute(
                                            """INSERT INTO exchange_orders
                                            (id,exchange,account_id,exchange_order_id,client_order_id,market,side,order_type,order_price,order_quantity,executed_quantity,remaining_quantity,executed_amount,status,execution_origin,data_source,ordered_at,updated_at,raw_payload)
                                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                                            ON CONFLICT (exchange, account_id, exchange_order_id) DO UPDATE SET
                                              status=EXCLUDED.status,
                                              executed_quantity=EXCLUDED.executed_quantity,
                                              remaining_quantity=EXCLUDED.remaining_quantity,
                                              executed_amount=EXCLUDED.executed_amount,
                                              updated_at=EXCLUDED.updated_at,
                                              raw_payload=EXCLUDED.raw_payload""",
                                            (
                                                uuid.uuid4().hex,
                                                "bithumb",
                                                account_id,
                                                ex_oid,
                                                coid,
                                                detail.get("market", ""),
                                                side,
                                                detail.get("ord_type", "UNKNOWN"),
                                                str(detail["price"]) if detail.get("price") not in (None, "") else None,
                                                str(detail.get("volume") or "0"),
                                                str(exec_vol),
                                                str(detail.get("remaining_volume") or "0"),
                                                str(detail.get("executed_funds") or "0"),
                                                detail.get("state", state),
                                                origin,
                                                "BITHUMB_API",
                                                detail.get("created_at") or now_dt.isoformat(),
                                                now_dt.isoformat(),
                                                json.dumps(
                                                    {"uuid": ex_oid, "state": detail.get("state")}
                                                ),
                                            ),
                                        )
                                        for tr in trades:
                                            tid = str(tr.get("uuid") or "")
                                            if not tid:
                                                continue
                                            t_price = D(str(tr.get("price") or "0"))
                                            t_vol = D(str(tr.get("volume") or "0"))
                                            t_funds = D(str(tr.get("funds") or (t_price * t_vol)))
                                            t_fee = D(str(tr.get("fee") or detail.get("paid_fee") or "0"))
                                            conn.execute(
                                                """INSERT OR IGNORE INTO exchange_fills
                                                (id,exchange,account_id,exchange_fill_id,exchange_order_id,client_order_id,market,side,fill_price,fill_quantity,fill_amount,fee_amount,fee_currency,fee_krw_equiv,fee_eval_note,execution_origin,data_source,source_hash,filled_at,raw_payload)
                                                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                                (
                                                    uuid.uuid4().hex,
                                                    "bithumb",
                                                    account_id,
                                                    tid,
                                                    ex_oid,
                                                    coid,
                                                    tr.get("market") or detail.get("market", ""),
                                                    side,
                                                    str(t_price),
                                                    str(t_vol),
                                                    str(t_funds),
                                                    str(t_fee),
                                                    "KRW",
                                                    str(t_fee),
                                                    "api_trade_fee",
                                                    origin,
                                                    "BITHUMB_API",
                                                    None,
                                                    tr.get("created_at") or detail.get("created_at") or now_dt.isoformat(),
                                                    json.dumps({"trade_uuid": tid, "order_uuid": ex_oid}),
                                                ),
                                            )
                                            inserted += conn.execute("SELECT changes()").fetchone()[0]
                                if len(orders) < 100:
                                    break
                                page += 1
                        cur_start = cur_end
                except Exception as err:
                    error_msg = f"{type(err).__name__}: {err}"
                    logger.warning("analytics sync paused: %s", error_msg)

            done_at = datetime.now(UTC).isoformat()
            status = "FAILED" if error_msg else "COMPLETED"
            if not error_msg:
                self.last_success_at = done_at
                self.last_error = None
            else:
                self.last_error = error_msg

            with self.store.identity_connect() as conn:
                conn.execute(
                    "UPDATE analytics_sync_jobs SET status=?,records_fetched=?,records_inserted=?,records_updated=?,error_message=?,completed_at=? WHERE id=?",
                    (status, fetched, inserted, updated, error_msg, done_at, job_id),
                )
            return {
                "job_id": job_id,
                "status": status,
                "records_fetched": fetched,
                "records_inserted": inserted,
                "records_updated": updated,
                "error_message": error_msg,
                "api_connected": client is not None,
            }

    def import_file(
        self, filename: str, raw_bytes: bytes, preview_only: bool = False, account_id: str = "default"
    ) -> dict:
        fmt, rows, parse_errors = parse_import_file(filename, raw_bytes)
        if preview_only:
            return {
                "preview": True,
                "filename": filename,
                "file_format": fmt,
                "total_rows": len(rows),
                "parse_errors": parse_errors[:20],
                "sample": rows[:20],
            }

        batch_id = uuid.uuid4().hex
        now_iso = datetime.now(UTC).isoformat()
        inserted = 0
        duplicates = 0
        conflicts = 0

        with self.store.connect() as conn:
            for r in rows:
                if r["side"] in ("DEPOSIT", "WITHDRAW"):
                    conn.execute(
                        "INSERT INTO account_cash_flows VALUES (?,?,?,?,?,?,?,?)",
                        (
                            uuid.uuid4().hex,
                            account_id,
                            r["market"].replace("KRW-", ""),
                            r["side"],
                            r["fill_quantity"],
                            r["fee_amount"],
                            r["filled_at"],
                            batch_id,
                        ),
                    )
                    inserted += 1
                    continue

                ex_fid = r["exchange_fill_id"]
                src_hash = hashlib.sha256(
                    f"{account_id}|{r['filled_at']}|{r['market']}|{r['side']}|{r['fill_price']}|{r['fill_quantity']}|{ex_fid}".encode()
                ).hexdigest()

                if ex_fid:
                    exists = conn.execute(
                        "SELECT 1 FROM exchange_fills WHERE account_id=? AND exchange_fill_id=?",
                        (account_id, ex_fid),
                    ).fetchone()
                    if exists:
                        duplicates += 1
                        continue
                else:
                    exists_hash = conn.execute(
                        "SELECT 1 FROM exchange_fills WHERE account_id=? AND source_hash=?",
                        (account_id, src_hash),
                    ).fetchone()
                    if exists_hash:
                        duplicates += 1
                        continue
                    # Check ambiguous match with existing API fills (never auto-merge!)
                    t0 = datetime.fromisoformat(r["filled_at"])
                    ambiguous = conn.execute(
                        "SELECT id, filled_at, fill_quantity FROM exchange_fills WHERE account_id=? AND market=? AND side=? AND data_source='BITHUMB_API'",
                        (account_id, r["market"], r["side"]),
                    ).fetchall()
                    is_ambig = False
                    for ab in ambiguous:
                        dt_diff = abs((datetime.fromisoformat(ab["filled_at"]) - t0).total_seconds())
                        if dt_diff <= 60 and D(ab["fill_quantity"]) == D(r["fill_quantity"]):
                            is_ambig = True
                            break
                    if is_ambig:
                        conflicts += 1
                        conn.execute(
                            "INSERT INTO analytics_reconciliation_issues VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (
                                uuid.uuid4().hex,
                                account_id,
                                "AMBIGUOUS_IMPORT_MATCH",
                                r["market"],
                                src_hash,
                                f"API 체결 기록과 유사한 시간/수량의 파일 행이 발견되어 자동 병합하지 않았습니다 ({r['market']} {r['filled_at']}).",
                                json.dumps(r),
                                "OPEN",
                                now_iso,
                                None,
                            ),
                        )
                        continue

                fee_krw = r["fee_amount"] if r["fee_currency"] == "KRW" else "0"
                fee_note = (
                    "imported_krw"
                    if r["fee_currency"] == "KRW"
                    else f"non_krw_fee:{r['fee_currency']}={r['fee_amount']}"
                )
                conn.execute(
                    """INSERT OR IGNORE INTO exchange_fills
                    (id,exchange,account_id,exchange_fill_id,exchange_order_id,client_order_id,market,side,fill_price,fill_quantity,fill_amount,fee_amount,fee_currency,fee_krw_equiv,fee_eval_note,execution_origin,data_source,source_hash,filled_at,raw_payload)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        uuid.uuid4().hex,
                        "bithumb",
                        account_id,
                        ex_fid or None,
                        r["exchange_order_id"] or None,
                        None,
                        r["market"],
                        r["side"],
                        r["fill_price"],
                        r["fill_quantity"],
                        r["fill_amount"],
                        r["fee_amount"],
                        r["fee_currency"],
                        fee_krw,
                        fee_note,
                        "UNKNOWN",
                        fmt,
                        src_hash,
                        r["filled_at"],
                        json.dumps({"row_num": r["row_num"], "batch": batch_id}),
                    ),
                )
                inserted += conn.execute("SELECT changes()").fetchone()[0]

            conn.execute(
                "INSERT INTO analytics_import_batches VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    batch_id,
                    account_id,
                    filename[:120],
                    fmt,
                    len(rows),
                    inserted,
                    duplicates,
                    conflicts,
                    "COMPLETED",
                    json.dumps(parse_errors[:50]),
                    now_iso,
                ),
            )
        return {
            "batch_id": batch_id,
            "filename": filename,
            "file_format": fmt,
            "total_rows": len(rows),
            "inserted_rows": inserted,
            "duplicate_rows": duplicates,
            "conflict_rows": conflicts,
            "parse_errors": parse_errors[:20],
        }

    def status(self) -> dict:
        _, account_id = self._client()
        with self.store.identity_connect() as conn:
            jobs = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM analytics_sync_jobs ORDER BY started_at DESC LIMIT 10"
                ).fetchall()
            ]
            batches = [
                dict(r)
                for r in conn.execute(
                    "SELECT id,filename,file_format,total_rows,inserted_rows,duplicate_rows,conflict_rows,status,created_at FROM analytics_import_batches ORDER BY created_at DESC LIMIT 10"
                ).fetchall()
            ]
            fill_row = conn.execute("SELECT count(*) AS cnt FROM exchange_fills").fetchone()
            fill_count = int(fill_row["cnt"]) if fill_row else 0
            issue_row = conn.execute(
                "SELECT count(*) AS cnt FROM analytics_reconciliation_issues WHERE status='OPEN'"
            ).fetchone()
            issue_count = int(issue_row["cnt"]) if issue_row else 0
        return {
            "account_id": account_id,
            "api_connected": self._client()[0] is not None,
            "last_success_at": self.last_success_at,
            "last_error": self.last_error,
            "total_exchange_fills": fill_count,
            "open_reconciliation_issues": issue_count,
            "recent_jobs": jobs,
            "recent_imports": batches,
        }

    async def run(self):
        self.running = True
        while True:
            try:
                await self.sync_history()
            except Exception as err:
                self.last_error = f"{type(err).__name__}: {err}"
                logger.warning("analytics background sync error: %s", self.last_error)
            await asyncio.sleep(60)
