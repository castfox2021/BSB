'use client';
import { FormEvent, useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { PublicHeader } from '../components/public-nav';

type Cred = { id: string; exchange: string; label: string; key_hint: string; created_at: string };
type MemberBalance = {
  currency: string;
  balance: string;
  locked: string;
  average_price?: string;
  unit_currency?: string;
};
type MemberTrade = {
  id: string;
  market: string;
  exchange?: string | null;
  allocated_quantity?: string;
  allocated_cost?: string;
  allocated_proceeds?: string;
  realized_pnl?: string;
  realized_pnl_pct?: string;
  closed_at?: string;
};

type CoinStat = {
  market: string;
  trades: number;
  pnl_krw: string;
  win_rate?: string | null;
};

function parseTickerPrices(items: { symbol?: string; market?: string; price?: string | null; trade_price?: string | number | null }[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const t of items) {
    const sym = t.symbol ?? t.market;
    const p = Number(t.price ?? t.trade_price ?? 0);
    if (sym && p > 0) out[sym] = p;
  }
  return out;
}

export default function MemberDashboardPage() {
  const router = useRouter();
  const [session, setSession] = useState<{ username?: string; role?: string; mfa_enabled?: boolean } | null>(null);
  const [creds, setCreds] = useState<Cred[]>([]);
  const [exchange, setExchange] = useState<'BITHUMB' | 'UPBIT'>('BITHUMB');
  const [label, setLabel] = useState('');
  const [accessKey, setAccessKey] = useState('');
  const [secretKey, setSecretKey] = useState('');
  const [mfaSecret, setMfaSecret] = useState('');
  const [mfaCode, setMfaCode] = useState('');
  const [curPassword, setCurPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [disableMfaCode, setDisableMfaCode] = useState('');
  const [deletePassword, setDeletePassword] = useState('');
  const [memberSummary, setMemberSummary] = useState<{
    trade_count: number;
    wins?: number;
    losses?: number;
    realized_pnl_krw: string;
    total_fees_krw?: string;
    win_rate: string | null;
    profit_factor?: string | null;
    max_drawdown_pct?: string | null;
  } | null>(null);
  const [byCoin, setByCoin] = useState<CoinStat[]>([]);
  const [dailyPnl, setDailyPnl] = useState<{ date: string; pnl_krw: string }[]>([]);
  const [cumPnl, setCumPnl] = useState<{ timestamp: string; cumulative_pnl_krw: string }[]>([]);
  const [memberBalances, setMemberBalances] = useState<MemberBalance[]>([]);
  const [memberTrades, setMemberTrades] = useState<MemberTrade[]>([]);
  const [tradesPage, setTradesPage] = useState(1);
  const [tradesTotal, setTradesTotal] = useState(0);
  const [openOrders, setOpenOrders] = useState<{ exchange: string; uuid: string; market: string; side: string; price: string; remaining_volume: string; created_at: string }[]>([]);
  const [watchingItems, setWatchingItems] = useState<{ symbol: string; coin_name: string; drops: string[]; base_bars: number }[]>([]);
  const [activeSessions, setActiveSessions] = useState<{ ip: string; created_at: string; is_current: boolean }[]>([]);
  const [bithumbPrices, setBithumbPrices] = useState<Record<string, number>>({});
  const [upbitPrices, setUpbitPrices] = useState<Record<string, number>>({});
  const [exFilter, setExFilter] = useState<'ALL' | 'BITHUMB' | 'UPBIT'>('ALL');
  const [periodFilter, setPeriodFilter] = useState<'all' | 'today' | '7d' | '30d'>('all');
  const [marketInput, setMarketInput] = useState('');
  const [marketFilter, setMarketFilter] = useState('');
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');

  function normalizeMarketParam(raw: string): string {
    const m = raw.trim().toUpperCase();
    if (!m) return 'ALL';
    return m.includes('-') ? m : `KRW-${m}`;
  }

  async function refreshPrices() {
    const [bt, ut, strat] = await Promise.all([
      fetch('/api/manual/public/market/tickers?limit=300').then((r) => (r.ok ? r.json() : { items: [] })).catch(() => ({ items: [] })),
      fetch('/api/manual/public/exchanges/tickers?exchange=upbit&limit=200').then((r) => (r.ok ? r.json() : { items: [] })).catch(() => ({ items: [] })),
      fetch('/api/manual/public/strategies/overview').then((r) => (r.ok ? r.json() : null)).catch(() => null),
    ]);
    setBithumbPrices(parseTickerPrices(bt?.items ?? []));
    setUpbitPrices(parseTickerPrices(ut?.items ?? []));
    setWatchingItems(strat?.watching_items ?? []);
  }

  async function load(
    ex: 'ALL' | 'BITHUMB' | 'UPBIT' = exFilter,
    period: 'all' | 'today' | '7d' | '30d' = periodFilter,
    marketRaw: string = marketFilter,
  ) {
    const mParam = normalizeMarketParam(marketRaw);
    const [s, c, a, bal, tr, oo, sess] = await Promise.all([
      fetch('/api/manual/auth/session').then((r) => r.json()).catch(() => null),
      fetch('/api/manual/member/credentials').then((r) => (r.ok ? r.json() : { items: [] })).catch(() => ({ items: [] })),
      fetch(`/api/manual/member/analytics?exchange=${ex}&period=${period}&market=${encodeURIComponent(mParam)}`).then((r) => (r.ok ? r.json() : null)).catch(() => null),
      fetch('/api/manual/member/accounts').then((r) => (r.ok ? r.json() : { items: [] })).catch(() => ({ items: [] })),
      fetch(`/api/manual/member/trades?page=1&page_size=20&exchange=${ex}&period=${period}&market=${encodeURIComponent(mParam)}`).then((r) => (r.ok ? r.json() : { items: [], total: 0 })).catch(() => ({ items: [], total: 0 })),
      fetch('/api/manual/member/open-orders').then((r) => (r.ok ? r.json() : { items: [] })).catch(() => ({ items: [] })),
      fetch('/api/manual/member/sessions').then((r) => (r.ok ? r.json() : { items: [] })).catch(() => ({ items: [] })),
    ]);
    setSession(s);
    setCreds(c.items ?? []);
    if (a?.summary) setMemberSummary(a.summary);
    setByCoin(a?.charts?.by_market ?? []);
    setDailyPnl(a?.charts?.daily_pnl ?? []);
    setCumPnl(a?.charts?.cumulative_pnl ?? []);
    setMemberBalances(bal.items ?? []);
    setMemberTrades(tr.items ?? []);
    setTradesPage(1);
    setTradesTotal(Number(tr.total ?? (tr.items ?? []).length));
    setOpenOrders(oo.items ?? []);
    setActiveSessions(sess.items ?? []);
    await refreshPrices();
  }

  async function applyFilters(
    nextEx: 'ALL' | 'BITHUMB' | 'UPBIT',
    nextPeriod: 'all' | 'today' | '7d' | '30d',
    nextMarket: string,
  ) {
    setExFilter(nextEx);
    setPeriodFilter(nextPeriod);
    setMarketFilter(nextMarket);
    const mParam = normalizeMarketParam(nextMarket);
    const [a, tr] = await Promise.all([
      fetch(`/api/manual/member/analytics?exchange=${nextEx}&period=${nextPeriod}&market=${encodeURIComponent(mParam)}`).then((r) => (r.ok ? r.json() : null)).catch(() => null),
      fetch(`/api/manual/member/trades?page=1&page_size=20&exchange=${nextEx}&period=${nextPeriod}&market=${encodeURIComponent(mParam)}`).then((r) => (r.ok ? r.json() : { items: [], total: 0 })).catch(() => ({ items: [], total: 0 })),
    ]);
    if (a?.summary) setMemberSummary(a.summary);
    setByCoin(a?.charts?.by_market ?? []);
    setDailyPnl(a?.charts?.daily_pnl ?? []);
    setCumPnl(a?.charts?.cumulative_pnl ?? []);
    setMemberTrades(tr.items ?? []);
    setTradesPage(1);
    setTradesTotal(Number(tr.total ?? (tr.items ?? []).length));
  }

  async function loadTradesPage(nextPage: number) {
    const mParam = normalizeMarketParam(marketFilter);
    const tr = await fetch(
      `/api/manual/member/trades?page=${nextPage}&page_size=20&exchange=${exFilter}&period=${periodFilter}&market=${encodeURIComponent(mParam)}`,
    )
      .then((r) => (r.ok ? r.json() : { items: [], total: 0 }))
      .catch(() => ({ items: [], total: 0 }));
    setTradesPage(nextPage);
    setMemberTrades(tr.items ?? []);
    setTradesTotal(Number(tr.total ?? (tr.items ?? []).length));
  }

  async function cancelOpenOrder(exchange: string, uuid: string) {
    setErr('');
    setMsg('');
    const res = await fetch(`/api/manual/member/open-orders/${encodeURIComponent(exchange)}/${encodeURIComponent(uuid)}`, {
      method: 'DELETE',
    });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) {
      setErr(d.detail || d.error || '주문 취소 실패');
      return;
    }
    setOpenOrders((prev) => prev.filter((o) => !(o.exchange === exchange && o.uuid === uuid)));
    setMsg(`${exchange} 미체결 주문을 취소했습니다.`);
  }

  async function syncMemberAccount() {
    setErr('');
    setMsg('');
    const res = await fetch('/api/manual/member/sync', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: '{}',
    });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) {
      setErr(d.detail || d.error || '동기화 실패');
      return;
    }
    if (Array.isArray(d.errors) && d.errors.length > 0) {
      setErr(`일부 거래소 동기화 오류: ${d.errors.join(' / ')}`);
    }
    setMsg(`내 계좌 동기화 완료 (조회 자산 ${d.accounts_count ?? 0}종 · 신규 체결 ${d.inserted_fills ?? 0}건)`);
    load();
  }

  async function revokeOtherSessions() {
    setErr('');
    setMsg('');
    const res = await fetch('/api/manual/member/sessions/revoke-others', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: '{}',
    });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) {
      setErr(d.detail || d.error || '세션 해제 실패');
      return;
    }
    setMsg(`다른 기기 세션 ${d.revoked ?? 0}건을 로그아웃했습니다.`);
    load();
  }

  useEffect(() => {
    load();
    const timer = setInterval(refreshPrices, 10000);
    return () => clearInterval(timer);
  }, []);

  async function saveCred(e: FormEvent) {
    e.preventDefault();
    setErr('');
    setMsg('');
    const res = await fetch('/api/manual/member/credentials', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ exchange, label, access_key: accessKey, secret_key: secretKey }),
    });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) {
      setErr(d.detail || d.error || '저장 실패');
      return;
    }
    setAccessKey('');
    setSecretKey('');
    setLabel('');
    setMsg('API 키를 암호화 금고에 저장했습니다.');
    load();
  }

  async function delCred(id: string) {
    await fetch(`/api/manual/member/credentials/${id}`, { method: 'DELETE' });
    load();
  }

  async function setupMfa() {
    const res = await fetch('/api/manual/auth/mfa/setup', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: '{}',
    });
    const d = await res.json().catch(() => ({}));
    if (d.secret) setMfaSecret(d.secret);
  }

  async function enableMfa() {
    const res = await fetch('/api/manual/auth/mfa/enable', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ code: mfaCode }),
    });
    if (res.ok) {
      setMfaSecret('');
      setMfaCode('');
      setMsg('2단계 인증(TOTP)이 활성화되었습니다.');
      load();
    } else {
      setErr('인증 코드가 올바르지 않습니다.');
    }
  }

  async function disableMfa() {
    setErr('');
    setMsg('');
    const res = await fetch('/api/manual/auth/mfa/disable', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ code: disableMfaCode }),
    });
    if (res.ok) {
      setDisableMfaCode('');
      setMsg('2단계 인증(TOTP)이 비활성화되었습니다.');
      load();
    } else {
      setErr('유효하지 않은 TOTP 코드입니다.');
    }
  }

  async function deleteAccount(e: FormEvent) {
    e.preventDefault();
    if (!window.confirm('정말 탈퇴하시겠습니까? 저장된 API 키·세션·동기화된 거래 통계가 모두 삭제됩니다.')) {
      return;
    }
    setErr('');
    setMsg('');
    const res = await fetch('/api/manual/member/account/delete', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ password: deletePassword }),
    });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) {
      setErr(d.detail || d.error || '회원 탈퇴 실패');
      return;
    }
    router.push('/');
  }

  async function logout() {
    await fetch('/api/manual/auth/logout', { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' });
    router.push('/login');
  }

  async function changePassword(e: FormEvent) {
    e.preventDefault();
    setErr('');
    setMsg('');
    const res = await fetch('/api/manual/member/password', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ current_password: curPassword, new_password: newPassword }),
    });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) {
      setErr(d.detail || d.error || '비밀번호 변경 실패');
      return;
    }
    setCurPassword('');
    setNewPassword('');
    setMsg('비밀번호가 변경되었습니다.');
  }

  const mNeedle = marketFilter.trim().toUpperCase().replace(/^KRW-/, '');
  const visibleBalances = memberBalances.filter((b) => {
    const isUpbit = b.currency.startsWith('UPBIT:');
    if (exFilter !== 'ALL' && (exFilter === 'UPBIT' ? !isUpbit : isUpbit)) return false;
    if (!mNeedle) return true;
    const sym = (isUpbit ? b.currency.slice(6) : b.currency).toUpperCase();
    return sym.includes(mNeedle);
  });
  const visibleOpenOrders = openOrders.filter((o) => {
    if (exFilter !== 'ALL' && o.exchange !== exFilter) return false;
    if (!mNeedle) return true;
    return o.market.toUpperCase().includes(mNeedle);
  });

  let totalEvalKrw = 0;
  let totalUnrealizedKrw = 0;
  let hasUnrealized = false;
  for (const b of visibleBalances) {
    const isUpbit = b.currency.startsWith('UPBIT:');
    const sym = isUpbit ? b.currency.slice(6) : b.currency;
    const qty = Number(b.balance) + Number(b.locked ?? 0);
    if (sym === 'KRW') {
      totalEvalKrw += qty;
      continue;
    }
    const market = `KRW-${sym}`;
    const curPrice = (isUpbit ? upbitPrices[market] : bithumbPrices[market]) ?? 0;
    const avg = Number(b.average_price ?? 0);
    if (curPrice > 0 && qty > 0) {
      totalEvalKrw += curPrice * qty;
      if (avg > 0) {
        totalUnrealizedKrw += (curPrice - avg) * qty;
        hasUnrealized = true;
      }
    }
  }

  return (
    <div className="min-h-screen bg-[#0B0E14] text-zinc-100">
      <PublicHeader />
      <main className="mx-auto max-w-4xl space-y-6 px-4 py-8 sm:px-6">
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[#1E2536] bg-[#121620] p-6">
          <div>
            <h1 className="text-lg font-bold">회원 대시보드 ({session?.role ?? 'MEMBER'})</h1>
            <p className="mt-1 text-xs text-zinc-400">계정: {session?.username ?? '—'} · 암호화 API 키 금고 및 보안 설정</p>
          </div>
          <div className="flex items-center gap-2 text-xs">
            {session?.role === 'ADMIN' && (
              <Link href="/admin" className="rounded-lg bg-zinc-100 px-3 py-1.5 font-semibold text-zinc-950">
                운영자 콘솔
              </Link>
            )}
            <button onClick={logout} className="rounded-lg border border-[#2A344B] bg-[#181E2B] px-3 py-1.5 text-zinc-200">
              로그아웃
            </button>
          </div>
        </div>

        {msg && <p className="text-xs font-medium text-emerald-400">{msg}</p>}
        {err && <p className="text-xs font-medium text-rose-400">{err}</p>}

        {/* 내 개인 거래 통계 (격리 조회) */}
        <section className="rounded-xl border border-[#1E2536] bg-[#121620] p-6 text-xs">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h2 className="text-base font-semibold">내 개인 거래 통계</h2>
              <p className="mt-1 text-zinc-400">회원 본인의 암호화 API 키로만 조회되며 타 회원·운영자 데이터와 완전히 격리됩니다.</p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {(['ALL', 'BITHUMB', 'UPBIT'] as const).map((ex) => (
                <button
                  key={ex}
                  type="button"
                  onClick={() => applyFilters(ex, periodFilter, marketFilter)}
                  className={`rounded-lg px-2.5 py-1.5 font-mono text-[11px] font-semibold ${
                    exFilter === ex ? 'bg-zinc-100 text-zinc-950' : 'border border-[#1E2536] bg-[#0E1119] text-zinc-400'
                  }`}
                >
                  {ex === 'ALL' ? '전체' : ex}
                </button>
              ))}
              <button type="button" onClick={syncMemberAccount} className="rounded-lg border border-emerald-500/40 bg-emerald-500/15 px-3.5 py-2 font-semibold text-emerald-300">
                내 계좌 동기화
              </button>
            </div>
          </div>
          <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-[#1E2536] pt-3">
            <div className="flex flex-wrap items-center gap-1.5 font-mono text-[11px]">
              {([
                ['all', '전체 기간'],
                ['today', '오늘'],
                ['7d', '7일'],
                ['30d', '30일'],
              ] as const).map(([p, label]) => (
                <button
                  key={p}
                  type="button"
                  onClick={() => applyFilters(exFilter, p, marketFilter)}
                  className={`rounded-lg px-2.5 py-1 ${
                    periodFilter === p ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40' : 'border border-[#1E2536] bg-[#0E1119] text-zinc-400'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                applyFilters(exFilter, periodFilter, marketInput);
              }}
              className="flex items-center gap-1.5 font-mono text-[11px]"
            >
              <input
                value={marketInput}
                onChange={(e) => setMarketInput(e.target.value)}
                placeholder="종목 검색 (예: BTC)"
                className="w-36 rounded-lg border border-[#1E2536] bg-[#0E1119] px-2.5 py-1 text-zinc-100 placeholder:text-zinc-500"
              />
              <button type="submit" className="rounded-lg border border-[#2A344B] bg-[#181E2B] px-2.5 py-1 text-zinc-200">
                적용
              </button>
              {marketFilter && (
                <button
                  type="button"
                  onClick={() => {
                    setMarketInput('');
                    applyFilters(exFilter, periodFilter, '');
                  }}
                  className="rounded-lg border border-[#1E2536] bg-[#0E1119] px-2 py-1 text-zinc-400"
                >
                  초기화
                </button>
              )}
            </form>
          </div>
          <div className="mt-4 grid grid-cols-2 gap-3 font-mono sm:grid-cols-5">
            <div className="rounded-lg border border-[#1E2536] bg-[#0E1119] p-3">
              <p className="text-zinc-400">총 보유 평가액</p>
              <p className="mt-1 text-base font-semibold">{Math.round(totalEvalKrw).toLocaleString('ko-KR')}원</p>
            </div>
            <div className="rounded-lg border border-[#1E2536] bg-[#0E1119] p-3">
              <p className="text-zinc-400">미실현 손익</p>
              <p className={`mt-1 text-base font-semibold ${!hasUnrealized ? 'text-zinc-400' : totalUnrealizedKrw >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                {hasUnrealized ? `${totalUnrealizedKrw >= 0 ? '+' : ''}${Math.round(totalUnrealizedKrw).toLocaleString('ko-KR')}원` : '—'}
              </p>
            </div>
            <div className="rounded-lg border border-[#1E2536] bg-[#0E1119] p-3">
              <p className="text-zinc-400">거래 수</p>
              <p className="mt-1 text-base font-semibold">{memberSummary?.trade_count ?? 0}건</p>
            </div>
            <div className="rounded-lg border border-[#1E2536] bg-[#0E1119] p-3">
              <p className="text-zinc-400">실현손익</p>
              <p className="mt-1 text-base font-semibold">{Number(memberSummary?.realized_pnl_krw ?? '0').toLocaleString('ko-KR')}원</p>
            </div>
            <div className="rounded-lg border border-[#1E2536] bg-[#0E1119] p-3">
              <p className="text-zinc-400">승률</p>
              <p className="mt-1 text-base font-semibold">{memberSummary?.win_rate ? `${memberSummary.win_rate}%` : '—'}</p>
            </div>
          </div>
          {memberSummary && (
            <div className="mt-2.5 grid grid-cols-2 gap-2 font-mono text-[11px] sm:grid-cols-4">
              <div className="rounded-lg border border-[#1E2536] bg-[#0E1119] px-3 py-2 text-zinc-400">
                승/패 <span className="ml-1 font-semibold text-zinc-200">{memberSummary.wins ?? 0}승 {memberSummary.losses ?? 0}패</span>
              </div>
              <div className="rounded-lg border border-[#1E2536] bg-[#0E1119] px-3 py-2 text-zinc-400">
                수익팩터 <span className="ml-1 font-semibold text-zinc-200">{memberSummary.profit_factor ?? '—'}</span>
              </div>
              <div className="rounded-lg border border-[#1E2536] bg-[#0E1119] px-3 py-2 text-zinc-400">
                총 수수료 <span className="ml-1 font-semibold text-zinc-200">{Math.round(Number(memberSummary.total_fees_krw ?? '0')).toLocaleString('ko-KR')}원</span>
              </div>
              <div className="rounded-lg border border-[#1E2536] bg-[#0E1119] px-3 py-2 text-zinc-400">
                최대 낙폭 <span className="ml-1 font-semibold text-rose-300">{memberSummary.max_drawdown_pct ? `-${memberSummary.max_drawdown_pct}%` : '—'}</span>
              </div>
            </div>
          )}
          {byCoin.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-2 font-mono text-[11px]">
              {byCoin.slice(0, 8).map((c) => {
                const pnl = Number(c.pnl_krw ?? 0);
                return (
                  <span key={c.market} className="rounded border border-[#1E2536] bg-[#0E1119] px-2.5 py-1 text-zinc-300">
                    {c.market.replace('KRW-', '')} ({c.trades}건){' '}
                    <span className={pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}>
                      {pnl >= 0 ? '+' : ''}{Math.round(pnl).toLocaleString('ko-KR')}원
                    </span>
                  </span>
                );
              })}
            </div>
          )}
          {cumPnl.length >= 2 && (() => {
            const vals = cumPnl.map((p) => Number(p.cumulative_pnl_krw ?? 0));
            const minV = Math.min(0, ...vals);
            const maxV = Math.max(1, ...vals);
            const span = Math.max(1, maxV - minV);
            const w = 520;
            const h = 80;
            const pts = vals
              .map((v, i) => {
                const x = (i / (vals.length - 1)) * w;
                const y = h - ((v - minV) / span) * (h - 12) - 6;
                return `${x.toFixed(1)},${y.toFixed(1)}`;
              })
              .join(' ');
            const zeroY = h - ((0 - minV) / span) * (h - 12) - 6;
            const lastV = vals[vals.length - 1] ?? 0;
            return (
              <div className="mt-4 rounded-lg border border-[#1E2536] bg-[#0E1119] p-3 font-mono text-[10px]">
                <div className="mb-2 flex items-center justify-between text-zinc-400">
                  <span>누적 실현손익 곡선 ({cumPnl.length}건)</span>
                  <span className={lastV >= 0 ? 'text-emerald-400' : 'text-rose-400'}>
                    최종 {lastV >= 0 ? '+' : ''}{Math.round(lastV).toLocaleString('ko-KR')}원
                  </span>
                </div>
                <svg viewBox={`0 0 ${w} ${h}`} className="h-20 w-full overflow-visible">
                  <line x1="0" y1={zeroY} x2={w} y2={zeroY} stroke="#1E2536" strokeDasharray="3 3" strokeWidth="1" />
                  <polyline
                    fill="none"
                    stroke={lastV >= 0 ? '#34d399' : '#fb7185'}
                    strokeWidth="2"
                    points={pts}
                  />
                </svg>
              </div>
            );
          })()}
          {dailyPnl.length > 0 && (() => {
            const recent = dailyPnl.slice(-14);
            const maxAbs = Math.max(1, ...recent.map((d) => Math.abs(Number(d.pnl_krw ?? 0))));
            return (
              <div className="mt-4 rounded-lg border border-[#1E2536] bg-[#0E1119] p-3 font-mono text-[10px]">
                <div className="mb-2 flex items-center justify-between text-zinc-400">
                  <span>일별 실현손익 추이 (최근 {recent.length}일)</span>
                  <span>최대폭 ±{Math.round(maxAbs).toLocaleString('ko-KR')}원</span>
                </div>
                <div className="flex h-24 items-end gap-1.5">
                  {recent.map((d) => {
                    const v = Number(d.pnl_krw ?? 0);
                    const pct = Math.max(8, Math.round((Math.abs(v) / maxAbs) * 100));
                    return (
                      <div key={d.date} className="flex flex-1 flex-col items-center gap-1" title={`${d.date}: ${v >= 0 ? '+' : ''}${Math.round(v).toLocaleString('ko-KR')}원`}>
                        <div className="flex h-16 w-full items-end justify-center">
                          <div
                            style={{ height: `${pct}%` }}
                            className={`w-full max-w-[24px] rounded-t ${v >= 0 ? 'bg-emerald-500/70' : 'bg-rose-500/70'}`}
                          />
                        </div>
                        <span className="text-[9px] text-zinc-500">{d.date.slice(5)}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })()}
          {visibleBalances.length > 0 && (
            <div className="mt-4 overflow-x-auto rounded-lg border border-[#1E2536] bg-[#0E1119]">
              <table className="w-full text-left font-mono text-[11px]">
                <thead className="border-b border-[#1E2536] text-zinc-400">
                  <tr>
                    <th className="px-3 py-2">거래소/자산</th>
                    <th className="px-3 py-2 text-right">보유 수량</th>
                    <th className="px-3 py-2 text-right">평균 매수가</th>
                    <th className="px-3 py-2 text-right">현재가</th>
                    <th className="px-3 py-2 text-right">평가손익</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#1E2536]/60">
                  {visibleBalances.map((b) => {
                    const isUpbit = b.currency.startsWith('UPBIT:');
                    const sym = isUpbit ? b.currency.slice(6) : b.currency;
                    const qty = Number(b.balance) + Number(b.locked ?? 0);
                    const avg = Number(b.average_price ?? 0);
                    const market = `KRW-${sym}`;
                    const curPrice = sym === 'KRW' ? 0 : (isUpbit ? upbitPrices[market] : bithumbPrices[market]) ?? 0;
                    const hasLivePnl = sym !== 'KRW' && curPrice > 0 && avg > 0 && qty > 0;
                    const pnlKrw = hasLivePnl ? (curPrice - avg) * qty : 0;
                    const pnlPct = hasLivePnl ? ((curPrice - avg) / avg) * 100 : 0;
                    return (
                      <tr key={b.currency}>
                        <td className="px-3 py-2 text-zinc-200">
                          <span className="mr-1.5 rounded bg-[#181E2B] px-1.5 py-0.5 text-[10px] text-zinc-400">
                            {isUpbit ? 'UPBIT' : 'BITHUMB'}
                          </span>
                          {sym}
                        </td>
                        <td className="px-3 py-2 text-right text-zinc-200">
                          {qty.toLocaleString('ko-KR', { maximumFractionDigits: 6 })}
                        </td>
                        <td className="px-3 py-2 text-right text-zinc-400">
                          {avg > 0 ? `${avg.toLocaleString('ko-KR', { maximumFractionDigits: 2 })}원` : '—'}
                        </td>
                        <td className="px-3 py-2 text-right text-zinc-200">
                          {curPrice > 0 ? `${curPrice.toLocaleString('ko-KR', { maximumFractionDigits: 2 })}원` : '—'}
                        </td>
                        <td className={`px-3 py-2 text-right font-semibold ${!hasLivePnl ? 'text-zinc-500' : pnlKrw >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                          {hasLivePnl
                            ? `${pnlKrw >= 0 ? '+' : ''}${Math.round(pnlKrw).toLocaleString('ko-KR')}원 (${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%)`
                            : '—'}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {/* 내 미체결 주문 & 급락 반등 관찰 */}
        <section className="rounded-xl border border-[#1E2536] bg-[#121620] p-6 text-xs">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h2 className="text-base font-semibold">내 미체결 주문 현황 (Bithumb · Upbit)</h2>
              <p className="mt-1 text-zinc-400">연결된 거래소 계정의 대기(wait) 지정가 주문을 조회하고 직접 취소할 수 있습니다.</p>
            </div>
            <button type="button" onClick={() => load()} className="rounded-lg border border-[#2A344B] bg-[#181E2B] px-3 py-1.5 text-zinc-200">
              새로고침
            </button>
          </div>
          {visibleOpenOrders.length === 0 ? (
            <p className="mt-3 text-zinc-500">현재 대기 중인 미체결 주문이 없습니다.</p>
          ) : (
            <div className="mt-3 overflow-x-auto rounded-lg border border-[#1E2536] bg-[#0E1119]">
              <table className="w-full text-left font-mono text-[11px]">
                <thead className="border-b border-[#1E2536] text-zinc-400">
                  <tr>
                    <th className="px-3 py-2">거래소/마켓</th>
                    <th className="px-3 py-2">구분</th>
                    <th className="px-3 py-2 text-right">주문가</th>
                    <th className="px-3 py-2 text-right">잔여 수량</th>
                    <th className="px-3 py-2 text-right">주문 시각</th>
                    <th className="px-3 py-2 text-right">관리</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#1E2536]/60">
                  {visibleOpenOrders.map((o) => (
                    <tr key={`${o.exchange}-${o.uuid}`}>
                      <td className="px-3 py-2 text-zinc-200">
                        <span className="mr-1.5 rounded bg-[#181E2B] px-1.5 py-0.5 text-[10px] text-zinc-400">{o.exchange}</span>
                        {o.market}
                      </td>
                      <td className={`px-3 py-2 font-semibold ${o.side === 'BUY' ? 'text-emerald-400' : 'text-rose-400'}`}>
                        {o.side === 'BUY' ? '매수' : '매도'}
                      </td>
                      <td className="px-3 py-2 text-right text-zinc-200">{Number(o.price).toLocaleString('ko-KR')}원</td>
                      <td className="px-3 py-2 text-right text-zinc-300">{o.remaining_volume}</td>
                      <td className="px-3 py-2 text-right text-zinc-500">{o.created_at ? o.created_at.slice(0, 16).replace('T', ' ') : '—'}</td>
                      <td className="px-3 py-2 text-right">
                        <button
                          type="button"
                          onClick={() => cancelOpenOrder(o.exchange, o.uuid)}
                          className="rounded border border-rose-500/40 bg-rose-500/15 px-2 py-0.5 text-[10px] text-rose-300 hover:bg-rose-500/25"
                        >
                          취소
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {watchingItems.length > 0 && (
            <div className="mt-4 border-t border-[#1E2536] pt-4">
              <p className="mb-2 font-semibold text-zinc-300">실시간 급락 반등 관찰 종목 ({watchingItems.length}종목)</p>
              <div className="flex flex-wrap gap-2 font-mono text-[11px]">
                {watchingItems.map((w) => {
                  const heldMatch = memberBalances.some(
                    (b) => (b.currency.startsWith('UPBIT:') ? b.currency.slice(6) : b.currency) === w.symbol.replace('KRW-', '') && Number(b.balance) > 0
                  );
                  return (
                    <span
                      key={w.symbol}
                      className={`rounded border px-2.5 py-1 ${
                        heldMatch ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300' : 'border-[#1E2536] bg-[#0E1119] text-zinc-300'
                      }`}
                    >
                      {w.coin_name} ({w.symbol.replace('KRW-', '')}) · 횡보 {w.base_bars}분{heldMatch ? ' · 보유중' : ''}
                    </span>
                  );
                })}
              </div>
            </div>
          )}
        </section>

        {/* 내 최근 거래 내역 */}
        <section className="rounded-xl border border-[#1E2536] bg-[#121620] p-6 text-xs">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-base font-semibold">내 최근 거래 내역</h2>
            <div className="flex flex-wrap items-center gap-2 font-mono text-[11px]">
              <span className="text-zinc-400">총 {tradesTotal}건</span>
              {tradesTotal > 20 && (
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    disabled={tradesPage <= 1}
                    onClick={() => loadTradesPage(tradesPage - 1)}
                    className="rounded border border-[#2A344B] bg-[#181E2B] px-2 py-1 text-zinc-200 disabled:opacity-40"
                  >
                    이전
                  </button>
                  <span className="px-1.5 text-zinc-400">
                    {tradesPage} / {Math.max(1, Math.ceil(tradesTotal / 20))}
                  </span>
                  <button
                    type="button"
                    disabled={tradesPage >= Math.ceil(tradesTotal / 20)}
                    onClick={() => loadTradesPage(tradesPage + 1)}
                    className="rounded border border-[#2A344B] bg-[#181E2B] px-2 py-1 text-zinc-200 disabled:opacity-40"
                  >
                    다음
                  </button>
                </div>
              )}
              <a
                href={`/api/manual/member/export?format=csv&exchange=${exFilter}&period=${periodFilter}&market=${encodeURIComponent(normalizeMarketParam(marketFilter))}`}
                className="rounded border border-[#2A344B] bg-[#181E2B] px-2.5 py-1 text-zinc-200 hover:border-zinc-400"
              >
                CSV 내보내기
              </a>
              <a
                href={`/api/manual/member/export?format=json&exchange=${exFilter}&period=${periodFilter}&market=${encodeURIComponent(normalizeMarketParam(marketFilter))}`}
                className="rounded border border-[#2A344B] bg-[#181E2B] px-2.5 py-1 text-zinc-200 hover:border-zinc-400"
              >
                JSON
              </a>
            </div>
          </div>
          {memberTrades.length === 0 ? (
            <p className="mt-3 text-zinc-500">동기화된 거래 내역이 없습니다. API 키 저장 후 &apos;내 계좌 동기화&apos;를 실행하세요.</p>
          ) : (
            <div className="mt-3 overflow-x-auto rounded-lg border border-[#1E2536] bg-[#0E1119]">
              <table className="w-full text-left font-mono text-[11px]">
                <thead className="border-b border-[#1E2536] text-zinc-400">
                  <tr>
                    <th className="px-3 py-2">거래소</th>
                    <th className="px-3 py-2">마켓</th>
                    <th className="px-3 py-2 text-right">수량</th>
                    <th className="px-3 py-2 text-right">진입가</th>
                    <th className="px-3 py-2 text-right">청산가</th>
                    <th className="px-3 py-2 text-right">실현손익</th>
                    <th className="px-3 py-2 text-right">시각</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#1E2536]/60">
                  {memberTrades.map((t) => {
                    const qty = Number(t.allocated_quantity ?? 0);
                    const entryP = qty > 0 && t.allocated_cost ? Number(t.allocated_cost) / qty : 0;
                    const exitP = qty > 0 && t.allocated_proceeds ? Number(t.allocated_proceeds) / qty : 0;
                    const pnl = Number(t.realized_pnl ?? 0);
                    const exLabel = (t.exchange ?? '').toUpperCase() || '—';
                    return (
                      <tr key={t.id}>
                        <td className="px-3 py-2">
                          <span
                            className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${
                              exLabel === 'UPBIT'
                                ? 'bg-sky-500/15 text-sky-300'
                                : exLabel === 'BITHUMB'
                                  ? 'bg-amber-500/15 text-amber-300'
                                  : 'bg-zinc-700/40 text-zinc-400'
                            }`}
                          >
                            {exLabel}
                          </span>
                        </td>
                        <td className="px-3 py-2 text-zinc-200">{t.market}</td>
                        <td className="px-3 py-2 text-right text-zinc-300">{t.allocated_quantity ?? '—'}</td>
                        <td className="px-3 py-2 text-right text-zinc-400">{entryP > 0 ? `${Math.round(entryP).toLocaleString('ko-KR')}원` : '—'}</td>
                        <td className="px-3 py-2 text-right text-zinc-300">{exitP > 0 ? `${Math.round(exitP).toLocaleString('ko-KR')}원` : '—'}</td>
                        <td className={`px-3 py-2 text-right font-semibold ${pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                          {t.realized_pnl != null ? `${pnl >= 0 ? '+' : ''}${Math.round(pnl).toLocaleString('ko-KR')}원` : '—'}
                        </td>
                        <td className="px-3 py-2 text-right text-zinc-500">{t.closed_at ? t.closed_at.slice(0, 16).replace('T', ' ') : '—'}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {/* 암호화 API 키 금고 */}
        <section className="rounded-xl border border-[#1E2536] bg-[#121620] p-6 text-xs">
          <h2 className="text-base font-semibold">거래소 API 키 금고 (Bithumb · Upbit)</h2>
          <p className="mt-1 text-zinc-400">키는 서버에서 암호화 저장되며 평문으로 다시 조회되지 않습니다.</p>
          <form onSubmit={saveCred} className="mt-4 grid gap-3 sm:grid-cols-4">
            <select
              value={exchange}
              onChange={(e) => setExchange(e.target.value as 'BITHUMB' | 'UPBIT')}
              className="rounded-lg border border-[#1E2536] bg-[#0B0E14] px-3 py-2 text-zinc-100"
            >
              <option value="BITHUMB">BITHUMB</option>
              <option value="UPBIT">UPBIT</option>
            </select>
            <input
              placeholder="별칭 (예: 조회용)"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              className="rounded-lg border border-[#1E2536] bg-[#0B0E14] px-3 py-2 text-zinc-100"
            />
            <input
              required
              placeholder="Access Key"
              value={accessKey}
              onChange={(e) => setAccessKey(e.target.value)}
              className="rounded-lg border border-[#1E2536] bg-[#0B0E14] px-3 py-2 font-mono text-zinc-100"
            />
            <input
              required
              type="password"
              placeholder="Secret Key"
              value={secretKey}
              onChange={(e) => setSecretKey(e.target.value)}
              className="rounded-lg border border-[#1E2536] bg-[#0B0E14] px-3 py-2 font-mono text-zinc-100"
            />
            <button type="submit" className="rounded-lg bg-zinc-100 px-4 py-2 font-semibold text-zinc-950 sm:col-span-4 sm:w-fit">
              암호화 저장
            </button>
          </form>

          <div className="mt-4 space-y-2">
            {creds.map((c) => (
              <div key={c.id} className="flex items-center justify-between rounded-lg border border-[#1E2536] bg-[#0E1119] px-4 py-2.5">
                <div className="font-mono">
                  <span className="rounded bg-[#181E2B] px-2 py-0.5 text-zinc-200">{c.exchange}</span>{' '}
                  <span className="ml-2 text-zinc-300">{c.label}</span>{' '}
                  <span className="ml-2 text-zinc-500">{c.key_hint}</span>
                </div>
                <button onClick={() => delCred(c.id)} className="text-rose-400 hover:underline">
                  삭제
                </button>
              </div>
            ))}
          </div>
        </section>

        {/* TOTP 2단계 인증 */}
        <section className="rounded-xl border border-[#1E2536] bg-[#121620] p-6 text-xs">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-base font-semibold">2단계 인증 (TOTP MFA)</h2>
              <p className="mt-1 text-zinc-400">상태: {session?.mfa_enabled ? '활성화됨' : '미설정'}</p>
            </div>
            <button onClick={setupMfa} className="rounded-lg border border-[#2A344B] bg-[#181E2B] px-3 py-1.5 text-zinc-200">
              새 시크릿 발급
            </button>
          </div>
          {mfaSecret && (
            <div className="mt-4 space-y-3 rounded-lg border border-[#1E2536] bg-[#0E1119] p-4">
              <p className="font-mono text-emerald-300">인증앱 시크릿 키: {mfaSecret}</p>
              <div className="flex gap-2">
                <input
                  placeholder="6자리 코드 입력"
                  value={mfaCode}
                  onChange={(e) => setMfaCode(e.target.value)}
                  className="rounded-lg border border-[#1E2536] bg-[#0B0E14] px-3 py-1.5 font-mono text-zinc-100"
                />
                <button onClick={enableMfa} className="rounded-lg bg-zinc-100 px-3 py-1.5 font-semibold text-zinc-950">
                  MFA 활성화
                </button>
              </div>
            </div>
          )}
          {session?.mfa_enabled && (
            <div className="mt-4 flex flex-wrap items-center gap-2 rounded-lg border border-[#1E2536] bg-[#0E1119] p-3">
              <input
                placeholder="비활성화용 6자리 TOTP"
                value={disableMfaCode}
                onChange={(e) => setDisableMfaCode(e.target.value)}
                className="rounded-lg border border-[#1E2536] bg-[#0B0E14] px-3 py-1.5 font-mono text-zinc-100"
              />
              <button
                type="button"
                onClick={disableMfa}
                className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-1.5 font-semibold text-rose-300"
              >
                MFA 비활성화
              </button>
            </div>
          )}
        </section>

        {/* 비밀번호 변경 */}
        <section className="rounded-xl border border-[#1E2536] bg-[#121620] p-6 text-xs">
          <h2 className="text-base font-semibold">계정 비밀번호 변경</h2>
          <p className="mt-1 text-zinc-400">8자 이상의 새 비밀번호로 주기적으로 변경해 주세요.</p>
          <form onSubmit={changePassword} className="mt-4 flex flex-wrap gap-3">
            <input
              required
              type="password"
              placeholder="현재 비밀번호"
              value={curPassword}
              onChange={(e) => setCurPassword(e.target.value)}
              className="rounded-lg border border-[#1E2536] bg-[#0B0E14] px-3 py-2 text-zinc-100"
            />
            <input
              required
              type="password"
              minLength={8}
              placeholder="새 비밀번호 (8자 이상)"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              className="rounded-lg border border-[#1E2536] bg-[#0B0E14] px-3 py-2 text-zinc-100"
            />
            <button type="submit" className="rounded-lg bg-zinc-100 px-4 py-2 font-semibold text-zinc-950">
              비밀번호 변경
            </button>
          </form>
        </section>

        {/* 활성 세션 관리 */}
        <section className="rounded-xl border border-[#1E2536] bg-[#121620] p-6 text-xs">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h2 className="text-base font-semibold">활성 로그인 세션 ({activeSessions.length}개)</h2>
              <p className="mt-1 text-zinc-400">현재 브라우저 외 다른 기기·브라우저의 로그인 세션을 즉시 만료시킬 수 있습니다.</p>
            </div>
            <button
              type="button"
              onClick={revokeOtherSessions}
              className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-3.5 py-2 font-semibold text-rose-300"
            >
              다른 기기 모두 로그아웃
            </button>
          </div>
          {activeSessions.length > 0 && (
            <div className="mt-3 space-y-1.5 font-mono text-[11px]">
              {activeSessions.map((s, idx) => (
                <div key={`${s.created_at}-${idx}`} className="flex items-center justify-between rounded border border-[#1E2536] bg-[#0E1119] px-3 py-2">
                  <span className="text-zinc-300">
                    IP: {s.ip} {s.is_current && <span className="ml-1.5 rounded bg-emerald-500/20 px-1.5 py-0.5 text-[10px] text-emerald-300">현재 기기</span>}
                  </span>
                  <span className="text-zinc-500">{s.created_at.slice(0, 16).replace('T', ' ')}</span>
                </div>
              ))}
            </div>
          )}
        </section>

        {session?.role !== 'ADMIN' && (
          <section className="rounded-xl border border-rose-900/50 bg-[#121620] p-6 text-xs">
            <h2 className="text-base font-semibold text-rose-300">회원 탈퇴</h2>
            <p className="mt-1 text-zinc-400">탈퇴 시 암호화된 API 키, 활성 세션 및 동기화된 개인 거래 데이터가 영구 삭제됩니다.</p>
            <form onSubmit={deleteAccount} className="mt-4 flex flex-wrap gap-3">
              <input
                required
                type="password"
                placeholder="현재 비밀번호 확인"
                value={deletePassword}
                onChange={(e) => setDeletePassword(e.target.value)}
                className="rounded-lg border border-[#1E2536] bg-[#0B0E14] px-3 py-2 text-zinc-100"
              />
              <button
                type="submit"
                className="rounded-lg border border-rose-500/50 bg-rose-500/15 px-4 py-2 font-semibold text-rose-300 hover:bg-rose-500/25"
              >
                계정 영구 삭제
              </button>
            </form>
          </section>
        )}
      </main>
    </div>
  );
}
