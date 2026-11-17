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
  side?: string;
  quantity?: string;
  entry_price?: string;
  exit_price?: string;
  net_pnl_krw?: string;
  return_pct?: string;
  closed_at?: string;
  status?: string;
};

type CoinStat = {
  market: string;
  trade_count: number;
  realized_pnl_krw: string;
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
  const [memberSummary, setMemberSummary] = useState<{ trade_count: number; realized_pnl_krw: string; win_rate: string | null } | null>(null);
  const [byCoin, setByCoin] = useState<CoinStat[]>([]);
  const [memberBalances, setMemberBalances] = useState<MemberBalance[]>([]);
  const [memberTrades, setMemberTrades] = useState<MemberTrade[]>([]);
  const [bithumbPrices, setBithumbPrices] = useState<Record<string, number>>({});
  const [upbitPrices, setUpbitPrices] = useState<Record<string, number>>({});
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');

  async function refreshPrices() {
    const [bt, ut] = await Promise.all([
      fetch('/api/manual/public/market/tickers?limit=300').then((r) => (r.ok ? r.json() : { items: [] })).catch(() => ({ items: [] })),
      fetch('/api/manual/public/exchanges/tickers?exchange=upbit&limit=200').then((r) => (r.ok ? r.json() : { items: [] })).catch(() => ({ items: [] })),
    ]);
    setBithumbPrices(parseTickerPrices(bt?.items ?? []));
    setUpbitPrices(parseTickerPrices(ut?.items ?? []));
  }

  async function load() {
    const [s, c, a, bal, tr] = await Promise.all([
      fetch('/api/manual/auth/session').then((r) => r.json()).catch(() => null),
      fetch('/api/manual/member/credentials').then((r) => (r.ok ? r.json() : { items: [] })).catch(() => ({ items: [] })),
      fetch('/api/manual/member/analytics').then((r) => (r.ok ? r.json() : null)).catch(() => null),
      fetch('/api/manual/member/accounts').then((r) => (r.ok ? r.json() : { items: [] })).catch(() => ({ items: [] })),
      fetch('/api/manual/member/trades?page=1&page_size=20').then((r) => (r.ok ? r.json() : { items: [] })).catch(() => ({ items: [] })),
    ]);
    setSession(s);
    setCreds(c.items ?? []);
    if (a?.summary) setMemberSummary(a.summary);
    setByCoin(a?.by_coin ?? []);
    setMemberBalances(bal.items ?? []);
    setMemberTrades(tr.items ?? []);
    await refreshPrices();
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
    setMsg(`내 계좌 동기화 완료 (조회 자산 ${d.accounts_count ?? 0}종 · 신규 체결 ${d.inserted_fills ?? 0}건)`);
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

  async function logout() {
    await fetch('/api/manual/auth/logout', { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' });
    router.push('/login');
  }

  let totalEvalKrw = 0;
  let totalUnrealizedKrw = 0;
  let hasUnrealized = false;
  for (const b of memberBalances) {
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
            <button type="button" onClick={syncMemberAccount} className="rounded-lg border border-emerald-500/40 bg-emerald-500/15 px-3.5 py-2 font-semibold text-emerald-300">
              내 계좌 동기화
            </button>
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
          {byCoin.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-2 font-mono text-[11px]">
              {byCoin.slice(0, 8).map((c) => {
                const pnl = Number(c.realized_pnl_krw ?? 0);
                return (
                  <span key={c.market} className="rounded border border-[#1E2536] bg-[#0E1119] px-2.5 py-1 text-zinc-300">
                    {c.market.replace('KRW-', '')} ({c.trade_count}건){' '}
                    <span className={pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}>
                      {pnl >= 0 ? '+' : ''}{Math.round(pnl).toLocaleString('ko-KR')}원
                    </span>
                  </span>
                );
              })}
            </div>
          )}
          {memberBalances.length > 0 && (
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
                  {memberBalances.map((b) => {
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

        {/* 내 최근 거래 내역 */}
        <section className="rounded-xl border border-[#1E2536] bg-[#121620] p-6 text-xs">
          <div className="flex items-center justify-between">
            <h2 className="text-base font-semibold">내 최근 거래 내역</h2>
            <span className="font-mono text-[11px] text-zinc-400">최근 {memberTrades.length}건</span>
          </div>
          {memberTrades.length === 0 ? (
            <p className="mt-3 text-zinc-500">동기화된 거래 내역이 없습니다. API 키 저장 후 &apos;내 계좌 동기화&apos;를 실행하세요.</p>
          ) : (
            <div className="mt-3 overflow-x-auto rounded-lg border border-[#1E2536] bg-[#0E1119]">
              <table className="w-full text-left font-mono text-[11px]">
                <thead className="border-b border-[#1E2536] text-zinc-400">
                  <tr>
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
                    const pnl = Number(t.net_pnl_krw ?? 0);
                    return (
                      <tr key={t.id}>
                        <td className="px-3 py-2 text-zinc-200">{t.market}</td>
                        <td className="px-3 py-2 text-right text-zinc-300">{t.quantity ?? '—'}</td>
                        <td className="px-3 py-2 text-right text-zinc-400">{t.entry_price ? `${Number(t.entry_price).toLocaleString('ko-KR')}원` : '—'}</td>
                        <td className="px-3 py-2 text-right text-zinc-300">{t.exit_price ? `${Number(t.exit_price).toLocaleString('ko-KR')}원` : '—'}</td>
                        <td className={`px-3 py-2 text-right font-semibold ${pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                          {t.net_pnl_krw != null ? `${pnl >= 0 ? '+' : ''}${Math.round(pnl).toLocaleString('ko-KR')}원` : '—'}
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
        </section>
      </main>
    </div>
  );
}
