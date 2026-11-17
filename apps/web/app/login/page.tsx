'use client';
import { FormEvent, useState } from 'react';
import { useRouter } from 'next/navigation';
import { PublicHeader } from '../components/public-nav';

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
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
        body: JSON.stringify({ username, password }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || data.error || '로그인 실패');
      }
      const next = new URLSearchParams(window.location.search).get('next');
      router.push(next && next.startsWith('/admin') ? next : '/admin');
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
        <form onSubmit={onSubmit} className="rounded-xl border border-[#1E2536] bg-[#121620] p-6 space-y-4">
          <div>
            <h1 className="text-lg font-semibold">운영자 로그인</h1>
            <p className="mt-1 text-xs text-zinc-400">실전 자동매매 관제 및 통합 거래 분석 접근을 위해 로그인하세요.</p>
          </div>
          <label className="block text-xs text-zinc-300">
            아이디
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
          {error && <p className="text-xs font-medium text-rose-400">{error}</p>}
          <button
            type="submit"
            disabled={busy}
            className="w-full rounded-lg bg-zinc-100 py-2.5 text-sm font-semibold text-zinc-950 hover:bg-white disabled:opacity-40"
          >
            {busy ? '인증 중...' : '로그인'}
          </button>
        </form>
      </main>
    </div>
  );
}
