'use client';
import { useEffect, useState } from 'react';
import AdminShell from './admin-shell';
import ReversionPanel, { Reversion } from '../reversion-panel';
import QuietBoard from '../quiet-board';
import QuietDetail from '../quiet-details';
import { api, kst, openBithumb } from '../trading-types';
import { Board, Holding, PaperTrade, pnlColor, priceText, quantityText, signedMoney } from '../quiet-types';

type RealAccount = { currency: string; balance: string; locked: string; average_price?: string };

export default function AdminDashboardPage() {
  const [board, setBoard] = useState<Board | null>(null);
  const [error, setError] = useState('');
  const [buyPage, setBuyPage] = useState(1);
  const [sellPage, setSellPage] = useState(1);
  const [trade, setTrade] = useState<PaperTrade | null>(null);
  const [symbol, setSymbol] = useState('KRW-BTC');
  const [accounts, setAccounts] = useState<RealAccount[]>([]);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const [res, acc] = await Promise.all([
          api<Board>(`board?buy_page=${buyPage}&sell_page=${sellPage}`),
          api<{ items?: RealAccount[] }>('private/accounts').catch(() => ({ items: [] })),
        ]);
        if (active) {
          setBoard(res);
          setAccounts(acc.items ?? []);
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

        {/* 실전 계좌 보유 현황 (빗썸 매도 바로가기) */}
        {accounts.length > 0 && (
          <section className="rounded-xl border border-[#1E2536] bg-[#121620] p-5">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#1E2536]/80 pb-3">
              <h2 className="text-sm font-semibold text-zinc-100">실전 계좌 보유 현황 (빗썸 수동 매도)</h2>
              <span className="font-mono text-xs text-zinc-400">
                주문 가능 원화:{' '}
                <strong className="text-zinc-100">
                  {Number(accounts.find((a) => a.currency === 'KRW')?.balance ?? '0').toLocaleString('ko-KR', { maximumFractionDigits: 0 })}원
                </strong>
              </span>
            </div>
            <div className="mt-3 grid gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
              {accounts
                .filter((a) => a.currency !== 'KRW' && Number(a.balance) + Number(a.locked) > 0)
                .map((a) => {
                  const sym = `KRW-${a.currency}`;
                  const qty = Number(a.balance) + Number(a.locked);
                  const avg = Number(a.average_price || 0);
                  return (
                    <div key={a.currency} className="flex items-center justify-between rounded-lg border border-[#1E2536] bg-[#0E1119] px-3.5 py-2.5 text-xs">
                      <div>
                        <strong className="font-mono text-sm text-zinc-100">{a.currency}</strong>
                        <p className="mt-0.5 font-mono text-[11px] text-zinc-400">
                          수량 {quantityText(qty)} · 평단 {avg > 0 ? `${priceText(String(avg))}원` : '—'}
                        </p>
                      </div>
                      <button
                        type="button"
                        onClick={() => openBithumb(sym)}
                        className="rounded-md border border-[#2A344B] bg-[#181E2B] px-2.5 py-1 text-[11px] font-medium text-zinc-200 hover:border-zinc-400"
                      >
                        빗썸 매도 ↗
                      </button>
                    </div>
                  );
                })}
            </div>
          </section>
        )}

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
