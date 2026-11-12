'use client';
import {useEffect,useRef,useState} from 'react';
import QuietBoard from './quiet-board';
import QuietDetail from './quiet-details';
import QuietSettings from './quiet-settings';
import ManualFillPanel from './manual-fill-panel';
import StrategyComparison from './strategy-comparison';
import StrategyLab from './strategy-lab';
import TradingWorkspace from './trading-review';
import FullMarketScanner from './full-market-scanner';
import PrivateAccount from './private-account';
import ReversionPanel,{Reversion} from './reversion-panel';
import {api,kst} from './trading-types';
import {Board,PaperTrade,Holding,signedMoney,pnlColor} from './quiet-types';
const menus=[['board','조용한 신호판'],['chart','매매 차트'],['journal','매매노트'],['strategy','전략 분석'],['settings','시스템 설정']];
export default function Home(){
 const [followTrade,setFollowTrade]=useState<PaperTrade|null>(null);
 const [tab,setTab]=useState('board'),[board,setBoard]=useState<Board|null>(null),[error,setError]=useState(''),[buyPage,setBuyPage]=useState(1),[sellPage,setSellPage]=useState(1),[revision,setRevision]=useState(0),[trade,setTrade]=useState<PaperTrade|null>(null),[symbol,setSymbol]=useState('KRW-BTC'),[journalTab,setJournalTab]=useState('paper');
 useEffect(()=>{let active=true;let loading=false;const load=async()=>{if(loading)return;loading=true;try{const result=await api<Board>(`board?buy_page=${buyPage}&sell_page=${sellPage}`);if(active){setBoard(result);setError('');}}catch{if(active)setError('신호판 연결이 끊겼습니다. 표시된 이전 시세를 현재가로 사용하지 마세요.');}finally{loading=false;}};load();const timer=setInterval(load,2000);return()=>{active=false;clearInterval(timer);};},[buyPage,sellPage,revision]);
 const selectedTradeId=useRef<string|null>(null);
 const selectTrade=(t:PaperTrade)=>{selectedTradeId.current=t.id;setTrade(t);setSymbol(t.symbol);setTab('chart');api<PaperTrade>(`board/trades/${t.id}`).then(detail=>{if(selectedTradeId.current===t.id)setTrade(detail);}).catch(()=>{if(selectedTradeId.current===t.id)setError('가상 체결 상세 기록을 읽을 수 없습니다.');});};
 const selectHolding=(p:Holding)=>{selectedTradeId.current=p.entry_trade_id??null;setTrade(null);setSymbol(p.symbol);setTab('chart');if(p.entry_trade_id)api<PaperTrade>(`board/trades/${p.entry_trade_id}`).then(t=>{if(selectedTradeId.current===p.entry_trade_id)setTrade(t);}).catch(()=>setError('가상보유의 매수 이유를 읽을 수 없습니다.'));};
 return <main className="mx-auto max-w-7xl px-4 py-6 text-slate-100 sm:px-6"><header className="border-b border-slate-700 pb-5"><div className="flex flex-wrap items-start justify-between gap-4"><div><p className="text-sm tracking-widest text-slate-400">BITHUMB · 조용한 수동 추종</p><h1 className="mt-2 text-3xl font-bold">가상매매 신호판</h1><p className="mt-2 text-sm text-slate-400">프로그램은 가상매매, 실제 주문은 빗썸에서 직접 실행합니다.</p></div><div className="flex flex-wrap gap-2 text-sm"><span className="rounded-full border border-emerald-800 bg-emerald-950/40 px-4 py-2 text-emerald-300">가상매매</span><span className="rounded-full border border-slate-600 px-4 py-2">실전 직접 매매 · API 주문 꺼짐</span></div></div><div className="mt-5 flex flex-wrap items-center gap-x-10 gap-y-3"><div><span className="text-sm text-slate-400">오늘 가상 실현손익</span><strong className={`ml-3 text-2xl ${pnlColor(board?.summary.today_realized_krw)}`}>{signedMoney(board?.summary.today_realized_krw)}</strong></div><div><span className="text-sm text-slate-400">가상보유</span><strong className="ml-3 text-2xl">{board?.summary.holding_count??'—'}종목</strong></div><p className="text-sm text-slate-400">{board?.selected_strategy_name??'전략 확인 중'} · {board?.selected_version?.id.slice(0,8)??'—'} · 독립 가상 자본 {Number(board?.summary?.initial_capital_krw??'150000').toLocaleString('ko-KR')}원</p></div></header><nav aria-label="주 메뉴" className="my-5 flex flex-wrap gap-2">{menus.map(([id,label])=><button key={id} aria-current={tab===id?'page':undefined} className={`rounded-xl px-4 py-3 text-base font-semibold ${tab===id?'bg-slate-200 text-slate-950':'bg-slate-900 text-slate-300'}`} onClick={()=>setTab(id)}>{label}</button>)}</nav>
 {tab==='board'&&board&&!error&&<div className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm"><span className={board.runtime?.running?'text-emerald-300':'text-amber-200'}>{board.runtime?.running?'●':'○'} {board.runtime?.message??'감시 상태 확인 중'}</span><span className="text-slate-400">마지막 분석 {kst(board.runtime?.last_analysis_at)} · 시세 연결 {board.runtime?.connected_markets??0}종목</span></div>}
 {error&&<p className="mb-4 rounded-lg border border-amber-900 p-3 text-sm text-amber-200">{error}</p>}
 {tab==='board'&&<div className="mb-5 grid"><ReversionPanel data={(board as (Board&{reversion?:Reversion|null})|null)?.reversion}/></div>}
 {tab==='board'&&<QuietBoard board={error?null:board} onTrade={selectTrade} onHolding={selectHolding} onBuyPage={setBuyPage} onSellPage={setSellPage}/>}
 {tab==='chart'&&<div className="space-y-5"><QuietDetail trade={trade} symbol={symbol} onRecord={t=>{setFollowTrade(t);setJournalTab('record');setTab('journal');}}/><details className="rounded-xl border border-slate-700 p-4"><summary className="cursor-pointer text-lg">전체 종목·상세 분석 보기</summary><FullMarketScanner onPick={s=>{setTrade(null);setSymbol(s);window.scrollTo({top:0,behavior:'smooth'});}}/></details></div>}
 {tab==='journal'&&<div><div className="mb-5 flex flex-wrap gap-2">{[['paper','가상매매 이력'],['live','실전 매매노트·복기'],['record','실제 체결 수동 등록'],['account','실제 계좌 조회']].map(([id,label])=><button key={id} className={`rounded-xl border border-slate-600 px-4 py-3 ${journalTab===id?'bg-slate-700':''}`} onClick={()=>setJournalTab(id)}>{label}</button>)}</div>{journalTab==='paper'&&<QuietBoard board={board} onTrade={selectTrade} onHolding={selectHolding} onBuyPage={setBuyPage} onSellPage={setSellPage}/>} {journalTab==='live'&&<TradingWorkspace mode="journal" symbol={symbol} ordersEnabled={false} onOrder={()=>{}}/>}{journalTab==='record'&&<ManualFillPanel followTrade={followTrade} onUnlink={()=>setFollowTrade(null)}/>}{journalTab==='account'&&<PrivateAccount/>}</div>}
 {tab==='strategy'&&<><StrategyComparison/><StrategyLab/></>}
 {tab==='settings'&&<QuietSettings board={board} onSaved={()=>{setBuyPage(1);setSellPage(1);setRevision(v=>v+1);}}/>}
 </main>;
}
