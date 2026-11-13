'use client';
import { useEffect, useState } from 'react';
import { api } from './trading-types';
import { pnlColor, signedMoney } from './quiet-types';

type ReportSummary = {
  mode: string;
  trade_count: number;
  open_count: number;
  wins: number;
  losses: number;
  breakeven: number;
  win_rate: string | null;
  gross_pnl_krw: string;
  fees_krw: string;
  net_pnl_krw: string;
  expectancy_krw: string | null;
  profit_factor: string | null;
  max_drawdown_pct: string;
  avg_holding_seconds: number | null;
  max_consecutive_losses: number;
  exit_reason_counts: Record<string, number>;
  by_symbol: { symbol: string; trades: number; net_pnl_krw: string }[];
  equity_curve: { timestamp_utc: string; total_equity_krw: string; drawdown_pct: string }[];
};

type DataQuality = {
  mode: string;
  closed_trades: number;
  open_positions_excluded_from_pnl: number;
  signals_count: number;
  orders_count: number;
  candles_rows: number;
  missing_candles_count: number;
  csv_encoding: string;
  csv_injection_escaped: boolean;
  sensitive_ids_hashed: boolean;
  warnings: string[];
};

type ReportItem = {
  report_id: string;
  status: string;
  mode: string;
  file_size_bytes: number;
  created_at: string;
  expires_at: string;
  summary: ReportSummary;
};

type ComparisonPolicy = {
  policy: string;
  trades: number;
  realized: string;
  max_drawdown: string;
  win_rate: string | null;
  profit_factor: string | null;
  avg_hold_seconds: number | null;
};

const inputCls =
  'w-full rounded-lg border border-[#1E2536] bg-[#0B0E14] px-3 py-2 text-xs font-mono text-zinc-100 focus:border-zinc-500 focus:outline-none';
const btnCls =
  'rounded-lg border border-[#2A344B] bg-[#181E2B] px-3.5 py-2 text-xs font-medium text-zinc-100 hover:border-zinc-400 disabled:opacity-40 transition';

const policyLabel: Record<string, string> = {
  structure_trailing: 'Task 16 구조+트레일링',
  fixed_trailing: '고정손절+트레일링',
  atr_trailing: 'ATR+트레일링',
  structure_fixed_take: '기존 고정 +2.5% 익절',
  partial_tp_trailing: 'Task 17 분할 익절+트레일링',
};

export default function AiStrategyReport() {
  const [startKst, setStartKst] = useState('');
  const [endKst, setEndKst] = useState('');
  const [mode, setMode] = useState<'PAPER' | 'LIVE'>('PAPER');
  const [version, setVersion] = useState('ALL');
  const [symbol, setSymbol] = useState('ALL');
  const [status, setStatus] = useState('ALL');

  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [preview, setPreview] = useState<{ summary: ReportSummary; data_quality: DataQuality } | null>(null);
  const [showQuality, setShowQuality] = useState(false);
  const [reports, setReports] = useState<ReportItem[]>([]);
  const [comparison, setComparison] = useState<ComparisonPolicy[] | null>(null);

  const filters = () => ({
    start_kst: startKst || null,
    end_kst: endKst || null,
    mode,
    strategy_version: version,
    symbol: symbol.trim().toUpperCase() || 'ALL',
    status,
  });

  async function loadReports() {
    try {
      const res = await api<{ items: ReportItem[] }>('admin/strategy-reports?limit=10');
      setReports(res.items ?? []);
    } catch {
      /* ignore initial list error */
    }
  }

  useEffect(() => {
    loadReports();
    runPreview();
  }, []);

  async function runPreview() {
    setBusy(true);
    setError('');
    try {
      const res = await api<{ summary: ReportSummary; data_quality: DataQuality }>(
        'admin/strategy-reports/preview',
        'POST',
        filters()
      );
      setPreview(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : '미리보기 조회 실패');
    } finally {
      setBusy(false);
    }
  }

  async function createAndDownload() {
    setBusy(true);
    setError('');
    setMessage('');
    try {
      const res = await api<ReportItem>('admin/strategy-reports', 'POST', filters());
      setMessage(`리포트 생성 완료 (${res.report_id.slice(0, 12)}) · 다운로드를 시작합니다.`);
      await loadReports();
      const a = document.createElement('a');
      a.href = `/api/manual/admin/strategy-reports/${res.report_id}/download`;
      a.download = `bsb-strategy-report-${res.report_id.slice(0, 12)}.zip`;
      document.body.appendChild(a);
      a.click();
      a.remove();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'ZIP 생성 실패');
    } finally {
      setBusy(false);
    }
  }

  async function runComparison() {
    setBusy(true);
    setError('');
    try {
      const res = await api<{ policies: ComparisonPolicy[] }>('admin/strategy-comparisons', 'POST', filters());
      setComparison(res.policies ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : '전략 비교 조회 실패');
    } finally {
      setBusy(false);
    }
  }

  const s = preview?.summary;
  const dq = preview?.data_quality;

  return (
    <section className="rounded-xl border border-[#1E2536] bg-[#121620] p-6">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#1E2536]/80 pb-4">
        <div>
          <h2 className="text-base font-semibold text-zinc-100">AI 전략 분석 리포트 브리지 (Task 18)</h2>
          <p className="mt-0.5 text-xs text-zinc-400">
            읽기 전용 분석 패키지를 생성해 ChatGPT에 전달합니다. API 키·개인정보는 제외되며 가상/실전 손익은 절대 혼합하지 않습니다.
          </p>
        </div>
        <span className="rounded-md border border-[#2A344B] bg-[#0E1119] px-2.5 py-1 font-mono text-[11px] text-zinc-300">
          bithumb-ai-report/1.0
        </span>
      </div>

      {/* 필터 */}
      <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <label className="text-[11px] text-zinc-400">
          시작일 (KST)
          <input type="date" className={`${inputCls} mt-1`} value={startKst} onChange={(e) => setStartKst(e.target.value)} />
        </label>
        <label className="text-[11px] text-zinc-400">
          종료일 (KST)
          <input type="date" className={`${inputCls} mt-1`} value={endKst} onChange={(e) => setEndKst(e.target.value)} />
        </label>
        <label className="text-[11px] text-zinc-400">
          실행 모드
          <select className={`${inputCls} mt-1`} value={mode} onChange={(e) => setMode(e.target.value as 'PAPER' | 'LIVE')}>
            <option value="PAPER">PAPER (가상매매)</option>
            <option value="LIVE">LIVE (실전매매)</option>
          </select>
        </label>
        <label className="text-[11px] text-zinc-400">
          전략 버전
          <select className={`${inputCls} mt-1`} value={version} onChange={(e) => setVersion(e.target.value)}>
            <option value="ALL">전체 버전</option>
            <option value="partial_tp_trailing">Task 17 분할 익절+트레일링</option>
            <option value="structure_trailing">Task 16 구조+트레일링</option>
            <option value="structure_fixed_take">기존 고정 +2.5% 익절</option>
            <option value="fixed_trailing">고정손절+트레일링</option>
            <option value="atr_trailing">ATR+트레일링</option>
          </select>
        </label>
        <label className="text-[11px] text-zinc-400">
          종목 (ALL 또는 KRW-...)
          <input className={`${inputCls} mt-1`} value={symbol} onChange={(e) => setSymbol(e.target.value)} placeholder="ALL" />
        </label>
        <label className="text-[11px] text-zinc-400">
          거래 상태
          <select className={`${inputCls} mt-1`} value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="ALL">전체</option>
            <option value="CLOSED">종료 (CLOSED)</option>
            <option value="OPEN">진행 중 (OPEN)</option>
          </select>
        </label>
      </div>

      {/* 액션 버튼 */}
      <div className="mt-4 flex flex-wrap items-center gap-2.5">
        <button type="button" disabled={busy} className={btnCls} onClick={runPreview}>
          미리보기
        </button>
        <button
          type="button"
          disabled={busy}
          className="rounded-lg border border-emerald-500/40 bg-emerald-500/15 px-4 py-2 text-xs font-semibold text-emerald-200 hover:bg-emerald-500/25 disabled:opacity-40 transition"
          onClick={createAndDownload}
        >
          분석 ZIP 다운로드
        </button>
        <button type="button" disabled={busy} className={btnCls} onClick={runComparison}>
          전략 버전 비교
        </button>
        <button type="button" className={btnCls} onClick={() => setShowQuality((v) => !v)}>
          데이터 품질 확인
        </button>
        {message && <span className="text-xs font-medium text-emerald-400">{message}</span>}
        {error && <span className="text-xs font-medium text-rose-400">{error}</span>}
      </div>

      {/* 요약 KPI */}
      {s && (
        <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
          <div className="rounded-lg border border-[#1E2536] bg-[#0E1119] p-3">
            <p className="text-[11px] text-zinc-400">종료 거래 ({s.mode})</p>
            <p className="mt-1 font-mono text-base font-semibold tabular-nums text-zinc-100">
              {s.trade_count}건 <span className="text-[11px] font-normal text-zinc-500">({s.wins}승 {s.losses}패)</span>
            </p>
          </div>
          <div className="rounded-lg border border-[#1E2536] bg-[#0E1119] p-3">
            <p className="text-[11px] text-zinc-400">승률</p>
            <p className="mt-1 font-mono text-base font-semibold tabular-nums text-zinc-100">
              {s.win_rate !== null ? `${s.win_rate}%` : '—'}
            </p>
          </div>
          <div className="rounded-lg border border-[#1E2536] bg-[#0E1119] p-3">
            <p className="text-[11px] text-zinc-400">순손익 (수수료 차감)</p>
            <p className={`mt-1 font-mono text-base font-semibold tabular-nums ${pnlColor(s.net_pnl_krw)}`}>
              {signedMoney(s.net_pnl_krw)}
            </p>
          </div>
          <div className="rounded-lg border border-[#1E2536] bg-[#0E1119] p-3">
            <p className="text-[11px] text-zinc-400">Profit Factor</p>
            <p className="mt-1 font-mono text-base font-semibold tabular-nums text-zinc-100">
              {s.profit_factor ?? '—'}
            </p>
          </div>
          <div className="rounded-lg border border-[#1E2536] bg-[#0E1119] p-3">
            <p className="text-[11px] text-zinc-400">최대 낙폭 (MDD)</p>
            <p className="mt-1 font-mono text-base font-semibold tabular-nums text-sky-400">
              -{Number(s.max_drawdown_pct).toFixed(2)}%
            </p>
          </div>
          <div className="rounded-lg border border-[#1E2536] bg-[#0E1119] p-3">
            <p className="text-[11px] text-zinc-400">최대 연속 손실</p>
            <p className="mt-1 font-mono text-base font-semibold tabular-nums text-zinc-200">
              {s.max_consecutive_losses}회
            </p>
          </div>
        </div>
      )}

      {/* 청산 사유 및 종목별 분포 */}
      {s && (Object.keys(s.exit_reason_counts).length > 0 || s.by_symbol.length > 0) && (
        <div className="mt-4 grid gap-4 md:grid-cols-2">
          <div className="rounded-lg border border-[#1E2536] bg-[#0E1119] p-4 text-xs">
            <p className="font-semibold text-zinc-300">청산 사유 분포</p>
            <div className="mt-2.5 space-y-1.5 font-mono tabular-nums text-zinc-400">
              {Object.entries(s.exit_reason_counts).map(([reason, count]) => (
                <div key={reason} className="flex items-center justify-between">
                  <span>{reason}</span>
                  <span className="text-zinc-200">{count}건</span>
                </div>
              ))}
            </div>
          </div>
          <div className="rounded-lg border border-[#1E2536] bg-[#0E1119] p-4 text-xs">
            <p className="font-semibold text-zinc-300">상위 종목별 손익</p>
            <div className="mt-2.5 space-y-1.5 font-mono tabular-nums">
              {s.by_symbol.slice(0, 6).map((b) => (
                <div key={b.symbol} className="flex items-center justify-between">
                  <span className="text-zinc-300">
                    {b.symbol.replace('KRW-', '')} ({b.trades}건)
                  </span>
                  <span className={pnlColor(b.net_pnl_krw)}>{signedMoney(b.net_pnl_krw)}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* 데이터 품질 */}
      {showQuality && dq && (
        <div className="mt-4 rounded-lg border border-[#1E2536] bg-[#0B0E14] p-4 font-mono text-[11px] text-zinc-300">
          <p className="font-sans text-xs font-semibold text-zinc-200">데이터 품질 및 보안 점검</p>
          <p className="mt-2">
            종료 거래 {dq.closed_trades}건 · 손익 제외 미종료 {dq.open_positions_excluded_from_pnl}건 · 시그널 {dq.signals_count}건 · 주문 {dq.orders_count}건 · 캔들 {dq.candles_rows}행 (결측 {dq.missing_candles_count}건)
          </p>
          <p className="mt-1 text-emerald-400">
            인코딩: {dq.csv_encoding} · CSV 수식 방어: {dq.csv_injection_escaped ? 'ON' : 'OFF'} · 민감 ID 해시화: {dq.sensitive_ids_hashed ? 'ON' : 'OFF'}
          </p>
          {dq.warnings.length > 0 && (
            <p className="mt-1 text-amber-300">경고: {dq.warnings.join(', ')}</p>
          )}
        </div>
      )}

      {/* P1 전략 비교 */}
      {comparison && (
        <div className="mt-4 overflow-x-auto rounded-lg border border-[#1E2536] bg-[#0E1119] p-4">
          <p className="mb-2 text-xs font-semibold text-zinc-200">동일 진입 신호 전략별 비교</p>
          <table className="w-full min-w-[540px] text-left font-mono text-xs tabular-nums">
            <thead className="border-b border-[#1E2536] text-[11px] text-zinc-400">
              <tr>
                <th className="py-2">정책</th>
                <th className="py-2">거래</th>
                <th className="py-2">승률</th>
                <th className="py-2">순손익</th>
                <th className="py-2">PF</th>
                <th className="py-2">MDD</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#1E2536]/60 text-zinc-300">
              {comparison.map((c) => (
                <tr key={c.policy}>
                  <td className="py-2 font-sans">{policyLabel[c.policy] ?? c.policy}</td>
                  <td className="py-2">{c.trades}건</td>
                  <td className="py-2">{c.win_rate ? `${(Number(c.win_rate) * 100).toFixed(1)}%` : '—'}</td>
                  <td className={`py-2 ${pnlColor(c.realized)}`}>{signedMoney(c.realized)}</td>
                  <td className="py-2">{c.profit_factor ?? '—'}</td>
                  <td className="py-2 text-sky-400">-{(Number(c.max_drawdown) * 100).toFixed(2)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* 생성된 ZIP 이력 */}
      {reports.length > 0 && (
        <div className="mt-5 border-t border-[#1E2536]/80 pt-4">
          <p className="text-xs font-semibold text-zinc-300">최근 생성된 분석 리포트 (7일 보관)</p>
          <div className="mt-2 space-y-2">
            {reports.map((r) => (
              <div
                key={r.report_id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-[#1E2536] bg-[#0E1119] px-3.5 py-2.5 text-xs"
              >
                <div className="flex flex-wrap items-center gap-2 font-mono text-[11px] text-zinc-300">
                  <span className="rounded bg-[#181E2B] px-1.5 py-0.5 text-zinc-200">{r.mode}</span>
                  <span>{r.report_id.slice(0, 12)}</span>
                  <span className="text-zinc-500">· {r.summary?.trade_count ?? 0}건</span>
                  <span className="text-zinc-500">· {(r.file_size_bytes / 1024).toFixed(1)} KB</span>
                </div>
                <a
                  href={`/api/manual/admin/strategy-reports/${r.report_id}/download`}
                  download={`bsb-strategy-report-${r.report_id.slice(0, 12)}.zip`}
                  className="rounded-md border border-[#2A344B] bg-[#181E2B] px-2.5 py-1 text-[11px] font-medium text-zinc-200 hover:border-zinc-400"
                >
                  ZIP 다운로드
                </a>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
