import {pnlColor,signedMoney,signedRate,priceText} from './quiet-types';
import {kst} from './trading-types';

export type ReversionPosition={
  id:string;
  coin_name?:string;
  symbol:string;
  entry_price:string;
  current_price:string|null;
  return_rate:string|null;
  current_stop:string;
  peak_price:string;
  trail_active:number;
  opened_at:string;
  closed_at?:string;
  exit_price?:string;
  net_pnl?:string;
  exit_reason?:string;
};

export type WatchingItem={symbol:string;coin_name?:string;low:string;drops:string[];base_bars?:number};

export type Reversion={
  paper_only:true;
  live_execution:false;
  watching:number;
  watching_symbols?:string[];
  watching_items?:WatchingItem[];
  positions:ReversionPosition[];
  recent_results:ReversionPosition[];
  comparison:{policy:string;trades:number;win_rate:string|null;realized:string;max_drawdown:string}[];
  config:Record<string,string|number|boolean>;
};

const names:Record<string,string>={
  structure_trailing:'구조+트레일링',
  fixed_trailing:'고정손절+트레일링',
  atr_trailing:'ATR+트레일링',
  structure_fixed_take:'구조+고정익절 비교',
};

const reasons:Record<string,string>={
  STRUCTURE_STOP:'손절',
  TRAILING_STOP:'트레일링 청산',
  REBOUND_FAILURE:'반등 실패',
  FIXED_TAKE_COMPARISON:'고정 익절 비교',
};

function formatDuration(startStr?: string, endStr?: string) {
  if (!startStr || !endStr) return '';
  const diffSec = Math.max(0, Math.round((new Date(endStr).getTime() - new Date(startStr).getTime()) / 1000));
  if (diffSec < 60) return `${diffSec}초간 보유`;
  const m = Math.floor(diffSec / 60);
  const s = diffSec % 60;
  return s > 0 ? `${m}분 ${s}초간 보유` : `${m}분간 보유`;
}

function calcReturnRate(entry?: string, exit?: string) {
  if (!entry || !exit) return null;
  const en = Number(entry);
  const ex = Number(exit);
  if (en <= 0) return null;
  return (ex / en - 1);
}

const coinLabel=(name:string|undefined,symbol:string)=>`${name??symbol.replace('KRW-','')}(${symbol.replace('KRW-','')})`;
const bithumbOrderUrl=(symbol:string)=>`https://www.bithumb.com/react/trade/order/${symbol.replace(/^KRW-/,'')}-KRW`;

export default function ReversionPanel({data}:{data?:Reversion|null}){
  if(!data)return null;

  const primaryComp = data.comparison.find((c) => c.policy === 'structure_trailing');
  const primaryRealized = primaryComp?.realized ?? '0';
  const primaryTrades = primaryComp?.trades ?? 0;

  return (
    <section className="rounded-2xl border border-amber-900 bg-slate-900/70 p-5 sm:p-7 lg:col-span-2">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-amber-900/40 pb-3">
        <div>
          <h2 className="text-xl font-bold sm:text-2xl text-white">급락 반등 · 가상 자동손절</h2>
          <p className="mt-0.5 text-xs text-amber-200/80">독립 가상 자본 150,000원 전용 · 실전 주문 미실행</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded-full border border-amber-800 bg-amber-950/70 px-3 py-1 text-xs text-amber-200">
            급락반등 실현손익: <strong className={Number(primaryRealized) > 0 ? 'text-red-400' : Number(primaryRealized) < 0 ? 'text-blue-400' : 'text-slate-200'}>{signedMoney(primaryRealized)}</strong> ({primaryTrades}건 체결 완료)
          </span>
        </div>
      </div>

      <div className="mt-3 text-sm text-slate-400">
        <p>
          급락 후 반등 관찰 <strong className="text-amber-200">{data.watching}종목</strong>
          {data.watching_symbols && data.watching_symbols.length > 0 ? (
            <span className="ml-1.5 font-semibold text-amber-300">
              ({data.watching_symbols.map((s) => s.replace('KRW-', '')).join(', ')})
            </span>
          ) : (
            <span className="ml-1.5 text-xs text-slate-400">
              (현재 급락 기준에 도달한 코인이 없어 시장을 스캔하며 대기 중입니다)
            </span>
          )}
          {' '}· 급락 후 바닥 횡보(최소 {String(data.config?.min_base_bars ?? 3)}분) 다지기 확인 후 반등 돌파 시 진입 · 구조 손절과 최대 -{(Number(data.config?.maximum_stop_rate ?? 0.015) * 100).toFixed(1)}% · +{(Number(data.config?.trailing_activation_rate ?? 0.015) * 100).toFixed(1)}%부터 최고가 대비 -{(Number(data.config?.trailing_drawdown_rate ?? 0.008) * 100).toFixed(1)}% 트레일링
        </p>
        {data.watching_items && data.watching_items.length > 0 && (
          <div className="mt-3.5 grid grid-cols-1 gap-3 sm:grid-cols-2">
            {data.watching_items.map((item) => (
              <a
                key={item.symbol}
                href={bithumbOrderUrl(item.symbol)}
                target="_blank"
                rel="noopener noreferrer"
                title={`${coinLabel(item.coin_name, item.symbol)} 빗썸 주문 페이지 새창으로 열기`}
                className="group flex flex-wrap items-center justify-between gap-2.5 rounded-xl border border-amber-800/80 bg-amber-950/40 px-4 py-3 text-sm text-amber-200 shadow-sm transition hover:border-amber-500 hover:bg-amber-950/80 hover:shadow-md cursor-pointer"
              >
                <div className="flex items-center gap-2">
                  <strong className="text-base font-bold text-white group-hover:text-amber-100 group-hover:underline">
                    {coinLabel(item.coin_name, item.symbol)}
                  </strong>
                  <span className="text-xs text-amber-400 opacity-70 group-hover:opacity-100">↗</span>
                  {item.base_bars !== undefined && item.base_bars > 0 ? (
                    <span className="rounded-md bg-amber-900/80 px-2 py-0.5 text-xs font-semibold text-amber-300">
                      바닥 다지기 {item.base_bars}분
                    </span>
                  ) : (
                    <span className="rounded-md bg-slate-800/80 px-2 py-0.5 text-xs text-slate-400">
                      저점 탐색 중
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-2 text-sm">
                  <span className="text-slate-400">
                    저점 <strong className="text-white">{priceText(item.low)}원</strong>
                  </span>
                  {item.drops && item.drops.length > 0 ? (
                    <span className="font-semibold text-blue-400">
                      ({item.drops.map((d) => `${(Number(d) * 100).toFixed(1)}%`).join(' / ')})
                    </span>
                  ) : null}
                </div>
              </a>
            ))}
          </div>
        )}
      </div>

      {/* 실시간 가상 포지션 (보유 중) */}
      <div className="mt-5 grid gap-3 md:grid-cols-2">
        {data.positions.map((p) => (
          <article key={p.id} className="rounded-xl border border-slate-700 bg-slate-950/60 p-4">
            <div className="flex justify-between">
              <strong className="text-xl text-white">{coinLabel(p.coin_name,p.symbol)}</strong>
              <strong className={pnlColor(p.return_rate)}>{signedRate(p.return_rate)}</strong>
            </div>
            <p className="mt-2 text-sm">
              진입 {priceText(p.entry_price)}원 · 현재 {p.current_price ? priceText(p.current_price) : '시세 지연'}원
            </p>
            <p className="mt-2 text-sm text-blue-300">손절 예정 {priceText(p.current_stop)}원</p>
            <p className="mt-1 text-xs text-slate-400">
              최고가 {priceText(p.peak_price)}원 · 트레일링 {p.trail_active ? '작동 중' : '대기'} · {kst(p.opened_at)}
            </p>
          </article>
        ))}
        {!data.positions.length && (
          <div className="rounded-xl border border-slate-800 bg-slate-950/40 p-4 text-center text-xs text-slate-400 md:col-span-2">
            현재 보유 중인 급락 반등 포지션이 없습니다. (급락 후 반등 캔들 출현 시 자동 매수)
          </div>
        )}
      </div>

      {/* 최근 익절/손절 완료 내역 */}
      {data.recent_results.length > 0 && (
        <div className="mt-5">
          <h3 className="text-sm font-semibold text-slate-200">최근 익절·손절 완료 내역</h3>
          <div className="mt-2.5 space-y-2">
            {data.recent_results.map((p) => {
              const rate = calcReturnRate(p.entry_price, p.exit_price);
              const duration = formatDuration(p.opened_at, p.closed_at);
              return (
                <div key={p.id} className="rounded-xl border border-slate-700 bg-slate-950/80 p-3.5 text-sm shadow-sm">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <strong className="text-base font-bold text-white">{coinLabel(p.coin_name,p.symbol)}</strong>
                      <span
                        className={`rounded px-2 py-0.5 text-xs font-semibold ${
                          p.exit_reason === 'TRAILING_STOP'
                            ? 'border border-cyan-800 bg-cyan-950 text-cyan-300'
                            : p.exit_reason === 'STRUCTURE_STOP'
                            ? 'border border-blue-900 bg-blue-950 text-blue-300'
                            : 'bg-slate-800 text-slate-300'
                        }`}
                      >
                        {reasons[p.exit_reason ?? ''] ?? p.exit_reason}
                      </span>
                      {duration && <span className="text-xs text-slate-400">({duration})</span>}
                    </div>

                    <div className="text-right">
                      <strong className={`text-base font-bold ${pnlColor(p.net_pnl)}`}>
                        {signedMoney(p.net_pnl)}
                      </strong>
                      {rate !== null && (
                        <span className={`ml-2 text-xs font-semibold ${rate > 0 ? 'text-red-400' : rate < 0 ? 'text-blue-400' : 'text-slate-300'}`}>
                          ({rate > 0 ? '+' : ''}{(rate * 100).toFixed(2)}%)
                        </span>
                      )}
                    </div>
                  </div>

                  <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-400 border-t border-slate-900 pt-2">
                    <div>
                      <span className="text-slate-500">매수시점:</span>{' '}
                      <strong className="text-slate-200">{p.opened_at ? kst(p.opened_at) : '—'}</strong>
                      <span className="ml-1 text-slate-400">({priceText(p.entry_price)}원)</span>
                    </div>
                    <span className="text-slate-600">→</span>
                    <div>
                      <span className="text-slate-500">매도시점:</span>{' '}
                      <strong className="text-slate-200">{p.closed_at ? kst(p.closed_at) : '—'}</strong>
                      <span className="ml-1 text-slate-400">({p.exit_price ? `${priceText(p.exit_price)}원` : '—'})</span>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* 손절 정책별 비교 */}
      <details className="mt-5 text-sm">
        <summary className="cursor-pointer text-slate-300">손절 정책별 가상 성과 비교</summary>
        {data.comparison.map((c) => (
          <p key={c.policy} className="mt-2 text-slate-400 text-xs">
            {names[c.policy] ?? c.policy}: {c.trades}건 · 승률 {c.win_rate === null ? '—' : `${(Number(c.win_rate) * 100).toFixed(1)}%`} · 순손익 {signedMoney(c.realized)} · MDD {(Number(c.max_drawdown) * 100).toFixed(2)}%
          </p>
        ))}
      </details>
      <p className="mt-4 text-xs text-slate-500">노트북 절전·종료 또는 시세 연결 장애 중에는 로컬 보호 엔진이 작동하지 않습니다.</p>
    </section>
  );
}
