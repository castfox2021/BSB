'use client';
import { FormEvent, useEffect, useRef, useState } from 'react';
import { api, kst, money, Signal, Position } from './trading-types';
import { PaperTrade } from './quiet-types';
import { TradeHistory } from './history-paging';

const input =
  'mt-1.5 w-full rounded-lg border border-[#1E2536] bg-[#0B0E14] px-3 py-2 text-sm font-mono tabular-nums text-zinc-100 focus:border-zinc-500 focus:outline-none';
const button =
  'rounded-lg border border-[#2A344B] bg-[#181E2B] px-4 py-2 text-xs font-medium text-zinc-200 hover:border-zinc-500 disabled:opacity-40 transition';

export default function ManualFillPanel({
  followTrade = null,
  onUnlink,
}: {
  followTrade?: PaperTrade | null;
  onUnlink?: () => void;
}) {
  useEffect(() => {
    if (followTrade) {
      setFill((f) => ({ ...f, symbol: followTrade.symbol, side: followTrade.side, signal_id: '' }));
    }
  }, [followTrade]);

  const [fill, setFill] = useState({
    symbol: 'KRW-BTC',
    side: 'BUY',
    quantity: '',
    price: '',
    fee_krw: '',
    filled_at: '',
    notes: '',
    signal_id: '',
  });
  const [confirmed, setConfirmed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState('');
  const [snapshot, setSnapshot] = useState<{ signals: { items: Signal[] } } | null>(null);
  const [positions, setPositions] = useState<{ items: (Position & { opened_at?: string })[] } | null>(null);
  const fillId = useRef('');

  useEffect(() => {
    setFill((f) => ({
      ...f,
      filled_at: new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 19),
    }));
    let active = true;
    const load = async () => {
      try {
        const [s, p] = await Promise.all([api<{ items: Signal[] }>('signals'), api<{ items: Position[] }>('positions')]);
        if (active) {
          setSnapshot({ signals: s });
          setPositions(p);
        }
      } catch {
        if (active) setNotice('실제 등록 원장을 읽을 수 없습니다.');
      }
    };
    load();
    const id = setInterval(load, 5000);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, []);

  const record = async (event: FormEvent) => {
    event.preventDefault();
    if (!confirmed) return;
    setSaving(true);
    setNotice('');
    try {
      if (!fillId.current) fillId.current = crypto.randomUUID();
      await api('manual-fills', 'POST', {
        ...fill,
        signal_id: followTrade ? undefined : fill.signal_id || undefined,
        paper_trade_id: followTrade?.id,
        id: fillId.current,
        filled_at: new Date(fill.filled_at).toISOString(),
        confirmed: true,
      });
      fillId.current = '';
      setConfirmed(false);
      setFill((f) => ({ ...f, quantity: '', price: '', fee_krw: '', notes: '', signal_id: '' }));
      setNotice('실제 체결을 등록했습니다.');
    } catch (e) {
      setNotice(`${String(e)} · 재시도 시 같은 ID를 사용합니다.`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-5">
      {followTrade && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-emerald-500/30 bg-[#121620] px-4 py-3 text-xs text-zinc-200">
          <span>
            연결된 가상 체결: <strong className="font-mono">{followTrade.symbol}</strong> ·{' '}
            {followTrade.side === 'BUY' ? '가상매수' : '가상매도'} · {kst(followTrade.at)}
          </span>
          <button type="button" className="text-zinc-400 underline hover:text-zinc-200" onClick={onUnlink}>
            연결 해제
          </button>
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        <section className="rounded-xl border border-[#1E2536] bg-[#121620] p-6">
          <div className="border-b border-[#1E2536]/80 pb-4">
            <h2 className="text-base font-semibold text-zinc-100">실제 확인 체결 등록</h2>
            <p className="mt-1 text-xs text-zinc-400">빗썸에서 이미 체결된 내역을 원장에 기록합니다.</p>
          </div>

          <form className="mt-5 space-y-4" onSubmit={record}>
            <div className="grid grid-cols-2 gap-4">
              <label className="text-xs font-medium text-zinc-300">
                종목
                <input
                  required
                  className={input}
                  value={fill.symbol}
                  onChange={(e) => setFill({ ...fill, symbol: e.target.value.toUpperCase() })}
                />
              </label>
              <label className="text-xs font-medium text-zinc-300">
                구분
                <select
                  className={`${input} font-sans`}
                  value={fill.side}
                  onChange={(e) => setFill({ ...fill, side: e.target.value, signal_id: '' })}
                >
                  <option>BUY</option>
                  <option>SELL</option>
                </select>
              </label>
            </div>

            {[
              ['quantity', '체결 수량'],
              ['price', '평균 체결가 (원)'],
              ['fee_krw', '수수료 (원, 무료면 0)'],
            ].map(([key, label]) => (
              <label key={key} className="block text-xs font-medium text-zinc-300">
                {label}
                <input
                  required
                  inputMode="decimal"
                  className={input}
                  value={fill[key as 'quantity' | 'price' | 'fee_krw']}
                  onChange={(e) => setFill({ ...fill, [key]: e.target.value })}
                />
              </label>
            ))}

            <label className="block text-xs font-medium text-zinc-300">
              체결 시각
              <input
                type="datetime-local"
                step="1"
                required
                className={input}
                value={fill.filled_at}
                onChange={(e) => setFill({ ...fill, filled_at: e.target.value })}
              />
            </label>

            <label className="block text-xs font-medium text-zinc-300">
              연결할 신호 (선택)
              <select
                disabled={!!followTrade}
                className={`${input} font-sans`}
                value={fill.signal_id}
                onChange={(e) => setFill({ ...fill, signal_id: e.target.value })}
              >
                <option value="">직접 판단 / 연결 안 함</option>
                {snapshot?.signals.items
                  .filter(
                    (s) =>
                      s.symbol === fill.symbol &&
                      (fill.side === 'BUY' ? s.type === 'BUY_CANDIDATE' : ['SELL_CANDIDATE', 'RISK_EXIT'].includes(s.type))
                  )
                  .map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.type} · {kst(s.generated_at)}
                    </option>
                  ))}
              </select>
            </label>

            <textarea
              aria-label="체결 메모"
              placeholder="부분체결·실행 지연 등 메모"
              className={`${input} font-sans`}
              value={fill.notes}
              onChange={(e) => setFill({ ...fill, notes: e.target.value })}
            />

            <label className="flex items-center gap-2 text-xs text-zinc-300">
              <input
                type="checkbox"
                required
                checked={confirmed}
                onChange={(e) => setConfirmed(e.target.checked)}
                className="rounded border-zinc-700 bg-zinc-900"
              />
              <span>빗썸에서 실제 체결된 내용임을 확인했습니다.</span>
            </label>

            <button
              className="rounded-lg bg-zinc-100 px-5 py-2.5 text-xs font-semibold text-zinc-950 hover:bg-white disabled:bg-zinc-800 disabled:text-zinc-500 transition"
              disabled={saving || !confirmed}
            >
              체결 기록 저장
            </button>
          </form>
        </section>

        <section className="rounded-xl border border-[#1E2536] bg-[#121620] p-6">
          <div className="border-b border-[#1E2536]/80 pb-4">
            <h2 className="text-base font-semibold text-zinc-100">등록 보유분 · 참고선</h2>
          </div>

          <div className="mt-4 space-y-3">
            {positions?.items.map((p) => (
              <article key={p.symbol} className="rounded-lg border border-[#1E2536] bg-[#0E1119] p-4 text-xs">
                <strong className="font-mono text-sm font-semibold text-zinc-100">{p.symbol}</strong>
                <div className="mt-2 grid grid-cols-2 gap-2 font-mono tabular-nums text-zinc-300">
                  <span>수량 {p.quantity}</span>
                  <span>평단 {money(p.average_entry_price)}</span>
                  <span className="text-sky-400">손절 참고 {money(p.stop_reference)}</span>
                  <span className="text-rose-400">익절 참고 {money(p.target_reference)}</span>
                </div>
                <p className="mt-2 font-mono tabular-nums text-zinc-400">
                  예상 평가 {money(p.net_estimate_krw)} · 시세 {money(p.mark_price)}
                </p>
              </article>
            ))}
            {!positions?.items.length && (
              <p className="py-6 text-center text-xs text-zinc-500">등록 원장에 보유 수량이 없습니다.</p>
            )}
          </div>

          <div className="mt-6 border-t border-[#1E2536]/60 pt-4">
            <TradeHistory />
          </div>
        </section>
      </div>

      {notice && <p className="text-xs font-medium text-zinc-300">{notice}</p>}
    </div>
  );
}
