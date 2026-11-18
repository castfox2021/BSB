'use client';
import { ReactNode, useEffect, useState } from 'react';
import { kst } from '../../trading-types';
import { formatKrw, krPnlColor, krPnlPrefix, TOKENS, Tone, TONE_STYLES } from '../design-tokens';
import { UnifiedAdminStatus } from '../runtime-status';

const TONE_ICONS: Record<Tone, string> = {
  ok: '●',
  warn: '▲',
  danger: '■',
  profit: '▲',
  loss: '▼',
  buy: '↗',
  sell: '↘',
  neutral: '○',
};

export function StatusBadge({
  tone = 'neutral',
  label,
  icon,
  title,
}: {
  tone?: Tone;
  label: string;
  icon?: string;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 font-mono text-[11px] font-medium ${TONE_STYLES[tone]}`}
    >
      <span aria-hidden="true" className="text-[9px]">
        {icon ?? TONE_ICONS[tone]}
      </span>
      <span>{label}</span>
    </span>
  );
}

export function FreshnessIndicator({
  freshCount,
  watchCount,
  connected,
}: {
  freshCount: number;
  watchCount: number;
  connected: boolean;
}) {
  const ok = connected && (watchCount === 0 || freshCount >= watchCount * 0.5);
  const label = !connected
    ? '시세 연결 중'
    : watchCount > 0
      ? `시세 신선 ${freshCount}/${watchCount}`
      : '시세 대기';
  return <StatusBadge tone={!connected ? 'warn' : ok ? 'ok' : 'danger'} label={label} />;
}

export function AdminStatusBar({ status }: { status: UnifiedAdminStatus }) {
  if (status.phase === 'loading') {
    return (
      <div
        role="status"
        aria-live="polite"
        className="border-b border-[#1E2536] bg-[#0E1119] py-2 text-xs text-zinc-400"
      >
        <div className={`${TOKENS.container} flex flex-wrap items-center gap-2`}>
          <StatusBadge tone="neutral" label="운영 상태 확인 중" />
          <span className="text-zinc-500">실전 주문·관제·시세 상태를 조회하고 있습니다.</span>
        </div>
      </div>
    );
  }

  if (status.phase === 'error') {
    return (
      <div
        role="alert"
        className="border-b border-red-500/40 bg-red-950/30 py-2 text-xs text-red-200"
      >
        <div className={`${TOKENS.container} flex flex-wrap items-center gap-2`}>
          <StatusBadge tone="danger" label={status.headline} />
          {status.actionMessage && <span>{status.actionMessage}</span>}
        </div>
      </div>
    );
  }

  return (
    <div
      role="status"
      aria-label="전역 운영 상태 표시줄"
      className="border-b border-[#1E2536] bg-[#0E1119] py-2"
    >
      <div className={`${TOKENS.container} flex flex-wrap items-center gap-2`}>
        <StatusBadge tone={status.headlineTone} label={status.headline} />
        <StatusBadge
          tone={status.autoBuyOnly ? (status.orderAllowed ? 'ok' : 'warn') : 'neutral'}
          label={status.autoBuyOnly ? '자동매수 ON' : '자동매수 OFF'}
        />
        <StatusBadge
          tone={status.controlState === 'RUNNING' ? 'ok' : status.controlState === 'EMERGENCY_STOP' ? 'danger' : 'warn'}
          label={`관제 ${status.controlState}`}
        />
        <FreshnessIndicator
          freshCount={status.freshCount}
          watchCount={status.watchCount}
          connected={status.marketConnected}
        />
        <StatusBadge
          tone={status.privateConnected ? 'ok' : 'warn'}
          label={status.privateConnected ? 'Private API 연결' : 'Private API 미연결'}
        />
        <span className="font-mono text-[11px] tabular-nums text-zinc-500 sm:ml-auto">
          마지막 분석 {status.lastAnalysisAt ? kst(status.lastAnalysisAt) : '—'}
        </span>
      </div>
    </div>
  );
}

export function MetricCard({
  label,
  value,
  subtext,
  tone = 'neutral',
  valueClassName,
  tooltip,
}: {
  label: string;
  value: string;
  subtext?: string;
  tone?: Tone;
  valueClassName?: string;
  tooltip?: string;
}) {
  return (
    <div title={tooltip} className={`${TOKENS.card} p-4`}>
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-medium text-zinc-400">{label}</p>
        {tooltip && (
          <span aria-label={tooltip} title={tooltip} className="cursor-help text-[11px] text-zinc-500">
            ⓘ
          </span>
        )}
      </div>
      <p
        className={`mt-2 font-mono text-xl font-semibold tabular-nums ${
          valueClassName ?? (tone === 'profit' ? 'text-rose-400' : tone === 'loss' ? 'text-sky-400' : 'text-zinc-100')
        }`}
      >
        {value}
      </p>
      {subtext && <p className="mt-1 font-mono text-[11px] text-zinc-500">{subtext}</p>}
    </div>
  );
}

export function RiskBanner({
  tone = 'warn',
  title,
  message,
  action,
}: {
  tone?: 'warn' | 'danger' | 'ok';
  title: string;
  message: string;
  action?: ReactNode;
}) {
  return (
    <div
      role="alert"
      className={`flex flex-wrap items-center justify-between gap-3 rounded-xl border px-4 py-3 text-xs ${TONE_STYLES[tone]}`}
    >
      <div className="flex items-start gap-2.5">
        <span aria-hidden="true" className="mt-0.5 font-mono text-xs">
          {TONE_ICONS[tone]}
        </span>
        <div>
          <p className="font-semibold">{title}</p>
          <p className="mt-0.5 opacity-90">{message}</p>
        </div>
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}

export function SectionHeader({
  title,
  subtitle,
  tooltip,
  actions,
}: {
  title: string;
  subtitle?: string;
  tooltip?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3 border-b border-[#1E2536] pb-3">
      <div>
        <div className="flex items-center gap-1.5">
          <h2 className="text-sm font-semibold text-zinc-100 sm:text-base">{title}</h2>
          {tooltip && (
            <span title={tooltip} aria-label={tooltip} className="cursor-help text-xs text-zinc-500">
              ⓘ
            </span>
          )}
        </div>
        {subtitle && <p className="mt-0.5 text-xs text-zinc-400">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function EmptyState({ message = '조건을 충족한 데이터가 없습니다' }: { message?: string }) {
  return (
    <div className={`${TOKENS.subCard} px-4 py-8 text-center text-xs text-zinc-500`}>
      {message}
    </div>
  );
}

export function Pagination({
  page,
  totalPages,
  totalItems,
  onPageChange,
}: {
  page: number;
  totalPages: number;
  totalItems?: number;
  onPageChange: (next: number) => void;
}) {
  const safeTotal = Math.max(1, totalPages);
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 font-mono text-xs text-zinc-400">
      <span>
        {page} / {safeTotal} 페이지{totalItems != null ? ` · 총 ${totalItems.toLocaleString('ko-KR')}건` : ''}
      </span>
      <div className="flex items-center gap-1.5">
        <button
          type="button"
          disabled={page <= 1}
          aria-label="이전 페이지"
          onClick={() => onPageChange(page - 1)}
          className="rounded-lg border border-[#1E2536] bg-[#0E1119] px-3 py-1.5 text-zinc-200 hover:border-zinc-500 disabled:opacity-40"
        >
          이전
        </button>
        <button
          type="button"
          disabled={page >= safeTotal}
          aria-label="다음 페이지"
          onClick={() => onPageChange(page + 1)}
          className="rounded-lg border border-[#1E2536] bg-[#0E1119] px-3 py-1.5 text-zinc-200 hover:border-zinc-500 disabled:opacity-40"
        >
          다음
        </button>
      </div>
    </div>
  );
}

export type PositionRow = {
  id: string;
  name: string;
  symbol: string;
  avgPrice: number;
  currentPrice: number;
  pnlPct: number | null;
  pnlKrw: number | null;
  protection: string;
  protectionTone?: Tone;
};

export function PositionTable({
  rows,
  emptyMessage,
  onSelect,
}: {
  rows: PositionRow[];
  emptyMessage?: string;
  onSelect?: (row: PositionRow) => void;
}) {
  if (rows.length === 0) return <EmptyState message={emptyMessage} />;
  return (
    <>
      <div className="hidden overflow-x-auto rounded-lg border border-[#1E2536] bg-[#0E1119] md:block">
        <table className="w-full text-left font-mono text-xs">
          <thead className="border-b border-[#1E2536] text-[11px] text-zinc-400">
            <tr>
              <th scope="col" className="px-3 py-2.5">종목</th>
              <th scope="col" className="px-3 py-2.5 text-right">평균 매수가</th>
              <th scope="col" className="px-3 py-2.5 text-right">현재가</th>
              <th scope="col" className="px-3 py-2.5 text-right">수익률</th>
              <th scope="col" className="px-3 py-2.5 text-right">평가손익</th>
              <th scope="col" className="px-3 py-2.5 text-right">보호 상태</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[#1E2536]/60">
            {rows.map((r) => (
              <tr
                key={r.id}
                onClick={() => onSelect?.(r)}
                className={onSelect ? 'cursor-pointer transition hover:bg-[#151B28]' : ''}
              >
                <td className="px-3 py-2.5 font-sans font-medium text-zinc-100">
                  {r.name} <span className="font-mono text-[11px] text-zinc-500">({r.symbol.replace('KRW-', '')})</span>
                </td>
                <td className="px-3 py-2.5 text-right tabular-nums text-zinc-300">
                  {r.avgPrice > 0 ? `${Math.round(r.avgPrice).toLocaleString('ko-KR')}원` : '—'}
                </td>
                <td className="px-3 py-2.5 text-right tabular-nums text-zinc-200">
                  {r.currentPrice > 0 ? `${Math.round(r.currentPrice).toLocaleString('ko-KR')}원` : '—'}
                </td>
                <td className={`px-3 py-2.5 text-right font-semibold tabular-nums ${krPnlColor(r.pnlPct)}`}>
                  {r.pnlPct != null ? `${krPnlPrefix(r.pnlPct)}${Math.abs(r.pnlPct).toFixed(2)}%` : '—'}
                </td>
                <td className={`px-3 py-2.5 text-right font-semibold tabular-nums ${krPnlColor(r.pnlKrw)}`}>
                  {r.pnlKrw != null ? formatKrw(r.pnlKrw, true) : '—'}
                </td>
                <td className="px-3 py-2.5 text-right">
                  <StatusBadge tone={r.protectionTone ?? 'neutral'} label={r.protection} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {/* Mobile card list */}
      <div className="space-y-2 md:hidden">
        {rows.map((r) => (
          <button
            key={r.id}
            type="button"
            onClick={() => onSelect?.(r)}
            className={`${TOKENS.subCard} w-full p-3 text-left text-xs`}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="font-semibold text-zinc-100">
                {r.name} <span className="font-mono text-[11px] text-zinc-500">({r.symbol.replace('KRW-', '')})</span>
              </span>
              <StatusBadge tone={r.protectionTone ?? 'neutral'} label={r.protection} />
            </div>
            <div className="mt-2 grid grid-cols-2 gap-1 font-mono text-[11px] tabular-nums text-zinc-400">
              <span>평균 {r.avgPrice > 0 ? `${Math.round(r.avgPrice).toLocaleString('ko-KR')}원` : '—'}</span>
              <span className="text-right">현재 {r.currentPrice > 0 ? `${Math.round(r.currentPrice).toLocaleString('ko-KR')}원` : '—'}</span>
              <span className={krPnlColor(r.pnlPct)}>
                {r.pnlPct != null ? `${krPnlPrefix(r.pnlPct)}${Math.abs(r.pnlPct).toFixed(2)}%` : '—'}
              </span>
              <span className={`text-right font-semibold ${krPnlColor(r.pnlKrw)}`}>
                {r.pnlKrw != null ? formatKrw(r.pnlKrw, true) : '—'}
              </span>
            </div>
          </button>
        ))}
      </div>
    </>
  );
}

export type TradeRow = {
  id: string;
  symbol: string;
  side: 'BUY' | 'SELL';
  price: number;
  pnlKrw?: number | null;
  time: string;
  tag?: string;
};

export function TradeTable({ rows, emptyMessage }: { rows: TradeRow[]; emptyMessage?: string }) {
  if (rows.length === 0) return <EmptyState message={emptyMessage} />;
  return (
    <div className="overflow-x-auto rounded-lg border border-[#1E2536] bg-[#0E1119]">
      <table className="w-full text-left font-mono text-xs">
        <thead className="border-b border-[#1E2536] text-[11px] text-zinc-400">
          <tr>
            <th scope="col" className="px-3 py-2">종목</th>
            <th scope="col" className="px-3 py-2">구분</th>
            <th scope="col" className="px-3 py-2 text-right">체결가</th>
            <th scope="col" className="px-3 py-2 text-right">손익</th>
            <th scope="col" className="px-3 py-2 text-right">시각</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[#1E2536]/60">
          {rows.map((t) => (
            <tr key={t.id}>
              <td className="px-3 py-2 text-zinc-200">{t.symbol.replace('KRW-', '')}</td>
              <td className="px-3 py-2">
                <StatusBadge tone={t.side === 'BUY' ? 'buy' : 'sell'} label={t.side === 'BUY' ? '매수' : '매도'} />
              </td>
              <td className="px-3 py-2 text-right tabular-nums text-zinc-300">
                {t.price > 0 ? `${Math.round(t.price).toLocaleString('ko-KR')}원` : '—'}
              </td>
              <td className={`px-3 py-2 text-right font-semibold tabular-nums ${krPnlColor(t.pnlKrw)}`}>
                {t.pnlKrw != null ? formatKrw(t.pnlKrw, true) : '—'}
              </td>
              <td className="px-3 py-2 text-right tabular-nums text-zinc-500">{kst(t.time)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function DetailDrawer({
  open,
  title,
  subtitle,
  onClose,
  children,
}: {
  open: boolean;
  title: string;
  subtitle?: string;
  onClose: () => void;
  children: ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title}
      className="fixed inset-0 z-50 flex justify-end bg-black/60"
      onClick={onClose}
    >
      <div
        className="h-full w-full max-w-md overflow-y-auto border-l border-[#1E2536] bg-[#121620] p-6 text-xs text-zinc-200"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3 border-b border-[#1E2536] pb-3">
          <div>
            <h3 className="text-base font-semibold text-zinc-100">{title}</h3>
            {subtitle && <p className="mt-0.5 font-mono text-[11px] text-zinc-400">{subtitle}</p>}
          </div>
          <button
            type="button"
            aria-label="상세 패널 닫기"
            onClick={onClose}
            className="rounded-lg border border-[#1E2536] bg-[#0E1119] px-2.5 py-1 text-zinc-300 hover:border-zinc-500"
          >
            닫기
          </button>
        </div>
        <div className="mt-4 space-y-4">{children}</div>
      </div>
    </div>
  );
}

export function ConfirmActionDialog({
  open,
  title,
  description,
  confirmPhrase,
  confirmLabel = '확인 실행',
  tone = 'warn',
  busy = false,
  onCancel,
  onConfirm,
  children,
}: {
  open: boolean;
  title: string;
  description: string;
  confirmPhrase?: string;
  confirmLabel?: string;
  tone?: 'warn' | 'danger' | 'ok';
  busy?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
  children?: ReactNode;
}) {
  const [typed, setTyped] = useState('');

  useEffect(() => {
    if (!open) setTyped('');
  }, [open]);

  if (!open) return null;
  const phraseOk = !confirmPhrase || typed.trim() === confirmPhrase;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      onClick={onCancel}
    >
      <div
        className="w-full max-w-lg rounded-xl border border-[#1E2536] bg-[#121620] p-6 text-xs text-zinc-200"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-base font-semibold text-zinc-100">{title}</h3>
        <p className="mt-1.5 text-zinc-400">{description}</p>
        {children && <div className="mt-4">{children}</div>}
        {confirmPhrase && (
          <div className="mt-4">
            <label className="block text-[11px] text-zinc-400">
              계속하려면 확인 문구 <strong className="font-mono text-zinc-200">{confirmPhrase}</strong> 를 입력하세요.
              <input
                value={typed}
                onChange={(e) => setTyped(e.target.value)}
                placeholder={confirmPhrase}
                className="mt-1.5 w-full rounded-lg border border-[#1E2536] bg-[#0E1119] px-3 py-2 font-mono text-zinc-100"
              />
            </label>
          </div>
        )}
        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-lg border border-[#1E2536] bg-[#0E1119] px-3.5 py-2 text-zinc-300 hover:border-zinc-500"
          >
            취소
          </button>
          <button
            type="button"
            disabled={busy || !phraseOk}
            onClick={onConfirm}
            className={`rounded-lg border px-4 py-2 font-semibold disabled:opacity-40 ${TONE_STYLES[tone]}`}
          >
            {busy ? '처리 중...' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
