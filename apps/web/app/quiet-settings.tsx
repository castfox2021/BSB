'use client';
import { useEffect, useState } from 'react';
import { api, Policy } from './trading-types';
import { Board, Preferences } from './quiet-types';

const input =
  'rounded-lg border border-[#1E2536] bg-[#0B0E14] px-3.5 py-2.5 text-sm font-mono tabular-nums text-zinc-100 focus:border-zinc-500 focus:outline-none';
const btnQuick =
  'rounded-md border border-[#1E2536] bg-[#181E2B] px-2.5 py-1 text-xs font-medium text-zinc-300 hover:border-zinc-500 hover:text-zinc-100 transition';

export default function QuietSettings({ board, onSaved }: { board: Board | null; onSaved: () => void }) {
  const [prefs, setPrefs] = useState<Preferences | null>(null);
  const [prefMessage, setPrefMessage] = useState('');
  const [policyMessage, setPolicyMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [form, setForm] = useState<{
    initial_capital_krw: string;
    max_entry_krw: string;
    minimum_cash_reserve_krw: string;
    daily_loss_alert_krw: string;
    max_positions: number;
    stop_loss_rate: string;
    take_profit_rate: string;
    exclude_major_coins: boolean;
    auto_buy_only: boolean;
  } | null>(null);
  const [system, setSystem] = useState<unknown>(null);
  const [apiStatus, setApiStatus] = useState<{ configured?: boolean; connection_state?: string } | null>(null);
  const [autoBuyMessage, setAutoBuyMessage] = useState('');

  useEffect(() => {
    if (board) setPrefs(board.preferences);
  }, [
    board?.preferences.follow_version,
    board?.preferences.recent_seconds,
    board?.preferences.stale_seconds,
    board?.preferences.chase_rate,
  ]);

  useEffect(() => {
    api<Policy>('settings')
      .then((p) => {
        setPolicy(p);
        setForm({
          initial_capital_krw: p.initial_capital_krw ?? '150000',
          max_entry_krw: p.max_entry_krw ?? '50000',
          minimum_cash_reserve_krw: p.minimum_cash_reserve_krw ?? '50000',
          daily_loss_alert_krw: p.daily_loss_alert_krw ?? '3000',
          max_positions: p.max_positions ?? 2,
          stop_loss_rate: p.stop_loss_rate ?? '0.008',
          take_profit_rate: p.take_profit_rate ?? '0.012',
          exclude_major_coins: Boolean(p.exclude_major_coins ?? false),
          auto_buy_only: Boolean(p.auto_buy_only ?? false),
        });
      })
      .catch(() => setPolicyMessage('설정을 읽을 수 없습니다.'));
    api('market/status')
      .then(setSystem)
      .catch(() => setSystem(null));
    api<{ configured?: boolean; connection_state?: string }>('private/status')
      .then(setApiStatus)
      .catch(() => setApiStatus(null));
  }, []);

  async function savePrefs() {
    if (!prefs) return;
    setBusy(true);
    try {
      await api('board/preferences', 'PATCH', prefs);
      setPrefMessage('신호판 표시 기준을 저장했습니다.');
      onSaved();
    } catch (e) {
      setPrefMessage(e instanceof Error ? e.message : '설정 저장 실패');
    } finally {
      setBusy(false);
    }
  }

  const cleanNum = (v: string | number | undefined) => Number(String(v ?? '0').replace(/,/g, '').trim());
  const initialCap = cleanNum(form?.initial_capital_krw);
  const maxEntry = cleanNum(form?.max_entry_krw);
  const minCash = cleanNum(form?.minimum_cash_reserve_krw);
  const dailyLoss = cleanNum(form?.daily_loss_alert_krw);
  const maxPositions = Number(form?.max_positions || 1);
  const requiredCapital = maxEntry * maxPositions + minCash;
  const isCapitalValid =
    Number.isFinite(initialCap) &&
    Number.isFinite(maxEntry) &&
    Number.isFinite(minCash) &&
    Number.isFinite(dailyLoss) &&
    initialCap > 0 &&
    maxEntry > 0 &&
    minCash >= 0 &&
    dailyLoss > 0 &&
    initialCap >= requiredCapital &&
    dailyLoss < initialCap &&
    maxPositions >= 1 &&
    maxPositions <= 10;
  const [policyIsError, setPolicyIsError] = useState(false);

  function autoBalance(total: number) {
    if (!form || total <= 0) return;
    const positions = form.max_positions || 2;
    const reserve = Math.floor(total / 3 / 10000) * 10000 || 50000;
    const allocatable = Math.max(0, total - reserve);
    const entry = Math.floor(allocatable / positions / 10000) * 10000 || 50000;
    const lossAlert = Math.max(3000, Math.floor((total * 0.02) / 1000) * 1000);

    setForm({
      ...form,
      initial_capital_krw: String(total),
      minimum_cash_reserve_krw: String(reserve),
      max_entry_krw: String(entry),
      daily_loss_alert_krw: String(lossAlert),
    });
  }

  async function savePolicy() {
    if (!form || !policy) return;
    if (!isCapitalValid) {
      setPolicyIsError(true);
      setPolicyMessage(
        initialCap < requiredCapital
          ? `필요 자본(${requiredCapital.toLocaleString('ko-KR')}원)이 총 운용 자본(${initialCap.toLocaleString('ko-KR')}원)보다 많습니다. "필요 자본에 맞추기"를 누르거나 총 운용 자본을 올려주세요.`
          : '입력한 자본금·한도·손실 기준 값을 확인해 주세요. (0보다 큰 숫자여야 합니다)'
      );
      return;
    }
    setBusy(true);
    setPolicyIsError(false);
    try {
      const patch = {
        ...policy,
        initial_capital_krw: String(Math.floor(initialCap)),
        max_entry_krw: String(Math.floor(maxEntry)),
        minimum_cash_reserve_krw: String(Math.floor(minCash)),
        daily_loss_alert_krw: String(Math.floor(dailyLoss)),
        max_positions: maxPositions,
        stop_loss_rate: String(form.stop_loss_rate).trim(),
        take_profit_rate: String(form.take_profit_rate).trim(),
        exclude_major_coins: form.exclude_major_coins,
        auto_buy_only: form.auto_buy_only,
      };
      const updated = await api<Policy>('settings', 'PATCH', patch);
      setPolicy(updated);
      setPolicyMessage(
        `운용 기준을 저장했습니다. (기준 자본금 ${Number(updated.initial_capital_krw).toLocaleString('ko-KR')}원 · 1회 최대 ${Number(updated.max_entry_krw).toLocaleString('ko-KR')}원 · 손절 -${(Number(updated.stop_loss_rate) * 100).toFixed(1)}%)`
      );
      onSaved();
    } catch (e) {
      setPolicyIsError(true);
      setPolicyMessage(e instanceof Error ? e.message : '운용 기준 저장 실패');
    } finally {
      setBusy(false);
    }
  }

  async function toggleAutoBuy(next: boolean) {
    if (!policy || !form) return;
    setBusy(true);
    setAutoBuyMessage('');
    try {
      const updated = await api<Policy>('settings', 'PATCH', {
        ...policy,
        auto_buy_only: next,
      });
      setPolicy(updated);
      setForm({ ...form, auto_buy_only: Boolean(updated.auto_buy_only) });
      setAutoBuyMessage(
        updated.auto_buy_only
          ? '자동 실전 매수를 켰습니다. (매수만 자동 실행 · 매도/손절은 수동, 자금 부족 시 가상매수로 유지)'
          : '자동 실전 매수를 껐습니다. (가상매매 전용 모드)'
      );
      onSaved();
    } catch (e) {
      setAutoBuyMessage(e instanceof Error ? e.message : '자동 실전 매수 설정 변경 실패');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="space-y-6">
      {/* 1. 시스템 모드 */}
      <div className="rounded-xl border border-[#1E2536] bg-[#121620] p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-base font-semibold text-zinc-100">시스템 설정</h2>
          <span
            className={`rounded-md border px-2.5 py-1 text-xs font-medium ${
              apiStatus?.configured
                ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
                : 'border-amber-500/30 bg-amber-500/10 text-amber-300'
            }`}
          >
            {apiStatus?.configured ? '빗썸 API 키 연결됨' : '빗썸 API 키 미설정 (가상매수로만 동작)'}
          </span>
        </div>
        <p className="mt-2 text-xs text-zinc-400">
          기본은 가상매매 모드입니다. 아래 옵션을 켜면 추종 전략이 매수 신호를 포착할 때만 빗썸 시장가 매수를 자동 실행합니다. 손절·익절 매도는 자동으로 실행하지 않으며, 주문 가능 원화 자금이 부족하면 가상매수로만 기록됩니다.
        </p>
        <div className="mt-4 space-y-2">
          <label className="inline-flex cursor-pointer items-center gap-2.5 text-sm font-medium text-zinc-200">
            <input
              type="checkbox"
              disabled={busy || !form}
              checked={Boolean(form?.auto_buy_only)}
              onChange={(e) => toggleAutoBuy(e.target.checked)}
              className="h-4 w-4 rounded border-zinc-600 bg-zinc-900 accent-emerald-500"
            />
            <span>신호 포착 시 자동 실전 매수 (매수만 자동 · 매도는 수동)</span>
          </label>
          {autoBuyMessage && <p className="text-xs font-medium text-emerald-400">{autoBuyMessage}</p>}
        </div>
      </div>

      {/* 2. 실전 운용 기준 자본금 및 주문 한도 설정 */}
      {form && (
        <div className="rounded-xl border border-[#1E2536] bg-[#121620] p-6">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#1E2536]/80 pb-4">
            <div>
              <h3 className="text-base font-semibold text-zinc-100">운용 자본금 및 주문 한도</h3>
              <p className="mt-0.5 text-xs text-zinc-400">
                가상 및 수동 매매의 기준 자본금과 1회 최대 주문 금액을 설정합니다.
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="mr-1 text-xs text-zinc-500">프리셋</span>
              <button type="button" className={btnQuick} onClick={() => autoBalance(150000)}>
                15만
              </button>
              <button type="button" className={btnQuick} onClick={() => autoBalance(300000)}>
                30만
              </button>
              <button type="button" className={btnQuick} onClick={() => autoBalance(500000)}>
                50만
              </button>
              <button type="button" className={btnQuick} onClick={() => autoBalance(1000000)}>
                100만
              </button>
              <button type="button" className={btnQuick} onClick={() => autoBalance(3000000)}>
                300만
              </button>
            </div>
          </div>

          <div className="mt-5 grid gap-5 sm:grid-cols-2">
            <div>
              <div className="flex items-center justify-between">
                <label className="text-xs font-medium text-zinc-300">총 운용 기준 자본금 (원)</label>
                <div className="flex gap-1">
                  <button type="button" className={btnQuick} onClick={() => autoBalance(initialCap + 50000)}>
                    +5만
                  </button>
                  <button type="button" className={btnQuick} onClick={() => autoBalance(initialCap + 100000)}>
                    +10만
                  </button>
                  <button type="button" className={btnQuick} onClick={() => autoBalance(initialCap + 500000)}>
                    +50만
                  </button>
                </div>
              </div>
              <input
                aria-label="총 운용 기준 자본금"
                type="number"
                step="10000"
                min="50000"
                className={`${input} mt-2 block w-full text-base font-semibold text-zinc-100`}
                value={form.initial_capital_krw}
                onChange={(e) => setForm({ ...form, initial_capital_krw: e.target.value })}
              />
              <p className="mt-1.5 font-mono text-xs tabular-nums text-zinc-500">
                {initialCap.toLocaleString('ko-KR')}원
              </p>
            </div>

            <div>
              <label className="text-xs font-medium text-zinc-300">1회 최대 매수 금액 (원)</label>
              <input
                aria-label="1회 최대 매수 금액"
                type="number"
                step="10000"
                min="10000"
                className={`${input} mt-2 block w-full`}
                value={form.max_entry_krw}
                onChange={(e) => setForm({ ...form, max_entry_krw: e.target.value })}
              />
              <p className="mt-1.5 font-mono text-xs tabular-nums text-zinc-500">
                {maxEntry.toLocaleString('ko-KR')}원 · 종목당 최대 진입 한도
              </p>
            </div>

            <div>
              <label className="text-xs font-medium text-zinc-300">최소 현금 여유 보유액 (원)</label>
              <input
                aria-label="최소 현금 여유 보유액"
                type="number"
                step="10000"
                min="0"
                className={`${input} mt-2 block w-full`}
                value={form.minimum_cash_reserve_krw}
                onChange={(e) => setForm({ ...form, minimum_cash_reserve_krw: e.target.value })}
              />
              <p className="mt-1.5 font-mono text-xs tabular-nums text-zinc-500">
                {minCash.toLocaleString('ko-KR')}원 · 비상 현금 유보
              </p>
            </div>

            <div>
              <label className="text-xs font-medium text-zinc-300">일일 손실 차단 한도 (원)</label>
              <input
                aria-label="일일 손실 차단 한도"
                type="number"
                step="1000"
                min="1000"
                className={`${input} mt-2 block w-full`}
                value={form.daily_loss_alert_krw}
                onChange={(e) => setForm({ ...form, daily_loss_alert_krw: e.target.value })}
              />
              <p className="mt-1.5 text-xs text-zinc-500">당일 누적 손실 도달 시 신규 매수 차단</p>
            </div>

            <div>
              <label className="text-xs font-medium text-zinc-300">최대 동시 보유 종목 수</label>
              <input
                aria-label="최대 동시 보유 종목 수"
                type="number"
                min="1"
                max="10"
                className={`${input} mt-2 block w-full`}
                value={form.max_positions}
                onChange={(e) => setForm({ ...form, max_positions: Number(e.target.value) })}
              />
              <p className="mt-1.5 text-xs text-zinc-500">1 ~ 10종목</p>
            </div>

            <div>
              <div className="flex items-center justify-between">
                <label className="text-xs font-medium text-zinc-300">손절 / 목표 익절 기준 (비율)</label>
                <button
                  type="button"
                  className={btnQuick}
                  onClick={() =>
                    setForm({
                      ...form,
                      stop_loss_rate: '0.013',
                      take_profit_rate: '0.018',
                      exclude_major_coins: true,
                    })
                  }
                >
                  알트 단타 최적화 (-1.3% / +1.8% / 메이저 제외)
                </button>
              </div>
              <div className="mt-2 grid grid-cols-2 gap-3">
                <div>
                  <input
                    aria-label="손절 비율"
                    inputMode="decimal"
                    className={`${input} block w-full text-sky-400`}
                    value={form.stop_loss_rate}
                    onChange={(e) => setForm({ ...form, stop_loss_rate: e.target.value })}
                  />
                  <p className="mt-1 font-mono text-[11px] text-zinc-500">
                    손절 -{(Number(form.stop_loss_rate || 0) * 100).toFixed(1)}%
                  </p>
                </div>
                <div>
                  <input
                    aria-label="목표 익절 비율"
                    inputMode="decimal"
                    className={`${input} block w-full text-rose-400`}
                    value={form.take_profit_rate}
                    onChange={(e) => setForm({ ...form, take_profit_rate: e.target.value })}
                  />
                  <p className="mt-1 font-mono text-[11px] text-zinc-500">
                    익절 +{(Number(form.take_profit_rate || 0) * 100).toFixed(1)}%
                  </p>
                </div>
              </div>
              <label className="mt-3 flex items-center gap-2 text-xs text-zinc-300 cursor-pointer">
                <input
                  type="checkbox"
                  checked={form.exclude_major_coins}
                  onChange={(e) => setForm({ ...form, exclude_major_coins: e.target.checked })}
                  className="rounded border-zinc-700 bg-zinc-900"
                />
                <span>메이저 저변동성 코인 제외 (BTC·ETH·XRP·SOL·DOGE·ADA 등)</span>
              </label>
            </div>

            <div className="flex flex-col justify-end">
              <div
                className={`rounded-lg border p-3.5 text-xs ${
                  isCapitalValid
                    ? 'border-[#1E2536] bg-[#0E1119] text-zinc-300'
                    : 'border-rose-500/40 bg-[#0E1119] text-rose-300'
                }`}
              >
                <div className="flex items-center gap-2 font-medium">
                  <span className={`h-1.5 w-1.5 rounded-full ${isCapitalValid ? 'bg-emerald-400' : 'bg-rose-400'}`} />
                  <span>{isCapitalValid ? '자본 배분 규칙 충족' : '자본 배분 규칙 확인 필요'}</span>
                </div>
                <p className="mt-1.5 font-mono tabular-nums text-zinc-400">
                  필요 자본: ({maxEntry.toLocaleString()} × {maxPositions}종목) + {minCash.toLocaleString()} ={' '}
                  <strong className="text-zinc-200">{requiredCapital.toLocaleString()}원</strong>
                </p>
                <p className="mt-0.5 font-mono tabular-nums text-zinc-400">
                  총 운용 자본: <strong className="text-zinc-200">{initialCap.toLocaleString()}원</strong>{' '}
                  {initialCap >= requiredCapital
                    ? `(여유 +${(initialCap - requiredCapital).toLocaleString()}원)`
                    : `(${(requiredCapital - initialCap).toLocaleString()}원 부족)`}
                </p>
                {!isCapitalValid && initialCap < requiredCapital && form && (
                  <button
                    type="button"
                    className="mt-2 rounded border border-rose-400/40 bg-rose-500/15 px-2.5 py-1 text-[11px] font-medium text-rose-200 hover:bg-rose-500/25"
                    onClick={() => setForm({ ...form, initial_capital_krw: String(Math.max(requiredCapital, dailyLoss + 1000)) })}
                  >
                    총 운용 자본을 {requiredCapital.toLocaleString()}원으로 자동 맞추기
                  </button>
                )}
              </div>
            </div>
          </div>

          <div className="mt-6 flex flex-wrap items-center gap-4 border-t border-[#1E2536]/60 pt-5">
            <button
              disabled={busy}
              className="rounded-lg bg-zinc-100 px-5 py-2.5 text-sm font-semibold text-zinc-950 hover:bg-white disabled:bg-zinc-800 disabled:text-zinc-500 transition"
              onClick={savePolicy}
            >
              {busy ? '저장 중...' : '운용 기준 저장'}
            </button>
            {policyMessage && (
              <p className={`text-xs font-medium ${policyIsError ? 'text-rose-400' : 'text-emerald-400'}`}>
                {policyMessage}
              </p>
            )}
          </div>
        </div>
      )}

      {/* 3. 신호판 표시 기준 */}
      {prefs && (
        <div className="rounded-xl border border-[#1E2536] bg-[#121620] p-6">
          <div className="border-b border-[#1E2536]/80 pb-4">
            <h3 className="text-base font-semibold text-zinc-100">신호판 표시 기준</h3>
            <p className="mt-0.5 text-xs text-zinc-400">시간·가격 표시 기준만 변경하며 전략 진입/청산 조건은 수정하지 않습니다.</p>
          </div>
          <div className="mt-5 grid gap-5 sm:grid-cols-2">
            <label className="text-xs font-medium text-zinc-300">
              추종 가상 전략 버전
              <select
                aria-label="추종 가상 전략"
                className={`${input} mt-2 block w-full font-sans`}
                value={prefs.follow_version ?? ''}
                onChange={(e) => setPrefs({ ...prefs, follow_version: e.target.value })}
              >
                {board?.versions.map((v) => (
                  <option key={v.id} value={v.id}>
                    {({ breakout: '거래량 돌파', pullback: '눌림목', momentum: '추세 모멘텀' } as Record<string, string>)[v.kind] ??
                      v.kind}{' '}
                    · {v.id.slice(0, 10)}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-xs font-medium text-zinc-300">
              최근 신호 기준 (초)
              <input
                aria-label="최근 신호 기준 초"
                type="number"
                min="10"
                max="86399"
                className={`${input} mt-2 block w-full`}
                value={prefs.recent_seconds}
                onChange={(e) => setPrefs({ ...prefs, recent_seconds: Number(e.target.value) })}
              />
            </label>
            <label className="text-xs font-medium text-zinc-300">
              오래된 신호 기준 (초)
              <input
                aria-label="오래된 신호 기준 초"
                type="number"
                min="11"
                max="86400"
                className={`${input} mt-2 block w-full`}
                value={prefs.stale_seconds}
                onChange={(e) => setPrefs({ ...prefs, stale_seconds: Number(e.target.value) })}
              />
            </label>
            <label className="text-xs font-medium text-zinc-300">
              추격 주의 상승률 (비율, 0.01 = 1%)
              <input
                aria-label="추격 주의 상승률"
                inputMode="decimal"
                className={`${input} mt-2 block w-full`}
                value={prefs.chase_rate}
                onChange={(e) => setPrefs({ ...prefs, chase_rate: e.target.value })}
              />
            </label>
          </div>
          <div className="mt-6 flex flex-wrap items-center gap-4 border-t border-[#1E2536]/60 pt-5">
            <button
              disabled={busy}
              className="rounded-lg border border-[#2A344B] bg-[#181E2B] px-5 py-2.5 text-sm font-medium text-zinc-100 hover:border-zinc-500 transition"
              onClick={savePrefs}
            >
              표시 기준 저장
            </button>
            {prefMessage && <p className="text-xs font-medium text-zinc-300">{prefMessage}</p>}
          </div>
        </div>
      )}

      {/* 4. 운용 정책·수집 상태 상세 */}
      <details className="rounded-xl border border-[#1E2536] bg-[#121620] p-5 text-xs">
        <summary className="cursor-pointer font-medium text-zinc-400 hover:text-zinc-200">
          운용 정책·수집 상태 상세
        </summary>
        <p className="mt-3 font-mono tabular-nums text-zinc-400">
          기준 자본 {Number(policy?.initial_capital_krw ?? '150000').toLocaleString()}원 · 최대 매수{' '}
          {Number(policy?.max_entry_krw ?? '50000').toLocaleString()}원 · 손실 차단{' '}
          {Number(policy?.daily_loss_alert_krw ?? '3000').toLocaleString()}원 · 현금 유보{' '}
          {Number(policy?.minimum_cash_reserve_krw ?? '50000').toLocaleString()}원
        </p>
        <pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap break-all rounded-lg border border-[#1E2536] bg-[#0B0E14] p-3 font-mono text-[11px] text-zinc-400">
          {JSON.stringify(system, null, 2)}
        </pre>
      </details>
    </section>
  );
}
