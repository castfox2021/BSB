export type Policy={initial_capital_krw:string;max_entry_krw:string;minimum_cash_reserve_krw:string;max_positions:number;stop_loss_rate:string;take_profit_rate:string;daily_loss_alert_krw:string;max_holding_seconds:number;sound_enabled:boolean;browser_alerts_enabled:boolean;execution_fee_rate:string;execution_slippage_rate:string;costs_confirmed:boolean};
export type Signal={id:string;symbol:string;type:string;strategy_version:string;reference_price:string|null;reasons:string[];generated_at:string;expires_at:string;data_at:string|null;status:string};
export type Position={symbol:string;quantity:string;average_entry_price:string;stop_reference:string;target_reference:string;net_estimate_krw:string|null;mark_price:string|null};
export type Candidate={symbol:string;price:string;score:string;volume_ratio:string;spread:string|null;ready:boolean;reason:string;data_at:string};
export type Snapshot={at:string;market:{source:string;connected_markets:number;market_count:number;last_received_at:string|null;seeded_markets:number;recovery_pending:number;clock_skew_detected:boolean;lag_ms_min:number|null;lag_ms_max:number|null;metrics:{signal_compute_ms:number|null;processing_ms?:number;events:number;reconnects:number};error:string|null};candidates:{items:Candidate[]};signals:{items:Signal[]};positions:{items:Position[];cash_krw:string;realized_net_krw:string};risk:{buy_signals_enabled:boolean;reasons:string[];daily_realized_plus_current_estimate_krw:string;active_version:string|null}};
export type Version={id:string;kind:string;params:Record<string,string|number>};
export type Metrics={net_profit?:string;net_realized_krw?:string;win_rate:string|null;payoff_ratio:string|null;mdd?:string;sampled_equity_mdd?:string|null;expectancy_krw:string|null;fees_krw:string;slippage_krw:string|null;trades_count?:number;observations?:number};
export type Report={id:string;version_id:string;symbol:string;training:Metrics;validation:Metrics;promotion_eligible:boolean;gate_reason:string;created_at:string;combinations:number;train_end:number;validation_start:number};
export type LabOverview={versions:Version[];active_version:string|null;datasets:{id:string;symbol:string}[];reports:Report[];jobs:{id:string;status:string;error:string|null}[];paper_accounts:{version_id:string;cash:string;realized:string;position:{holdings:Record<string,{quantity:string}>}}[];comparison:{manual_metrics:Metrics;paper_metrics:Record<string,Metrics>;manual_attribution_coverage:number;note:string}};
export const money=(value?:string|null)=>value==null?'—':`${Number(value).toLocaleString('ko-KR',{maximumFractionDigits:2})}원`;
export const pct=(value?:string|null)=>value==null?'—':`${(Number(value)*100).toFixed(2)}%`;
export const kst=(value?:string|null)=>value?new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',dateStyle:'short',timeStyle:'medium',hour12:false}).format(new Date(value)):'—';
export const quantityText=(v?:string|number|null)=>{
  if(v==null||v==='')return '—';
  const n=Number(v);
  if(isNaN(n))return String(v);
  if(n===0)return '0';
  if(Math.abs(n)>=1){
    return new Intl.NumberFormat('ko-KR',{maximumFractionDigits:4}).format(n);
  }
  return new Intl.NumberFormat('ko-KR',{maximumSignificantDigits:6}).format(n);
};
export async function api<T>(path:string,method='GET',body?:unknown):Promise<T>{
 const response=await fetch(`/api/manual/${path}`,{method,cache:'no-store',headers:{'content-type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
 const data=await response.json();
 if(!response.ok)throw new Error(typeof(data.error??data.detail)==='string'?(data.error??data.detail):JSON.stringify(data.error??data.detail??response.status));
 return data as T;
}

export function openBithumb(symbol: string) {
  if (typeof window === 'undefined') return;
  const coin = symbol.replace(/^KRW-/, '').toUpperCase();
  const webUrl = `https://www.bithumb.com/react/trade/order/${coin}-KRW`;

  const ua = navigator.userAgent || '';
  const isAndroid = /Android/i.test(ua);
  const isIOS = /iPhone|iPad|iPod/i.test(ua);
  const isMobile = isAndroid || isIOS;

  if (!isMobile) {
    // PC (데스크톱): 새 탭으로 빗썸 웹 주문창 열기
    window.open(webUrl, '_blank', 'noopener,noreferrer');
    return;
  }

  // 모바일 (Android): 빗썸 공식 App Link Intent (도메인 전체 경로를 전달하여 해당 코인 주문창으로 직접 진입)
  if (isAndroid) {
    const intentUrl = `intent://www.bithumb.com/react/trade/order/${coin}-KRW#Intent;scheme=https;package=com.btckorea.bithumb;S.browser_fallback_url=${encodeURIComponent(webUrl)};end`;
    window.location.href = intentUrl;
    return;
  }

  // 모바일 (iOS): 코인 심볼 파라미터가 포함된 커스텀 스킴 시도 후 웹 폴백
  if (isIOS) {
    const appUrl = `bithumb://trade?symbol=${coin}_KRW`;
    const start = Date.now();
    window.location.href = appUrl;

    setTimeout(() => {
      if (Date.now() - start < 1500 && !document.hidden) {
        window.location.href = webUrl;
      }
    }, 1200);
    return;
  }

  window.location.href = webUrl;
}
