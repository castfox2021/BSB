import { NextRequest, NextResponse } from 'next/server';

export function middleware(req: NextRequest) {
  const pathname = req.nextUrl.pathname;

  // 허용 경로 (정적 자산, robots, 파비콘, 헬스체크 등)
  if (
    pathname.startsWith('/_next') ||
    pathname === '/robots.txt' ||
    pathname === '/favicon.ico' ||
    pathname === '/icon.svg' ||
    pathname === '/api/health'
  ) {
    return NextResponse.next();
  }

  const authUser = process.env.BSB_AUTH_USER || 'admin';
  const authPass = process.env.BSB_AUTH_PASSWORD || 'bsb1234!';

  const basicAuth = req.headers.get('authorization');

  if (basicAuth) {
    const authValue = basicAuth.split(' ')[1];
    if (authValue) {
      try {
        const decoded = atob(authValue);
        const [user, pwd] = decoded.split(':');
        if (user === authUser && pwd === authPass) {
          return NextResponse.next();
        }
      } catch {
        // base64 decode fail -> 401
      }
    }
  }

  return new NextResponse('접속 권한이 필요합니다 (Authentication Required)', {
    status: 401,
    headers: {
      'WWW-Authenticate': 'Basic realm="BSB Secure Signal Board"',
    },
  });
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico|icon.svg).*)'],
};
