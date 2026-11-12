import { pnlColor, signedMoney, signedRate, priceText } from './quiet-types';
import { kst, openBithumb } from './trading-types';

export type ReversionPosition = {
  id: string;
  coin_name?: string;
  symbol: string;
  entry_price: string;
  current_price: string | null;
  return_rate: string | null;
  current_stop: string;
  peak_price: string;
  trail_active: number;
  opened_at: string;
  closed_at?: string;
  exit_price?: string;
  net_pnl?: string;
  exit_reason?: string;
};

export type WatchingItem = { symbol: string; coin_name?: string; low: string; drops: string[]; base_bars?: number };

export type Reversion = {
  paper_only: true;
  live_execution: false;
  watching: number;
  watching_symbols?: string[];
  watching_items?: WatchingItem[];
  positions: ReversionPosition[];
  recent_results: ReversionPosition[];
  comparison: { policy: string; trades: number; win_rate: string | null; realized: string; max_drawdown: string }[];
  config: Record<string, string | number | boolean>;
};

const names: Record<string, string> = {
  structure_trailing: '구조+트레일링',
  fixed_trailing: '고정손절+트레일링',
  atr_trailing: 'ATR+트레일링',
  structure_fixed_take: '구조+고정익절 비교',
};

const reasons: Record<string, string> = {
  STRUCTURE_STOP: '손절',
  TRAILING_STOP: '트레일링 청산',
  REBOUND_FAILURE: '반등 실패',
  FIXED_TAKE_COMPARISON: '고정 익절 비교',
};

function formatDuration(startStr?: string, endStr?: string) {
  if (!startStr || !endStr) return '';
  const diffSec = Math.max(0, Math.round((new Date(endStr).getTime() - new Date(startStr).getTime()) / 1000));
  if (diffSec < 60) return `${diffSec}초`;
  const m = Math.floor(diffSec / 60);
  const s = diffSec % 60;
  return s > 0 ? `${m}분 ${s}초` : `${m}분`;
}

function calcReturnRate(entry?: string, exit?: string) {
  if (!entry || !exit) return null;
  const en = Number(entry);
  const ex = Number(exit);
  if (en <= 0) return null;
  return ex / en - 1;
}

const coinLabel = (name: string | undefined, symbol: string) =>
  `${name ?? symbol.replace('KRW-', '')} (${symbol.replace('KRW-', '')})`;
const bithumbOrderUrl = (symbol: string) =>
  `https://www.bithumb.com/react/trade/order/${symbol.replace(/^KRW-/, '')}-KRW`;

export default function ReversionPanel({ data }: { data?: Reversion | null }) {
  if (!data) return null;

  const primaryComp = data.comparison.find((c) => c.policy === 'structure_trailing');
  const primaryRealized = primaryComp?.realized ?? '0';
  const primaryTrades = primaryComp?.trades ?? 0;

  return (
    <section className="rounded-xl border border-[#1E2536] bg-[#121620] p-6 lg:col-span-2">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#1E2536]/80 pb-4">
        <div>
          <h2 className="text-base font-semibold text-zinc-100">급락 반등 · 가상 자동손절</h2>
          <p className="mt-0.5 text-xs text-zinc-500">독립 가상 자본 · 시뮬레이션</p>
        </div>
        <div className="flex items-center gap-2 rounded-md border border-[#1E2536] bg-[#0E1119] px-3 py-1.5 text-xs">
          <span className="text-zinc-400">실현손익</span>
          <strong className={`font-mono font-semibold tabular-nums ${pnlColor(primaryRealized)}`}>
            {signedMoney(primaryRealized)}
          </strong>
          <span className="text-zinc-600">·</span>
          <span className="font-mono tabular-nums text-zinc-400">{primaryTrades}건</span>
        </div>
      </div>

      {/* 감시 현황 */}
      <div className="mt-4 text-xs leading-relaxed text-zinc-400">
        <p>
          반등 관찰 <strong className="font-mono tabular-nums text-zinc-100">{data.watching}종목</strong>
          {data.watching_symbols && data.watching_symbols.length > 0 ? (
            <span className="ml-1.5 font-mono text-zinc-300">
              ({data.watching_symbols.map((s) => s.replace('KRW-', '')).join(', ')})
            </span>
          ) : (
            <span className="ml-1.5 text-zinc-500">· 기준 도달 종목 스캔 대기 중</span>
          )}
          <span className="mx-2 text-zinc-700">|</span>
          <span>
            바닥 횡보 {String(data.config?.min_base_bars ?? 3)}분 후 돌파 진입 · 손절 -
            {(Number(data.config?.maximum_stop_rate ?? 0.015) * 100).toFixed(1)}% · +
            {(Number(data.config?.trailing_activation_rate ?? 0.015) * 100).toFixed(1)}% 트레일링(-
            {(Number(data.config?.trailing_drawdown_rate ?? 0.008) * 100).toFixed(1)}%)
          </span>
        </p>

        {data.watching_items && data.watching_items.length > 0 && (
          <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
            {data.watching_items.map((item) => (
              <a
                key={item.symbol}
                href={bithumbOrderUrl(item.symbol)}
                target="_blank"
                rel="noopener noreferrer"
                onClick={(e) => {
                  e.preventDefault();
                  openBithumb(item.symbol);
                }}
                title={`${coinLabel(item.coin_name, item.symbol)} 빗썸 주문 열기`}
                className="group flex flex-wrap items-center justify-between gap-2 rounded-lg border border-[#1E2536] bg-[#0E1119] px-4 py-3 text-xs transition hover:border-[#2E3852] hover:bg-[#181E2B]"
              >
                <div className="flex items-center gap-2.5">
                  <strong className="text-sm font-semibold text-zinc-100 group-hover:underline">
                    {coinLabel(item.coin_name, item.symbol)}
                  </strong>
                  <span className="text-zinc-500 group-hover:text-zinc-300">↗</span>
                  {item.base_bars !== undefined && item.base_bars > 0 ? (
                    <span className="rounded border border-[#2A344B] bg-[#181E2B] px-2 py-0.5 text-[11px] font-medium text-zinc-300">
                      횡보 {item.base_bars}분
                    </span>
                  ) : (
                    <span className="rounded border border-[#1E2536] px-2 py-0.5 text-[11px] text-zinc-500">
                      저점 탐색
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-2 font-mono tabular-nums">
                  <span className="text-zinc-400">
                    저점 <strong className="text-zinc-200">{priceText(item.low)}원</strong>
                  </span>
                  {item.drops && item.drops.length > 0 && (
                    <span className="text-sky-400">
                      ({item.drops.map((d) => `${(Number(d) * 100).toFixed(1)}%`).join(' / ')})
                    </span>
                  )}
                </div>
              </a>
            ))}
          </div>
        )}
      </div>

      {/* 실시간 가상 포지션 (보유 중) */}
      <div className="mt-5 grid gap-3 md:grid-cols-2">
        {data.positions.map((p) => (
          <article key={p.id} className="rounded-lg border border-[#1E2536] bg-[#0E1119] p-4">
            <div className="flex items-center justify-between">
              <strong className="text-base font-semibold text-zinc-100">{coinLabel(p.coin_name, p.symbol)}</strong>
              <strong className={`font-mono text-base font-bold tabular-nums ${pnlColor(p.return_rate)}`}>
                {signedRate(p.return_rate)}
              </strong>
            </div>
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 font-mono text-xs tabular-nums text-zinc-400">
              <span>진입 {priceText(p.entry_price)}원</span>
              <span>현재 {p.current_price ? `${priceText(p.current_price)}원` : '지연'}</span>
              <span className="text-sky-400">손절선 {priceText(p.current_stop)}원</span>
            </div>
            <p className="mt-2 font-mono text-[11px] tabular-nums text-zinc-500">
              최고가 {priceText(p.peak_price)}원 · 트레일링 {p.trail_active ? '활성' : '대기'} · {kst(p.opened_at)}
            </p>
          </article>
        ))}
        {!data.positions.length && (
          <div className="rounded-lg border border-[#1E2536]/60 bg-[#0E1119]/60 py-6 text-center text-xs text-zinc-500 md:col-span-2">
            보유 중인 급락 반등 포지션이 없습니다.
          </div>
        )}
      </div>

      {/* 최근 익절/손절 완료 내역 */}
      {data.recent_results.length > 0 && (
        <div className="mt-5">
          <h3 className="text-xs font-medium text-zinc-400">최근 청산 내역</h3>
          <div className="mt-2.5 divide-y divide-[#1E2536]/60 rounded-lg border border-[#1E2536] bg-[#0E1119] px-4">
            {data.recent_results.map((p) => {
              const rate = calcReturnRate(p.entry_price, p.exit_price);
              const duration = formatDuration(p.opened_at, p.closed_at);
              return (
                <div key={p.id} className="py-3 text-xs">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <strong className="text-sm font-semibold text-zinc-100">{coinLabel(p.coin_name, p.symbol)}</strong>
                      <span className="rounded border border-[#2A344B] bg-[#181E2B] px-2 py-0.5 text-[11px] text-zinc-300">
                        {reasons[p.exit_reason ?? ''] ?? p.exit_reason}
                      </span>
                      {duration && <span className="font-mono text-zinc-500">{duration}</span>}
                    </div>
                    <div className="flex items-center gap-2 font-mono tabular-nums">
                      <strong className={`text-sm font-semibold ${pnlColor(p.net_pnl)}`}>{signedMoney(p.net_pnl)}</strong>
                      {rate !== null && (
                        <span className={rate > 0 ? 'text-rose-400' : rate < 0 ? 'text-sky-400' : 'text-zinc-400'}>
                          ({rate > 0 ? '+' : ''}{(rate * 100).toFixed(2)}%)
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[11px] tabular-nums text-zinc-500">
                    <span>
                      매수 {p.opened_at ? kst(p.opened_at) : '—'} ({priceText(p.entry_price)}원)
                    </span>
                    <span>→</span>
                    <span>
                      매도 {p.closed_at ? kst(p.closed_at) : '—'} ({p.exit_price ? `${priceText(p.exit_price)}원` : '—'})
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* 손절 정책별 비교 */}
      <details className="mt-4 text-xs">
        <summary className="cursor-pointer font-medium text-zinc-400 hover:text-zinc-200">손절 정책별 가상 성과 비교</summary>
        <div className="mt-2 space-y-1 font-mono tabular-nums text-zinc-400">
          {data.comparison.map((c) => (
            <p key={c.policy}>
              {names[c.policy] ?? c.policy}: {c.trades}건 · 승률 {c.win_rate === null ? '—' : `${(Number(c.win_rate) * 100).toFixed(1)}%`} · 순손익{' '}
              {signedMoney(c.realized)} · MDD {(Number(c.max_drawdown) * 100).toFixed(2)}%
            </p>
          ))}
        </div>
      </details>
    </section>
  );
}
