'use client';
import { useEffect, useState } from 'react';
import { api, kst } from './trading-types';
import { pnlColor, signedMoney, priceText, quantityText } from './quiet-types';
import AiStrategyReport from './ai-strategy-report';

type Summary = {
  mode: string;
  calculation_version: string;
  total_buy_krw: string;
  total_sell_krw: string;
  total_fees_krw: string;
  realized_pnl_krw: string;
  unrealized_pnl_krw: string;
  net_equity_krw: string;
  trade_count: number;
  wins: number;
  losses: number;
  win_rate: string | null;
  avg_profit_krw: string | null;
  avg_loss_krw: string | null;
  payoff_ratio: string | null;
  profit_factor: string | null;
  expectancy_krw: string | null;
  max_drawdown_pct: string;
  avg_holding_seconds: number | null;
  max_consecutive_wins: number;
  max_consecutive_losses: number;
  open_positions_count: number;
  open_issues_count: number;
  sharpe_ratio: number | null;
  sortino_ratio: number | null;
};

type AnalyticsData = {
  summary: Summary;
  charts: {
    cumulative_pnl: { timestamp: string; cumulative_pnl_krw: string; drawdown_pct: string }[];
    daily_pnl: { date: string; pnl_krw: string }[];
    monthly_pnl: { month: string; pnl_krw: string }[];
    by_market: { market: string; trades: number; win_rate: string | null; pnl_krw: string; fees_krw: string }[];
    by_origin: { origin: string; trades: number; win_rate: string | null; pnl_krw: string }[];
    by_strategy: { strategy: string; trades: number; win_rate: string | null; pnl_krw: string; avg_hold_seconds: number }[];
    hourly_kst: { hour: number; trades: number; pnl_krw: string; low_sample: boolean }[];
    weekday_kst: { weekday: number; trades: number; pnl_krw: string; win_rate: string | null; low_sample: boolean }[];
  };
  trades: {
    id: string;
    market: string;
    execution_origin: string;
    strategy_name: string;
    allocated_quantity: string;
    allocated_cost: string;
    allocated_proceeds: string;
    realized_pnl: string;
    realized_pnl_pct: string | null;
    holding_seconds: number;
    opened_at: string;
    closed_at: string;
    exit_reason: string;
  }[];
  open_positions: Record<string, string>[];
  issues: { id: string; issue_type: string; market: string; description: string; status: string }[];
};

type SyncStatus = {
  account_id: string;
  api_connected: boolean;
  last_success_at: string | null;
  last_error: string | null;
  total_exchange_fills: number;
  open_reconciliation_issues: number;
};

const subMenus = [
  ['overview', '종합 Overview'],
  ['pnl', '손익 P&L'],
  ['strategy', '전략 성과'],
  ['entry', '진입 타이밍'],
  ['exit', '익절·손절'],
  ['market', '코인별'],
  ['time', '시간대별'],
  ['risk', '리스크'],
  ['trades', '거래내역·동기화'],
  ['ai', 'AI 리포트'],
] as const;

const weekdays = ['월', '화', '수', '목', '금', '토', '일'];

function formatHold(sec: number | null) {
  if (sec === null || sec === undefined) return '—';
  if (sec < 60) return `${sec}초`;
  if (sec < 3600) return `${Math.floor(sec / 60)}분`;
  return `${(sec / 3600).toFixed(1)}시간`;
}

function CumulativeSvg({ points }: { points: { timestamp: string; cumulative_pnl_krw: string }[] }) {
  if (!points.length) {
    return <p className="py-8 text-center text-xs text-zinc-500">누적 손익 데이터가 없습니다.</p>;
  }
  const vals = points.map((p) => Number(p.cumulative_pnl_krw));
  const min = Math.min(0, ...vals);
  const max = Math.max(1, ...vals);
  const span = max - min || 1;
  const w = 640;
  const h = 160;
  const coords = vals
    .map((v, i) => {
      const x = points.length === 1 ? w / 2 : (i / (points.length - 1)) * (w - 20) + 10;
      const y = h - 14 - ((v - min) / span) * (h - 28);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
  const last = vals[vals.length - 1] ?? 0;
  const stroke = last >= 0 ? '#34d399' : '#fb7185';
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-40 w-full">
      <line x1="10" x2={w - 10} y1={h - 14 - ((0 - min) / span) * (h - 28)} y2={h - 14 - ((0 - min) / span) * (h - 28)} stroke="#2A344B" strokeDasharray="4 4" />
      <polyline fill="none" stroke={stroke} strokeWidth="2.2" points={coords} />
    </svg>
  );
}

export default function TradingAnalytics() {
  const [sub, setSub] = useState<(typeof subMenus)[number][0]>('overview');
  const [light, setLight] = useState(false);
  const [mode, setMode] = useState<'LIVE' | 'PAPER'>('LIVE');
  const [period, setPeriod] = useState('all');
  const [market, setMarket] = useState('ALL');
  const [origin, setOrigin] = useState('ALL');
  const [outcome, setOutcome] = useState('ALL');
  const [data, setData] = useState<AnalyticsData | null>(null);
  const [sync, setSync] = useState<SyncStatus | null>(null);
  const [reversionComp, setReversionComp] = useState<{ policies?: { policy: string; trades: number; realized: string; win_rate: string | null; profit_factor: string | null; max_drawdown: string }[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');

  // Baseline cost form
  const [baseMarket, setBaseMarket] = useState('');
  const [baseQty, setBaseQty] = useState('');
  const [basePrice, setBasePrice] = useState('');

  const queryStr = () =>
    new URLSearchParams({ mode, period, market, origin, outcome }).toString();

  async function loadAll() {
    setBusy(true);
    setErr('');
    try {
      const qs = queryStr();
      if (typeof window !== 'undefined') {
        window.history.replaceState(null, '', `?${qs}`);
      }
      const [sum, st, comp] = await Promise.all([
        api<AnalyticsData>(`analytics/summary?${qs}`),
        api<SyncStatus>('analytics/sync/status'),
        api<{ reversion_comparison?: { policies?: { policy: string; trades: number; realized: string; win_rate: string | null; profit_factor: string | null; max_drawdown: string }[] } }>(`analytics/strategies?${qs}`),
      ]);
      setData(sum);
      setSync(st);
      setReversionComp(comp.reversion_comparison ?? null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : '거래 분석 데이터를 불러오지 못했습니다.');
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    loadAll();
  }, [mode, period, market, origin, outcome]);

  async function triggerSync() {
    setBusy(true);
    setMsg('');
    setErr('');
    try {
      const res = await api<{ status: string; records_inserted?: number }>('analytics/sync', 'POST', {});
      setMsg(`동기화 완료 (${res.status} · 신규 ${res.records_inserted ?? 0}건)`);
      await loadAll();
    } catch (e) {
      setErr(e instanceof Error ? e.message : '동기화 실패');
    } finally {
      setBusy(false);
    }
  }

  async function triggerRecalc() {
    setBusy(true);
    setMsg('');
    try {
      const res = await api<{ positions: number; allocations: number }>('analytics/recalculate', 'POST', {});
      setMsg(`원장 재계산 완료 (포지션 ${res.positions}건 · 배분 ${res.allocations}건)`);
      await loadAll();
    } catch (e) {
      setErr(e instanceof Error ? e.message : '재계산 실패');
    } finally {
      setBusy(false);
    }
  }

  async function handleImport(file: File | null) {
    if (!file) return;
    setBusy(true);
    setMsg('');
    setErr('');
    try {
      const buf = await file.arrayBuffer();
      const bytes = new Uint8Array(buf);
      let bin = '';
      for (let i = 0; i < bytes.length; i += 1) bin += String.fromCharCode(bytes[i]);
      const b64 = btoa(bin);
      const res = await api<{ inserted_rows: number; duplicate_rows: number; conflict_rows: number }>(
        'analytics/import',
        'POST',
        { filename: file.name, content_base64: b64 }
      );
      setMsg(
        `파일 가져오기 완료: 신규 ${res.inserted_rows}건 · 중복 ${res.duplicate_rows}건 · 검토 충돌 ${res.conflict_rows}건`
      );
      await loadAll();
    } catch (e) {
      setErr(e instanceof Error ? e.message : '파일 가져오기 실패');
    } finally {
      setBusy(false);
    }
  }

  async function saveBaseline() {
    if (!baseMarket || !baseQty || !basePrice) return;
    setBusy(true);
    setErr('');
    try {
      await api('analytics/baseline', 'POST', {
        market: baseMarket,
        initial_quantity: baseQty,
        avg_cost_krw: basePrice,
      });
      setMsg(`${baseMarket.toUpperCase()} 기초 원가를 저장하고 재계산했습니다.`);
      setBaseMarket('');
      setBaseQty('');
      setBasePrice('');
      await loadAll();
    } catch (e) {
      setErr(e instanceof Error ? e.message : '기초 원가 저장 실패');
    } finally {
      setBusy(false);
    }
  }

  const card = light
    ? 'rounded-xl border border-zinc-300 bg-white text-zinc-900'
    : 'rounded-xl border border-[#1E2536] bg-[#121620] text-zinc-100';
  const subCard = light
    ? 'rounded-lg border border-zinc-200 bg-zinc-50 p-3.5'
    : 'rounded-lg border border-[#1E2536] bg-[#0E1119] p-3.5';
  const inputCls = light
    ? 'rounded-lg border border-zinc-300 bg-white px-2.5 py-1.5 text-xs font-mono text-zinc-900'
    : 'rounded-lg border border-[#1E2536] bg-[#0B0E14] px-2.5 py-1.5 text-xs font-mono text-zinc-100';

  const s = data?.summary;

  return (
    <section className="space-y-5">
      {/* 상단 필터 & 내보내기 바 */}
      <div className={`${card} p-5`}>
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#1E2536]/60 pb-4">
          <div>
            <h2 className="text-base font-semibold">통합 거래 분석 (Task 18 / 18-A)</h2>
            <p className="mt-0.5 text-xs text-zinc-400">
              빗썸 API · CSV/XLSX 원장 통합 · 실거래(LIVE)와 가상매매(PAPER)는 엄격히 분리됩니다.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => setLight((v) => !v)}
              className="rounded-md border border-[#2A344B] bg-[#181E2B] px-2.5 py-1 text-xs text-zinc-200 hover:border-zinc-400"
            >
              {light ? '다크모드' : '라이트모드'}
            </button>
            {(['csv', 'xlsx', 'json', 'markdown'] as const).map((fmt) => (
              <a
                key={fmt}
                href={`/api/manual/analytics/export?format=${fmt}&${queryStr()}`}
                className="rounded-md border border-[#2A344B] bg-[#181E2B] px-2.5 py-1 text-xs font-mono uppercase text-zinc-200 hover:border-zinc-400"
              >
                {fmt}
              </a>
            ))}
          </div>
        </div>

        {/* 공통 필터 */}
        <div className="mt-4 grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-6">
          <select className={inputCls} value={mode} onChange={(e) => setMode(e.target.value as 'LIVE' | 'PAPER')}>
            <option value="LIVE">실거래 (LIVE)</option>
            <option value="PAPER">가상매매 (PAPER)</option>
          </select>
          <select className={inputCls} value={period} onChange={(e) => setPeriod(e.target.value)}>
            <option value="all">전체 기간</option>
            <option value="today">오늘 (KST)</option>
            <option value="7d">최근 7일</option>
            <option value="30d">최근 30일</option>
            <option value="90d">최근 90일</option>
            <option value="year">올해</option>
          </select>
          <select className={inputCls} value={origin} onChange={(e) => setOrigin(e.target.value)}>
            <option value="ALL">전체 출처</option>
            <option value="BOT">자동매매 (BOT)</option>
            <option value="MANUAL">수동매매 (MANUAL)</option>
            <option value="UNKNOWN">미확정 (UNKNOWN)</option>
          </select>
          <select className={inputCls} value={outcome} onChange={(e) => setOutcome(e.target.value)}>
            <option value="ALL">수익·손실 전체</option>
            <option value="WIN">수익 거래만</option>
            <option value="LOSS">손실 거래만</option>
          </select>
          <input
            className={inputCls}
            value={market}
            onChange={(e) => setMarket(e.target.value.toUpperCase() || 'ALL')}
            placeholder="종목 (ALL 또는 KRW-BTC)"
          />
          <button
            type="button"
            disabled={busy}
            onClick={triggerSync}
            className="rounded-lg border border-emerald-500/40 bg-emerald-500/15 px-3 py-1.5 text-xs font-semibold text-emerald-300 hover:bg-emerald-500/25 disabled:opacity-40"
          >
            {busy ? '동기화 중...' : '빗썸 지금 동기화'}
          </button>
        </div>

        {/* 서브 메뉴 (9개 분석 화면 + AI 리포트) */}
        <div className="mt-4 flex flex-wrap gap-1.5 border-t border-[#1E2536]/60 pt-3">
          {subMenus.map(([id, label]) => (
            <button
              key={id}
              type="button"
              onClick={() => setSub(id)}
              className={`rounded-md px-3 py-1.5 text-xs font-medium transition ${
                sub === id
                  ? 'bg-zinc-100 text-zinc-950'
                  : 'border border-[#1E2536] bg-[#0E1119] text-zinc-400 hover:text-zinc-200'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        {msg && <p className="mt-3 text-xs font-medium text-emerald-400">{msg}</p>}
        {err && <p className="mt-3 text-xs font-medium text-rose-400">{err}</p>}
      </div>

      {/* 1. 종합 Overview */}
      {sub === 'overview' && s && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
            {[
              ['총 실현손익', signedMoney(s.realized_pnl_krw), pnlColor(s.realized_pnl_krw)],
              ['총 미실현손익', signedMoney(s.unrealized_pnl_krw), pnlColor(s.unrealized_pnl_krw)],
              ['승률', s.win_rate ? `${s.win_rate}%` : '—', ''],
              ['Profit Factor', s.profit_factor ?? '—', ''],
              ['최대 낙폭(MDD)', `-${Math.min(100, Math.max(0, Number(s.max_drawdown_pct))).toFixed(2)}%`, 'text-sky-400'],
              ['총 거래 수수료', `${Number(s.total_fees_krw).toLocaleString('ko-KR')}원`, ''],
              ['총 거래 횟수', `${s.trade_count}건 (${s.wins}승 ${s.losses}패)`, ''],
              ['평균 손익비', s.payoff_ratio ?? '—', ''],
              ['기대값', signedMoney(s.expectancy_krw), pnlColor(s.expectancy_krw)],
              ['평균 보유시간', formatHold(s.avg_holding_seconds), ''],
              ['미청산 포지션', `${s.open_positions_count}종목`, ''],
              ['연속 승 / 패', `${s.max_consecutive_wins}연승 / ${s.max_consecutive_losses}연패`, ''],
            ].map(([label, val, cls]) => (
              <div key={String(label)} className={subCard}>
                <p className="text-[11px] text-zinc-400">{label}</p>
                <p className={`mt-1 font-mono text-sm font-semibold tabular-nums ${cls}`}>{val}</p>
              </div>
            ))}
          </div>

          <div className={`${card} p-5`}>
            <h3 className="text-sm font-semibold">누적 실현손익 추이 ({s.mode})</h3>
            <CumulativeSvg points={data.charts.cumulative_pnl} />
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <div className={`${card} p-5 text-xs`}>
              <h3 className="text-sm font-semibold">매매 방식별 성과 (자동 vs 수동)</h3>
              <div className="mt-3 space-y-2 font-mono tabular-nums">
                {data.charts.by_origin.length ? (
                  data.charts.by_origin.map((o) => (
                    <div key={o.origin} className="flex items-center justify-between border-b border-[#1E2536]/50 pb-1.5">
                      <span>{o.origin} ({o.trades}건 · 승률 {o.win_rate ?? '—'}%)</span>
                      <span className={pnlColor(o.pnl_krw)}>{signedMoney(o.pnl_krw)}</span>
                    </div>
                  ))
                ) : (
                  <p className="text-zinc-500">집계된 거래가 없습니다.</p>
                )}
              </div>
            </div>
            <div className={`${card} p-5 text-xs`}>
              <h3 className="text-sm font-semibold">코인별 수익 기여도 (클릭 시 필터)</h3>
              <div className="mt-3 space-y-2 font-mono tabular-nums">
                {data.charts.by_market.slice(0, 8).map((m) => (
                  <button
                    key={m.market}
                    type="button"
                    onClick={() => setMarket(m.market)}
                    className="flex w-full items-center justify-between border-b border-[#1E2536]/50 pb-1.5 text-left hover:opacity-80"
                  >
                    <span>{m.market.replace('KRW-', '')} ({m.trades}건)</span>
                    <span className={pnlColor(m.pnl_krw)}>{signedMoney(m.pnl_krw)}</span>
                  </button>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 2. 손익 P&L */}
      {sub === 'pnl' && data && (
        <div className={`${card} p-5 text-xs`}>
          <h3 className="text-sm font-semibold">일별 · 월별 실현손익 ({s?.mode})</h3>
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <div className={subCard}>
              <p className="mb-2 font-semibold text-zinc-300">일별 손익 (Asia/Seoul)</p>
              <div className="max-h-64 space-y-1.5 overflow-auto font-mono tabular-nums">
                {data.charts.daily_pnl.map((d) => (
                  <div key={d.date} className="flex justify-between">
                    <span>{d.date}</span>
                    <span className={pnlColor(d.pnl_krw)}>{signedMoney(d.pnl_krw)}</span>
                  </div>
                ))}
                {!data.charts.daily_pnl.length && <p className="text-zinc-500">데이터 없음</p>}
              </div>
            </div>
            <div className={subCard}>
              <p className="mb-2 font-semibold text-zinc-300">월별 손익</p>
              <div className="space-y-1.5 font-mono tabular-nums">
                {data.charts.monthly_pnl.map((m) => (
                  <div key={m.month} className="flex justify-between">
                    <span>{m.month}</span>
                    <span className={pnlColor(m.pnl_krw)}>{signedMoney(m.pnl_krw)}</span>
                  </div>
                ))}
                {!data.charts.monthly_pnl.length && <p className="text-zinc-500">데이터 없음</p>}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 3. 전략 성과 & 5. 익절·손절 */}
      {(sub === 'strategy' || sub === 'exit' || sub === 'entry') && (
        <div className={`${card} p-5 text-xs`}>
          <h3 className="text-sm font-semibold">
            {sub === 'entry'
              ? '진입 타이밍 및 MFE/MAE 분석'
              : sub === 'exit'
                ? 'Task 16 손절 · Task 17 분할 익절 성과 비교'
                : '전략별 성과 비교'}
          </h3>
          <p className="mt-1 text-zinc-400">
            가상 시뮬레이션 비교와 실거래(`{s?.mode}`) 원장 통계는 분리 표시됩니다.
          </p>
          {reversionComp?.policies && (
            <div className="mt-4 overflow-x-auto">
              <table className="w-full min-w-[520px] text-left font-mono tabular-nums">
                <thead className="border-b border-[#1E2536] text-[11px] text-zinc-400">
                  <tr>
                    <th className="py-2">정책 (PAPER 시뮬레이션)</th>
                    <th className="py-2">거래</th>
                    <th className="py-2">승률</th>
                    <th className="py-2">순손익</th>
                    <th className="py-2">PF</th>
                    <th className="py-2">MDD</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#1E2536]/60">
                  {reversionComp.policies.map((p) => (
                    <tr key={p.policy}>
                      <td className="py-2 font-sans">{p.policy}</td>
                      <td className="py-2">{p.trades}건</td>
                      <td className="py-2">{p.win_rate ? `${(Number(p.win_rate) * 100).toFixed(1)}%` : '—'}</td>
                      <td className={`py-2 ${pnlColor(p.realized)}`}>{signedMoney(p.realized)}</td>
                      <td className="py-2">{p.profit_factor ?? '—'}</td>
                      <td className="py-2 text-sky-400">-{(Number(p.max_drawdown) * 100).toFixed(2)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* 6. 코인별 */}
      {sub === 'market' && data && (
        <div className={`${card} overflow-x-auto p-5 text-xs`}>
          <h3 className="mb-3 text-sm font-semibold">코인별 상세 성과</h3>
          <table className="w-full min-w-[500px] text-left font-mono tabular-nums">
            <thead className="border-b border-[#1E2536] text-[11px] text-zinc-400">
              <tr>
                <th className="py-2">종목</th>
                <th className="py-2">거래</th>
                <th className="py-2">승률</th>
                <th className="py-2">실현손익</th>
                <th className="py-2">수수료</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#1E2536]/60">
              {data.charts.by_market.map((m) => (
                <tr key={m.market}>
                  <td className="py-2">{m.market}</td>
                  <td className="py-2">{m.trades}건</td>
                  <td className="py-2">{m.win_rate ? `${m.win_rate}%` : '—'}</td>
                  <td className={`py-2 ${pnlColor(m.pnl_krw)}`}>{signedMoney(m.pnl_krw)}</td>
                  <td className="py-2">{Number(m.fees_krw).toLocaleString('ko-KR')}원</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* 7. 시간대별 */}
      {sub === 'time' && data && (
        <div className={`${card} space-y-4 p-5 text-xs`}>
          <h3 className="text-sm font-semibold">시간대별 · 요일별 성과 (Asia/Seoul)</h3>
          <div className="grid grid-cols-4 gap-2 sm:grid-cols-6 lg:grid-cols-8">
            {data.charts.hourly_kst.map((h) => (
              <div key={h.hour} className={subCard}>
                <div className="flex justify-between font-mono text-[11px] text-zinc-400">
                  <span>{String(h.hour).padStart(2, '0')}시</span>
                  <span>{h.trades}건</span>
                </div>
                <p className={`mt-1 font-mono text-xs font-semibold ${pnlColor(h.pnl_krw)}`}>{signedMoney(h.pnl_krw)}</p>
                {h.low_sample && h.trades > 0 && <span className="text-[10px] text-zinc-500">표본 적음</span>}
              </div>
            ))}
          </div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-7">
            {data.charts.weekday_kst.map((w) => (
              <div key={w.weekday} className={subCard}>
                <p className="text-[11px] text-zinc-400">{weekdays[w.weekday]}요일 ({w.trades}건)</p>
                <p className={`mt-1 font-mono text-xs font-semibold ${pnlColor(w.pnl_krw)}`}>{signedMoney(w.pnl_krw)}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 8. 리스크 & 기초 원가 보정 */}
      {sub === 'risk' && s && (
        <div className={`${card} space-y-4 p-5 text-xs`}>
          <h3 className="text-sm font-semibold">리스크 및 데이터 완전성</h3>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <div className={subCard}>
              <p className="text-zinc-400">Sharpe Ratio (5건 이상)</p>
              <p className="mt-1 font-mono text-base font-semibold">{s.sharpe_ratio ?? '표본 부족'}</p>
            </div>
            <div className={subCard}>
              <p className="text-zinc-400">Sortino Ratio</p>
              <p className="mt-1 font-mono text-base font-semibold">{s.sortino_ratio ?? '표본 부족'}</p>
            </div>
            <div className={subCard} title="초기 운용자본 대비 최고점→최저점 최대 낙폭 (0~100% 범위)">
              <p className="text-zinc-400">최대 낙폭 (MDD) ⓘ</p>
              <p className="mt-1 font-mono text-base font-semibold text-sky-400">
                -{Math.min(100, Math.max(0, Number(s.max_drawdown_pct))).toFixed(2)}%
              </p>
            </div>
            <div className={subCard}>
              <p className="text-zinc-400">미해결 정합성 이슈</p>
              <p className="mt-1 font-mono text-base font-semibold text-amber-300">{s.open_issues_count}건</p>
            </div>
          </div>

          {data.issues.length > 0 && (
            <div className="space-y-2">
              <p className="font-semibold text-amber-300">취득원가 누락 / 중복 검토 항목</p>
              {data.issues.map((iss) => (
                <div key={iss.id} className={subCard}>
                  <span className="font-mono text-amber-300">[{iss.issue_type}]</span> {iss.description}
                </div>
              ))}
            </div>
          )}

          <div className={`${subCard} space-y-2`}>
            <p className="font-semibold">과거 보유 자산 기초 원가 입력 (취득원가 누락 해결)</p>
            <div className="grid gap-2 sm:grid-cols-4">
              <input className={inputCls} placeholder="종목 (예: KRW-BTC)" value={baseMarket} onChange={(e) => setBaseMarket(e.target.value)} />
              <input className={inputCls} placeholder="기초 수량" value={baseQty} onChange={(e) => setBaseQty(e.target.value)} />
              <input className={inputCls} placeholder="평균 매수가 (KRW)" value={basePrice} onChange={(e) => setBasePrice(e.target.value)} />
              <button
                type="button"
                onClick={saveBaseline}
                className="rounded-lg bg-zinc-100 px-3 py-1.5 font-semibold text-zinc-950 hover:bg-white"
              >
                기초 원가 저장·재계산
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 9. 거래내역 · 동기화 · CSV/XLSX 업로드 */}
      {sub === 'trades' && (
        <div className="space-y-4">
          <div className={`${card} flex flex-wrap items-center justify-between gap-3 p-5 text-xs`}>
            <div>
              <p className="font-semibold">
                동기화 상태: {sync?.api_connected ? 'API 연결됨' : 'API 미연결 (CSV/XLSX 업로드 가능)'} · 누적 체결{' '}
                {sync?.total_exchange_fills ?? 0}건
              </p>
              <p className="mt-0.5 text-zinc-400">
                마지막 성공: {sync?.last_success_at ? kst(sync.last_success_at) : '아직 없음'}
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <label className="cursor-pointer rounded-lg border border-[#2A344B] bg-[#181E2B] px-3 py-1.5 font-medium text-zinc-200 hover:border-zinc-400">
                CSV / XLSX 업로드
                <input
                  type="file"
                  accept=".csv,.xlsx"
                  className="hidden"
                  onChange={(e) => handleImport(e.target.files?.[0] ?? null)}
                />
              </label>
              <button
                type="button"
                onClick={triggerRecalc}
                className="rounded-lg border border-[#2A344B] bg-[#181E2B] px-3 py-1.5 font-medium text-zinc-200 hover:border-zinc-400"
              >
                원장 재계산
              </button>
            </div>
          </div>

          <div className={`${card} overflow-x-auto p-5 text-xs`}>
            <h3 className="mb-3 text-sm font-semibold">실현 손익 배분 원장 ({data?.trades.length ?? 0}건)</h3>
            <table className="w-full min-w-[640px] text-left font-mono tabular-nums">
              <thead className="border-b border-[#1E2536] text-[11px] text-zinc-400">
                <tr>
                  <th className="py-2">청산 시각</th>
                  <th className="py-2">종목</th>
                  <th className="py-2">출처</th>
                  <th className="py-2">수량</th>
                  <th className="py-2">매수원가</th>
                  <th className="py-2">매도정산</th>
                  <th className="py-2">실현손익</th>
                  <th className="py-2">보유</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#1E2536]/60">
                {(data?.trades ?? []).map((t) => (
                  <tr key={t.id}>
                    <td className="py-2">{kst(t.closed_at)}</td>
                    <td className="py-2">{t.market.replace('KRW-', '')}</td>
                    <td className="py-2">{t.execution_origin}</td>
                    <td className="py-2">{quantityText(t.allocated_quantity)}</td>
                    <td className="py-2">{priceText(t.allocated_cost)}원</td>
                    <td className="py-2">{priceText(t.allocated_proceeds)}원</td>
                    <td className={`py-2 ${pnlColor(t.realized_pnl)}`}>{signedMoney(t.realized_pnl)}</td>
                    <td className="py-2">{formatHold(t.holding_seconds)}</td>
                  </tr>
                ))}
                {!(data?.trades ?? []).length && (
                  <tr>
                    <td colSpan={8} className="py-6 text-center text-zinc-500">
                      조회된 거래가 없습니다. 상단「빗썸 지금 동기화」또는 CSV/XLSX 업로드를 이용하세요.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 10. AI 분석 리포트 브리지 */}
      {sub === 'ai' && <AiStrategyReport />}
    </section>
  );
}
