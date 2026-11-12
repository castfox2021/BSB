'use client';
import { useEffect, useState } from 'react';
import { api, kst, money, pct } from './trading-types';

type Row = {
  trading_available: boolean;
  baseline_ready: boolean;
  paper_held: boolean;
  symbol: string;
  korean_name: string;
  english_name: string;
  price: string | null;
  score: string;
  volume_ratio_5m: string | null;
  turnover_ratio_5m: string | null;
  price_change_5m: string;
  buy_pressure: string | null;
  state: string;
  reason: string;
  candidate: boolean;
  detail_selected: boolean;
  monitored: boolean;
  fresh: boolean;
  ready: boolean;
  held: boolean;
  expected_slippage: string | null;
  first_activity: boolean;
  data_at: string | null;
  ai: { probability: number; model_id: string; advisory_only: boolean } | null;
};

type Model = {
  state: string;
  model_id: string | null;
  inference_count: number;
  error: string | null;
  metrics: {
    train_samples: number;
    validation_samples: number;
    validation_accuracy: number;
    majority_baseline: number;
  } | null;
};

type Status = {
  analysis_budget_krw: string;
  scope: string;
  watch_count: number;
  fresh_count: number;
  candidate_count: number;
  detail_count: number;
  order_ready_count: number;
  held_count: number;
  overloaded: boolean;
  detail_interval_seconds: number;
  trade_queue_size: number;
  universe_refreshed_at: string | null;
  model: Model;
};

type Result = {
  items: Row[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
  status: Status;
};

const increase = (ratio: string | null) =>
  ratio === null ? '준비 중' : `${((Number(ratio) - 1) * 100).toFixed(0)}%`;
const button =
  'rounded-md border border-[#1E2536] bg-[#181E2B] px-3 py-1.5 text-xs font-medium text-zinc-300 hover:border-zinc-500 disabled:opacity-30 transition';

export default function FullMarketScanner({ onPick }: { onPick: (symbol: string) => void }) {
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(20);
  const [only, setOnly] = useState(false);
  const [data, setData] = useState<Result | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const result = await api<Result>(
          `market/scanner?page=${page}&page_size=${size}&candidates_only=${only}`
        );
        if (active) {
          setData(result);
          setError('');
          if (page !== result.page) setPage(result.page);
        }
      } catch {
        if (active) setError('전체 시장 분석 상태를 읽을 수 없습니다.');
      }
    };
    load();
    const id = setInterval(load, 3000);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, [page, size, only]);

  const s = data?.status;
  const model = s?.model;

  return (
    <section className="rounded-xl border border-[#1E2536] bg-[#0E1119] p-5">
      <div className="border-b border-[#1E2536]/80 pb-3">
        <h2 className="text-sm font-semibold text-zinc-100">전체 KRW · 급등 초기 탐지 순위</h2>
        <p className="mt-1 text-xs text-zinc-400">
          진행 중 5분 거래량·거래대금을 직전 5분과 실시간 비교합니다.
        </p>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {[
          ['전체 분석', s?.watch_count],
          ['최신 시세', s?.fresh_count],
          ['초기 후보', s?.candidate_count],
          ['상세 호가', s?.detail_count],
          ['유동성 통과', s?.order_ready_count],
          ['보유 감시', s?.held_count],
        ].map(([label, value]) => (
          <div key={String(label)} className="rounded-lg border border-[#1E2536] bg-[#121620] p-3">
            <p className="text-[11px] text-zinc-400">{label}</p>
            <strong className="mt-1 block font-mono text-base font-semibold tabular-nums text-zinc-100">
              {value ?? '—'}
            </strong>
          </div>
        ))}
      </div>

      <p className="mt-3 font-mono text-[11px] tabular-nums text-zinc-500">
        유동성 검사 예산 {money(s?.analysis_budget_krw)} · 목록 갱신 {kst(s?.universe_refreshed_at)} · 분석 간격{' '}
        {s?.detail_interval_seconds ?? '—'}초 · {s?.overloaded ? '부하 조절 중' : '정상 감시'}
      </p>

      <div className="mt-4 flex flex-wrap items-center gap-3 text-xs">
        <label className="inline-flex items-center gap-2 text-zinc-300">
          <input
            type="checkbox"
            checked={only}
            onChange={(e) => {
              setOnly(e.target.checked);
              setPage(1);
            }}
            className="rounded border-zinc-700 bg-zinc-900"
          />
          <span>급증 후보만 보기</span>
        </label>
        <div className="flex items-center gap-2 sm:ml-auto">
          <button className={button} disabled={page <= 1} onClick={() => setPage(page - 1)}>
            이전
          </button>
          <span className="font-mono tabular-nums text-zinc-400">
            {page} / {data?.pages ?? 1} · {data?.total ?? 0}종목
          </span>
          <button
            className={button}
            disabled={page >= (data?.pages ?? 1)}
            onClick={() => setPage(page + 1)}
          >
            다음
          </button>
          <select
            aria-label="전체 KRW 페이지당 종목 수"
            className="rounded-md border border-[#1E2536] bg-[#121620] px-2.5 py-1.5 text-xs text-zinc-200 focus:outline-none"
            value={size}
            onChange={(e) => {
              setSize(Number(e.target.value));
              setPage(1);
            }}
          >
            {[20, 50, 100].map((n) => (
              <option key={n} value={n}>
                {n}종목씩
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[1100px] text-left text-xs">
          <thead className="border-b border-[#1E2536] text-zinc-400">
            <tr>
              <th className="py-2.5 font-medium">종목 / 시세</th>
              <th className="font-medium">5분 거래량 증가</th>
              <th className="font-medium">5분 거래대금 증가</th>
              <th className="font-medium">5분 가격 변화</th>
              <th className="font-medium">모멘텀</th>
              <th className="font-medium">매수 비중</th>
              <th className="font-medium">상세 / 매수 상태</th>
              <th className="font-medium">예상 슬리피지</th>
              <th className="font-medium">AI 참고</th>
              <th className="font-medium">차트</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[#1E2536]/60">
            {data?.items.map((r) => (
              <tr key={r.symbol} className="hover:bg-[#181E2B]/40 transition">
                <td className="py-3">
                  <strong className="font-medium text-zinc-100">
                    {r.symbol}
                    {r.held ? ' · 실전' : r.paper_held ? ' · 모의' : ''}
                  </strong>
                  <p className="mt-0.5 font-mono tabular-nums text-zinc-400">
                    {r.price === null ? '대기' : `${r.price}원`}
                  </p>
                </td>
                <td className="font-mono tabular-nums text-zinc-300">
                  {!r.baseline_ready ? '준비' : r.first_activity ? '첫 활동' : increase(r.volume_ratio_5m)}
                </td>
                <td className="font-mono tabular-nums text-zinc-300">
                  {!r.baseline_ready ? '준비' : r.first_activity ? '첫 활동' : increase(r.turnover_ratio_5m)}
                </td>
                <td className={`font-mono tabular-nums ${Number(r.price_change_5m) > 0 ? 'text-rose-400' : Number(r.price_change_5m) < 0 ? 'text-sky-400' : 'text-zinc-300'}`}>
                  {pct(r.price_change_5m)}
                </td>
                <td className="font-mono tabular-nums text-zinc-200">{Number(r.score).toFixed(1)}</td>
                <td className="font-mono tabular-nums text-zinc-300">{pct(r.buy_pressure)}</td>
                <td className={r.ready ? 'text-emerald-400' : 'text-zinc-400'}>
                  {!r.monitored
                    ? '연결 대기'
                    : !r.fresh
                    ? '시세 지연'
                    : !r.trading_available
                    ? '거래 제한'
                    : r.detail_selected
                    ? r.reason
                    : r.candidate
                    ? '상세 대기'
                    : '감시 중'}
                  <p className="mt-0.5 text-[11px] text-zinc-500">
                    {r.ready ? '유동성 통과' : r.state}
                  </p>
                </td>
                <td className="font-mono tabular-nums text-zinc-300">{pct(r.expected_slippage)}</td>
                <td className="font-mono tabular-nums text-zinc-400">
                  {r.ai
                    ? `${(r.ai.probability * 100).toFixed(1)}%`
                    : r.candidate
                    ? model?.state === 'UNTRAINED'
                      ? '준비 중'
                      : '대기'
                    : '—'}
                </td>
                <td>
                  <button
                    className={button}
                    disabled={!r.monitored || r.price === null}
                    onClick={() => onPick(r.symbol)}
                  >
                    차트 보기
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {error && (
        <p role="status" className="mt-3 text-xs text-rose-300">
          {error}
        </p>
      )}
      {data?.total === 0 && (
        <p className="mt-4 text-center text-xs text-zinc-500">종목 목록을 불러오는 중입니다.</p>
      )}

      <details className="mt-5 rounded-lg border border-[#1E2536] bg-[#121620] p-3.5 text-xs">
        <summary className="cursor-pointer font-medium text-zinc-400 hover:text-zinc-200">
          로컬 AI 참고 모델 상태
        </summary>
        <p className="mt-2 font-mono text-zinc-400">
          {model?.state ?? '확인 중'} · 추론 {model?.inference_count ?? 0}회 · 모델 {model?.model_id ?? '없음'}
        </p>
        {model?.metrics && (
          <p className="mt-1 font-mono tabular-nums text-zinc-500">
            학습 {model.metrics.train_samples} · 검증 {model.metrics.validation_samples} · 정확도{' '}
            {(model.metrics.validation_accuracy * 100).toFixed(1)}%
          </p>
        )}
        {model?.error && <p className="mt-1 text-rose-300">{model.error}</p>}
      </details>
    </section>
  );
}
