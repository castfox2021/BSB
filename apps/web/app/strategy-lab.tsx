'use client';
import { useCallback, useEffect, useState } from 'react';
import { api, LabOverview, Metrics, money, pct, Report } from './trading-types';

const STRATEGY_PRESETS = [
  {
    kind: 'pullback',
    name: '급락 후 반등 (추천)',
    badge: '단기 반등',
    description: '단기 급락 후 반등 캔들이 출현할 때 진입하여 빠른 기술적 반등을 노립니다.',
    defaultParams: {
      lookback: 20,
      volume_ratio: '1.5',
      stop_loss: '0.008',
      take_profit: '0.012',
      max_bars: 15,
    },
    takeProfitPct: '1.2',
    stopLossPct: '0.8',
    volRatio: '1.5',
    lookbackMin: 20,
    maxBarsMin: 15,
  },
  {
    kind: 'breakout',
    name: '전고점 돌파',
    badge: '돌파 매매',
    description: '최근 20분간의 최고가를 대량 거래량으로 강하게 돌파할 때 진입합니다.',
    defaultParams: {
      lookback: 20,
      volume_ratio: '1.5',
      stop_loss: '0.010',
      take_profit: '0.018',
      max_bars: 30,
    },
    takeProfitPct: '1.8',
    stopLossPct: '1.0',
    volRatio: '1.5',
    lookbackMin: 20,
    maxBarsMin: 30,
  },
  {
    kind: 'momentum',
    name: '상승 추세 추종',
    badge: '추세 추종',
    description: '1분·3분·5분봉 정배열 상승 정렬 확인 후 상승 흐름에 편승합니다.',
    defaultParams: {
      lookback: 25,
      volume_ratio: '1.2',
      stop_loss: '0.010',
      take_profit: '0.020',
      max_bars: 30,
    },
    takeProfitPct: '2.0',
    stopLossPct: '1.0',
    volRatio: '1.2',
    lookbackMin: 25,
    maxBarsMin: 30,
  },
] as const;

const QUICK_COINS = [
  { symbol: 'KRW-BTC', name: '비트코인' },
  { symbol: 'KRW-ETH', name: '이더리움' },
  { symbol: 'KRW-XRP', name: '리플' },
  { symbol: 'KRW-SOL', name: '솔라나' },
];

function SummaryMetrics({ value, title, isValidation }: { value: Metrics; title: string; isValidation?: boolean }) {
  const net = Number(value.net_profit ?? value.net_realized_krw ?? 0);
  const winRate = value.win_rate != null ? Number(value.win_rate) : null;
  const mddVal = Number(value.mdd ?? value.sampled_equity_mdd ?? 0);
  const trades = value.trades_count ?? value.observations ?? 0;

  return (
    <div className={`rounded-xl border p-4 ${isValidation ? 'border-cyan-700 bg-cyan-950/20' : 'border-slate-800 bg-slate-900/60'}`}>
      <div className="flex items-center justify-between">
        <h4 className={`text-sm font-semibold ${isValidation ? 'text-cyan-300' : 'text-slate-300'}`}>{title}</h4>
        {isValidation && (
          <span className="rounded bg-cyan-900/70 px-2 py-0.5 text-[11px] font-medium text-cyan-200">
            실전 판정 기준 구간
          </span>
        )}
      </div>

      <div className="mt-3 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
        <div className="rounded-lg bg-slate-950/60 p-2.5">
          <span className="text-xs text-slate-400">예상 순손익</span>
          <p className={`mt-1 text-base font-bold ${net > 0 ? 'text-red-400' : net < 0 ? 'text-blue-400' : 'text-slate-200'}`}>
            {net > 0 ? '+' : ''}{money(value.net_profit ?? value.net_realized_krw)}
          </p>
        </div>

        <div className="rounded-lg bg-slate-950/60 p-2.5">
          <span className="text-xs text-slate-400">승률</span>
          <p className={`mt-1 text-base font-bold ${winRate && winRate >= 0.5 ? 'text-red-400' : 'text-slate-200'}`}>
            {winRate != null ? `${(winRate * 100).toFixed(1)}%` : '—'}
          </p>
        </div>

        <div className="rounded-lg bg-slate-950/60 p-2.5">
          <span className="text-xs text-slate-400">최대 낙폭 (MDD)</span>
          <p className={`mt-1 text-base font-bold ${mddVal <= 0.05 ? 'text-slate-200' : mddVal <= 0.1 ? 'text-amber-300' : 'text-blue-400'}`}>
            {pct(value.mdd ?? value.sampled_equity_mdd)}
          </p>
        </div>

        <div className="rounded-lg bg-slate-950/60 p-2.5">
          <span className="text-xs text-slate-400">거래 횟수</span>
          <p className="mt-1 text-base font-bold text-slate-200">
            {trades}회
          </p>
        </div>
      </div>

      <div className="mt-2.5 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-400">
        <span>비용(수수료+슬리피지): <strong className="text-slate-300">{money(value.fees_krw)}</strong></span>
        <span>손익비: <strong className="text-slate-300">{value.payoff_ratio == null ? '—' : Number(value.payoff_ratio).toFixed(2)}</strong></span>
        <span>1회 기대값: <strong className="text-slate-300">{money(value.expectancy_krw)}</strong></span>
      </div>
    </div>
  );
}

export default function StrategyLab() {
  const [lab, setLab] = useState<LabOverview | null>(null);
  const [error, setError] = useState('');
  const [successMsg, setSuccessMsg] = useState('');
  const [busy, setBusy] = useState(false);
  const [stepStatus, setStepStatus] = useState<string | null>(null);

  // 간편 모드 설정 상태
  const [selectedCoin, setSelectedCoin] = useState('KRW-BTC');
  const [selectedPresetIndex, setSelectedPresetIndex] = useState(0);
  const [takeProfitPct, setTakeProfitPct] = useState('1.2');
  const [stopLossPct, setStopLossPct] = useState('0.8');
  const [volRatio, setVolRatio] = useState('1.5');
  const [lookbackMin, setLookbackMin] = useState(20);
  const [maxBarsMin, setMaxBarsMin] = useState(15);

  // 고급 모드 아코디언 토글
  const [showAdvanced, setShowAdvanced] = useState(false);

  // 고급 모드 상태 (기존 호환)
  const [version, setVersion] = useState('');
  const [params, setParams] = useState('');
  const [dataset, setDataset] = useState('');
  const [symbol, setSymbol] = useState('KRW-BTC');
  const [grid, setGrid] = useState('{"volume_ratio":["1.5","2","3"]}');
  const [optimize, setOptimize] = useState(false);
  const [fee, setFee] = useState('0.0004');
  const [slip, setSlip] = useState('0.001');

  const refresh = useCallback(async () => {
    try {
      const data = await api<LabOverview>('lab/overview');
      setLab(data);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, [refresh]);

  // 전략 버전 동기화
  useEffect(() => {
    if (lab && !version && lab.versions[0]) {
      setVersion(lab.versions[0].id);
      setParams(JSON.stringify(lab.versions[0].params, null, 2));
    }
    if (lab && !dataset && lab.datasets[0]) {
      setDataset(lab.datasets[0].id);
    }
  }, [lab, version, dataset]);

  // 프리셋 선택 시 간편 폼 자동 채움
  const handleSelectPreset = (idx: number) => {
    setSelectedPresetIndex(idx);
    const p = STRATEGY_PRESETS[idx];
    setTakeProfitPct(p.takeProfitPct);
    setStopLossPct(p.stopLossPct);
    setVolRatio(p.volRatio);
    setLookbackMin(p.lookbackMin);
    setMaxBarsMin(p.maxBarsMin);
  };

  const action = async (work: () => Promise<unknown>) => {
    setBusy(true);
    setError('');
    setSuccessMsg('');
    try {
      await work();
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  // 비동기 잡 대기 헬퍼
  const waitForJob = async (jobId: string, maxWaitMs = 15000) => {
    const start = Date.now();
    while (Date.now() - start < maxWaitMs) {
      const job = await api<{ status: string; error?: string | null }>(`lab/jobs/${jobId}`);
      if (job.status === 'DONE') return job;
      if (job.status === 'FAILED') throw new Error(job.error || '백테스트 실행에 실패했습니다.');
      await new Promise((r) => setTimeout(r, 600));
    }
    throw new Error('백테스트 작업 응답 시간이 초과되었습니다.');
  };

  // ⚡ 1-클릭 간편 원클릭 전략 분석 실행
  const runOneClickAnalysis = async () => {
    setBusy(true);
    setError('');
    setSuccessMsg('');

    try {
      const tpRate = (parseFloat(takeProfitPct) / 100).toFixed(4);
      const slRate = (parseFloat(stopLossPct) / 100).toFixed(4);

      if (parseFloat(tpRate) <= parseFloat(slRate)) {
        throw new Error('목표 익절선(%)은 손절선(%)보다 커야 합니다.');
      }

      const preset = STRATEGY_PRESETS[selectedPresetIndex];
      const strategyParams = {
        lookback: Number(lookbackMin),
        volume_ratio: String(volRatio),
        stop_loss: slRate,
        take_profit: tpRate,
        max_bars: Number(maxBarsMin),
      };

      // 1. 거래소 캔들 데이터 수집
      setStepStatus(`[1/4] ${selectedCoin} 과거 1,000분 캔들 수집 중...`);
      const histRun = await api<{ job_id: string }>('lab/runs', 'POST', {
        operation: 'history',
        symbol: selectedCoin,
        pages: 5,
      });
      await waitForJob(histRun.job_id);

      // 2. 결측 없는 고정 데이터셋 생성
      setStepStatus('[2/4] 결측 없는 연속 1분봉 데이터셋 고정 중...');
      const ds = await api<{ id: string; candles_count: number }>('lab/datasets', 'POST', {
        symbol: selectedCoin,
      });
      setDataset(ds.id);

      // 3. 전략 파라미터 버전 자동 등록
      setStepStatus('[3/4] 전략 조건 버전 등록 중...');
      const ver = await api<{ id: string }>('lab/versions', 'POST', {
        kind: preset.kind,
        params: strategyParams,
      });
      setVersion(ver.id);
      setParams(JSON.stringify(strategyParams, null, 2));

      // 4. 백테스트 시뮬레이션 즉시 실행 (학습 70% / 검증 30%)
      setStepStatus('[4/4] 70% 학습 및 30% 별도 검증 시뮬레이션 실행 중...');
      const evalRun = await api<{ job_id: string }>('lab/runs', 'POST', {
        version_id: ver.id,
        dataset_id: ds.id,
        fee_rate: fee,
        slippage: slip,
      });
      await waitForJob(evalRun.job_id);

      setStepStatus(null);
      setSuccessMsg(`🎉 [${preset.name}] 전략 분석 완료! 아래 검증 보고서에서 결과를 확인하세요.`);
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setStepStatus(null);
      setBusy(false);
    }
  };

  // 실전 전략 승격
  const promote = (report: Report) =>
    action(async () => {
      await api('lab/promote', 'POST', {
        version_id: report.version_id,
        report_id: report.id,
        approval: '실전 신호 적용 승인',
      });
      setSuccessMsg(`✅ 전략(${report.version_id.slice(0, 8)})이 실전 자동매매에 성공적으로 반영되었습니다!`);
    });

  // 실전 신호 일시 중지
  const pauseLive = () =>
    action(async () => {
      await api('lab/pause', 'POST', {});
      setSuccessMsg('실전 매수 신호가 일시 중지되었습니다.');
    });

  const activePreset = STRATEGY_PRESETS[selectedPresetIndex];

  return (
    <section className="space-y-6">
      {/* 1. 상단: 현재 실전 활성 전략 상태 배너 */}
      <div className="rounded-2xl border border-cyan-800 bg-slate-900/80 p-5 shadow-xl sm:p-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold tracking-wider text-cyan-400">TRADING STRATEGY LAB</span>
              <span className="rounded-full bg-cyan-950 px-2.5 py-0.5 text-xs text-cyan-200 border border-cyan-800">
                실전 안전 승격 시스템
              </span>
            </div>
            <h2 className="mt-1 text-2xl font-bold">전략 분석 및 검증 연구소</h2>
            <p className="mt-1 text-xs text-slate-400 sm:text-sm">
              손실 위험이 있는 전략을 사전에 차단하고, 엄격한 검증을 통과한 전략만 실전 신호로 승격합니다.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <div className="rounded-xl border border-slate-700 bg-slate-950 px-4 py-2 text-right">
              <span className="block text-xs text-slate-400">실전 활성 전략</span>
              <strong className={`text-sm ${lab?.active_version ? 'text-emerald-300' : 'text-amber-300'}`}>
                {lab?.active_version ? `활성 (${lab.active_version.slice(0, 10)})` : '없음 · 매수 차단 중'}
              </strong>
            </div>

            {lab?.active_version ? (
              <button
                disabled={busy}
                className="rounded-xl border border-rose-800 bg-rose-950/60 px-4 py-3 text-sm font-semibold text-rose-200 hover:bg-rose-900"
                onClick={pauseLive}
              >
                실전 매수 신호 일시 중지
              </button>
            ) : null}
          </div>
        </div>
      </div>

      {/* 상태 메시지 알림 */}
      {error && (
        <div role="alert" className="rounded-xl border border-rose-800 bg-rose-950/80 p-4 text-sm text-rose-200 shadow-md">
          <strong className="font-semibold">오류 발생: </strong> {error}
        </div>
      )}

      {successMsg && (
        <div className="rounded-xl border border-emerald-800 bg-emerald-950/80 p-4 text-sm text-emerald-200 shadow-md">
          {successMsg}
        </div>
      )}

      {stepStatus && (
        <div className="rounded-xl border border-cyan-700 bg-cyan-950/90 p-4 text-sm text-cyan-200 shadow-lg animate-pulse">
          <div className="flex items-center gap-3">
            <span className="inline-block h-3 w-3 rounded-full bg-cyan-400 animate-ping" />
            <strong className="font-medium">{stepStatus}</strong>
          </div>
        </div>
      )}

      {/* 2. 간편 모드: 급락 조건처럼 알기 쉽고 직관적인 3초 전략 분석 */}
      <div className="rounded-2xl border border-slate-700 bg-slate-900/90 p-5 sm:p-7 shadow-xl">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 pb-4">
          <div>
            <h3 className="text-xl font-bold text-white">⚡ 간편 전략 분석 (원클릭 백테스트)</h3>
            <p className="mt-1 text-xs text-slate-400 sm:text-sm">
              복잡한 수식이나 JSON 코드 없이, 마치 급락 반등 조건처럼 몇 가지 직관적인 설정만으로 즉시 시뮬레이션합니다.
            </p>
          </div>
          <button
            onClick={() => setShowAdvanced((v) => !v)}
            className="rounded-lg border border-slate-700 bg-slate-800/80 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-700"
          >
            {showAdvanced ? '⚙️ 고급 엔지니어링 모드 닫기' : '⚙️ 고급 엔지니어링 모드 열기 (JSON/수수료)'}
          </button>
        </div>

        <div className="mt-6 space-y-6">
          {/* STEP 1: 종목 선택 */}
          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-slate-400">
              1. 분석할 코인 선택
            </label>
            <div className="mt-2.5 flex flex-wrap gap-2">
              {QUICK_COINS.map((c) => (
                <button
                  key={c.symbol}
                  type="button"
                  onClick={() => setSelectedCoin(c.symbol)}
                  className={`rounded-xl px-4 py-2.5 text-sm font-semibold transition ${
                    selectedCoin === c.symbol
                      ? 'border border-cyan-500 bg-cyan-600 text-white shadow-lg shadow-cyan-900/30'
                      : 'border border-slate-700 bg-slate-800 text-slate-300 hover:bg-slate-700'
                  }`}
                >
                  {c.name} ({c.symbol.replace('KRW-', '')})
                </button>
              ))}
              <input
                aria-label="종목 직접 입력"
                className="w-36 rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white focus:border-cyan-500 focus:outline-none"
                placeholder="KRW-DOGE 등"
                value={selectedCoin}
                onChange={(e) => setSelectedCoin(e.target.value.toUpperCase())}
              />
            </div>
          </div>

          {/* STEP 2: 전략 템플릿 선택 */}
          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-slate-400">
              2. 전략 템플릿 선택
            </label>
            <div className="mt-2.5 grid gap-3 sm:grid-cols-3">
              {STRATEGY_PRESETS.map((p, idx) => (
                <div
                  key={p.kind}
                  onClick={() => handleSelectPreset(idx)}
                  className={`cursor-pointer rounded-xl border p-4 transition ${
                    selectedPresetIndex === idx
                      ? 'border-cyan-500 bg-cyan-950/40 ring-1 ring-cyan-500'
                      : 'border-slate-800 bg-slate-950/60 hover:border-slate-700 hover:bg-slate-900'
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <strong className="text-base font-bold text-white">{p.name}</strong>
                    <span className="rounded bg-slate-800 px-2 py-0.5 text-[11px] text-slate-300">
                      {p.badge}
                    </span>
                  </div>
                  <p className="mt-2 text-xs leading-relaxed text-slate-400">{p.description}</p>
                </div>
              ))}
            </div>
          </div>

          {/* STEP 3: 직관적인 조건 조절 (JSON 불필요!) */}
          <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-5">
            <h4 className="text-sm font-semibold text-slate-200">
              3. [{activePreset.name}] 조건 미세 조정
            </h4>
            <p className="mt-1 text-xs text-slate-400">
              급락 감시 조건처럼 직관적인 % 수치로 조정할 수 있습니다.
            </p>

            <div className="mt-4 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
              {/* 목표 익절 */}
              <div>
                <span className="text-xs text-slate-400">🎯 목표 익절선 (%)</span>
                <div className="mt-1.5 flex items-center gap-2">
                  <input
                    type="number"
                    step="0.1"
                    min="0.1"
                    max="50"
                    value={takeProfitPct}
                    onChange={(e) => setTakeProfitPct(e.target.value)}
                    className="w-24 rounded-lg border border-slate-700 bg-slate-900 p-2 text-sm text-red-400 font-bold focus:border-cyan-500 focus:outline-none"
                  />
                  <span className="text-sm text-slate-300">% 상승 시</span>
                </div>
                <div className="mt-2 flex gap-1">
                  {['0.8', '1.2', '1.5', '2.0'].map((v) => (
                    <button
                      key={v}
                      type="button"
                      onClick={() => setTakeProfitPct(v)}
                      className={`rounded px-2 py-1 text-xs ${takeProfitPct === v ? 'bg-cyan-700 text-white' : 'bg-slate-800 text-slate-400'}`}
                    >
                      +{v}%
                    </button>
                  ))}
                </div>
              </div>

              {/* 손절 기준 */}
              <div>
                <span className="text-xs text-slate-400">🛡️ 손절 기준선 (%)</span>
                <div className="mt-1.5 flex items-center gap-2">
                  <input
                    type="number"
                    step="0.1"
                    min="0.1"
                    max="20"
                    value={stopLossPct}
                    onChange={(e) => setStopLossPct(e.target.value)}
                    className="w-24 rounded-lg border border-slate-700 bg-slate-900 p-2 text-sm text-blue-400 font-bold focus:border-cyan-500 focus:outline-none"
                  />
                  <span className="text-sm text-slate-300">% 하락 시</span>
                </div>
                <div className="mt-2 flex gap-1">
                  {['0.5', '0.8', '1.0', '1.5'].map((v) => (
                    <button
                      key={v}
                      type="button"
                      onClick={() => setStopLossPct(v)}
                      className={`rounded px-2 py-1 text-xs ${stopLossPct === v ? 'bg-cyan-700 text-white' : 'bg-slate-800 text-slate-400'}`}
                    >
                      -{v}%
                    </button>
                  ))}
                </div>
              </div>

              {/* 거래량 급증 배수 */}
              <div>
                <span className="text-xs text-slate-400">📊 거래량 급증 기준</span>
                <div className="mt-1.5 flex items-center gap-2">
                  <input
                    type="number"
                    step="0.1"
                    min="0.5"
                    max="10"
                    value={volRatio}
                    onChange={(e) => setVolRatio(e.target.value)}
                    className="w-24 rounded-lg border border-slate-700 bg-slate-900 p-2 text-sm text-cyan-300 font-bold focus:border-cyan-500 focus:outline-none"
                  />
                  <span className="text-sm text-slate-300">배 이상</span>
                </div>
                <div className="mt-2 flex gap-1">
                  {['1.2', '1.5', '2.0', '3.0'].map((v) => (
                    <button
                      key={v}
                      type="button"
                      onClick={() => setVolRatio(v)}
                      className={`rounded px-2 py-1 text-xs ${volRatio === v ? 'bg-cyan-700 text-white' : 'bg-slate-800 text-slate-400'}`}
                    >
                      {v}x
                    </button>
                  ))}
                </div>
              </div>

              {/* 보유 시간 */}
              <div>
                <span className="text-xs text-slate-400">⏱️ 관찰 / 최대 보유 시간</span>
                <div className="mt-1.5 flex items-center gap-2 text-sm text-slate-300">
                  <span>관찰</span>
                  <input
                    type="number"
                    min="5"
                    max="60"
                    value={lookbackMin}
                    onChange={(e) => setLookbackMin(Number(e.target.value))}
                    className="w-14 rounded-lg border border-slate-700 bg-slate-900 p-2 text-center text-sm text-white font-bold"
                  />
                  <span>분 / 보유</span>
                  <input
                    type="number"
                    min="1"
                    max="120"
                    value={maxBarsMin}
                    onChange={(e) => setMaxBarsMin(Number(e.target.value))}
                    className="w-14 rounded-lg border border-slate-700 bg-slate-900 p-2 text-center text-sm text-white font-bold"
                  />
                  <span>분</span>
                </div>
              </div>
            </div>

            {/* 조건 요약 가이드 문구 */}
            <div className="mt-4 rounded-lg bg-slate-900 p-3 text-xs text-slate-400">
              💡 <strong>작동 규칙:</strong> {selectedCoin}에서 최근 {lookbackMin}분 대비 거래량이 {volRatio}배 이상 급증하며 {activePreset.name} 신호가 뜨면 진입합니다. 진입 후 <span className="text-red-400">+{takeProfitPct}%</span> 상승 시 자동 익절, <span className="text-blue-400">-{stopLossPct}%</span> 하락 시 즉시 구조 손절하며, 최대 {maxBarsMin}분이 지나면 안전 청산합니다.
            </div>
          </div>

          {/* STEP 4: ⚡ 원클릭 즉시 분석 실행 버튼 */}
          <div className="pt-2">
            <button
              disabled={busy}
              onClick={runOneClickAnalysis}
              className="flex w-full items-center justify-center gap-2 rounded-xl bg-cyan-600 px-6 py-4 text-base font-bold text-white shadow-lg shadow-cyan-900/40 hover:bg-cyan-500 disabled:opacity-50 transition"
            >
              <span>⚡</span>
              <span>{busy ? '전략 분석 진행 중...' : `[${selectedCoin.replace('KRW-', '')}] ${activePreset.name} 전략 원클릭 분석 실행`}</span>
            </button>
            <p className="mt-2 text-center text-xs text-slate-500">
              클릭 시 거래소 최신 캔들 로딩부터 데이터셋 고정, 70% 학습 / 30% 미공개 검증 시뮬레이션까지 약 3초 만에 원스톱으로 실행됩니다.
            </p>
          </div>
        </div>
      </div>

      {/* 3. 분석 결과 검증 보고서 및 실전 승격 섹션 */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-xl font-bold text-white">📊 전략 백테스트 검증 결과 보고서</h3>
          <span className="text-xs text-slate-400">
            총 {lab?.reports.length ?? 0}개 검증 이력 (최근 72시간 유효)
          </span>
        </div>

        {(!lab?.reports || lab.reports.length === 0) && (
          <div className="rounded-xl border border-dashed border-slate-700 p-8 text-center text-slate-400">
            아직 분석된 전략 보고서가 없습니다. 위에서 [⚡ 원클릭 분석 실행]을 눌러 첫 백테스트를 실행해보세요!
          </div>
        )}

        {lab?.reports.map((report) => {
          const isEligible = report.promotion_eligible;
          const isExpired = Date.now() - Date.parse(report.created_at) > 72 * 3600000;
          const isActive = lab.active_version === report.version_id;

          return (
            <article
              key={report.id}
              className={`rounded-2xl border p-5 sm:p-6 transition shadow-lg ${
                isActive
                  ? 'border-emerald-600 bg-emerald-950/20'
                  : isEligible
                  ? 'border-cyan-700 bg-slate-900/90'
                  : 'border-slate-800 bg-slate-950/60 opacity-80'
              }`}
            >
              {/* 상단 타이틀 & 뱃지 */}
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 pb-4">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold text-slate-400">{report.symbol}</span>
                    <span className="rounded bg-slate-800 px-2 py-0.5 text-xs text-slate-300">
                      ID: {report.version_id.slice(0, 10)}
                    </span>
                    {isActive && (
                      <span className="rounded-full bg-emerald-900 px-2.5 py-0.5 text-xs font-bold text-emerald-200 border border-emerald-500">
                        ● 현재 실전 활성 중
                      </span>
                    )}
                  </div>
                  <h4 className="mt-1 text-lg font-bold text-white">
                    {report.symbol} · {report.version_id.startsWith('312b') ? '급락 후 반등 (Pullback)' : '맞춤형 전략'}
                  </h4>
                </div>

                <div className="text-right">
                  <span
                    className={`inline-block rounded-full px-3 py-1 text-xs font-bold ${
                      isEligible
                        ? 'border border-emerald-700 bg-emerald-950 text-emerald-300'
                        : 'border border-amber-800 bg-amber-950 text-amber-300'
                    }`}
                  >
                    {isEligible ? '✅ 실전 승격 가능 (기술 게이트 통과)' : '⚠️ 실전 반영 차단 (검증 기준 미달)'}
                  </span>
                  <p className="mt-1 text-[11px] text-slate-400">
                    생성: {new Date(report.created_at).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' })}
                  </p>
                </div>
              </div>

              {/* 성과 지표 비교 그리드 (학습 vs 별도 검증) */}
              <div className="mt-4 grid gap-4 lg:grid-cols-2">
                <SummaryMetrics value={report.training} title="1. 과거 학습 구간 성과 (70%)" />
                <SummaryMetrics value={report.validation} title="2. 별도 검증 구간 성과 (30%)" isValidation />
              </div>

              {/* 하단: 실전 승인 및 적용 영역 */}
              <div className="mt-5 flex flex-wrap items-center justify-between gap-4 border-t border-slate-800 pt-4">
                <div className="text-xs text-slate-400">
                  {isEligible ? (
                    <p className="text-emerald-300">
                      💡 <strong>검증 통과:</strong> 최소 5회 이상 거래, 순손익 플러스, MDD 10% 이하 등 필수 안전 요건을 통과했습니다.
                    </p>
                  ) : (
                    <p className="text-amber-300">
                      ⚠️ <strong>미통과 사유:</strong> {report.gate_reason || '검증 구간 거래 횟수 부족 또는 기대 손익 마이너스'}. 실전 승격이 차단됩니다.
                    </p>
                  )}
                </div>

                <div>
                  <button
                    disabled={busy || !isEligible || isExpired || isActive}
                    onClick={() => promote(report)}
                    className={`rounded-xl px-5 py-3 text-sm font-bold shadow-md transition ${
                      isActive
                        ? 'bg-slate-800 text-slate-500 cursor-not-allowed'
                        : isEligible
                        ? 'bg-emerald-600 text-white hover:bg-emerald-500 shadow-emerald-900/30'
                        : 'bg-slate-800 text-slate-500 cursor-not-allowed'
                    }`}
                  >
                    {isActive ? '현재 실전 사용 중' : '⚡ 이 전략을 실전 신호로 즉시 승인/적용'}
                  </button>
                </div>
              </div>
            </article>
          );
        })}
      </div>

      {/* 4. 고급 엔지니어링 모드 (접이식 아코디언) */}
      {showAdvanced && (
        <div className="rounded-2xl border border-slate-800 bg-slate-950 p-6 space-y-6">
          <div className="border-b border-slate-800 pb-3">
            <h3 className="text-lg font-bold text-slate-300">⚙️ 고급 엔지니어링 설정 (직접 JSON 편집 / 수수료율)</h3>
            <p className="text-xs text-slate-500">
              기존 파이프라인의 JSON 파라미터 직접 제어, 격자 최적화 탐색, 과거 잡 큐 내역을 직접 확인합니다.
            </p>
          </div>

          <div className="grid gap-5 lg:grid-cols-2">
            {/* 데이터셋 수동 관리 */}
            <article className="rounded-xl border border-slate-800 p-4 bg-slate-900/40">
              <h4 className="font-semibold text-sm text-slate-300">1 · 데이터셋 수동 관리</h4>
              <div className="mt-3 flex flex-wrap gap-2">
                <input
                  aria-label="데이터셋 종목"
                  className="w-36 rounded bg-slate-800 p-2 text-sm text-white"
                  value={symbol}
                  onChange={(e) => setSymbol(e.target.value.toUpperCase())}
                />
                <button
                  disabled={busy}
                  className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800"
                  onClick={() => action(() => api('lab/runs', 'POST', { operation: 'history', symbol, pages: 5 }))}
                >
                  과거 1,000분 수동 로딩
                </button>
                <button
                  disabled={busy}
                  className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800"
                  onClick={() =>
                    action(async () => {
                      const d = await api<{ id: string }>('lab/datasets', 'POST', { symbol });
                      setDataset(d.id);
                    })
                  }
                >
                  데이터셋 고정
                </button>
              </div>
              <select
                aria-label="데이터셋 선택"
                className="mt-3 w-full rounded bg-slate-800 p-2 text-xs text-slate-300"
                value={dataset}
                onChange={(e) => setDataset(e.target.value)}
              >
                <option value="">데이터셋 없음</option>
                {lab?.datasets.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.symbol} · {d.id.slice(0, 12)}
                  </option>
                ))}
              </select>
            </article>

            {/* 전략 버전 수동 관리 */}
            <article className="rounded-xl border border-slate-800 p-4 bg-slate-900/40">
              <h4 className="font-semibold text-sm text-slate-300">2 · 전략 버전 JSON 수동 저장</h4>
              <select
                aria-label="전략 버전"
                className="mt-3 w-full rounded bg-slate-800 p-2 text-xs text-slate-300"
                value={version}
                onChange={(e) => {
                  setVersion(e.target.value);
                  setParams(JSON.stringify(lab?.versions.find((v) => v.id === e.target.value)?.params ?? {}, null, 2));
                }}
              >
                {lab?.versions.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.kind} · {v.id}
                  </option>
                ))}
              </select>
              <textarea
                aria-label="전략 파라미터"
                className="mt-3 h-28 w-full rounded bg-slate-950 p-2.5 font-mono text-xs text-slate-300"
                value={params}
                onChange={(e) => setParams(e.target.value)}
              />
              <button
                disabled={busy || !version}
                className="mt-2 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800"
                onClick={() =>
                  action(async () => {
                    const saved = await api<{ id: string }>('lab/versions', 'POST', {
                      kind: lab?.versions.find((v) => v.id === version)?.kind,
                      params: JSON.parse(params),
                    });
                    setVersion(saved.id);
                  })
                }
              >
                새 버전 저장 · 실전 미반영
              </button>
            </article>
          </div>

          {/* 비용 가정 및 격자 최적화 */}
          <article className="rounded-xl border border-slate-800 p-4 bg-slate-900/40">
            <h4 className="font-semibold text-sm text-slate-300">3 · 비용 가정 및 격자 최적화</h4>
            <div className="mt-3 flex flex-wrap items-center gap-4 text-xs text-slate-300">
              <label>
                편도 수수료율{' '}
                <input className="w-20 rounded bg-slate-800 p-1.5" value={fee} onChange={(e) => setFee(e.target.value)} />
              </label>
              <label>
                편도 슬리피지{' '}
                <input className="w-20 rounded bg-slate-800 p-1.5" value={slip} onChange={(e) => setSlip(e.target.value)} />
              </label>
              <label className="flex items-center gap-1.5">
                <input type="checkbox" checked={optimize} onChange={(e) => setOptimize(e.target.checked)} /> 학습 구간 격자 최적화
              </label>
            </div>
            {optimize && (
              <textarea
                aria-label="최적화 조합"
                className="mt-3 h-16 w-full rounded bg-slate-950 p-2 font-mono text-xs text-slate-300"
                value={grid}
                onChange={(e) => setGrid(e.target.value)}
              />
            )}
            <div className="mt-3 flex items-center justify-between">
              <button
                disabled={busy || !version || !dataset}
                className="rounded-lg border border-slate-700 bg-slate-800 px-4 py-2 text-xs text-white hover:bg-slate-700"
                onClick={() =>
                  action(() =>
                    api('lab/runs', 'POST', {
                      version_id: version,
                      dataset_id: dataset,
                      fee_rate: fee,
                      slippage: slip,
                      grid: optimize ? JSON.parse(grid) : undefined,
                    })
                  )
                }
              >
                수동 백테스트 / 최적화 실행
              </button>
              <div className="text-xs text-slate-500">
                {lab?.jobs.slice(0, 3).map((j) => (
                  <span key={j.id} className="ml-2">
                    {j.id.slice(0, 6)}: {j.status}
                  </span>
                ))}
              </div>
            </div>
          </article>
        </div>
      )}

      {/* 5. 실전 기록 / 모의 성과(PAPER) 비교 */}
      <article className="rounded-2xl border border-slate-700 bg-slate-900/60 p-5 sm:p-6">
        <h3 className="font-bold text-lg text-white">가상 모의 계좌(PAPER) 현황</h3>
        <p className="mt-1 text-xs text-slate-400">
          모의 자본 150,000원으로 독립 구동되며, 실시간 빗썸 호가 잔량과 수수료(0.04%)·슬리피지(0.1%)가 반영됩니다.
        </p>

        <div className="mt-4 grid gap-3 md:grid-cols-2">
          {lab?.paper_accounts.map((p) => (
            <div key={p.version_id} className="rounded-xl border border-slate-800 bg-slate-950 p-4">
              <div className="flex items-center justify-between">
                <span className="text-sm font-semibold text-cyan-300">PAPER 계좌</span>
                <span className="text-xs text-slate-400">버전: {p.version_id.slice(0, 10)}</span>
              </div>
              <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-300">
                <span>모의 현금: <strong className="text-white">{money(p.cash)}</strong></span>
                <span>실현 손익: <strong className={Number(p.realized) > 0 ? 'text-red-400' : Number(p.realized) < 0 ? 'text-blue-400' : 'text-slate-300'}>{money(p.realized)}</strong></span>
                <span>보유: {Object.keys(p.position.holdings).length}종목</span>
              </div>
              {lab.comparison.paper_metrics[p.version_id] && (
                <div className="mt-2 text-xs text-slate-400">
                  승률 {pct(lab.comparison.paper_metrics[p.version_id].win_rate)} · MDD {pct(lab.comparison.paper_metrics[p.version_id].mdd)}
                </div>
              )}
            </div>
          ))}
        </div>
      </article>
    </section>
  );
}
