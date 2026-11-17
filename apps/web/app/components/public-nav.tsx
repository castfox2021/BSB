'use client';
import Link from 'next/link';
import { usePathname } from 'next/navigation';

const links = [
  ['/', '홈'],
  ['/market', '실시간 시장'],
  ['/strategies', '전략 관찰'],
  ['/performance', '공개 성과'],
] as const;

export function PublicHeader() {
  const pathname = usePathname();
  return (
    <header className="sticky top-0 z-30 border-b border-[#1E2536] bg-[#0B0E14]/90 backdrop-blur">
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3.5 sm:px-6">
        <Link href="/" className="flex items-center gap-2.5 text-sm font-semibold tracking-tight text-zinc-100">
          <span className="h-2.5 w-2.5 rounded-full bg-emerald-400" />
          <span>BSB Quant Platform</span>
        </Link>
        <nav className="flex flex-wrap items-center gap-1.5 text-xs sm:text-sm">
          {links.map(([href, label]) => {
            const active = pathname === href;
            return (
              <Link
                key={href}
                href={href}
                className={`rounded-lg px-3 py-1.5 font-medium transition ${
                  active ? 'bg-zinc-100 text-zinc-950' : 'text-zinc-400 hover:bg-[#121620] hover:text-zinc-100'
                }`}
              >
                {label}
              </Link>
            );
          })}
          <Link
            href="/login"
            className="ml-1 rounded-lg border border-[#2A344B] bg-[#121620] px-3 py-1.5 font-medium text-zinc-200 hover:border-zinc-400"
          >
            운영자 로그인
          </Link>
        </nav>
      </div>
    </header>
  );
}

export function PublicFooter() {
  return (
    <footer className="mt-16 border-t border-[#1E2536] bg-[#0B0E14] py-8 text-xs text-zinc-500">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-4 sm:px-6">
        <p>BSB Quantitative Market Observation & Paper Strategy Platform · 공개 화면은 가상 시뮬레이션 통계만 제공합니다.</p>
        <p className="font-mono">Investment Disclaimer: Not financial advice.</p>
      </div>
    </footer>
  );
}
