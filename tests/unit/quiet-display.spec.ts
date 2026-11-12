import {priceText,signedMoney,signedRate,elapsed} from '../../apps/web/app/quiet-types';
test('shows positive and negative realized amounts with explicit signs',()=>{
 expect(signedMoney('850')).toBe('+850원');expect(signedMoney('-300')).toBe('-300원');expect(signedMoney(null)).toBe('—');
 expect(signedRate('0.032')).toBe('+3.20%');expect(signedRate('-0.007')).toBe('-0.70%');
});
test('small and low-price coins remain readable without rounding to zero',()=>{
 expect(priceText('23.34')).toBe('23.34');expect(priceText('0.00000000123')).not.toBe('0');expect(priceText(null)).toBe('—');
});
test('elapsed time is readable at a glance',()=>{
 expect(elapsed(25)).toBe('25초');expect(elapsed(150)).toBe('2분 30초');expect(elapsed(3720)).toBe('1시간 2분');
});
