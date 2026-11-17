import { NextRequest, NextResponse } from 'next/server';

const COOKIE_NAME = 'bsb_session';

export async function middleware(req: NextRequest) {
  const pathname = req.nextUrl.pathname;

  // 1. 공개 페이지 및 공개 API / 정적 자산 허용
  if (
    pathname === '/' ||
    pathname === '/market' ||
    pathname === '/strategies' ||
    pathname === '/performance' ||
    pathname === '/login' ||
    pathname.startsWith('/_next') ||
    pathname === '/robots.txt' ||
    pathname === '/favicon.ico' ||
    pathname === '/icon.svg' ||
    pathname === '/api/health' ||
    pathname.startsWith('/api/manual/public/') ||
    pathname.startsWith('/api/manual/auth/')
  ) {
    return NextResponse.next();
  }

  // 2. 관리자 영역(/admin*, /api/manual/*)은 세션 쿠키 필수 (실제 토큰 유효성은 FastAPI가 검증)
  const sessionCookie = req.cookies.get(COOKIE_NAME)?.value;
  if (sessionCookie) {
    return NextResponse.next();
  }

  if (pathname.startsWith('/api/')) {
    return NextResponse.json({ error: '운영자 로그인이 필요합니다.' }, { status: 401 });
  }

  const loginUrl = req.nextUrl.clone();
  loginUrl.pathname = '/login';
  loginUrl.searchParams.set('next', pathname);
  return NextResponse.redirect(loginUrl);
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico|icon.svg).*)'],
};
