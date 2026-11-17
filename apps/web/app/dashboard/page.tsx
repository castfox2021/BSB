'use client';
import { FormEvent, useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { PublicHeader } from '../components/public-nav';

type Cred = { id: string; exchange: string; label: string; key_hint: string; created_at: string };

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
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');

  async function load() {
    const s = await fetch('/api/manual/auth/session').then((r) => r.json()).catch(() => null);
    setSession(s);
    const c = await fetch('/api/manual/member/credentials').then((r) => (r.ok ? r.json() : { items: [] })).catch(() => ({ items: [] }));
    setCreds(c.items ?? []);
  }

  useEffect(() => {
    load();
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
