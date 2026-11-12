import {GET, PATCH} from './route';
const context = (path:string) => ({params:Promise.resolve({path:path.split('/')})});
afterEach(()=>{jest.restoreAllMocks();delete process.env.MANUAL_API_URL;});
test('forwards only local reads without caching',async()=>{
 const fetcher=jest.spyOn(globalThis,'fetch').mockResolvedValue(Response.json({status:'ok'}));
 const response=await GET(new Request('http://127.0.0.1:13000/api/manual/health'),context('health'));
 expect(response.status).toBe(200);expect(await response.json()).toEqual({status:'ok'});
 expect(fetcher.mock.calls[0][0].toString()).toBe('http://127.0.0.1:18000/api/health');
});
test('blocks cross-origin writes before contacting API',async()=>{
 const fetcher=jest.spyOn(globalThis,'fetch');
 const response=await PATCH(new Request('http://127.0.0.1:13000/api/manual/settings',{method:'PATCH',headers:{origin:'https://other.example','content-type':'application/json'},body:'{}'}),context('settings'));
 expect(response.status).toBe(403);expect(fetcher).not.toHaveBeenCalled();
});
test('rejects non-local upstream configuration',async()=>{
 process.env.MANUAL_API_URL='https://other.example';
 const response=await GET(new Request('http://127.0.0.1:13000/api/manual/health'),context('health'));
 expect(response.status).toBe(503);
});
test('offline upstream stays unavailable instead of creating fake data',async()=>{
 jest.spyOn(globalThis,'fetch').mockRejectedValue(new Error('offline'));
 const response=await GET(new Request('http://127.0.0.1:13000/api/manual/health'),context('health'));
 expect(response.status).toBe(503);
});
test('unknown routes and non-local hosts are blocked',async()=>{
 expect((await GET(new Request('http://127.0.0.1:13000/api/manual/orders'),context('orders'))).status).toBe(404);
 expect((await GET(new Request('http://other.example/api/manual/health'),context('health'))).status).toBe(403);
});
