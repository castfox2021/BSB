import { NextRequest, NextResponse } from 'next/server';

const COOKIE_NAME = 'bsb_session';
const ONE_YEAR_SECONDS = 60 * 60 * 24 * 365;

// 사용자명/비밀번호 기반의 세션 검증 토큰 생성
async function getExpectedToken(user: string, pass: string): Promise<string> {
  const encoder = new TextEncoder();
  const data = encoder.encode(`${user}:${pass}:bsb-secure-session-salt-v1`);
  const hashBuffer = await crypto.subtle.digest('SHA-256', data);
  const hashArray = Array.from(new Uint8Array(hashBuffer));
  return hashArray.map((b) => b.toString(16).padStart(2, '0')).join('');
}

export async function middleware(req: NextRequest) {
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
  const expectedToken = await getExpectedToken(authUser, authPass);

  // 1. 쿠키 검사 (아이폰 등 모바일에서 앱을 다녀와도 로그인 상태가 유지되도록 함)
  const sessionCookie = req.cookies.get(COOKIE_NAME)?.value;
  if (sessionCookie && sessionCookie === expectedToken) {
    return NextResponse.next();
  }

  // 2. HTTP Basic Auth 검사
  const basicAuth = req.headers.get('authorization');
  if (basicAuth) {
    const authValue = basicAuth.split(' ')[1];
    if (authValue) {
      try {
        const decoded = atob(authValue);
        const [user, pwd] = decoded.split(':');
        if (user === authUser && pwd === authPass) {
          // 인증 성공 시 1년 동안 유지되는 쿠키를 세팅하여 이후 로그인 창이 다시 뜨지 않도록 처리
          const res = NextResponse.next();
          res.cookies.set({
            name: COOKIE_NAME,
            value: expectedToken,
            httpOnly: true,
            secure: false, // http(포트 13000) 환경에서도 동작하도록 false
            sameSite: 'lax',
            path: '/',
            maxAge: ONE_YEAR_SECONDS,
          });
          return res;
        }
      } catch {
        // base64 decode fail -> 401
      }
    }
  }

  // 3. 미인증 시 401 반환 (로그인 팝업 요청)
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
