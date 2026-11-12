const routes = new Set(['board','board/preferences','board/comparison','trading/candidates','trading/positions','trading/chart','trading/journal','trading/journal.csv','trading/performance','health','settings','market/status','market/candidates','market/scanner','signals','positions','trades','risk/status','manual-fills','lab/overview','lab/versions','lab/datasets','lab/runs','lab/promote','lab/pause','private/status','private/accounts','private/orders','private/journal','private/chance','private/sync','private/reconcile','private/notes','trading/control','orders/status','orders/activate','orders/stop','orders/preview','orders/submit','orders/recover','automation/status','automation/activate','automation/stop','history/signals','history/orders','history/trades','history/journal','history/private-orders','history/automation']);
type Context = {params: Promise<{path:string[]}>};
async function forward(request: Request, context: Context) {
 const incoming = new URL(request.url);
 const allowedHosts = new Set([
  'localhost',
  '127.0.0.1',
  'careerbook.kr',
  'www.careerbook.kr',
  ...(process.env.BSB_ALLOWED_HOSTS?.split(',').map(h=>h.trim().toLowerCase())??[])
 ]);
 const rawHost = (request.headers.get('host') ?? incoming.host).split(':')[0].toLowerCase();
 const isIp = /^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(rawHost) || /^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(incoming.hostname);
 if (!allowedHosts.has(incoming.hostname.toLowerCase()) && !allowedHosts.has(rawHost) && !isIp && process.env.BSB_ALLOW_ALL_HOSTS !== '1') {
  return Response.json({error:'host access denied'},{status:403});
 }
 const proto = request.headers.get('x-forwarded-proto') ?? (incoming.protocol.replace(':','') || 'http');
 const host = request.headers.get('host') ?? incoming.host;
 const browserOrigin = `${proto}://${host}`;
 const path = (await context.params).path.join('/');
 const reviewRead = /^board\/trades\/[a-f0-9]{64}$/.test(path) || /^trading\/positions\/[a-f0-9]{24}\/(replay|reasoning|reviews)$/.test(path);
 const reviewWrite = (request.method==='PATCH' && /^trading\/positions\/[a-f0-9]{24}\/journal$/.test(path)) || (request.method==='POST' && /^trading\/positions\/[a-f0-9]{24}\/review$/.test(path));
 if ((!reviewRead && !reviewWrite && !routes.has(path) && !/^lab\/jobs\/[a-zA-Z0-9-]+$/.test(path) && !/^market\/candles\/KRW-[A-Z0-9]+$/.test(path)) || (request.method !== 'GET' && !reviewWrite && !['board/preferences','settings','manual-fills','lab/versions','lab/datasets','lab/runs','lab/promote','lab/pause','private/sync','private/reconcile','private/notes','trading/control','orders/activate','orders/stop','orders/preview','orders/submit','orders/recover','automation/activate','automation/stop'].includes(path))) return Response.json({error:'unsupported route'},{status:404});
 if (request.method !== 'GET' && (request.headers.get('origin') !== browserOrigin || !request.headers.get('content-type')?.startsWith('application/json'))) return Response.json({error:'same-origin JSON required'},{status:403});
 const base = new URL(process.env.MANUAL_API_URL ?? 'http://127.0.0.1:18000');
 if (!['127.0.0.1','localhost'].includes(base.hostname) || base.protocol !== 'http:' || base.username || base.password || base.pathname !== '/') return Response.json({error:'invalid local API URL'},{status:503});
 try {
  const response = await fetch(new URL(`/api/${path}${incoming.search}`,base), {
   method:request.method, cache:'no-store', signal:AbortSignal.timeout(path.startsWith('orders/')||reviewWrite?25000:10000),
   headers:{'content-type':'application/json','origin':browserOrigin,'x-bsb-local':'1'},
   body:request.method !== 'GET' ? await request.text() : undefined,
  });
  return new Response(await response.text(),{status:response.status,headers:{'content-type':response.headers.get('content-type')??'application/json','cache-control':'no-store',...(path==='trading/journal.csv'?{'content-disposition':response.headers.get('content-disposition')??'attachment; filename=bsb-journal.csv'}:{})}});
 } catch { return Response.json({error:'로컬 API에 연결되지 않았습니다. 신호·손절 감시는 작동하지 않습니다.'},{status:503}); }
}
export const GET = forward;
export const PATCH = forward;

export const POST = forward;
