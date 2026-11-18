'use client';
import { useEffect, useState } from 'react';
import { PublicFooter, PublicHeader } from '../components/public-nav';
import { pnlColor, signedMoney } from '../quiet-types';

type Overview = {
  watching_count: number;
  watching_items: { symbol: string; coin_name: string; drops: string[]; base_bars: number }[];
  comparison: { policy: string; trades: number; win_rate: string | null; realized_krw: string; max_drawdown: string }[];
};

export default function PublicStrategiesPage() {
  const [data, setData] = useState<Overview | null>(null);

  useEffect(() => {
    const load = () =>
      fetch('/api/manual/public/strategies/overview')
        .then((r) => (r.ok ? r.json() : null))
        .then(setData)
        .catch(() => {});
    load();
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="min-h-screen bg-[#0B0E14] text-zinc-100">
      <PublicHeader />
      <main className="mx-auto max-w-6xl space-y-6 px-4 py-8 sm:px-6">
        <div>
          <h1 className="text-xl font-bold">전략 관찰 (가상 시뮬레이션)</h1>
          <p className="mt-1 text-xs text-zinc-400">
            실계좌 잔고·주문 식별자 없이 급락 반등 관찰 종목과 Task 16/17 가상 전략 비교 결과만 공개합니다.
          </p>
        </div>

        <section className="rounded-xl border border-[#1E2536] bg-[#121620] p-5 text-xs">
          <h2 className="text-sm font-semibold text-zinc-100">급락 반등 주전략 기준 (Structure Trailing)</h2>
          <p className="mt-1.5 text-zinc-400">
            구조 손절 최대 <span className="font-mono text-sky-400">-1.5%</span> · 수익{' '}
            <span className="font-mono text-rose-400">+2.0%</span> 도달 시 트레일링 활성화 · 최고가 대비{' '}
            <span className="font-mono text-zinc-200">-1.0%</span> 하락 시 청산 · 상승 추세 중 시간 청산 제외
          </p>
        </section>

        <section className="rounded-xl border border-[#1E2536] bg-[#121620] p-6">
          <h2 className="text-base font-semibold">현재 반등 관찰 종목 ({data?.watching_count ?? 0}종목)</h2>
          <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {(data?.watching_items ?? []).map((w) => (
              <div key={w.symbol} className="rounded-lg border border-[#1E2536] bg-[#0E1119] p-4 text-xs">
                <div className="flex items-center justify-between">
                  <strong className="text-sm text-zinc-100">{w.coin_name} ({w.symbol.replace('KRW-', '')})</strong>
                  <span className="rounded bg-[#181E2B] px-2 py-0.5 font-mono text-[11px] text-zinc-300">
                    횡보 {w.base_bars}분
                  </span>
                </div>
                <p className="mt-2 font-mono text-sky-400">
                  급락률: {w.drops.map((d) => `${(Number(d) * 100).toFixed(1)}%`).join(' / ')}
                </p>
              </div>
            ))}
            {!(data?.watching_items ?? []).length && (
              <p className="col-span-full py-6 text-center text-xs text-zinc-500">
                현재 급락 반등 관찰 조건에 도달한 종목이 없습니다.
              </p>
            )}
          </div>
        </section>

        <section className="rounded-xl border border-[#1E2536] bg-[#121620] p-6">
          <h2 className="text-base font-semibold">청산 정책별 가상 시뮬레이션 비교</h2>
          <div className="mt-4 overflow-x-auto">
            <table className="w-full min-w-[500px] text-left font-mono text-xs tabular-nums">
              <thead className="border-b border-[#1E2536] text-[11px] text-zinc-400">
                <tr>
                  <th className="py-2">정책</th>
                  <th className="py-2">거래</th>
                  <th className="py-2">승률</th>
                  <th className="py-2">가상 순손익</th>
                  <th className="py-2">MDD</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#1E2536]/60">
                {(data?.comparison ?? []).map((c) => (
                  <tr key={c.policy}>
                    <td className="py-2.5">{c.policy}</td>
                    <td className="py-2.5">{c.trades}건</td>
                    <td className="py-2.5">{c.win_rate ? `${(Number(c.win_rate) * 100).toFixed(1)}%` : '—'}</td>
                    <td className={`py-2.5 ${pnlColor(c.realized_krw)}`}>{signedMoney(c.realized_krw)}</td>
                    <td className="py-2.5 text-sky-400">
                      -{Math.min(100, Math.max(0, Number(c.max_drawdown) * 100)).toFixed(2)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </main>
      <PublicFooter />
    </div>
  );
}
