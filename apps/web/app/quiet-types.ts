export type PaperTrade={elapsed_basis?:'SIGNAL'|'FILL_FALLBACK';derived_fill_price?:boolean;id:string;symbol:string;side:'BUY'|'SELL';at:string;signal_at?:string;fill_price:string|null;quantity?:string;current_price:string|null;last_price:string|null;market_fresh:boolean;market_at:string|null;price_change_rate:string|null;elapsed_seconds:number;signal_status:'RECENT'|'CHASE_CAUTION'|'OLD';reason_summary:string;amount_krw:string|null;net_pnl?:string;fee_krw:string;slippage_krw:string;version_id:string;reasons?:string[];reason?:string;strategy_snapshot?:unknown;basis?:unknown;legacy_missing_execution_fields:boolean;simulated:true;actual_exchange_fill:false};
export type Holding={entry_trade_id?:string|null;id:string;symbol:string;quantity:string;entry_price:string;current_price:string|null;last_price:string|null;market_fresh:boolean;net_estimate_krw:string|null;net_return_rate:string|null;opened_at:string;version_id:string;peak_price?:string|null;trail_active?:boolean};
export type Preferences={follow_version:string|null;recent_seconds:number;stale_seconds:number;chase_rate:string};
export type Board={runtime?:{state:string;message:string;running:boolean;last_analysis_at:string|null;connected_markets:number;ready_candidates:number;selected_check:{checked_at:string;reasons:Record<string,number>;last_evaluations?:{symbol:string;evaluated_at:string;passed:boolean;reasons:string[]}[]}|null};at:string;operation_mode:'QUIET_FOLLOW';trading_mode:'PAPER';live_execution_allowed:false;selected_strategy_name:string;selected_version:{id:string;kind:string;params?:Record<string,string|number>;created_at?:string}|null;preferences:Preferences;versions:{id:string;kind:string}[];summary:{today_realized_krw:string;holding_count:number;cash_krw:string;initial_capital_krw:string};buys:{items:PaperTrade[];page:number;pages:number;total:number};sells:{items:PaperTrade[];page:number;pages:number;total:number};holdings:Holding[]};
export const signedMoney=(value:string|null|undefined)=>value==null?'—':`${Number(value)>0?'+':''}${Number(value).toLocaleString('ko-KR',{maximumFractionDigits:2})}원`;
export const signedRate=(value:string|null|undefined)=>value==null?'—':`${Number(value)>0?'+':''}${(Number(value)*100).toFixed(2)}%`;
export const elapsed=(seconds:number)=>seconds<60?`${seconds}초`:seconds<3600?`${Math.floor(seconds/60)}분 ${seconds%60}초`:`${Math.floor(seconds/3600)}시간 ${Math.floor(seconds%3600/60)}분`;
export const pnlColor=(v:string|null|undefined)=>v==null?'text-zinc-500':Number(v)>0?'text-rose-400':Number(v)<0?'text-sky-400':'text-zinc-200';

export const priceText=(v:string|null|undefined)=>v==null?'—':new Intl.NumberFormat('ko-KR',{maximumSignificantDigits:10}).format(Number(v));

export const quantityText=(v:string|number|null|undefined)=>{
  if(v==null||v==='')return '—';
  const n=Number(v);
  if(isNaN(n))return String(v);
  if(n===0)return '0';
  if(Math.abs(n)>=1){
    return new Intl.NumberFormat('ko-KR',{maximumFractionDigits:4}).format(n);
  }
  return new Intl.NumberFormat('ko-KR',{maximumSignificantDigits:6}).format(n);
};
