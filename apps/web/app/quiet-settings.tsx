'use client';
import {useEffect,useState} from 'react';
import {api,Policy} from './trading-types';
import {Board,Preferences} from './quiet-types';

const input='rounded border border-slate-600 bg-slate-900 p-3';
const btnQuick='rounded border border-slate-700 bg-slate-800/80 px-2.5 py-1 text-xs text-slate-300 hover:border-slate-500 hover:bg-slate-700 transition';

export default function QuietSettings({board,onSaved}:{board:Board|null;onSaved:()=>void}){
  const [prefs,setPrefs]=useState<Preferences|null>(null);
  const [prefMessage,setPrefMessage]=useState('');
  const [policyMessage,setPolicyMessage]=useState('');
  const [busy,setBusy]=useState(false);
  const [policy,setPolicy]=useState<Policy|null>(null);
  const [form,setForm]=useState<{
    initial_capital_krw:string;
    max_entry_krw:string;
    minimum_cash_reserve_krw:string;
    daily_loss_alert_krw:string;
    max_positions:number;
  } | null>(null);
  const [system,setSystem]=useState<unknown>(null);

  useEffect(()=>{
    if(board) setPrefs(board.preferences);
  },[board?.preferences.follow_version,board?.preferences.recent_seconds,board?.preferences.stale_seconds,board?.preferences.chase_rate]);

  useEffect(()=>{
    api<Policy>('settings')
      .then(p=>{
        setPolicy(p);
        setForm({
          initial_capital_krw:p.initial_capital_krw??'150000',
          max_entry_krw:p.max_entry_krw??'50000',
          minimum_cash_reserve_krw:p.minimum_cash_reserve_krw??'50000',
          daily_loss_alert_krw:p.daily_loss_alert_krw??'3000',
          max_positions:p.max_positions??2,
        });
      })
      .catch(()=>setPolicyMessage('설정을 읽을 수 없습니다.'));
    api('market/status').then(setSystem).catch(()=>setSystem(null));
  }, []);

  async function savePrefs(){
    if(!prefs) return;
    setBusy(true);
    try{
      await api('board/preferences','PATCH',prefs);
      setPrefMessage('신호판 표시 기준을 저장했습니다. 전략과 위험관리 조건은 변경하지 않았습니다.');
      onSaved();
    }catch(e){
      setPrefMessage(e instanceof Error?e.message:'설정 저장 실패');
    }finally{
      setBusy(false);
    }
  }

  // 자본금 및 주문 기준 검증
  const initialCap = Number(form?.initial_capital_krw || 0);
  const maxEntry = Number(form?.max_entry_krw || 0);
  const minCash = Number(form?.minimum_cash_reserve_krw || 0);
  const dailyLoss = Number(form?.daily_loss_alert_krw || 0);
  const maxPositions = form?.max_positions || 2;
  const requiredCapital = maxEntry * maxPositions + minCash;
  const isCapitalValid = initialCap > 0 && initialCap >= requiredCapital && dailyLoss < initialCap && maxPositions >= 1 && maxPositions <= 10;

  function autoBalance(total: number) {
    if (!form || total <= 0) return;
    const positions = form.max_positions || 2;
    // 1/3을 현금 예비금으로, 나머지 2/3을 종목수로 균등 분할
    const reserve = Math.floor(total / 3 / 10000) * 10000 || 50000;
    const allocatable = Math.max(0, total - reserve);
    const entry = Math.floor(allocatable / positions / 10000) * 10000 || 50000;
    const lossAlert = Math.max(3000, Math.floor(total * 0.02 / 1000) * 1000); // 2% 손실 시 차단

    setForm({
      ...form,
      initial_capital_krw: String(total),
      minimum_cash_reserve_krw: String(reserve),
      max_entry_krw: String(entry),
      daily_loss_alert_krw: String(lossAlert),
    });
  }

  async function savePolicy(){
    if(!form || !policy || !isCapitalValid) return;
    setBusy(true);
    try{
      const patch = {
        ...policy,
        initial_capital_krw: String(Math.floor(initialCap)),
        max_entry_krw: String(Math.floor(maxEntry)),
        minimum_cash_reserve_krw: String(Math.floor(minCash)),
        daily_loss_alert_krw: String(Math.floor(dailyLoss)),
        max_positions: maxPositions,
      };
      const updated = await api<Policy>('settings','PATCH',patch);
      setPolicy(updated);
      setPolicyMessage(`✅ 운용 기준 설정이 저장되었습니다. (기준 자본금: ${Number(updated.initial_capital_krw).toLocaleString('ko-KR')}원)`);
      onSaved();
    }catch(e){
      setPolicyMessage(e instanceof Error?e.message:'운용 기준 저장 실패');
    }finally{
      setBusy(false);
    }
  }

  return (
    <section className="space-y-6">
      {/* 1. 시스템 모드 안내 */}
      <div className="rounded-2xl border border-slate-700 bg-slate-900/40 p-6">
        <h2 className="text-2xl font-bold">시스템 설정</h2>
        <p className="mt-3 text-slate-300">현재 모드: 가상매매 · 수동 추종. 소리·팝업·토스트·브라우저 알림은 사용하지 않습니다.</p>
        <p className="mt-2 text-amber-200">API 실전 주문은 비활성화되어 있습니다. 실제 주문은 빗썸에서 직접 실행하세요.</p>
        <fieldset disabled className="mt-4 text-sm text-slate-500">
          <label><input type="checkbox"/> 향후 API 실전매매 (별도 승인·설정 필요)</label>
        </fieldset>
      </div>

      {/* 2. 실전 운용 기준 자본금 및 주문 한도 설정 */}
      {form && (
        <div className="rounded-2xl border border-slate-700 bg-slate-900/60 p-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 className="text-xl font-semibold text-emerald-400">실전 / 가상 운용 자본금 및 주문 한도</h3>
              <p className="mt-1 text-sm text-slate-400">프로그램 가상매매와 실전 주문 검증의 기준 자본금과 1회 주문 한도를 설정합니다.</p>
            </div>
            <div className="flex flex-wrap gap-1.5">
              <span className="self-center text-xs text-slate-400 mr-1">빠른 추천:</span>
              <button type="button" className={btnQuick} onClick={()=>autoBalance(150000)}>15만원 (기본)</button>
              <button type="button" className={btnQuick} onClick={()=>autoBalance(300000)}>30만원</button>
              <button type="button" className={btnQuick} onClick={()=>autoBalance(500000)}>50만원</button>
              <button type="button" className={btnQuick} onClick={()=>autoBalance(1000000)}>100만원</button>
              <button type="button" className={btnQuick} onClick={()=>autoBalance(3000000)}>300만원</button>
            </div>
          </div>

          <div className="mt-6 grid gap-5 sm:grid-cols-2">
            {/* 총 운용 자본금 */}
            <div>
              <div className="flex items-center justify-between">
                <label className="text-sm font-semibold text-slate-200">총 운용 기준 자본금 (원)</label>
                <div className="flex gap-1 text-xs">
                  <button type="button" className={btnQuick} onClick={()=>autoBalance(initialCap + 50000)}>+5만</button>
                  <button type="button" className={btnQuick} onClick={()=>autoBalance(initialCap + 100000)}>+10만</button>
                  <button type="button" className={btnQuick} onClick={()=>autoBalance(initialCap + 500000)}>+50만</button>
                </div>
              </div>
              <input
                aria-label="총 운용 기준 자본금"
                type="number"
                step="10000"
                min="50000"
                className={`${input} mt-2 block w-full text-lg font-bold text-emerald-300`}
                value={form.initial_capital_krw}
                onChange={e=>setForm({...form, initial_capital_krw: e.target.value})}
              />
              <p className="mt-1 text-xs text-slate-400">현재 입력: {initialCap.toLocaleString('ko-KR')}원</p>
            </div>

            {/* 1회 최대 매수 금액 */}
            <div>
              <label className="text-sm font-semibold text-slate-200">1회 최대 매수 금액 (원)</label>
              <input
                aria-label="1회 최대 매수 금액"
                type="number"
                step="10000"
                min="10000"
                className={`${input} mt-2 block w-full`}
                value={form.max_entry_krw}
                onChange={e=>setForm({...form, max_entry_krw: e.target.value})}
              />
              <p className="mt-1 text-xs text-slate-400">현재 입력: {maxEntry.toLocaleString('ko-KR')}원 (1종목당 최대 진입액)</p>
            </div>

            {/* 최소 현금 여유 보유액 */}
            <div>
              <label className="text-sm font-semibold text-slate-200">최소 현금 여유 보유액 (원)</label>
              <input
                aria-label="최소 현금 여유 보유액"
                type="number"
                step="10000"
                min="0"
                className={`${input} mt-2 block w-full`}
                value={form.minimum_cash_reserve_krw}
                onChange={e=>setForm({...form, minimum_cash_reserve_krw: e.target.value})}
              />
              <p className="mt-1 text-xs text-slate-400">현재 입력: {minCash.toLocaleString('ko-KR')}원 (항상 남겨둘 비상금)</p>
            </div>

            {/* 일일 손실 차단 한도 */}
            <div>
              <label className="text-sm font-semibold text-slate-200">일일 손실 차단 한도 (원)</label>
              <input
                aria-label="일일 손실 차단 한도"
                type="number"
                step="1000"
                min="1000"
                className={`${input} mt-2 block w-full`}
                value={form.daily_loss_alert_krw}
                onChange={e=>setForm({...form, daily_loss_alert_krw: e.target.value})}
              />
              <p className="mt-1 text-xs text-slate-400">당일 누적 손실이 이 금액에 도달하면 신규 매수를 자동 차단합니다.</p>
            </div>

            {/* 최대 동시 보유 종목 수 */}
            <div>
              <label className="text-sm font-semibold text-slate-200">최대 동시 보유 종목 수</label>
              <input
                aria-label="최대 동시 보유 종목 수"
                type="number"
                min="1"
                max="10"
                className={`${input} mt-2 block w-full`}
                value={form.max_positions}
                onChange={e=>setForm({...form, max_positions: Number(e.target.value)})}
              />
              <p className="mt-1 text-xs text-slate-400">1 ~ 10종목 (기본 2종목 분산)</p>
            </div>

            {/* 자본 배분 검증 상태 박스 */}
            <div className="flex flex-col justify-end">
              <div className={`rounded-xl border p-3.5 text-xs ${isCapitalValid ? 'border-emerald-700/60 bg-emerald-950/30 text-emerald-300' : 'border-amber-700/60 bg-amber-950/30 text-amber-300'}`}>
                <p className="font-semibold">{isCapitalValid ? '✓ 자본 배분 규칙 충족' : '⚠️ 자본 배분 규칙 확인 필요'}</p>
                <p className="mt-1">
                  필요 자본: (1회 매수 {maxEntry.toLocaleString()}원 × {maxPositions}종목) + 현금여유 {minCash.toLocaleString()}원 = <strong>{requiredCapital.toLocaleString()}원</strong>
                </p>
                <p className="mt-0.5">총 운용 자본금: <strong>{initialCap.toLocaleString()}원</strong> {initialCap >= requiredCapital ? `(여유: +${(initialCap - requiredCapital).toLocaleString()}원)` : `(${(requiredCapital - initialCap).toLocaleString()}원 부족)`}</p>
                {!isCapitalValid && (
                  <p className="mt-1 text-red-300">총 운용 자본금이 최소 필요 자본보다 커야 저장이 가능합니다. 위의 [빠른 추천] 버튼을 누르면 알맞게 자동 계산됩니다.</p>
                )}
              </div>
            </div>
          </div>

          <div className="mt-6 flex flex-wrap items-center gap-4">
            <button
              disabled={busy || !isCapitalValid}
              className="rounded-xl bg-emerald-600 px-6 py-3 font-semibold text-white hover:bg-emerald-500 disabled:bg-slate-700 disabled:text-slate-400 transition"
              onClick={savePolicy}
            >
              {busy ? '저장 중...' : '운용 기준 자본금 저장'}
            </button>
            {policyMessage && <p className="text-sm font-semibold text-emerald-300">{policyMessage}</p>}
          </div>
        </div>
      )}

      {/* 3. 신호판 표시 기준 */}
      {prefs && (
        <div className="rounded-2xl border border-slate-700 bg-slate-900/40 p-6">
          <h3 className="text-xl font-semibold">신호판 표시 기준</h3>
          <p className="mt-2 text-sm text-slate-400">시간·가격 표시만 변경합니다. 전략의 진입·청산 기준이나 실전 승인 버전은 변경하지 않습니다.</p>
          <div className="mt-5 grid gap-4 sm:grid-cols-2">
            <label>추종할 가상 전략 버전
              <select
                aria-label="추종 가상 전략"
                className={`${input} mt-2 block w-full`}
                value={prefs.follow_version??''}
                onChange={e=>setPrefs({...prefs,follow_version:e.target.value})}
              >
                {board?.versions.map(v=>(
                  <option key={v.id} value={v.id}>
                    {({breakout:'거래량 돌파',pullback:'눌림목',momentum:'추세 모멘텀'} as Record<string,string>)[v.kind]??v.kind} · {v.id.slice(0,10)}
                  </option>
                ))}
              </select>
            </label>
            <label>최근 신호 기준 (초)
              <input
                aria-label="최근 신호 기준 초"
                type="number"
                min="10"
                max="86399"
                className={`${input} mt-2 block w-full`}
                value={prefs.recent_seconds}
                onChange={e=>setPrefs({...prefs,recent_seconds:Number(e.target.value)})}
              />
            </label>
            <label>오래된 신호 기준 (초)
              <input
                aria-label="오래된 신호 기준 초"
                type="number"
                min="11"
                max="86400"
                className={`${input} mt-2 block w-full`}
                value={prefs.stale_seconds}
                onChange={e=>setPrefs({...prefs,stale_seconds:Number(e.target.value)})}
              />
            </label>
            <label>추격 주의 상승률 (비율, 0.01 = 1%)
              <input
                aria-label="추격 주의 상승률"
                inputMode="decimal"
                className={`${input} mt-2 block w-full`}
                value={prefs.chase_rate}
                onChange={e=>setPrefs({...prefs,chase_rate:e.target.value})}
              />
            </label>
          </div>
          <button
            disabled={busy}
            className="mt-5 rounded-xl bg-slate-700 px-5 py-3 hover:bg-slate-600 transition"
            onClick={savePrefs}
          >
            표시 기준 저장
          </button>
          {prefMessage && <p className="mt-3 text-sm text-amber-200">{prefMessage}</p>}
        </div>
      )}

      {/* 4. 운용 정책·수집 상태 상세 (디버그/상세정보) */}
      <details className="rounded-2xl border border-slate-700 p-5">
        <summary className="cursor-pointer text-slate-300">운용 정책·수집 상태 상세 보기</summary>
        <p className="mt-3 text-sm text-slate-400">
          현재 DB 설정: 실전 운용 기준 {Number(policy?.initial_capital_krw??'150000').toLocaleString()}원 · 신규 매수 최대 {Number(policy?.max_entry_krw??'50000').toLocaleString()}원 · 일일 손실 차단 {Number(policy?.daily_loss_alert_krw??'3000').toLocaleString()}원 · 최소 현금 {Number(policy?.minimum_cash_reserve_krw??'50000').toLocaleString()}원
        </p>
        <pre className="mt-4 max-h-96 overflow-auto whitespace-pre-wrap break-all text-xs text-slate-400">{JSON.stringify(system,null,2)}</pre>
      </details>
    </section>
  );
}
