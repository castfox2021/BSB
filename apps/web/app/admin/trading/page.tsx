'use client';
import { Suspense, useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import AdminShell from '../admin-shell';
import { EmptyState, MetricCard, RiskBanner, SectionHeader, StatusBadge } from '../components/admin-ui';
import { formatKrw, krPnlPrefix, TOKENS } from '../design-tokens';
import ManualFillPanel from '../../manual-fill-panel';
import PrivateAccount from '../../private-account';
import TradingAnalytics from '../../trading-analytics';
import TradingWorkspace from '../../trading-review';
import { api, kst } from '../../trading-types';

type TabId = 'holdings' | 'analytics' | 'journal' | 'logs';
type AuditItem = { id: number; type: string; details: Record<string, unknown>; created_at: string };
type AnalyticsSummary = {
  realized_pnl_krw?: string;
  unrealized_pnl_krw?: string;
  win_rate?: string | null;
  profit_factor?: string | null;
  max_drawdown_pct?: string | null;
  total_fees_krw?: string;
  trade_count?: number;
};

const TABS: { id: TabId; label: string }[] = [
  { id: 'holdings', label: '보유·체결' },
  { id: 'analytics', label: '손익 분석' },
  { id: 'journal', label: '매매노트' },
  { id: 'logs', label: '주문·감사 로그' },
];

function TradingContent() {
  const params = useSearchParams();
  const initialTab = (params.get('tab') as TabId) || 'holdings';
  const [tab, setTab] = useState<TabId>(TABS.some((t) => t.id === initialTab) ? initialTab : 'holdings');
  const [showManualFill, setShowManualFill] = useState(false);
  const [showAdvancedAnalytics, setShowAdvancedAnalytics] = useState(false);
  const [showDevLogs, setShowDevLogs] = useState(false);
  const [summary, setSummary] = useState<AnalyticsSummary | null>(null);
  const [audits, setAudits] = useState<AuditItem[]>([]);
  const [error, setError] = useState('');

  useEffect(() => {
    if (tab === 'analytics') {
      api<{ summary?: AnalyticsSummary }>('analytics/query?mode=LIVE&period=all')
        .then((d) => {
          setSummary(d.summary ?? null);
          setError('');
        })
        .catch(() => setError('손익 분석 데이터를 불러올 수 없습니다.'));
    } else if (tab === 'logs') {
      api<{ items?: AuditItem[] }>('admin/audit-logs?limit=80')
        .then((d) => {
          setAudits(d.items ?? []);
          setError('');
        })
        .catch(() => setError('주문·감사 로그를 불러올 수 없습니다.'));
    }
  }, [tab]);

  const mddNum = Math.min(100, Math.max(0, Number(summary?.max_drawdown_pct ?? 0)));
  const realizedNum = Number(summary?.realized_pnl_krw ?? 0);
  const unrealizedNum = Number(summary?.unrealized_pnl_krw ?? 0);

  return (
    <div className="space-y-6">
      {/* 4개 통합 탭 */}
      <div role="tablist" aria-label="거래 메뉴 탭" className="flex flex-wrap gap-2">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            type="button"
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
            className={`rounded-lg px-4 py-2 text-xs font-medium transition ${
              tab === t.id
                ? 'bg-zinc-100 text-zinc-950'
                : 'border border-[#1E2536] bg-[#121620] text-zinc-400 hover:text-zinc-200'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {error && <RiskBanner tone="danger" title="조회 오류" message={error} />}

      {/* 1. 보유·체결 탭 */}
      {tab === 'holdings' && (
        <div className="space-y-4">
          <PrivateAccount />
          <div className={`${TOKENS.card} p-5`}>
            <SectionHeader
              title="수동 체결 기록 (보조)"
              subtitle="외부 거래소에서 직접 체결한 주문을 수동으로 기록할 때만 펼치세요."
              actions={
                <button
                  type="button"
                  onClick={() => setShowManualFill((v) => !v)}
                  className="rounded-lg border border-[#1E2536] bg-[#0E1119] px-3 py-1.5 text-xs text-zinc-200 hover:border-zinc-500"
                >
                  {showManualFill ? '접기' : '수동 기록 펼치기'}
                </button>
              }
            />
            {showManualFill && (
              <div className="mt-4">
                <ManualFillPanel followTrade={null} onUnlink={() => {}} />
              </div>
            )}
          </div>
        </div>
      )}

      {/* 2. 손익 분석 탭 */}
      {tab === 'analytics' && (
        <div className="space-y-4">
          <section aria-label="손익 핵심 지표" className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-7">
            <MetricCard
              label="총 실현손익"
              value={summary ? `${krPnlPrefix(realizedNum)}${formatKrw(realizedNum, true)}` : '—'}
              tone={realizedNum > 0 ? 'profit' : realizedNum < 0 ? 'loss' : 'neutral'}
            />
            <MetricCard
              label="미실현손익"
              value={summary ? `${krPnlPrefix(unrealizedNum)}${formatKrw(unrealizedNum, true)}` : '—'}
              tone={unrealizedNum > 0 ? 'profit' : unrealizedNum < 0 ? 'loss' : 'neutral'}
            />
            <MetricCard label="승률" value={summary?.win_rate ? `${summary.win_rate}%` : '—'} />
            <MetricCard label="Profit Factor" value={summary?.profit_factor ?? '—'} />
            <MetricCard
              label="MDD"
              value={summary ? `-${mddNum.toFixed(2)}%` : '—'}
              tone={mddNum > 0 ? 'loss' : 'neutral'}
              tooltip="초기 운용자본 대비 최고점→최저점 최대 낙폭 (0~100% 범위)"
            />
            <MetricCard label="수수료" value={summary ? formatKrw(summary.total_fees_krw) : '—'} />
            <MetricCard label="총 거래 수" value={summary ? `${summary.trade_count ?? 0}건` : '—'} />
          </section>

          <div className="flex justify-end">
            <button
              type="button"
              onClick={() => setShowAdvancedAnalytics((v) => !v)}
              className="rounded-lg border border-[#1E2536] bg-[#121620] px-3.5 py-2 text-xs text-zinc-200 hover:border-zinc-500"
            >
              {showAdvancedAnalytics ? '상세 필터 · 고급 분석 접기' : '상세 필터 · 고급 분석 펼치기'}
            </button>
          </div>

          {showAdvancedAnalytics && <TradingAnalytics />}
        </div>
      )}

      {/* 3. 매매노트 탭 */}
      {tab === 'journal' && (
        <TradingWorkspace mode="journal" symbol="KRW-BTC" ordersEnabled={false} onOrder={() => {}} />
      )}

      {/* 4. 주문·감사 로그 탭 */}
      {tab === 'logs' && (
        <section className={`${TOKENS.card} space-y-4 p-5`}>
          <SectionHeader
            title="주문 · 감사 로그"
            subtitle="핵심 주문·보안 이벤트를 요약해 표시합니다."
            actions={
              <button
                type="button"
                onClick={() => setShowDevLogs((v) => !v)}
                className="rounded-lg border border-[#1E2536] bg-[#0E1119] px-3 py-1.5 text-xs text-zinc-300 hover:border-zinc-500"
              >
                {showDevLogs ? '고급 로그 접기' : '고급 로그 펼치기'}
              </button>
            }
          />
          {audits.length === 0 ? (
            <EmptyState message="표시할 주문 또는 감사 로그가 없습니다." />
          ) : (
            <div className="overflow-x-auto rounded-lg border border-[#1E2536] bg-[#0E1119]">
              <table className="w-full text-left font-mono text-xs">
                <thead className="border-b border-[#1E2536] text-[11px] text-zinc-400">
                  <tr>
                    <th scope="col" className="px-3 py-2.5">시각</th>
                    <th scope="col" className="px-3 py-2.5">종목</th>
                    <th scope="col" className="px-3 py-2.5">구분</th>
                    <th scope="col" className="px-3 py-2.5">상태</th>
                    <th scope="col" className="px-3 py-2.5 text-right">금액</th>
                    <th scope="col" className="px-3 py-2.5">출처</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#1E2536]/60">
                  {audits.map((a) => {
                    const d = a.details ?? {};
                    const sym = String(d.symbol ?? d.market ?? '—');
                    const side = String(d.side ?? (a.type.includes('buy') ? 'BUY' : a.type.includes('sell') ? 'SELL' : '—'));
                    const amt = d.order_krw ?? d.amount_krw ?? d.price ?? null;
                    const src = String(d.exchange ?? (a.type.startsWith('auto_') ? '자동엔진' : '관리/회원'));
                    return (
                      <tr key={a.id}>
                        <td className="px-3 py-2 tabular-nums text-zinc-400">{kst(a.created_at)}</td>
                        <td className="px-3 py-2 text-zinc-200">{sym}</td>
                        <td className="px-3 py-2">
                          {side === 'BUY' ? (
                            <StatusBadge tone="buy" label="매수" />
                          ) : side === 'SELL' ? (
                            <StatusBadge tone="sell" label="매도" />
                          ) : (
                            <span className="text-zinc-500">—</span>
                          )}
                        </td>
                        <td className="px-3 py-2">
                          <StatusBadge
                            tone={a.type.includes('failed') || a.type.includes('blocked') ? 'danger' : a.type.includes('skipped') ? 'warn' : 'neutral'}
                            label={a.type}
                          />
                        </td>
                        <td className="px-3 py-2 text-right tabular-nums text-zinc-300">
                          {amt != null ? formatKrw(String(amt)) : '—'}
                        </td>
                        <td className="px-3 py-2 text-zinc-400">{src}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {showDevLogs && audits.length > 0 && (
            <div className="space-y-2 border-t border-[#1E2536] pt-4 font-mono text-[11px]">
              <p className="text-zinc-400">고급 개발 로그 (원본 JSON)</p>
              {audits.slice(0, 30).map((a) => (
                <div key={`raw-${a.id}`} className={`${TOKENS.subCard} break-all px-3 py-2 text-zinc-400`}>
                  <span className="text-zinc-200">{a.type}</span> · {JSON.stringify(a.details)}
                </div>
              ))}
            </div>
          )}
        </section>
      )}
    </div>
  );
}

export default function AdminTradingPage() {
  return (
    <AdminShell>
      <Suspense fallback={<EmptyState message="거래 화면을 불러오는 중입니다..." />}>
        <TradingContent />
      </Suspense>
    </AdminShell>
  );
}
