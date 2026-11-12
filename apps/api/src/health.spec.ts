import { mode } from './health';
describe('paper-only configuration', () => {
 test('labels mock data and disables trading', () => { expect(mode('mock')).toEqual({mode:'paper',dataSource:'mock',tradingEnabled:false}); });
 test('rejects live data configuration before connector exists', () => { expect(() => mode('live')).toThrow(); });
});
