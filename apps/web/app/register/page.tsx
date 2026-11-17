'use client';
import { FormEvent, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { PublicHeader } from '../components/public-nav';

export default function RegisterPage() {
  const router = useRouter();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      const res = await fetch('/api/manual/auth/register', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ email, password }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.detail || d.error || '회원가입 실패');
      }
      router.push('/login');
    } catch (err) {
      setError(err instanceof Error ? err.message : '회원가입 오류');
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
            <h1 className="text-lg font-semibold">회원가입 (MEMBER)</h1>
            <p className="mt-1 text-xs text-zinc-400">회원 전용 대시보드와 암호화 API 키 금고, 2단계 인증(MFA)을 이용할 수 있습니다.</p>
          </div>
          <label className="block text-xs text-zinc-300">
            이메일
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="mt-1.5 w-full rounded-lg border border-[#1E2536] bg-[#0B0E14] px-3 py-2 text-sm text-zinc-100 focus:border-zinc-500 focus:outline-none"
            />
          </label>
          <label className="block text-xs text-zinc-300">
            비밀번호 (8자 이상)
            <input
              type="password"
              required
              minLength={8}
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
            {busy ? '가입 처리 중...' : '회원가입'}
          </button>
          <p className="text-center text-xs text-zinc-400">
            이미 계정이 있나요?{' '}
            <Link href="/login" className="text-zinc-200 underline">
              로그인
            </Link>
          </p>
        </form>
      </main>
    </div>
  );
}
