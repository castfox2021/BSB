'use client';
import { useEffect, useState } from 'react';
import AdminShell from './admin-shell';
import ReversionPanel, { Reversion } from '../reversion-panel';
import QuietBoard from '../quiet-board';
import QuietDetail from '../quiet-details';
import { api, kst } from '../trading-types';
import { Board, Holding, PaperTrade, pnlColor, signedMoney } from '../quiet-types';

export default function AdminDashboardPage() {
  const [board, setBoard] = useState<Board | null>(null);
  const [error, setError] = useState('');
  const [buyPage, setBuyPage] = useState(1);
  const [sellPage, setSellPage] = useState(1);
  const [trade, setTrade] = useState<PaperTrade | null>(null);
  const [symbol, setSymbol] = useState('KRW-BTC');

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const res = await api<Board>(`board?buy_page=${buyPage}&sell_page=${sellPage}`);
        if (active) {
          setBoard(res);
          setError('');
        }
      } catch {
        if (active) setError('신호판 연결이 끊겼습니다.');
      }
    };
    load();
    const timer = setInterval(load, 2000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [buyPage, sellPage]);

  return (
    <AdminShell>
      <div className="space-y-6">
        {/* 상단 KPI 요약 */}
        <div className="flex flex-wrap items-baseline gap-x-10 gap-y-3 rounded-xl border border-[#1E2536] bg-[#121620] px-6 py-4">
          <div className="flex items-baseline gap-3">
            <span className="text-xs text-zinc-400">오늘 가상 실현손익</span>
            <strong className={`font-mono text-xl font-semibold ${pnlColor(board?.summary.today_realized_krw)}`}>
              {signedMoney(board?.summary.today_realized_krw)}
            </strong>
          </div>
          <div className="flex items-baseline gap-3">
            <span className="text-xs text-zinc-400">가상보유</span>
            <strong className="font-mono text-xl font-semibold text-zinc-100">
              {board?.summary.holding_count ?? '—'}종목
            </strong>
          </div>
          <div className="text-xs text-zinc-400 sm:ml-auto">
            {board?.runtime?.message ?? '감시 확인 중'} · 마지막 분석 {kst(board?.runtime?.last_analysis_at)}
          </div>
        </div>

        {error && <p className="rounded-lg border border-rose-900/50 bg-[#121620] px-4 py-3 text-sm text-rose-300">{error}</p>}

        {/* 핵심 급락 반등 패널 */}
        <ReversionPanel data={(board as (Board & { reversion?: Reversion | null }) | null)?.reversion} />

        {/* 기존 가상 신호판 */}
        <QuietBoard
          board={error ? null : board}
          onTrade={(t) => {
            setTrade(t);
            setSymbol(t.symbol);
          }}
          onHolding={(h: Holding) => setSymbol(h.symbol)}
          onBuyPage={setBuyPage}
          onSellPage={setSellPage}
        />

        {trade && (
          <QuietDetail
            symbol={symbol}
            trade={trade}
            onRecord={() => {}}
          />
        )}
      </div>
    </AdminShell>
  );
}
