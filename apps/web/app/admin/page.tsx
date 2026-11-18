'use client';
import { useEffect, useState } from 'react';
import Link from 'next/link';
import AdminShell from './admin-shell';
import {
  DetailDrawer,
  EmptyState,
  FreshnessIndicator,
  MetricCard,
  PositionRow,
  PositionTable,
  RiskBanner,
  SectionHeader,
  StatusBadge,
  TradeRow,
  TradeTable,
} from './components/admin-ui';
import { formatKrw, krPnlPrefix, TOKENS } from './design-tokens';
import { useAdminStatus } from './runtime-status';
import { api, kst, openBithumb } from '../trading-types';
import { Board, quantityText } from '../quiet-types';

type RealAccount = { currency: string; balance: string; locked: string; average_price?: string };
type TickerItem = { symbol: string; coin_name?: string; price: string | null };
type AuditRow = { id: number; type: string; details_json?: string; created_at: string };

export default function AdminHomePage() {
  const { status } = useAdminStatus();
  const [board, setBoard] = useState<Board | null>(null);
  const [accounts, setAccounts] = useState<RealAccount[]>([]);
  const [prices, setPrices] = useState<Record<string, number>>({});
  const [names, setNames] = useState<Record<string, string>>({});
  const [audits, setAudits] = useState<AuditRow[]>([]);
  const [todayLiveRealized, setTodayLiveRealized] = useState<number>(0);
  const [fetchError, setFetchError] = useState('');
  const [selectedPos, setSelectedPos] = useState<{ kind: 'LIVE' | 'PAPER'; row: PositionRow; raw?: unknown } | null>(null);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const [b, acc, ticks, aud, liveToday] = await Promise.all([
          api<Board>('board?buy_page=1&sell_page=1'),
          api<{ items?: RealAccount[] }>('private/accounts').catch(() => ({ items: [] })),
          api<{ items?: TickerItem[] }>('public/market/tickers?limit=500').catch(() => ({ items: [] })),
          api<{ items?: AuditRow[] }>('admin/audit-logs?limit=5').catch(() => ({ items: [] })),
          api<{ summary?: { realized_pnl_krw?: string } }>('analytics/query?mode=LIVE&period=today').catch(() => ({ summary: undefined })),
        ]);
        if (!active) return;
        setBoard(b);
        setAccounts(acc.items ?? []);
        const pmap: Record<string, number> = {};
        const nmap: Record<string, string> = {};
        for (const t of ticks.items ?? []) {
          if (t.price) pmap[t.symbol] = Number(t.price);
          if (t.coin_name) nmap[t.symbol] = t.coin_name;
        }
        setPrices(pmap);
        setNames(nmap);
        setAudits(aud.items ?? []);
        setTodayLiveRealized(Number(liveToday?.summary?.realized_pnl_krw ?? 0));
        setFetchError('');
      } catch {
        if (active) setFetchError('운영 데이터를 불러올 수 없습니다.');
      }
    };
    load();
    const timer = setInterval(load, 4000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);

  const krwAvail = Number(accounts.find((a) => a.currency === 'KRW')?.balance ?? '0');
  const realHeldRows: PositionRow[] = accounts
    .filter((a) => a.currency !== 'KRW' && Number(a.balance) + Number(a.locked) > 0)
    .map((a) => {
      const sym = `KRW-${a.currency}`;
      const qty = Number(a.balance) + Number(a.locked);
      const avg = Number(a.average_price || 0);
      const cur = prices[sym] ?? 0;
      const pnlKrw = avg > 0 && cur > 0 ? (cur - avg) * qty : null;
      const pnlPct = avg > 0 && cur > 0 ? ((cur / avg) - 1) * 100 : null;
      return {
        id: `live-${a.currency}`,
        name: names[sym] || a.currency,
        symbol: sym,
        avgPrice: avg,
        currentPrice: cur,
        pnlPct,
        pnlKrw,
        protection: '매도 수동',
        protectionTone: 'warn',
      };
    });

  const paperHeldRows: PositionRow[] = (board?.holdings ?? []).map((h) => {
    const avg = Number(h.entry_price || 0);
    const cur = Number(h.current_price ?? h.last_price ?? prices[h.symbol] ?? 0);
    const pnlKrw = h.net_estimate_krw != null ? Number(h.net_estimate_krw) : null;
    const pnlPct = h.net_return_rate != null ? Number(h.net_return_rate) * 100 : null;
    return {
      id: h.id,
      name: names[h.symbol] || h.symbol.replace('KRW-', ''),
      symbol: h.symbol,
      avgPrice: avg,
      currentPrice: cur,
      pnlPct,
      pnlKrw,
      protection: h.trail_active ? '트레일링 활성' : '구조 손절 대기',
      protectionTone: h.trail_active ? 'ok' : 'neutral',
    };
  });

  const realUnrealized = realHeldRows.reduce((sum, r) => sum + (r.pnlKrw ?? 0), 0);
  const paperUnrealized = paperHeldRows.reduce((sum, r) => sum + (r.pnlKrw ?? 0), 0);
  const hasRealUnrealized = realHeldRows.some((r) => r.pnlKrw != null);
  const todayPaperRealized = Number(board?.summary.today_realized_krw ?? 0);
  const realInvestedKrw = accounts
    .filter((a) => a.currency !== 'KRW' && Number(a.balance) + Number(a.locked) > 0)
    .reduce((sum, a) => {
      const qty = Number(a.balance) + Number(a.locked);
      const px = prices[`KRW-${a.currency}`] ?? Number(a.average_price || 0);
      return sum + qty * px;
    }, 0);
  const dailyLossUsed = Math.max(0, -todayLiveRealized);

  const recentTrades: TradeRow[] = [
    ...(board?.sells.items ?? []).map((t) => ({
      id: `s-${t.id}`,
      symbol: t.symbol,
      side: 'SELL' as const,
      price: Number(t.fill_price ?? t.current_price ?? 0),
      pnlKrw: t.net_pnl != null ? Number(t.net_pnl) : null,
      time: t.at,
    })),
    ...(board?.buys.items ?? []).map((t) => ({
      id: `b-${t.id}`,
      symbol: t.symbol,
      side: 'BUY' as const,
      price: Number(t.fill_price ?? t.current_price ?? 0),
      pnlKrw: null,
      time: t.at,
    })),
  ]
    .sort((a, b) => b.time.localeCompare(a.time))
    .slice(0, 5);

  return (
    <AdminShell>
      <div className="space-y-6">
        {fetchError && <RiskBanner tone="danger" title="데이터 연결 오류" message={fetchError} />}

        {/* 1행: 핵심 상태 카드 (4개) */}
        <section aria-label="핵심 상태 요약" className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <MetricCard
            label="오늘 실현손익 (실전)"
            value={`${krPnlPrefix(todayLiveRealized)}${formatKrw(todayLiveRealized, true)}`}
            tone={todayLiveRealized > 0 ? 'profit' : todayLiveRealized < 0 ? 'loss' : 'neutral'}
            subtext={`가상 오늘 ${formatKrw(todayPaperRealized, true)}`}
          />
          <MetricCard
            label="미실현손익 (실전)"
            value={hasRealUnrealized ? `${krPnlPrefix(realUnrealized)}${formatKrw(realUnrealized, true)}` : '—'}
            tone={!hasRealUnrealized ? 'neutral' : realUnrealized > 0 ? 'profit' : realUnrealized < 0 ? 'loss' : 'neutral'}
            subtext={`가상 미실현 ${formatKrw(paperUnrealized, true)}`}
          />
          <MetricCard
            label="현재 보유 종목"
            value={`실전 ${realHeldRows.length} · 가상 ${paperHeldRows.length}`}
            subtext="클릭 시 상세 정보 확인"
          />
          <MetricCard
            label="주문 가능 원화"
            value={formatKrw(krwAvail)}
            subtext={status.privateConnected ? '빗썸 실전 계좌' : 'API 미연결'}
          />
        </section>

        {/* 2행: 운영 안전 상태 통합 카드 */}
        <section aria-label="운영 안전 상태" className={`${TOKENS.card} space-y-4 p-5`}>
          <SectionHeader
            title="운영 안전 상태"
            subtitle="주문 엔진·관제·시세 신선도·자본 한도를 단일 기준으로 점검합니다."
            actions={
              <Link
                href="/admin/settings"
                className="rounded-lg border border-[#1E2536] bg-[#0E1119] px-3 py-1.5 text-xs text-zinc-200 hover:border-zinc-500"
              >
                안전 설정 열기
              </Link>
            }
          />
          {status.actionMessage && (
            <RiskBanner
              tone={status.headlineTone === 'danger' ? 'danger' : status.headlineTone === 'ok' ? 'ok' : 'warn'}
              title={status.headline}
              message={status.actionMessage}
            />
          )}
          <div className="grid grid-cols-2 gap-3 text-xs sm:grid-cols-3 lg:grid-cols-6">
            <div className={`${TOKENS.subCard} p-3`}>
              <p className="text-[11px] text-zinc-400">주문 엔진 상태</p>
              <div className="mt-1.5">
                <StatusBadge
                  tone={status.controlState === 'RUNNING' ? 'ok' : status.controlState === 'EMERGENCY_STOP' ? 'danger' : 'warn'}
                  label={status.controlState}
                />
              </div>
            </div>
            <div className={`${TOKENS.subCard} p-3`}>
              <p className="text-[11px] text-zinc-400">자동매수 상태</p>
              <div className="mt-1.5">
                <StatusBadge
                  tone={status.autoBuyOnly ? (status.orderAllowed ? 'ok' : 'warn') : 'neutral'}
                  label={status.autoBuyOnly ? (status.orderAllowed ? '실행 중' : '대기(차단)') : 'OFF'}
                />
              </div>
            </div>
            <div className={`${TOKENS.subCard} p-3`}>
              <p className="text-[11px] text-zinc-400">시세 신선도</p>
              <div className="mt-1.5">
                <FreshnessIndicator
                  freshCount={status.freshCount}
                  watchCount={status.watchCount}
                  connected={status.marketConnected}
                />
              </div>
            </div>
            <div className={`${TOKENS.subCard} p-3`}>
              <p className="text-[11px] text-zinc-400">일일 손실 사용 / 한도</p>
              <p className="mt-1.5 font-mono text-xs font-semibold tabular-nums text-zinc-200">
                {formatKrw(dailyLossUsed)} / {formatKrw(status.policy?.daily_loss_alert_krw)}
              </p>
            </div>
            <div className={`${TOKENS.subCard} p-3`}>
              <p className="text-[11px] text-zinc-400">운용 자본 사용 / 기준</p>
              <p className="mt-1.5 font-mono text-xs font-semibold tabular-nums text-zinc-200">
                {formatKrw(realInvestedKrw)} / {formatKrw(status.policy?.initial_capital_krw)}
              </p>
            </div>
            <div className={`${TOKENS.subCard} p-3`}>
              <p className="text-[11px] text-zinc-400">API 연결 상태</p>
              <div className="mt-1.5">
                <StatusBadge
                  tone={status.privateConnected ? 'ok' : 'warn'}
                  label={status.privateConnected ? '연결됨' : '미연결'}
                />
              </div>
            </div>
          </div>
        </section>

        {/* 3행: 실전 보유 자산 / 가상 보유 포지션 */}
        <section className="grid gap-4 lg:grid-cols-2">
          <div className={`${TOKENS.card} space-y-3 p-5`}>
            <SectionHeader
              title="현재 실전 보유 자산"
              subtitle="항목을 클릭하면 상세 정보와 매도 바로가기를 볼 수 있습니다."
            />
            <PositionTable
              rows={realHeldRows}
              emptyMessage="보유 중인 실전 코인이 없습니다."
              onSelect={(row) =>
                setSelectedPos({
                  kind: 'LIVE',
                  row,
                  raw: accounts.find((a) => `KRW-${a.currency}` === row.symbol),
                })
              }
            />
          </div>

          <div className={`${TOKENS.card} space-y-3 p-5`}>
            <SectionHeader
              title="현재 가상 보유 포지션"
              subtitle="가상 추종 전략이 감시 중인 포지션입니다."
            />
            <PositionTable
              rows={paperHeldRows}
              emptyMessage="보유 중인 가상 포지션이 없습니다."
              onSelect={(row) =>
                setSelectedPos({
                  kind: 'PAPER',
                  row,
                  raw: board?.holdings.find((h) => h.id === row.id),
                })
              }
            />
          </div>
        </section>

        {/* 4행: 최근 체결 5건 / 최근 경고 5건 */}
        <section className="grid gap-4 lg:grid-cols-2">
          <div className={`${TOKENS.card} space-y-3 p-5`}>
            <SectionHeader
              title="최근 체결 5건"
              actions={
                <Link href="/admin/trading" className="text-xs text-zinc-400 hover:text-zinc-200">
                  거래 전체 보기 →
                </Link>
              }
            />
            <TradeTable rows={recentTrades} emptyMessage="최근 체결 내역이 없습니다." />
          </div>

          <div className={`${TOKENS.card} space-y-3 p-5`}>
            <SectionHeader
              title="최근 경고 · 시스템 이벤트 5건"
              actions={
                <Link href="/admin/trading?tab=logs" className="text-xs text-zinc-400 hover:text-zinc-200">
                  로그 전체 보기 →
                </Link>
              }
            />
            {audits.length === 0 ? (
              <EmptyState message="최근 경고 또는 이벤트가 없습니다." />
            ) : (
              <div className="space-y-2">
                {audits.slice(0, 5).map((a) => (
                  <div key={a.id} className={`${TOKENS.subCard} flex items-center justify-between gap-2 px-3 py-2 text-xs`}>
                    <span className="truncate font-mono text-zinc-200">{a.type}</span>
                    <span className="shrink-0 font-mono text-[11px] tabular-nums text-zinc-500">{kst(a.created_at)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </section>
      </div>

      {/* 포지션 상세 드로어 */}
      <DetailDrawer
        open={Boolean(selectedPos)}
        title={selectedPos ? `${selectedPos.row.name} (${selectedPos.row.symbol})` : ''}
        subtitle={selectedPos?.kind === 'LIVE' ? '실전 보유 자산 상세' : '가상 포지션 상세'}
        onClose={() => setSelectedPos(null)}
      >
        {selectedPos && (
          <div className="space-y-4">
            <div className={`${TOKENS.subCard} space-y-2 p-4 font-mono`}>
              <div className="flex justify-between">
                <span className="text-zinc-400">평균 매수가</span>
                <span>{selectedPos.row.avgPrice > 0 ? formatKrw(selectedPos.row.avgPrice) : '—'}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-zinc-400">현재가</span>
                <span>{selectedPos.row.currentPrice > 0 ? formatKrw(selectedPos.row.currentPrice) : '—'}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-zinc-400">평가손익</span>
                <span>{selectedPos.row.pnlKrw != null ? formatKrw(selectedPos.row.pnlKrw, true) : '—'}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-zinc-400">보호 상태</span>
                <StatusBadge tone={selectedPos.row.protectionTone ?? 'neutral'} label={selectedPos.row.protection} />
              </div>
              {selectedPos.kind === 'LIVE' && Boolean(selectedPos.raw) && (
                <div className="flex justify-between">
                  <span className="text-zinc-400">보유 수량</span>
                  <span>
                    {quantityText(
                      Number((selectedPos.raw as RealAccount).balance) + Number((selectedPos.raw as RealAccount).locked),
                    )}
                  </span>
                </div>
              )}
            </div>
            {selectedPos.kind === 'LIVE' && (
              <div className="flex justify-end">
                <button
                  type="button"
                  onClick={() => openBithumb(selectedPos.row.symbol)}
                  className="rounded-lg border border-rose-500/40 bg-rose-500/15 px-3.5 py-2 font-semibold text-rose-300 hover:bg-rose-500/25"
                >
                  빗썸에서 매도하기 (외부 이동)
                </button>
              </div>
            )}
          </div>
        )}
      </DetailDrawer>
    </AdminShell>
  );
}
