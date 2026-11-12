import { GET } from './route';
test('web health identifies mock paper mode', async () => { expect(await GET().json()).toMatchObject({status:'ok',mode:'paper',dataSource:'mock',tradingEnabled:false}); });
