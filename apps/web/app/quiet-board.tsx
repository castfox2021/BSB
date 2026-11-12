'use client';
import { Board, PaperTrade, Holding, elapsed, signedMoney, signedRate, pnlColor, priceText, quantityText } from './quiet-types';
import { kst, openBithumb } from './trading-types';

const smallButton = 'rounded-md border border-[#1E2536] bg-[#181E2B] px-3 py-1.5 text-xs font-medium text-zinc-300 hover:border-zinc-500 disabled:opacity-30 transition';
const bithumbBtn = 'inline-flex items-center gap-1 rounded-md border border-[#2A344B] bg-[#181E2B] px-2.5 py-1 text-xs font-medium text-zinc-200 hover:border-zinc-400 hover:bg-[#1F2737] transition cursor-pointer';

type Named = { symbol: string; coin_name?: string };
const coinLabel = (item: Named) => `${item.coin_name ?? item.symbol.replace('KRW-', '')} (${item.symbol.replace('KRW-', '')})`;

function Pager({
  value,
  onPage,
  label,
}: {
  value: { page: number; pages: number; total: number };
  onPage: (n: number) => void;
  label: string;
}) {
  return (
    <div className="mt-5 flex items-center justify-end gap-3 border-t border-[#1E2536]/60 pt-4 text-xs text-zinc-400">
      <span className="font-mono tabular-nums">
        {value.total}건 · {value.page}/{value.pages}
      </span>
      <button aria-label={`${label} 이전`} className={smallButton} disabled={value.page <= 1} onClick={() => onPage(value.page - 1)}>
        이전
      </button>
      <button aria-label={`${label} 다음`} className={smallButton} disabled={value.page >= value.pages} onClick={() => onPage(value.page + 1)}>
        다음
      </button>
    </div>
  );
}

function SignalState({ trade }: { trade: PaperTrade }) {
  const isRecent = trade.signal_status === 'RECENT';
  const text = isRecent ? '최근 신호' : trade.signal_status === 'CHASE_CAUTION' ? '추격 주의' : '오래된 신호';
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-md border px-2.5 py-0.5 text-xs font-medium ${
        isRecent
          ? 'border-rose-500/30 bg-rose-500/10 text-rose-300'
          : 'border-[#2A344B] bg-[#181E2B] text-zinc-400'
      }`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${isRecent ? 'bg-rose-400' : 'bg-zinc-500'}`} />
      {text}
    </span>
  );
}

export default function QuietBoard({
  board,
  onTrade,
  onHolding,
  onBuyPage,
  onSellPage,
}: {
  board: Board | null;
  onTrade: (t: PaperTrade) => void;
  onHolding: (p: Holding) => void;
  onBuyPage: (n: number) => void;
  onSellPage: (n: number) => void;
}) {
  const newest = board?.buys.items[0];

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      {/* 1. 최근 가상매수 카드 */}
      <section className="flex flex-col justify-between rounded-xl border border-[#1E2536] bg-[#121620] p-6">
        <div>
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#1E2536]/80 pb-4">
            <h2 className="text-base font-semibold text-zinc-100">
              {board && board.buys.page > 1 ? '이전 가상매수' : '가장 최근 가상매수'}
            </h2>
            <span className="text-xs text-zinc-500">가상 체결 · 시뮬레이션</span>
          </div>

          {newest ? (
            <>
              <button className="mt-5 w-full text-left group" onClick={() => onTrade(newest)}>
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="flex flex-wrap items-center gap-3">
                    <strong className="text-2xl font-bold tracking-tight text-rose-400 sm:text-3xl group-hover:underline">
                      {coinLabel(newest)}
                    </strong>
                    <SignalState trade={newest} />
                  </div>
                  <span
                    role="button"
                    tabIndex={0}
                    onClick={(e) => {
                      e.stopPropagation();
                      openBithumb(newest.symbol);
                    }}
                    className={bithumbBtn}
                  >
                    빗썸 열기 ↗
                  </span>
                </div>

                <p className="mt-1.5 font-mono text-xs text-zinc-400">
                  {newest.symbol} · {kst(newest.at)}
                </p>

                <div className="mt-6 grid grid-cols-2 gap-4 rounded-lg border border-[#1E2536] bg-[#0E1119] p-4">
                  <div>
                    <p className="text-xs text-zinc-400">가상 매수가</p>
                    <p className="mt-1 font-mono text-lg font-semibold tabular-nums text-zinc-100 sm:text-xl">
                      {newest.fill_price ? `${priceText(newest.fill_price)}원` : '기록 없음'}
                    </p>
                    {newest.best_bid ? (
                      <p className="mt-1 font-mono text-[11px] text-emerald-400">
                        권장 지정가: {priceText(newest.best_bid)}원 (매수1호가)
                      </p>
                    ) : newest.fill_price ? (
                      <p className="mt-1 text-[11px] text-zinc-500">호가창 지정가 매수 권장 (슬리피지 절감)</p>
                    ) : null}
                  </div>
                  <div>
                    <p className="text-xs text-zinc-400">현재 시장가</p>
                    <p className="mt-1 font-mono text-lg font-semibold tabular-nums text-zinc-100 sm:text-xl">
                      {newest.market_fresh ? `${priceText(newest.current_price)}원` : '시세 지연'}
                    </p>
                  </div>
                  <div>
                    <p className="text-xs text-zinc-400">매수 후 변화율</p>
                    <p className={`mt-1 font-mono text-base font-semibold tabular-nums ${pnlColor(newest.price_change_rate)}`}>
                      {signedRate(newest.price_change_rate)}
                    </p>
                  </div>
                  <div>
                    <p className="text-xs text-zinc-400">
                      {newest.elapsed_basis === 'FILL_FALLBACK' ? '체결 후 경과' : '신호 경과'}
                    </p>
                    <p className="mt-1 font-mono text-base font-medium tabular-nums text-zinc-200">
                      {elapsed(newest.elapsed_seconds)}
                    </p>
                  </div>
                </div>

                <div className="mt-4 flex items-center justify-between text-sm">
                  <span className="text-zinc-400">가상매수 금액</span>
                  <strong className="font-mono tabular-nums text-zinc-100">
                    {Number(newest.amount_krw ?? 0).toLocaleString('ko-KR', { maximumFractionDigits: 0 })}원
                  </strong>
                </div>
                <p className="mt-2 truncate text-xs text-zinc-400" title={newest.reason_summary}>
                  {newest.reason_summary}
                </p>
              </button>

              {board!.buys.items.length > 1 && (
                <div className="mt-5 space-y-2 border-t border-[#1E2536]/60 pt-4">
                  {board!.buys.items.slice(1).map((t) => (
                    <button
                      key={t.id}
                      onClick={() => onTrade(t)}
                      className="flex w-full items-center justify-between gap-3 rounded-md px-2 py-1.5 text-left text-xs hover:bg-[#181E2B] transition"
                    >
                      <span className="font-medium text-rose-400">{coinLabel(t)}</span>
                      <span className="font-mono tabular-nums text-zinc-300">{priceText(t.fill_price)}원</span>
                      <span className="font-mono tabular-nums text-zinc-500">{kst(t.at)}</span>
                    </button>
                  ))}
                </div>
              )}
            </>
          ) : (
            <div className="py-10">
              <p className="text-sm text-zinc-400">{board?.runtime?.message ?? '가상 체결을 확인하고 있습니다.'}</p>
              {board && (
                <details className="mt-4 rounded-lg border border-[#1E2536] bg-[#0E1119] p-3 text-xs">
                  <summary className="cursor-pointer font-medium text-zinc-300">매수 대기 조건 상세</summary>
                  <p className="mt-2.5 text-zinc-400">
                    안전검사 통과 후보: <span className="font-mono text-zinc-200">{board.runtime?.ready_candidates ?? 0}</span>종목
                  </p>
                  <div className="mt-2 space-y-1">
                    {Object.entries(board.runtime?.selected_check?.reasons ?? {}).map(([reason, n]) => (
                      <div key={reason} className="flex justify-between text-zinc-500">
                        <span>{reason}</span>
                        <span className="font-mono tabular-nums">{n}종목</span>
                      </div>
                    ))}
                  </div>
                </details>
              )}
            </div>
          )}
        </div>

        <div>
          <p className="mt-4 text-[11px] text-zinc-500">참고용 시뮬레이션 지표이며 투자 권유가 아닙니다.</p>
          {board && <Pager value={board.buys} onPage={onBuyPage} label="가상매수" />}
        </div>
      </section>

      {/* 2. 현재 가상보유 카드 */}
      <section className="flex flex-col justify-between rounded-xl border border-[#1E2536] bg-[#121620] p-6">
        <div>
          <div className="flex items-center justify-between border-b border-[#1E2536]/80 pb-4">
            <h2 className="text-base font-semibold text-zinc-100">현재 가상보유</h2>
            <span className="font-mono text-xs font-medium tabular-nums text-zinc-400">
              {board?.holdings.length ?? 0} 종목
            </span>
          </div>

          {board?.holdings.length ? (
            <div className="mt-5 space-y-4">
              {board.holdings.map((p) => (
                <button
                  key={p.id}
                  onClick={() => onHolding(p)}
                  className="w-full rounded-lg border border-[#1E2536] bg-[#0E1119] p-4 text-left hover:border-[#2E3852] transition"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex flex-wrap items-center gap-2.5">
                      <strong className="text-lg font-bold text-zinc-100">{coinLabel(p)}</strong>
                      <span
                        role="button"
                        tabIndex={0}
                        onClick={(e) => {
                          e.stopPropagation();
                          openBithumb(p.symbol);
                        }}
                        className={bithumbBtn}
                      >
                        빗썸 열기 ↗
                      </span>
                      {p.trail_active && (
                        <span className="inline-flex items-center gap-1.5 rounded-md border border-rose-500/30 bg-rose-500/10 px-2 py-0.5 text-[11px] font-medium text-rose-300">
                          <span className="h-1.5 w-1.5 rounded-full bg-rose-400" />
                          트레일링 익절 추적
                        </span>
                      )}
                    </div>
                    <span className={`font-mono text-lg font-bold tabular-nums ${pnlColor(p.net_return_rate)}`}>
                      {signedRate(p.net_return_rate)}
                    </span>
                  </div>

                  <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 font-mono text-xs tabular-nums text-zinc-400">
                    <span>매수가 {priceText(p.entry_price)}원</span>
                    <span>현재가 {p.market_fresh ? `${priceText(p.current_price)}원` : '지연'}</span>
                    {p.peak_price && p.trail_active && (
                      <span className="text-zinc-300">최고가 {priceText(p.peak_price)}원</span>
                    )}
                  </div>

                  <div className="mt-3 flex items-center justify-between border-t border-[#1E2536]/60 pt-3 text-xs">
                    <span className="text-zinc-400">
                      수량 <span className="font-mono tabular-nums text-zinc-300">{quantityText(p.quantity)}</span>
                    </span>
                    <div className="flex items-center gap-2">
                      <span className="text-zinc-500">평가손익</span>
                      <strong className={`font-mono text-sm font-semibold tabular-nums ${pnlColor(p.net_estimate_krw)}`}>
                        {signedMoney(p.net_estimate_krw)}
                      </strong>
                    </div>
                  </div>
                </button>
              ))}
            </div>
          ) : (
            <div className="py-16 text-center text-sm text-zinc-500">
              {board ? '현재 가상보유 종목이 없습니다.' : '가상보유 상태를 확인하고 있습니다.'}
            </div>
          )}
        </div>
      </section>

      {/* 3. 최근 가상매도 카드 */}
      <section className="rounded-xl border border-[#1E2536] bg-[#121620] p-6 lg:col-span-2">
        <div className="flex items-center justify-between border-b border-[#1E2536]/80 pb-4">
          <h2 className="text-base font-semibold text-zinc-100">최근 가상매도</h2>
          <span className="text-xs text-zinc-500">청산 체결 내역</span>
        </div>

        {board?.sells.items.length ? (
          <div className="mt-4 divide-y divide-[#1E2536]/60">
            {board.sells.items.map((t) => (
              <button
                key={t.id}
                onClick={() => onTrade(t)}
                className="grid w-full grid-cols-2 items-center gap-4 py-3.5 text-left hover:bg-[#181E2B]/50 transition sm:grid-cols-4 px-2 rounded-md"
              >
                <div>
                  <strong className="text-sm font-semibold text-sky-400">{coinLabel(t)}</strong>
                  <p className="mt-0.5 font-mono text-xs tabular-nums text-zinc-500">{kst(t.at)}</p>
                </div>
                <div className="text-xs text-zinc-400">
                  체결가 <strong className="ml-1 font-mono text-sm tabular-nums text-zinc-200">{priceText(t.fill_price)}원</strong>
                </div>
                <strong className={`font-mono text-base font-semibold tabular-nums ${pnlColor(t.net_pnl)}`}>
                  {signedMoney(t.net_pnl)}
                </strong>
                <p className="truncate text-xs text-zinc-400" title={t.reason_summary}>
                  {t.reason_summary}
                </p>
              </button>
            ))}
          </div>
        ) : (
          <p className="py-10 text-center text-sm text-zinc-500">가상매도 체결 내역이 없습니다.</p>
        )}

        {board && <Pager value={board.sells} onPage={onSellPage} label="가상매도" />}
      </section>
    </div>
  );
}
