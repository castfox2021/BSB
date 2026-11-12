'use client';
import { useCallback, useEffect, useState } from 'react';
import { HistoryPager, useHistory } from './history-paging';
import { api, kst, money, quantityText } from './trading-types';

type Status = {
  enabled: boolean;
  configured: boolean;
  connection_state: string;
  rest_fresh: boolean;
  ws_connected: boolean;
  error_code: string | null;
  message: string;
  reconciled: boolean;
  imported_fills: number;
  history_complete: boolean;
  pending_orders: number;
  strategy_capital_krw?: string;
};

type Account = { currency: string; balance: string; locked: string; average_price: string; updated_at: string };
type Fill = {
  trade_id: string;
  order_id: string;
  symbol: string;
  side: string;
  quantity: string;
  price: string;
  fee_krw: string | null;
  fee_status: string;
  filled_at: string;
  notes: string;
};
type Order = {
  order_id: string;
  market: string;
  side: string;
  state: string;
  executed_quantity: string;
  paid_fee: string;
  created_at: string;
};
type Control = { mode: string; state: string; updated_at: string; live_order_enabled: false };

const button =
  'rounded-md border border-[#1E2536] bg-[#181E2B] px-3 py-1.5 text-xs font-medium text-zinc-200 hover:border-zinc-500 disabled:opacity-30 transition';

export default function PrivateAccount() {
  const [status, setStatus] = useState<Status | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [control, setControl] = useState<Control | null>(null);
  const [visible, setVisible] = useState(false);
  const [notice, setNotice] = useState('');
  const [day, setDay] = useState('');
  const [approval, setApproval] = useState('');
  const [market, setMarket] = useState('KRW-BTC');
  const [chance, setChance] = useState<{
    bid_fee?: string;
    ask_fee?: string;
    bid?: { min_total?: string };
    ask?: { min_total?: string };
  } | null>(null);

  const journal = useHistory<Fill>('journal', day ? `day=${day}` : '');
  const orderHistory = useHistory<Order>('private-orders');
  const fills = journal.data?.items ?? [];
  const orders = orderHistory.data?.items ?? [];

  const load = useCallback(async () => {
    try {
      const [s, a, c] = await Promise.all([
        api<Status>('private/status'),
        api<{ items: Account[] }>('private/accounts'),
        api<Control>('trading/control'),
      ]);
      setStatus(s);
      setAccounts(a.items);
      setControl(c);
    } catch {
      setNotice('계좌 연결 상태를 읽을 수 없습니다.');
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [load]);

  async function change(path: string, data: unknown) {
    try {
      await api(path, 'POST', data);
      setNotice('저장했습니다.');
      await load();
    } catch (e) {
      setNotice(e instanceof Error ? e.message : '요청 실패');
    }
  }

  return (
    <div className="space-y-6">
      <section className="rounded-xl border border-[#1E2536] bg-[#121620] p-6">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#1E2536]/80 pb-4">
          <h2 className="text-base font-semibold text-zinc-100">빗썸 계좌 · 조회 전용</h2>
          <span className="font-mono text-xs text-zinc-400">
            전략 기준 자본 {Number(status?.strategy_capital_krw ?? '150000').toLocaleString('ko-KR')}원
          </span>
        </div>

        <p className="mt-4 font-mono text-xs text-zinc-200">
          {status?.connection_state ?? '연결 확인 중'} · REST {status?.rest_fresh ? '최신' : '미연결'} · Private WS v2{' '}
          {status?.ws_connected ? '연결' : '미연결'}
        </p>
        <p className="mt-1.5 text-xs text-zinc-400">
          {status?.message} · 체결 {status?.imported_fills ?? 0}건 · 원장 대조 {status?.reconciled ? '완료' : '필요'} ·
          복구 주문 {status?.pending_orders ?? 0}건
        </p>
        <p className="mt-2 text-[11px] text-zinc-500">API 키는 루트 .env에서 읽습니다.</p>
        <div className="mt-4 flex items-center gap-3">
          <button className={button} onClick={() => change('private/sync', {})}>
            조회 동기화 요청
          </button>
          {notice && (
            <p role="status" className="text-xs text-zinc-300">
              {notice}
            </p>
          )}
        </div>
      </section>

      <section className="rounded-xl border border-[#1E2536] bg-[#121620] p-6">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold text-zinc-100">실제 계좌 자산</h2>
          <button className={button} onClick={() => setVisible(!visible)}>
            {visible ? '잔고 숨기기' : '잔고 표시'}
          </button>
        </div>
        {visible && (
          <div className="mt-4 divide-y divide-[#1E2536]/60 text-xs">
            {accounts.map((a) => (
              <p className="py-2 font-mono tabular-nums text-zinc-300" key={a.currency}>
                <strong className="text-zinc-100">{a.currency}</strong> · 사용 가능 {a.balance} · 잠김 {a.locked} ·
                평단 {a.average_price} · {kst(a.updated_at)}
              </p>
            ))}
            {!accounts.length && <p className="py-4 text-center text-zinc-500">조회된 자산이 없습니다.</p>}
          </div>
        )}
      </section>

      <section className="rounded-xl border border-[#1E2536] bg-[#121620] p-6">
        <div className="border-b border-[#1E2536]/80 pb-4">
          <h2 className="text-base font-semibold text-zinc-100">일자별 자동 매매노트 (KST)</h2>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2">
          <input
            aria-label="자동 매매노트 날짜"
            type="date"
            className="rounded-md border border-[#1E2536] bg-[#0B0E14] px-3 py-1.5 text-xs text-zinc-200"
            value={day}
            onChange={(e) => setDay(e.target.value)}
          />
          <button className={button} onClick={() => setDay('')}>
            최근 전체
          </button>
        </div>

        <HistoryPager label="자동 매매노트" {...journal} />

        <div className="mt-3 space-y-2">
          {fills.map((f) => (
            <article key={f.trade_id} className="rounded-lg border border-[#1E2536] bg-[#0E1119] p-3.5 text-xs">
              <strong className="font-mono text-zinc-100">
                {f.symbol} · {f.side}
              </strong>
              <p className="mt-1.5 font-mono tabular-nums text-zinc-300">
                {kst(f.filled_at)} · 수량 {quantityText(f.quantity)} · {money(f.price)} · 수수료{' '}
                {f.fee_krw === null ? '미확인' : money(f.fee_krw)}
              </p>
              <p className="mt-1 font-mono text-[11px] text-zinc-500">
                {f.fee_status} · {f.trade_id}
              </p>
              <Note fill={f} save={change} />
            </article>
          ))}
          {!fills.length && <p className="py-4 text-center text-xs text-zinc-500">수집된 체결이 없습니다.</p>}
        </div>

        <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-[#1E2536]/60 pt-4">
          <input
            aria-label="원장 대조 승인 문구"
            className="min-w-[220px] flex-1 rounded-md border border-[#1E2536] bg-[#0B0E14] px-3 py-1.5 text-xs text-zinc-200"
            placeholder="계좌·전략원장 대조 완료"
            value={approval}
            onChange={(e) => setApproval(e.target.value)}
          />
          <button
            className={button}
            disabled={
              approval !== '계좌·전략원장 대조 완료' ||
              !status?.rest_fresh ||
              !status?.ws_connected ||
              !status?.history_complete
            }
            onClick={() => change('private/reconcile', { approval })}
          >
            원장 대조 확인
          </button>
        </div>
      </section>

      <div className="grid gap-6 lg:grid-cols-2">
        <section className="rounded-xl border border-[#1E2536] bg-[#121620] p-6">
          <h2 className="text-base font-semibold text-zinc-100">종목별 주문 가능 정보</h2>
          <div className="mt-4 flex gap-2">
            <input
              aria-label="주문 가능 정보 종목"
              className="w-32 rounded-md border border-[#1E2536] bg-[#0B0E14] px-3 py-1.5 font-mono text-xs text-zinc-100"
              value={market}
              onChange={(e) => setMarket(e.target.value.toUpperCase())}
            />
            <button
              className={button}
              onClick={async () => {
                try {
                  const data = await api<NonNullable<typeof chance>>(
                    `private/chance?market=${encodeURIComponent(market)}`
                  );
                  setChance(data);
                } catch (e) {
                  setNotice(e instanceof Error ? e.message : '조회 실패');
                }
              }}
            >
              수수료·최소금액 조회
            </button>
          </div>
          {chance && (
            <p className="mt-3 font-mono text-xs tabular-nums text-zinc-300">
              매수 수수료 {chance.bid_fee ?? '미확인'} · 매도 수수료 {chance.ask_fee ?? '미확인'} · 최소 매수{' '}
              {money(chance.bid?.min_total)} · 최소 매도 {money(chance.ask?.min_total)}
            </p>
          )}
        </section>

        <section className="rounded-xl border border-[#1E2536] bg-[#121620] p-6">
          <h2 className="text-base font-semibold text-zinc-100">실주문 제어</h2>
          <p className="mt-2 font-mono text-xs text-zinc-300">
            상태 {control?.state ?? 'STOPPED'} · 참고 모드 {control?.mode ?? 'MANUAL_APPROVAL'}
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <button className={button} onClick={() => change('trading/control', { action: 'stop' })}>
              전체 주문 정지 · 긴급 정지 해제
            </button>
            <button className={button} onClick={() => change('trading/control', { action: 'emergency-stop' })}>
              시스템 긴급 정지
            </button>
          </div>
        </section>
      </div>

      <section className="rounded-xl border border-[#1E2536] bg-[#121620] p-6">
        <h2 className="text-base font-semibold text-zinc-100">주문 조회 내역</h2>
        <HistoryPager label="계좌 주문 조회" {...orderHistory} />
        <div className="mt-3 space-y-2">
          {orders.map((o) => (
            <p key={o.order_id} className="font-mono text-xs tabular-nums text-zinc-300">
              {o.market} · {o.side} · {o.state} · 체결 {quantityText(o.executed_quantity)} · 수수료 {o.paid_fee} ·{' '}
              {kst(o.created_at)}
            </p>
          ))}
        </div>
      </section>
    </div>
  );
}

function Note({ fill, save }: { fill: Fill; save: (path: string, data: unknown) => Promise<void> }) {
  const [note, setNote] = useState(fill.notes);
  return (
    <div className="mt-2.5 flex gap-2">
      <input
        aria-label={`${fill.trade_id} 복기 메모`}
        className="min-w-0 flex-1 rounded-md border border-[#1E2536] bg-[#0B0E14] px-2.5 py-1.5 text-xs text-zinc-200"
        maxLength={2000}
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder="매매 복기 메모"
      />
      <button className={button} onClick={() => save('private/notes', { trade_id: fill.trade_id, notes: note })}>
        저장
      </button>
    </div>
  );
}
