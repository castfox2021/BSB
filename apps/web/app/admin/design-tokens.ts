/**
 * Task 21 — Shared Design Tokens for BSB Admin Console
 * Strictly limited to 3 surface levels, max 3 accent colors per view,
 * and Korean market convention (rise/profit = red, fall/loss = blue).
 */

export const TOKENS = {
  bg: 'bg-[#0B0E14]',
  surface: 'bg-[#121620]',
  elevated: 'bg-[#0E1119]',
  border: 'border-[#1E2536]',
  container: 'mx-auto max-w-[1440px] px-4 sm:px-6',
  card: 'rounded-xl border border-[#1E2536] bg-[#121620]',
  subCard: 'rounded-lg border border-[#1E2536] bg-[#0E1119]',
  num: 'font-mono tabular-nums text-right',
  mono: 'font-mono tabular-nums',
} as const;

export type Tone = 'ok' | 'warn' | 'danger' | 'profit' | 'loss' | 'buy' | 'sell' | 'neutral';

export const TONE_STYLES: Record<Tone, string> = {
  ok: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300',
  warn: 'border-amber-500/40 bg-amber-500/10 text-amber-300',
  danger: 'border-red-500/50 bg-red-500/15 text-red-300',
  profit: 'border-rose-500/40 bg-rose-500/10 text-rose-300',
  loss: 'border-sky-500/40 bg-sky-500/10 text-sky-300',
  buy: 'border-emerald-500/40 bg-emerald-500/15 text-emerald-300',
  sell: 'border-rose-500/40 bg-rose-500/15 text-rose-300',
  neutral: 'border-[#1E2536] bg-[#0E1119] text-zinc-300',
};

/** Korean trading color: positive/rise = red (rose), negative/fall = blue (sky). */
export function krPnlColor(v: number | string | null | undefined): string {
  const n = Number(v ?? 0);
  if (!Number.isFinite(n) || n === 0) return 'text-zinc-300';
  return n > 0 ? 'text-rose-400' : 'text-sky-400';
}

export function krPnlPrefix(v: number | string | null | undefined): string {
  const n = Number(v ?? 0);
  if (!Number.isFinite(n) || n === 0) return '';
  return n > 0 ? '▲ +' : '▼ ';
}

export function formatKrw(v: number | string | null | undefined, signed = false): string {
  const n = Number(v ?? 0);
  if (!Number.isFinite(n)) return '—';
  const rounded = Math.round(n);
  const abs = Math.abs(rounded).toLocaleString('ko-KR');
  if (!signed) return `${rounded.toLocaleString('ko-KR')}원`;
  if (rounded > 0) return `+${abs}원`;
  if (rounded < 0) return `-${abs}원`;
  return '0원';
}
