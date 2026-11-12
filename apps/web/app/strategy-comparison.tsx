'use client';
import { useEffect, useState } from 'react';
import { api, money } from './trading-types';
import { signedRate, signedMoney, pnlColor } from './quiet-types';

type Item = {
  version_id: string;
  name: string;
  paper_sell_fills: number;
  paper_net_krw: string;
  paper_realized_return_rate: string;
  paper_fees_krw: string;
  paper_slippage_krw: string;
  live_sell_fills: number;
  live_net_krw: string;
};

export default function StrategyComparison() {
  const [data, setData] = useState<{ items: Item[]; note: string } | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    api<{ items: Item[]; note: string }>('board/comparison')
      .then((d) => {
        if (active) setData(d);
      })
      .catch(() => {
        if (active) setError('전략 비교를 읽을 수 없습니다.');
      });
    return () => {
      active = false;
    };
  }, []);

  return (
    <section className="rounded-xl border border-[#1E2536] bg-[#121620] p-6">
      <div className="border-b border-[#1E2536]/80 pb-4">
        <h2 className="text-base font-semibold text-zinc-100">전략별 가상·실전 성과 비교</h2>
      </div>
      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[850px] text-left text-xs">
          <thead>
            <tr className="border-b border-[#1E2536] text-zinc-400">
              {[
                '전략 / 버전',
                '가상 매도',
                '가상 실현 순손익',
                '가상 수익률',
                '가상 수수료 / 슬리피지',
                '실전 매도',
                '실전 확인 순손익',
              ].map((h) => (
                <th key={h} className="pb-3 font-medium">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-[#1E2536]/60">
            {data?.items.map((r) => (
              <tr key={r.version_id} className="hover:bg-[#181E2B]/40 transition">
                <td className="py-3.5">
                  <span className="font-medium text-zinc-100">{r.name}</span>
                  <p className="mt-0.5 font-mono text-[11px] text-zinc-500">{r.version_id.slice(0, 10)}</p>
                </td>
                <td className="font-mono tabular-nums text-zinc-300">{r.paper_sell_fills}건</td>
                <td className={`font-mono font-semibold tabular-nums ${pnlColor(r.paper_net_krw)}`}>
                  {signedMoney(r.paper_net_krw)}
                </td>
                <td className={`font-mono tabular-nums ${pnlColor(r.paper_realized_return_rate)}`}>
                  {signedRate(r.paper_realized_return_rate)}
                </td>
                <td className="font-mono tabular-nums text-zinc-400">
                  {money(r.paper_fees_krw)} / {money(r.paper_slippage_krw)}
                </td>
                <td className="font-mono tabular-nums text-zinc-300">{r.live_sell_fills}건</td>
                <td className={`font-mono font-medium tabular-nums ${r.live_sell_fills ? pnlColor(r.live_net_krw) : 'text-zinc-500'}`}>
                  {r.live_sell_fills ? signedMoney(r.live_net_krw) : '체결 없음'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-4 text-xs text-zinc-500">{data?.note ?? error}</p>
    </section>
  );
}
