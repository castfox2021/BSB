'use client';
import { ReactNode, useEffect, useState } from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { api, Policy } from '../trading-types';

const adminMenus = [
  ['/admin', '운영 대시보드'],
  ['/admin/trading', '자동매매 관제'],
  ['/admin/market', '시장 분석'],
  ['/admin/analytics', '거래 통계'],
  ['/admin/lab', '전략 연구소'],
  ['/admin/settings', '운영 설정'],
] as const;

export default function AdminShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [autoBuyOnly, setAutoBuyOnly] = useState(false);

  useEffect(() => {
    api<Policy>('settings')
      .then((p) => setAutoBuyOnly(Boolean(p.auto_buy_only)))
      .catch(() => {});
  }, [pathname]);

  async function logout() {
    await fetch('/api/manual/auth/logout', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: '{}',
    }).catch(() => {});
    router.push('/login');
  }

  return (
    <div className="min-h-screen bg-[#0B0E14] text-zinc-100">
      <header className="border-b border-[#1E2536] bg-[#0B0E14]">
        <div className="mx-auto max-w-6xl px-4 py-4 sm:px-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <Link href="/admin" className="text-base font-bold tracking-tight text-zinc-100 sm:text-lg">
                BSB Admin Console
              </Link>
              <span
                className={`rounded-md border px-2.5 py-1 text-xs font-medium ${
                  autoBuyOnly
                    ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300'
                    : 'border-[#1E2536] bg-[#121620] text-zinc-400'
                }`}
              >
                {autoBuyOnly ? '자동 실전매수 켜짐 (매도 수동)' : 'API 주문 꺼짐'}
              </span>
            </div>
            <div className="flex items-center gap-2 text-xs">
              <Link href="/" className="rounded-md border border-[#1E2536] bg-[#121620] px-3 py-1.5 text-zinc-300 hover:border-zinc-500">
                공개 홈
              </Link>
              <button
                type="button"
                onClick={logout}
                className="rounded-md border border-[#2A344B] bg-[#181E2B] px-3 py-1.5 text-zinc-200 hover:border-zinc-400"
              >
                로그아웃
              </button>
            </div>
          </div>
          <nav className="mt-4 flex flex-wrap gap-1.5">
            {adminMenus.map(([href, label]) => {
              const active = pathname === href;
              return (
                <Link
                  key={href}
                  href={href}
                  className={`rounded-lg px-3.5 py-2 text-xs font-medium transition sm:text-sm ${
                    active ? 'bg-zinc-100 text-zinc-950' : 'text-zinc-400 hover:bg-[#121620] hover:text-zinc-200'
                  }`}
                >
                  {label}
                </Link>
              );
            })}
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6 sm:px-6">{children}</main>
    </div>
  );
}
