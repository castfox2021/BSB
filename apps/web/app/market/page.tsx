'use client';
import { useEffect, useState } from 'react';
import { PublicFooter, PublicHeader } from '../components/public-nav';
import { priceText } from '../quiet-types';

type Ticker = {
  exchange?: string;
  symbol: string;
  coin_name: string;
  price: string | null;
  ready: boolean;
  change_1m?: string | null;
  change_3m?: string | null;
  change_5m?: string | null;
  change_24h?: string | null;
};

export default function PublicMarketPage() {
  const [exchange, setExchange] = useState<'bithumb' | 'upbit'>('bithumb');
  const [items, setItems] = useState<Ticker[]>([]);
  const [q, setQ] = useState('');
  const [page, setPage] = useState(1);
  const pageSize = 25;

  useEffect(() => {
    const load = () =>
      fetch(`/api/manual/public/exchanges/tickers?exchange=${exchange}&limit=300`)
        .then((r) => (r.ok ? r.json() : { items: [] }))
        .then((d) => setItems(d.items ?? []))
        .catch(() => {});
    load();
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [exchange]);

  const filtered = items.filter(
    (it) =>
      !q.trim() ||
      it.symbol.toLowerCase().includes(q.trim().toLowerCase()) ||
      it.coin_name.toLowerCase().includes(q.trim().toLowerCase())
  );
  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const visibleItems = filtered.slice((safePage - 1) * pageSize, safePage * pageSize);

  const fmtPct = (v?: string | null) =>
    v == null ? '—' : `${Number(v) > 0 ? '+' : ''}${(Number(v) * 100).toFixed(2)}%`;

  return (
    <div className="min-h-screen bg-[#0B0E14] text-zinc-100">
      <PublicHeader />
      <main className="mx-auto max-w-6xl space-y-6 px-4 py-8 sm:px-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-bold">실시간 시장 (공개 · 다중 거래소)</h1>
            <p className="mt-1 text-xs text-zinc-400">Bithumb 및 Upbit KRW 마켓 공개 시세</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {(['bithumb', 'upbit'] as const).map((ex) => (
              <button
                key={ex}
                type="button"
                onClick={() => setExchange(ex)}
                className={`rounded-lg px-3 py-1.5 text-xs font-semibold uppercase ${
                  exchange === ex ? 'bg-zinc-100 text-zinc-950' : 'border border-[#1E2536] bg-[#121620] text-zinc-400'
                }`}
              >
                {ex}
              </button>
            ))}
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="코인 검색 (BTC, XPR...)"
              className="rounded-lg border border-[#1E2536] bg-[#121620] px-3.5 py-1.5 text-xs text-zinc-100 focus:border-zinc-500 focus:outline-none"
            />
          </div>
        </div>

        <div className="overflow-x-auto rounded-xl border border-[#1E2536] bg-[#121620] p-4">
          <table className="w-full min-w-[540px] text-left font-mono text-xs tabular-nums">
            <thead className="border-b border-[#1E2536] text-[11px] text-zinc-400">
              <tr>
                <th className="py-2.5">종목</th>
                <th className="py-2.5">현재가</th>
                <th className="py-2.5">{exchange === 'upbit' ? '24시간' : '1분'}</th>
                <th className="py-2.5">3분</th>
                <th className="py-2.5">5분</th>
                <th className="py-2.5">상태</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#1E2536]/60">
              {visibleItems.map((it) => {
                const c1 = exchange === 'upbit' ? it.change_24h : it.change_1m;
                return (
                  <tr key={it.symbol}>
                    <td className="py-2.5 font-sans font-medium text-zinc-200">
                      {it.coin_name} <span className="font-mono text-zinc-500">({it.symbol.replace('KRW-', '')})</span>
                    </td>
                    <td className="py-2.5">{priceText(it.price)}원</td>
                    <td className={`py-2.5 ${Number(c1) > 0 ? 'text-rose-400' : Number(c1) < 0 ? 'text-sky-400' : 'text-zinc-400'}`}>
                      {fmtPct(c1)}
                    </td>
                    <td className="py-2.5 text-zinc-400">{fmtPct(it.change_3m)}</td>
                    <td className="py-2.5 text-zinc-400">{fmtPct(it.change_5m)}</td>
                    <td className="py-2.5 text-zinc-400">{it.ready ? '정상' : '수집 중'}</td>
                  </tr>
                );
              })}
              {!filtered.length && (
                <tr>
                  <td colSpan={6} className="py-8 text-center font-sans text-zinc-500">
                    표시할 종목이 없습니다.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="flex items-center justify-between font-mono text-xs text-zinc-400">
          <span>
            {safePage} / {totalPages} 페이지 · 총 {filtered.length}종목
          </span>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={safePage <= 1}
              onClick={() => setPage(safePage - 1)}
              className="rounded-lg border border-[#1E2536] bg-[#121620] px-3 py-1.5 text-zinc-200 disabled:opacity-40"
            >
              이전
            </button>
            <button
              type="button"
              disabled={safePage >= totalPages}
              onClick={() => setPage(safePage + 1)}
              className="rounded-lg border border-[#1E2536] bg-[#121620] px-3 py-1.5 text-zinc-200 disabled:opacity-40"
            >
              다음
            </button>
          </div>
        </div>
      </main>
      <PublicFooter />
    </div>
  );
}
