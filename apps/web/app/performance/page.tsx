'use client';
import { useEffect, useState } from 'react';
import { PublicFooter, PublicHeader } from '../components/public-nav';
import { pnlColor, signedMoney } from '../quiet-types';
import { kst } from '../trading-types';

type PerfData = {
  policies: { policy: string; trades: number; realized: string; win_rate: string | null; profit_factor: string | null; max_drawdown: string }[];
  recent_anonymized_results: { symbol: string; coin_name: string; exit_reason: string; net_pnl: string; closed_at: string }[];
};

export default function PublicPerformancePage() {
  const [data, setData] = useState<PerfData | null>(null);

  useEffect(() => {
    fetch('/api/manual/public/performance/summary')
      .then((r) => (r.ok ? r.json() : null))
      .then(setData)
      .catch(() => {});
  }, []);

  return (
    <div className="min-h-screen bg-[#0B0E14] text-zinc-100">
      <PublicHeader />
      <main className="mx-auto max-w-6xl space-y-6 px-4 py-8 sm:px-6">
        <div>
          <h1 className="text-xl font-bold">공개 성과 분석 (가상 시뮬레이션)</h1>
          <p className="mt-1 text-xs text-zinc-400">익명화된 가상매매 성과 통계입니다. 실계좌 거래내역은 포함되지 않습니다.</p>
        </div>

        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {(data?.policies ?? []).map((p) => (
            <div key={p.policy} className="rounded-xl border border-[#1E2536] bg-[#121620] p-5 text-xs">
              <p className="font-mono text-zinc-300">{p.policy}</p>
              <p className={`mt-2 font-mono text-xl font-semibold ${pnlColor(p.realized)}`}>{signedMoney(p.realized)}</p>
              <p className="mt-2 font-mono text-zinc-400">
                {p.trades}건 · 승률 {p.win_rate ? `${(Number(p.win_rate) * 100).toFixed(1)}%` : '—'} · PF {p.profit_factor ?? '—'} · MDD -{(Number(p.max_drawdown) * 100).toFixed(2)}%
              </p>
            </div>
          ))}
        </div>

        <section className="rounded-xl border border-[#1E2536] bg-[#121620] p-6 text-xs">
          <h2 className="text-base font-semibold">최근 익명화 가상 청산 내역</h2>
          <div className="mt-4 divide-y divide-[#1E2536]">
            {(data?.recent_anonymized_results ?? []).map((r, idx) => (
              <div key={`${r.symbol}-${idx}`} className="flex flex-wrap items-center justify-between gap-2 py-3">
                <div>
                  <strong className="text-zinc-100">{r.coin_name} ({r.symbol.replace('KRW-', '')})</strong>
                  <span className="ml-2 rounded bg-[#181E2B] px-2 py-0.5 font-mono text-[11px] text-zinc-400">{r.exit_reason}</span>
                  <p className="mt-1 font-mono text-[11px] text-zinc-500">{kst(r.closed_at)}</p>
                </div>
                <strong className={`font-mono text-sm ${pnlColor(r.net_pnl)}`}>{signedMoney(r.net_pnl)}</strong>
              </div>
            ))}
            {!(data?.recent_anonymized_results ?? []).length && (
              <p className="py-6 text-center text-zinc-500">아직 기록된 가상 청산 내역이 없습니다.</p>
            )}
          </div>
        </section>
      </main>
      <PublicFooter />
    </div>
  );
}
