const routes = new Set([
  'board',
  'board/preferences',
  'board/comparison',
  'trading/candidates',
  'trading/positions',
  'trading/chart',
  'trading/journal',
  'trading/journal.csv',
  'trading/performance',
  'trading/exit-policy',
  'trading/exit-events',
  'trading/exit-comparison',
  'trading/control',
  'health',
  'settings',
  'reversion/config',
  'market/status',
  'market/candidates',
  'market/scanner',
  'signals',
  'positions',
  'trades',
  'risk/status',
  'manual-fills',
  'lab/overview',
  'lab/versions',
  'lab/datasets',
  'lab/runs',
  'lab/promote',
  'lab/pause',
  'private/status',
  'private/accounts',
  'private/orders',
  'private/journal',
  'private/chance',
  'private/sync',
  'private/reconcile',
  'private/notes',
  'orders/status',
  'orders/activate',
  'orders/stop',
  'orders/preview',
  'orders/submit',
  'orders/recover',
  'automation/status',
  'automation/activate',
  'automation/stop',
  'history/signals',
  'history/orders',
  'history/trades',
  'history/journal',
  'history/private-orders',
  'history/automation',
  'admin/audit-logs',
  'admin/members',
  'admin/pg-migration/preview',
  'admin/strategy-reports',
  'admin/strategy-reports/preview',
  'admin/strategy-comparisons',
  'admin/strategy-proposals/preview',
  'analytics/sync',
  'analytics/sync/status',
  'analytics/import',
  'analytics/summary',
  'analytics/trades',
  'analytics/performance',
  'analytics/strategies',
  'analytics/risk',
  'analytics/reconciliation',
  'analytics/baseline',
  'analytics/recalculate',
  'analytics/export',
  'auth/login',
  'auth/register',
  'auth/logout',
  'auth/session',
  'auth/mfa/setup',
  'auth/mfa/enable',
  'auth/mfa/disable',
  'member/credentials',
  'member/sync',
  'member/analytics',
  'member/accounts',
  'member/trades',
  'member/export',
  'member/password',
  'member/sessions',
  'member/sessions/revoke-others',
  'member/account/delete',
  'public/market/summary',
  'public/market/tickers',
  'public/exchanges/tickers',
  'public/strategies/overview',
  'public/performance/summary',
  'public/platform/status',
]);

type Context = { params: Promise<{ path: string[] }> };

async function forward(request: Request, context: Context) {
  const incoming = new URL(request.url);
  const allowedHosts = new Set([
    'localhost',
    '127.0.0.1',
    'careerbook.kr',
    'www.careerbook.kr',
    ...(process.env.BSB_ALLOWED_HOSTS?.split(',').map((h) => h.trim().toLowerCase()) ?? []),
  ]);
  const rawHost = (request.headers.get('host') ?? incoming.host).split(':')[0].toLowerCase();
  const isIp =
    /^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(rawHost) ||
    /^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(incoming.hostname);
  if (
    !allowedHosts.has(incoming.hostname.toLowerCase()) &&
    !allowedHosts.has(rawHost) &&
    !isIp &&
    process.env.BSB_ALLOW_ALL_HOSTS !== '1'
  ) {
    return Response.json({ error: 'host access denied' }, { status: 403 });
  }
  const proto = request.headers.get('x-forwarded-proto') ?? (incoming.protocol.replace(':', '') || 'http');
  const host = request.headers.get('host') ?? incoming.host;
  const browserOrigin = `${proto}://${host}`;
  const path = (await context.params).path.join('/');

  const dynamicAllowed =
    /^board\/trades\/[a-f0-9]{64}$/.test(path) ||
    /^trading\/positions\/[a-f0-9]{24}\/(replay|reasoning|reviews)$/.test(path) ||
    /^trading\/positions\/[a-f0-9]+\/exit-state$/.test(path) ||
    /^trading\/positions\/[a-f0-9]{24}\/journal$/.test(path) ||
    /^trading\/positions\/[a-f0-9]{24}\/review$/.test(path) ||
    /^lab\/jobs\/[a-zA-Z0-9-]+$/.test(path) ||
    /^market\/candles\/KRW-[A-Z0-9]+$/.test(path) ||
    /^admin\/strategy-reports\/[a-f0-9]{32}(\/download)?$/.test(path) ||
    /^analytics\/trades\/[a-zA-Z0-9_-]+$/.test(path) ||
    /^analytics\/reconciliation\/[a-zA-Z0-9_-]+\/resolve$/.test(path) ||
    /^member\/credentials\/[a-f0-9]{32}$/.test(path);

  if (!dynamicAllowed && !routes.has(path)) {
    return Response.json({ error: 'unsupported route' }, { status: 404 });
  }

  if (
    request.method !== 'GET' &&
    (request.headers.get('origin') !== browserOrigin ||
      (request.method !== 'DELETE' && !request.headers.get('content-type')?.startsWith('application/json')))
  ) {
    return Response.json({ error: 'same-origin JSON required' }, { status: 403 });
  }

  const base = new URL(process.env.MANUAL_API_URL ?? 'http://127.0.0.1:18000');
  if (
    !['127.0.0.1', 'localhost'].includes(base.hostname) ||
    base.protocol !== 'http:' ||
    base.username ||
    base.password ||
    base.pathname !== '/'
  ) {
    return Response.json({ error: 'invalid local API URL' }, { status: 503 });
  }

  const cookieHeader = request.headers.get('cookie') ?? '';
  const sessionMatch = cookieHeader.match(/(?:^|;\s*)bsb_session=([^;]+)/);
  const sessionToken = sessionMatch ? decodeURIComponent(sessionMatch[1]) : '';
  try {
    const response = await fetch(new URL(`/api/${path}${incoming.search}`, base), {
      method: request.method,
      cache: 'no-store',
      signal: AbortSignal.timeout(path.startsWith('orders/') ? 25000 : 15000),
      headers: {
        'content-type': 'application/json',
        origin: browserOrigin,
        'x-bsb-local': '1',
        ...(sessionToken ? { 'x-bsb-admin-session': sessionToken } : {}),
      },
      body: request.method !== 'GET' ? await request.text() : undefined,
    });

    const buf = await response.arrayBuffer();
    const resHeaders = new Headers({
      'content-type': response.headers.get('content-type') ?? 'application/json',
      'cache-control': 'no-store',
    });
    const disp = response.headers.get('content-disposition');
    if (disp) resHeaders.set('content-disposition', disp);

    if (path === 'auth/login' && response.ok) {
      try {
        const parsed = JSON.parse(new TextDecoder().decode(buf));
        if (parsed.token) {
          const isHttps = proto === 'https';
          resHeaders.append(
            'set-cookie',
            `bsb_session=${parsed.token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000${isHttps ? '; Secure' : ''}`
          );
          delete parsed.token;
          return new Response(JSON.stringify(parsed), { status: response.status, headers: resHeaders });
        }
      } catch {
        /* pass through */
      }
    }
    if (path === 'auth/logout') {
      resHeaders.append('set-cookie', 'bsb_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0');
    }

    return new Response(buf, { status: response.status, headers: resHeaders });
  } catch {
    return Response.json(
      { error: '로컬 API에 연결되지 않았습니다. 신호·손절 감시는 작동하지 않습니다.' },
      { status: 503 }
    );
  }
}

export const GET = forward;
export const PATCH = forward;
export const POST = forward;
export const PUT = forward;
export const DELETE = forward;
