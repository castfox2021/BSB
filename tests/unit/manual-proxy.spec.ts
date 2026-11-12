import {PATCH,GET,POST} from '../../apps/web/app/api/manual/[...path]/route';
const path='trading/positions/0123456789abcdef01234567/journal';
const context=(value=path)=>({params:Promise.resolve({path:value.split('/')})});
afterEach(()=>jest.restoreAllMocks());
test('uses browser Host when Next canonicalizes the request URL',async()=>{
 const fetch=jest.spyOn(globalThis,'fetch').mockResolvedValue(Response.json({saved:true}));
 const request=new Request(`http://localhost:13001/api/manual/${path}`,{method:'PATCH',headers:{host:'127.0.0.1:13001',origin:'http://127.0.0.1:13001','content-type':'application/json'},body:'{"note":"fixture","tags":[]}'});
 const response=await PATCH(request,context());
 expect(response.status).toBe(200);
 expect(await response.json()).toEqual({saved:true});
 expect(fetch.mock.calls[0][1]?.headers).toMatchObject({origin:'http://127.0.0.1:13001'});
});
test.each(['https://evil.example','http://localhost:13001','null'])('rejects foreign Origin %s without forwarding',async origin=>{
 const fetch=jest.spyOn(globalThis,'fetch');
 const response=await PATCH(new Request(`http://localhost:13001/api/manual/${path}`,{method:'PATCH',headers:{host:'127.0.0.1:13001',origin,'content-type':'application/json'},body:'{}'}),context());
 expect(response.status).toBe(403);expect(fetch).not.toHaveBeenCalled();
});
test('preserves CSV content type and download header',async()=>{
 jest.spyOn(globalThis,'fetch').mockResolvedValue(new Response('symbol,quantity\nKRW-TEST,1',{headers:{'content-type':'text/csv; charset=utf-8','content-disposition':'attachment; filename="journal.csv"'}}));
 const r=await GET(new Request('http://localhost:13001/api/manual/trading/journal.csv'),context('trading/journal.csv'));
 expect(r.headers.get('content-type')).toContain('text/csv');expect(r.headers.get('content-disposition')).toContain('journal.csv');
});
test('cannot mutate replay or unlisted exchange routes',async()=>{
 const fetch=jest.spyOn(globalThis,'fetch');
 for(const route of ['trading/positions/0123456789abcdef01234567/replay','withdrawals/coin','orders/cancel']){
  const r=await POST(new Request(`http://localhost:13001/api/manual/${route}`,{method:'POST',headers:{origin:'http://localhost:13001','content-type':'application/json'},body:'{}'}),context(route));
  expect(r.status).toBe(404);
 }
 expect(fetch).not.toHaveBeenCalled();
});
