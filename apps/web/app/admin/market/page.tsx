'use client';
import { useEffect, useMemo, useState } from 'react';
import AdminShell from '../admin-shell';
import {
  DetailDrawer,
  EmptyState,
  MetricCard,
  Pagination,
  RiskBanner,
  SectionHeader,
  StatusBadge,
} from '../components/admin-ui';
import { formatKrw, krPnlColor, krPnlPrefix, TOKENS } from '../design-tokens';
import { useAdminStatus } from '../runtime-status';
import QuietDetail from '../../quiet-details';
import { api, kst, money, pct } from '../../trading-types';

type ScannerRow = {
  symbol: string;
  korean_name: string;
  english_name: string;
  price: string | null;
  score: string;
  volume_ratio_5m: string | null;
  price_change_5m: string;
  state: string;
  reason: string;
  candidate: boolean;
  detail_selected: boolean;
  fresh: boolean;
  ready: boolean;
  held: boolean;
  paper_held: boolean;
  expected_slippage: string | null;
  data_at: string | null;
  ai: { probability: number; model_id: string; advisory_only: boolean } | null;
};

type ScannerStatus = {
  watch_count: number;
  fresh_count: number;
  candidate_count: number;
  detail_count: number;
  order_ready_count: number;
  held_count: number;
};

type ScannerPage = {
  items: ScannerRow[];
  total: number;
  pages: number;
  status: ScannerStatus;
};

type PublicTicker = {
  symbol: string;
  change_1m?: string | null;
  change_3m?: string | null;
  change_5m?: string | null;
};

const PAGE_SIZE = 25;

export default function AdminMarketPage() {
  const { status: runtimeStatus } = useAdminStatus();
  const [rows, setRows] = useState<ScannerRow[]>([]);
  const [scanStatus, setScanStatus] = useState<ScannerStatus | null>(null);
  const [watchRebound, setWatchRebound] = useState<Set<string>>(new Set());
  const [changes, setChanges] = useState<Record<string, PublicTicker>>({});
  const [error, setError] = useState('');

  // Filters & sorting
  const [query, setQuery] = useState('');
  const [freshOnly, setFreshOnly] = useState(false);
  const [reboundOnly, setReboundOnly] = useState(false);
  const [surgeOnly, setSurgeOnly] = useState(false);
  const [heldOnly, setHeldOnly] = useState(false);
  const [stateFilter, setStateFilter] = useState('ALL');
  const [sortBy, setSortBy] = useState<'default' | 'price' | 'change5m' | 'vol5m' | 'name'>('default');
  const [sortDesc, setSortDesc] = useState(true);
  const [page, setPage] = useState(1);

  // Detail drawer
  const [detailRow, setDetailRow] = useState<ScannerRow | null>(null);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const first = await api<ScannerPage>('market/scanner?page=1&page_size=100');
        const totalPages = Math.min(10, Math.max(1, first.pages || 1));
        const restPromises: Promise<ScannerPage>[] = [];
        for (let p = 2; p <= totalPages; p += 1) {
          restPromises.push(api<ScannerPage>(`market/scanner?page=${p}&page_size=100`));
        }
        const [rest, ticks, strat] = await Promise.all([
          Promise.all(restPromises),
          api<{ items?: PublicTicker[] }>('public/market/tickers?limit=300').catch(() => ({ items: [] })),
          api<{ watching_items?: { symbol: string }[] }>('public/strategies/overview').catch(() => ({ watching_items: [] })),
        ]);
        if (!active) return;
        const allRows = [...(first.items ?? []), ...rest.flatMap((r) => r.items ?? [])];
        setRows(allRows);
        setScanStatus(first.status);
        const cmap: Record<string, PublicTicker> = {};
        for (const t of ticks.items ?? []) cmap[t.symbol] = t;
        setChanges(cmap);
        setWatchRebound(new Set((strat.watching_items ?? []).map((w) => w.symbol)));
        setError('');
      } catch {
        if (active) setError('시장 스캐너 데이터를 불러올 수 없습니다.');
      }
    };
    load();
    const timer = setInterval(load, 6000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);

  const uniqueStates = useMemo(() => {
    const s = new Set<string>();
    for (const r of rows) if (r.state) s.add(r.state);
    return ['ALL', ...Array.from(s).sort()];
  }, [rows]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = rows.filter((r) => {
      if (freshOnly && !r.fresh) return false;
      if (reboundOnly && !watchRebound.has(r.symbol)) return false;
      if (surgeOnly && !r.candidate) return false;
      if (heldOnly && !r.held && !r.paper_held) return false;
      if (stateFilter !== 'ALL' && r.state !== stateFilter) return false;
      if (q) {
        const hay = `${r.symbol} ${r.korean_name} ${r.english_name}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
    if (sortBy !== 'default') {
      list.sort((a, b) => {
        let av = 0;
        let bv = 0;
        if (sortBy === 'price') {
          av = Number(a.price ?? 0);
          bv = Number(b.price ?? 0);
        } else if (sortBy === 'change5m') {
          av = Number(changes[a.symbol]?.change_5m ?? a.price_change_5m ?? 0);
          bv = Number(changes[b.symbol]?.change_5m ?? b.price_change_5m ?? 0);
        } else if (sortBy === 'vol5m') {
          av = Number(a.volume_ratio_5m ?? 0);
          bv = Number(b.volume_ratio_5m ?? 0);
        } else if (sortBy === 'name') {
          return sortDesc
            ? b.korean_name.localeCompare(a.korean_name, 'ko')
            : a.korean_name.localeCompare(b.korean_name, 'ko');
        }
        return sortDesc ? bv - av : av - bv;
      });
    }
    return list;
  }, [rows, freshOnly, reboundOnly, surgeOnly, heldOnly, stateFilter, query, sortBy, sortDesc, watchRebound, changes]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const pageRows = filtered.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

  const watchCount = scanStatus?.watch_count ?? rows.length;
  const freshCount = scanStatus?.fresh_count ?? rows.filter((r) => r.fresh).length;
  const staleCount = Math.max(0, watchCount - freshCount);
  const lowFreshness = !runtimeStatus.marketConnected || (watchCount > 0 && freshCount < watchCount * 0.5);

  const fmtPctCell = (raw: string | null | undefined) => {
    if (raw == null) return <span className="text-zinc-600">—</span>;
    const n = Number(raw) * 100;
    if (!Number.isFinite(n)) return <span className="text-zinc-600">—</span>;
    return (
      <span className={krPnlColor(n)}>
        {krPnlPrefix(n)}
        {Math.abs(n).toFixed(2)}%
      </span>
    );
  };

  return (
    <AdminShell>
      <div className="space-y-6">
        {error && <RiskBanner tone="danger" title="시장 데이터 오류" message={error} />}

        {/* 시세 지연/누락 고정 경고 (50% 미만 시) */}
        {lowFreshness && (
          <RiskBanner
            tone="danger"
            title="시세 지연/누락 — 신규 실전매수 차단"
            message={`전체 ${watchCount}종목 중 최신 시세가 ${freshCount}종목(${watchCount > 0 ? Math.round((freshCount / watchCount) * 100) : 0}%)뿐입니다. 시세 복구 전까지 신규 실전매수가 차단됩니다.`}
          />
        )}

        {/* 상단 요약 (6개) */}
        <section aria-label="시장 요약" className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <MetricCard label="전체 종목" value={`${watchCount}`} />
          <MetricCard label="신선한 종목" value={`${freshCount}`} tone={lowFreshness ? 'loss' : 'neutral'} />
          <MetricCard label="지연 종목" value={`${staleCount}`} tone={staleCount > 0 ? 'warn' : 'neutral'} />
          <MetricCard label="급등 후보" value={`${scanStatus?.candidate_count ?? 0}`} />
          <MetricCard label="급락 반등 후보" value={`${watchRebound.size}`} />
          <MetricCard label="상세 호가 분석 중" value={`${scanStatus?.detail_count ?? 0}`} />
        </section>

        {/* 필터 및 종목 테이블 (기본 25개 페이지네이션) */}
        <section className={`${TOKENS.card} space-y-4 p-5`}>
          <SectionHeader
            title="전체 시장 스캐너"
            subtitle={`페이지당 ${PAGE_SIZE}종목만 렌더링합니다. 상세 호가·AI 점수·슬리피지는 '상세 보기'에서 확인하세요.`}
          />

          <div className="flex flex-wrap items-center gap-2 text-xs">
            <label className="sr-only" htmlFor="market-search">
              종목 검색
            </label>
            <input
              id="market-search"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setPage(1);
              }}
              placeholder="심볼·한글명 검색 (예: BTC, 비트코인)"
              className="w-56 rounded-lg border border-[#1E2536] bg-[#0E1119] px-3 py-1.5 text-zinc-100 placeholder:text-zinc-500"
            />
            {[
              ['신선 종목만', freshOnly, setFreshOnly],
              ['급락 후보만', reboundOnly, setReboundOnly],
              ['급등 후보만', surgeOnly, setSurgeOnly],
              ['보유 종목만', heldOnly, setHeldOnly],
            ].map(([label, val, setter]) => (
              <button
                key={String(label)}
                type="button"
                aria-pressed={Boolean(val)}
                onClick={() => {
                  (setter as (v: boolean) => void)(!val);
                  setPage(1);
                }}
                className={`rounded-lg border px-2.5 py-1.5 font-medium ${
                  val
                    ? 'border-zinc-300 bg-zinc-100 text-zinc-950'
                    : 'border-[#1E2536] bg-[#0E1119] text-zinc-300 hover:border-zinc-500'
                }`}
              >
                {String(label)}
              </button>
            ))}
            <select
              aria-label="상태별 필터"
              value={stateFilter}
              onChange={(e) => {
                setStateFilter(e.target.value);
                setPage(1);
              }}
              className="rounded-lg border border-[#1E2536] bg-[#0E1119] px-2.5 py-1.5 font-mono text-zinc-200"
            >
              {uniqueStates.map((st) => (
                <option key={st} value={st}>
                  {st === 'ALL' ? '상태: 전체' : st}
                </option>
              ))}
            </select>
            <select
              aria-label="정렬 기준"
              value={sortBy}
              onChange={(e) => {
                setSortBy(e.target.value as typeof sortBy);
                setPage(1);
              }}
              className="rounded-lg border border-[#1E2536] bg-[#0E1119] px-2.5 py-1.5 font-mono text-zinc-200"
            >
              <option value="default">정렬: 기본</option>
              <option value="price">현재가</option>
              <option value="change5m">5분 변동률</option>
              <option value="vol5m">거래량 변화</option>
              <option value="name">한글명</option>
            </select>
            {sortBy !== 'default' && (
              <button
                type="button"
                onClick={() => setSortDesc((d) => !d)}
                className="rounded-lg border border-[#1E2536] bg-[#0E1119] px-2.5 py-1.5 font-mono text-zinc-300"
              >
                {sortDesc ? '내림차순 ↓' : '오름차순 ↑'}
              </button>
            )}
          </div>

          {pageRows.length === 0 ? (
            <EmptyState />
          ) : (
            <div className="overflow-x-auto rounded-lg border border-[#1E2536] bg-[#0E1119]">
              <table className="w-full text-left font-mono text-xs">
                <thead className="border-b border-[#1E2536] text-[11px] text-zinc-400">
                  <tr>
                    <th scope="col" className="px-3 py-2.5">종목</th>
                    <th scope="col" className="px-3 py-2.5 text-right">현재가</th>
                    <th scope="col" className="px-3 py-2.5 text-right">1분</th>
                    <th scope="col" className="px-3 py-2.5 text-right">3분</th>
                    <th scope="col" className="px-3 py-2.5 text-right">5분</th>
                    <th scope="col" className="px-3 py-2.5 text-right">거래량 변화</th>
                    <th scope="col" className="px-3 py-2.5">상태</th>
                    <th scope="col" className="px-3 py-2.5 text-right">상세</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#1E2536]/60">
                  {pageRows.map((r) => {
                    const ch = changes[r.symbol];
                    const volRatio = r.volume_ratio_5m != null ? `${((Number(r.volume_ratio_5m) - 1) * 100).toFixed(0)}%` : '—';
                    return (
                      <tr key={r.symbol} className="hover:bg-[#151B28]">
                        <td className="px-3 py-2.5 font-sans">
                          <span className="font-medium text-zinc-100">{r.korean_name || r.symbol.replace('KRW-', '')}</span>
                          <span className="ml-1.5 font-mono text-[11px] text-zinc-500">
                            ({r.symbol.replace('KRW-', '')})
                          </span>
                        </td>
                        <td className="px-3 py-2.5 text-right tabular-nums text-zinc-200">
                          {r.price != null ? formatKrw(r.price) : '—'}
                        </td>
                        <td className="px-3 py-2.5 text-right tabular-nums">{fmtPctCell(ch?.change_1m)}</td>
                        <td className="px-3 py-2.5 text-right tabular-nums">{fmtPctCell(ch?.change_3m)}</td>
                        <td className="px-3 py-2.5 text-right tabular-nums">
                          {fmtPctCell(ch?.change_5m ?? r.price_change_5m)}
                        </td>
                        <td className="px-3 py-2.5 text-right tabular-nums text-zinc-300">{volRatio}</td>
                        <td className="px-3 py-2.5">
                          <StatusBadge
                            tone={r.fresh ? (r.candidate ? 'profit' : 'ok') : 'warn'}
                            label={r.fresh ? r.state || '신선' : 'STALE'}
                          />
                        </td>
                        <td className="px-3 py-2.5 text-right">
                          <button
                            type="button"
                            onClick={() => setDetailRow(r)}
                            className="rounded-md border border-[#1E2536] bg-[#121620] px-2.5 py-1 text-[11px] text-zinc-200 hover:border-zinc-500"
                          >
                            상세 보기
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          <Pagination
            page={safePage}
            totalPages={totalPages}
            totalItems={filtered.length}
            onPageChange={(next) => setPage(next)}
          />
        </section>
      </div>

      {/* 종목 상세 드로어 (상세 호가 · AI 점수 · 슬리피지) */}
      <DetailDrawer
        open={Boolean(detailRow)}
        title={detailRow ? `${detailRow.korean_name} (${detailRow.symbol})` : ''}
        subtitle={detailRow ? `${detailRow.english_name} · ${detailRow.reason || detailRow.state}` : ''}
        onClose={() => setDetailRow(null)}
      >
        {detailRow && (
          <div className="space-y-4">
            <div className={`${TOKENS.subCard} space-y-2 p-4 font-mono`}>
              <div className="flex justify-between">
                <span className="text-zinc-400">현재가</span>
                <span>{detailRow.price != null ? money(detailRow.price) : '—'}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-zinc-400">예상 슬리피지</span>
                <span>{detailRow.expected_slippage != null ? pct(detailRow.expected_slippage) : '—'}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-zinc-400">AI 참고 점수</span>
                <span>
                  {detailRow.ai ? `${(detailRow.ai.probability * 100).toFixed(1)}% (참고용)` : '미산출'}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-zinc-400">데이터 수신 시각</span>
                <span>{detailRow.data_at ? kst(detailRow.data_at) : '—'}</span>
              </div>
            </div>
            <QuietDetail symbol={detailRow.symbol} trade={null} onRecord={() => {}} />
          </div>
        )}
      </DetailDrawer>
    </AdminShell>
  );
}
