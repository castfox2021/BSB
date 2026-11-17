'use client';
import { FormEvent, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { PublicHeader } from '../components/public-nav';

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [totpCode, setTotpCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      const res = await fetch('/api/manual/auth/login', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ username, password, totp_code: totpCode }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.detail || data.error || '로그인 실패');
      }
      const next = new URLSearchParams(window.location.search).get('next');
      if (data.role === 'ADMIN') {
        router.push(next && next.startsWith('/admin') ? next : '/admin');
      } else {
        router.push('/dashboard');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '로그인 오류');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen bg-[#0B0E14] text-zinc-100">
      <PublicHeader />
      <main className="mx-auto flex max-w-md flex-col px-4 py-16">
        <form onSubmit={onSubmit} className="space-y-4 rounded-xl border border-[#1E2536] bg-[#121620] p-6">
          <div>
            <h1 className="text-lg font-semibold">로그인</h1>
            <p className="mt-1 text-xs text-zinc-400">운영자(admin) 또는 회원 이메일로 로그인하세요.</p>
          </div>
          <label className="block text-xs text-zinc-300">
            아이디 / 이메일
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="mt-1.5 w-full rounded-lg border border-[#1E2536] bg-[#0B0E14] px-3 py-2 text-sm text-zinc-100 focus:border-zinc-500 focus:outline-none"
            />
          </label>
          <label className="block text-xs text-zinc-300">
            비밀번호
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="mt-1.5 w-full rounded-lg border border-[#1E2536] bg-[#0B0E14] px-3 py-2 text-sm text-zinc-100 focus:border-zinc-500 focus:outline-none"
            />
          </label>
          <label className="block text-xs text-zinc-300">
            2단계 인증(TOTP) 코드 (설정된 경우 6자리)
            <input
              inputMode="numeric"
              maxLength={6}
              placeholder="미설정 시 비워두세요"
              value={totpCode}
              onChange={(e) => setTotpCode(e.target.value)}
              className="mt-1.5 w-full rounded-lg border border-[#1E2536] bg-[#0B0E14] px-3 py-2 font-mono text-sm text-zinc-100 focus:border-zinc-500 focus:outline-none"
            />
          </label>
          {error && <p className="text-xs font-medium text-rose-400">{error}</p>}
          <button
            type="submit"
            disabled={busy}
            className="w-full rounded-lg bg-zinc-100 py-2.5 text-sm font-semibold text-zinc-950 hover:bg-white disabled:opacity-40"
          >
            {busy ? '인증 중...' : '로그인'}
          </button>
          <p className="text-center text-xs text-zinc-400">
            회원이 아니신가요?{' '}
            <Link href="/register" className="text-zinc-200 underline">
              회원가입
            </Link>
          </p>
        </form>
      </main>
    </div>
  );
}
