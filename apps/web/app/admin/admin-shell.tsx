'use client';
import { ReactNode, useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { AdminStatusBar } from './components/admin-ui';
import { TOKENS } from './design-tokens';
import {
  AdminStatusContext,
  fetchUnifiedAdminStatus,
  INITIAL_ADMIN_STATUS,
  UnifiedAdminStatus,
} from './runtime-status';

const adminMenus = [
  { href: '/admin', label: '홈', match: (p: string) => p === '/admin' },
  { href: '/admin/market', label: '시장', match: (p: string) => p.startsWith('/admin/market') },
  {
    href: '/admin/trading',
    label: '거래',
    match: (p: string) => p.startsWith('/admin/trading') || p.startsWith('/admin/analytics'),
  },
  { href: '/admin/lab', label: '전략', match: (p: string) => p.startsWith('/admin/lab') },
  { href: '/admin/settings', label: '설정', match: (p: string) => p.startsWith('/admin/settings') },
] as const;

export default function AdminShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [status, setStatus] = useState<UnifiedAdminStatus>(INITIAL_ADMIN_STATUS);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  const refreshStatus = useCallback(async () => {
    const next = await fetchUnifiedAdminStatus();
    setStatus(next);
  }, []);

  useEffect(() => {
    let active = true;
    fetchUnifiedAdminStatus().then((next) => {
      if (active) setStatus(next);
    });
    const timer = setInterval(() => {
      fetchUnifiedAdminStatus().then((next) => {
        if (active) setStatus(next);
      });
    }, 5000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [pathname]);

  useEffect(() => {
    setMobileMenuOpen(false);
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
    <AdminStatusContext.Provider value={{ status, refreshStatus }}>
      <div className={`min-h-screen ${TOKENS.bg} pb-16 text-zinc-100 sm:pb-8`}>
        <header className="sticky top-0 z-30 border-b border-[#1E2536] bg-[#0B0E14]/95 backdrop-blur">
          <div className={`${TOKENS.container} py-3`}>
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-4">
                <Link href="/admin" className="text-base font-bold tracking-tight text-zinc-100 sm:text-lg">
                  BSB Admin
                </Link>
                <nav aria-label="관리자 주 메뉴" className="hidden items-center gap-1 sm:flex">
                  {adminMenus.map((m) => {
                    const active = m.match(pathname);
                    return (
                      <Link
                        key={m.href}
                        href={m.href}
                        className={`rounded-lg px-3.5 py-1.5 text-sm font-medium transition ${
                          active
                            ? 'bg-zinc-100 text-zinc-950'
                            : 'text-zinc-400 hover:bg-[#121620] hover:text-zinc-200'
                        }`}
                      >
                        {m.label}
                      </Link>
                    );
                  })}
                </nav>
              </div>
              <div className="flex items-center gap-2 text-xs">
                <Link
                  href="/"
                  className="hidden rounded-md border border-[#1E2536] bg-[#121620] px-3 py-1.5 text-zinc-300 hover:border-zinc-500 sm:inline-block"
                >
                  공개 홈
                </Link>
                <button
                  type="button"
                  onClick={logout}
                  className="rounded-md border border-[#1E2536] bg-[#121620] px-3 py-1.5 text-zinc-200 hover:border-zinc-500"
                >
                  로그아웃
                </button>
                <button
                  type="button"
                  aria-label="메뉴 열기"
                  aria-expanded={mobileMenuOpen}
                  onClick={() => setMobileMenuOpen((v) => !v)}
                  className="rounded-md border border-[#1E2536] bg-[#121620] px-2.5 py-1.5 text-zinc-200 sm:hidden"
                >
                  {mobileMenuOpen ? '닫기' : '메뉴'}
                </button>
              </div>
            </div>
            {mobileMenuOpen && (
              <nav aria-label="모바일 관리자 메뉴" className="mt-3 flex flex-wrap gap-1.5 border-t border-[#1E2536] pt-3 sm:hidden">
                {adminMenus.map((m) => {
                  const active = m.match(pathname);
                  return (
                    <Link
                      key={m.href}
                      href={m.href}
                      className={`rounded-lg px-3 py-1.5 text-xs font-medium ${
                        active ? 'bg-zinc-100 text-zinc-950' : 'border border-[#1E2536] bg-[#121620] text-zinc-300'
                      }`}
                    >
                      {m.label}
                    </Link>
                  );
                })}
                <Link href="/" className="rounded-lg border border-[#1E2536] bg-[#121620] px-3 py-1.5 text-xs text-zinc-400">
                  공개 홈
                </Link>
              </nav>
            )}
          </div>
          <AdminStatusBar status={status} />
        </header>

        <main className={`${TOKENS.container} py-6`}>{children}</main>

        {/* Mobile bottom navigation (navigation only — never order/save buttons) */}
        <nav
          aria-label="하단 빠른 메뉴"
          className="fixed inset-x-0 bottom-0 z-30 grid grid-cols-5 border-t border-[#1E2536] bg-[#0B0E14]/95 py-1.5 text-center text-[11px] backdrop-blur sm:hidden"
        >
          {adminMenus.map((m) => {
            const active = m.match(pathname);
            return (
              <Link
                key={m.href}
                href={m.href}
                className={`py-1 font-medium ${active ? 'text-zinc-100' : 'text-zinc-500'}`}
              >
                {m.label}
              </Link>
            );
          })}
        </nav>
      </div>
    </AdminStatusContext.Provider>
  );
}
