'use client';
import { useEffect, useState } from 'react';
import { api, kst, money } from './trading-types';
import { CandleChart } from './trading-review';
import { PaperTrade, signedMoney, priceText, quantityText, pnlColor } from './quiet-types';

type Chart = Parameters<typeof CandleChart>[0]['data'];

export default function QuietDetail({
  trade,
  symbol,
  onRecord,
}: {
  trade: PaperTrade | null;
  symbol: string;
  onRecord: (t: PaperTrade) => void;
}) {
  const [data, setData] = useState<Chart>(null);
  const [tf, setTf] = useState(60);
  const [then, setThen] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    setData(null);
    const end = trade ? Math.min(Date.now(), Date.parse(trade.at) + 1800000) : Date.now();
    const begin = trade ? Date.parse(trade.at) - 1800000 : end - 5400000;
    const params = new URLSearchParams({
      symbol: trade?.symbol ?? symbol,
      timeframe: String(tf),
      from_at: new Date(begin).toISOString(),
      to_at: new Date(end).toISOString(),
      ...(then && trade ? { cursor: trade.at } : {}),
    });

    api<NonNullable<Chart>>(`trading/chart?${params}`)
      .then((c) => {
        if (active) {
          const markers =
            trade && Date.parse(trade.at) <= Date.parse(c.cursor)
              ? [
                  {
                    id: trade.id,
                    kind: 'FILL',
                    at: trade.at,
                    price: trade.fill_price,
                    side: trade.side,
                    quantity: trade.quantity,
                    partial: false,
                    source: 'PAPER',
                  },
                ]
              : [];
          const stop = (trade as (PaperTrade & { stop_price?: string }))?.stop_price;
          setData({ ...c, reference_lines: stop ? { stop, target: '' } : null, markers, evidence: [] });
          setError('');
        }
      })
      .catch((e) => {
        if (active) setError(e instanceof Error ? e.message : '차트 조회 실패');
      });

    return () => {
      active = false;
    };
  }, [trade, symbol, tf, then]);

  return (
    <section className="rounded-xl border border-[#1E2536] bg-[#121620] p-6">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#1E2536]/80 pb-4">
        <h2 className="text-base font-semibold text-zinc-100">
          <span className="font-mono">{trade?.symbol ?? symbol}</span>
          <span className="mx-2 text-zinc-600">·</span>
          <span>{trade ? '가상 체결 상세' : '매매 차트'}</span>
        </h2>

        <div className="flex flex-wrap items-center gap-3 text-xs">
          <select
            aria-label="가상 체결 차트 분봉"
            className="rounded-md border border-[#1E2536] bg-[#0B0E14] px-3 py-1.5 font-medium text-zinc-200 focus:border-zinc-500 focus:outline-none"
            value={tf}
            onChange={(e) => setTf(Number(e.target.value))}
          >
            {[60, 180, 300].map((n) => (
              <option key={n} value={n}>
                {n / 60}분봉
              </option>
            ))}
          </select>

          {trade && (
            <label className="inline-flex items-center gap-2 text-zinc-400">
              <input
                type="checkbox"
                checked={then}
                onChange={(e) => setThen(e.target.checked)}
                className="rounded border-zinc-700 bg-zinc-900"
              />
              <span>체결 시점 캔들만 보기</span>
            </label>
          )}
        </div>
      </div>

      <div className="mt-5">
        <CandleChart data={data} paper />
      </div>

      {trade && (
        <div className="mt-6 rounded-lg border border-[#1E2536] bg-[#0E1119] p-5">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#1E2536]/60 pb-3">
            <h3 className={`text-sm font-semibold ${trade.side === 'BUY' ? 'text-rose-400' : 'text-sky-400'}`}>
              {trade.side === 'BUY' ? '가상매수' : '가상매도'} 체결 · 시뮬레이션
            </h3>
            <span className="font-mono text-xs tabular-nums text-zinc-400">{kst(trade.at)}</span>
          </div>

          <div className="mt-4 grid gap-4 sm:grid-cols-4 text-xs">
            <div>
              <p className="text-zinc-500">체결가</p>
              <p className="mt-1 font-mono text-sm font-semibold tabular-nums text-zinc-100">
                {trade.fill_price ? `${priceText(trade.fill_price)}원` : '미기록'}
              </p>
            </div>
            <div>
              <p className="text-zinc-500">수량</p>
              <p className="mt-1 font-mono text-sm font-medium tabular-nums text-zinc-200">
                {quantityText(trade.quantity)}
              </p>
            </div>
            <div>
              <p className="text-zinc-500">가상 금액</p>
              <p className="mt-1 font-mono text-sm font-medium tabular-nums text-zinc-200">{money(trade.amount_krw)}</p>
            </div>
            <div>
              <p className="text-zinc-500">가정 수수료 / 슬리피지</p>
              <p className="mt-1 font-mono text-xs tabular-nums text-zinc-400">
                {money(trade.fee_krw)} / {money(trade.slippage_krw)}
              </p>
            </div>
          </div>

          {trade.side === 'SELL' && (
            <div className="mt-4 flex items-center gap-2 border-t border-[#1E2536]/60 pt-3 text-sm">
              <span className="text-zinc-400">실현 순손익</span>
              <strong className={`font-mono font-semibold tabular-nums ${pnlColor(trade.net_pnl)}`}>
                {signedMoney(trade.net_pnl)}
              </strong>
            </div>
          )}

          <div className="mt-5">
            <button
              className="rounded-lg border border-[#2A344B] bg-[#181E2B] px-4 py-2 text-xs font-medium text-zinc-200 hover:border-zinc-500 transition"
              onClick={() => onRecord(trade)}
            >
              실제 체결 수동 등록하기
            </button>
          </div>

          <div className="mt-5 border-t border-[#1E2536]/60 pt-4 text-xs">
            <h4 className="font-medium text-zinc-300">당시 {trade.side === 'BUY' ? '매수' : '매도'} 판단 근거</h4>
            <p className="mt-2 text-zinc-200">{trade.reason_summary}</p>
            {trade.reasons?.map((r, i) => (
              <p key={i} className="mt-1 text-zinc-400">
                · {r}
              </p>
            ))}
            <p className="mt-3 font-mono text-[11px] text-zinc-500">전략 버전 {trade.version_id}</p>
            {trade.legacy_missing_execution_fields && (
              <p className="mt-2 text-amber-300">이전 기록에는 체결 가격/수량이 없습니다.</p>
            )}
          </div>

          <details className="mt-4 text-xs">
            <summary className="cursor-pointer text-zinc-400 hover:text-zinc-200">보존된 전략·판단 원시 데이터</summary>
            <pre className="mt-2.5 max-h-80 overflow-auto whitespace-pre-wrap break-all rounded-md border border-[#1E2536] bg-[#0B0E14] p-3 font-mono text-[11px] text-zinc-400">
              {JSON.stringify({ strategy: trade.strategy_snapshot, basis: trade.basis, reason: trade.reason }, null, 2)}
            </pre>
          </details>
        </div>
      )}

      {error && <p className="mt-3 text-xs text-rose-300">{error}</p>}
    </section>
  );
}
