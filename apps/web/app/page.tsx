'use client';
import { useEffect, useRef, useState } from 'react';
import QuietBoard from './quiet-board';
import QuietDetail from './quiet-details';
import QuietSettings from './quiet-settings';
import ManualFillPanel from './manual-fill-panel';
import StrategyComparison from './strategy-comparison';
import StrategyLab from './strategy-lab';
import TradingWorkspace from './trading-review';
import FullMarketScanner from './full-market-scanner';
import PrivateAccount from './private-account';
import ReversionPanel, { Reversion } from './reversion-panel';
import { api, kst, Policy } from './trading-types';
import { Board, PaperTrade, Holding, signedMoney, pnlColor } from './quiet-types';

const menus = [
  ['board', '조용한 신호판'],
  ['chart', '매매 차트'],
  ['journal', '매매노트'],
  ['strategy', '전략 분석'],
  ['settings', '시스템 설정'],
];

export default function Home() {
  const [followTrade, setFollowTrade] = useState<PaperTrade | null>(null);
  const [tab, setTab] = useState('board');
  const [board, setBoard] = useState<Board | null>(null);
  const [error, setError] = useState('');
  const [buyPage, setBuyPage] = useState(1);
  const [sellPage, setSellPage] = useState(1);
  const [revision, setRevision] = useState(0);
  const [trade, setTrade] = useState<PaperTrade | null>(null);
  const [symbol, setSymbol] = useState('KRW-BTC');
  const [journalTab, setJournalTab] = useState('paper');
  const [autoBuyOnly, setAutoBuyOnly] = useState(false);

  useEffect(() => {
    api<Policy>('settings')
      .then((p) => setAutoBuyOnly(Boolean(p.auto_buy_only)))
      .catch(() => {});
  }, [revision]);

  useEffect(() => {
    let active = true;
    let loading = false;
    const load = async () => {
      if (loading) return;
      loading = true;
      try {
        const result = await api<Board>(`board?buy_page=${buyPage}&sell_page=${sellPage}`);
        if (active) {
          setBoard(result);
          setError('');
        }
      } catch {
        if (active) setError('신호판 연결이 끊겼습니다. 표시된 이전 시세를 현재가로 사용하지 마세요.');
      } finally {
        loading = false;
      }
    };
    load();
    const timer = setInterval(load, 2000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [buyPage, sellPage, revision]);

  const selectedTradeId = useRef<string | null>(null);

  const selectTrade = (t: PaperTrade) => {
    selectedTradeId.current = t.id;
    setTrade(t);
    setSymbol(t.symbol);
    setTab('chart');
    api<PaperTrade>(`board/trades/${t.id}`)
      .then((detail) => {
        if (selectedTradeId.current === t.id) setTrade(detail);
      })
      .catch(() => {
        if (selectedTradeId.current === t.id) setError('가상 체결 상세 기록을 읽을 수 없습니다.');
      });
  };

  const selectHolding = (p: Holding) => {
    selectedTradeId.current = p.entry_trade_id ?? null;
    setTrade(null);
    setSymbol(p.symbol);
    setTab('chart');
    if (p.entry_trade_id) {
      api<PaperTrade>(`board/trades/${p.entry_trade_id}`)
        .then((t) => {
          if (selectedTradeId.current === p.entry_trade_id) setTrade(t);
        })
        .catch(() => setError('가상보유의 매수 이유를 읽을 수 없습니다.'));
    }
  };

  return (
    <main className="mx-auto max-w-7xl px-4 py-6 text-zinc-100 sm:px-6 lg:py-8">
      {/* 상단 터미널 헤더 */}
      <header className="border-b border-[#1E2536] pb-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 text-xs font-medium tracking-wider text-zinc-400">
              <span>BITHUMB</span>
              <span className="text-zinc-600">/</span>
              <span>MANUAL FOLLOW TERMINAL</span>
            </div>
            <h1 className="mt-2 text-2xl font-semibold tracking-tight text-zinc-100 sm:text-3xl">
              가상매매 신호판
            </h1>
            <p className="mt-1 text-sm text-zinc-400">
              가상 시뮬레이션 기반 매매 신호 추적 · 실전 주문은 빗썸 직접 실행
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2 text-xs font-medium">
            <span className="inline-flex items-center gap-1.5 rounded-md border border-[#1E2536] bg-[#121620] px-3 py-1.5 text-zinc-300">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
              가상매매
            </span>
            <span
              className={`rounded-md border px-3 py-1.5 ${
                autoBuyOnly
                  ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300'
                  : 'border-[#1E2536] bg-[#121620] text-zinc-400'
              }`}
            >
              {autoBuyOnly ? '자동 실전매수 켜짐 (매도 수동)' : 'API 주문 꺼짐'}
            </span>
          </div>
        </div>

        {/* 핵심 지표 바 */}
        <div className="mt-6 flex flex-wrap items-baseline gap-x-10 gap-y-3 border-t border-[#1E2536]/60 pt-4">
          <div className="flex items-baseline gap-3">
            <span className="text-xs font-medium text-zinc-400">오늘 가상 실현손익</span>
            <strong className={`font-mono text-xl font-semibold tabular-nums sm:text-2xl ${pnlColor(board?.summary.today_realized_krw)}`}>
              {signedMoney(board?.summary.today_realized_krw)}
            </strong>
          </div>
          <div className="flex items-baseline gap-3">
            <span className="text-xs font-medium text-zinc-400">가상보유</span>
            <strong className="font-mono text-xl font-semibold tabular-nums text-zinc-100 sm:text-2xl">
              {board?.summary.holding_count ?? '—'}
              <span className="ml-0.5 text-sm font-normal text-zinc-400">종목</span>
            </strong>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs text-zinc-400 sm:ml-auto">
            <span className="text-zinc-300">{board?.selected_strategy_name ?? '전략 확인 중'}</span>
            <span className="text-zinc-600">·</span>
            <span className="font-mono">{board?.selected_version?.id.slice(0, 8) ?? '—'}</span>
            <span className="text-zinc-600">·</span>
            <span className="font-mono tabular-nums">
              독립 가상 자본 {Number(board?.summary?.initial_capital_krw ?? '150000').toLocaleString('ko-KR')}원
            </span>
          </div>
        </div>
      </header>

      {/* 네비게이션 탭 */}
      <nav aria-label="주 메뉴" className="my-6 flex flex-wrap gap-1.5 border-b border-[#1E2536] pb-3">
        {menus.map(([id, label]) => {
          const active = tab === id;
          return (
            <button
              key={id}
              aria-current={active ? 'page' : undefined}
              className={`rounded-lg px-4 py-2 text-sm font-medium transition ${
                active
                  ? 'bg-zinc-100 text-zinc-950'
                  : 'text-zinc-400 hover:bg-[#121620] hover:text-zinc-200'
              }`}
              onClick={() => setTab(id)}
            >
              {label}
            </button>
          );
        })}
      </nav>

      {/* 실시간 런타임 상태 바 */}
      {tab === 'board' && board && !error && (
        <div className="mb-6 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-[#1E2536] bg-[#121620] px-4 py-2.5 text-xs">
          <div className="flex items-center gap-2.5">
            <span
              className={`h-2 w-2 rounded-full ${
                board.runtime?.running ? 'bg-emerald-400' : 'bg-zinc-500'
              }`}
            />
            <span className={board.runtime?.running ? 'text-zinc-200' : 'text-zinc-400'}>
              {board.runtime?.message ?? '감시 상태 확인 중'}
            </span>
          </div>
          <div className="font-mono tabular-nums text-zinc-500">
            마지막 분석 {kst(board.runtime?.last_analysis_at)} · 시세 연결 {board.runtime?.connected_markets ?? 0}종목
          </div>
        </div>
      )}

      {error && (
        <p className="mb-6 rounded-lg border border-rose-900/50 bg-[#121620] px-4 py-3 text-sm text-rose-300">
          {error}
        </p>
      )}

      {/* 탭 콘텐츠 */}
      {tab === 'board' && (
        <div className="mb-6 grid">
          <ReversionPanel data={(board as (Board & { reversion?: Reversion | null }) | null)?.reversion} />
        </div>
      )}

      {tab === 'board' && (
        <QuietBoard
          board={error ? null : board}
          onTrade={selectTrade}
          onHolding={selectHolding}
          onBuyPage={setBuyPage}
          onSellPage={setSellPage}
        />
      )}

      {tab === 'chart' && (
        <div className="space-y-6">
          <QuietDetail
            trade={trade}
            symbol={symbol}
            onRecord={(t) => {
              setFollowTrade(t);
              setJournalTab('record');
              setTab('journal');
            }}
          />
          <details className="rounded-xl border border-[#1E2536] bg-[#121620] p-4">
            <summary className="cursor-pointer text-sm font-medium text-zinc-300">
              전체 종목·상세 분석 보기
            </summary>
            <div className="mt-4">
              <FullMarketScanner
                onPick={(s) => {
                  setTrade(null);
                  setSymbol(s);
                  window.scrollTo({ top: 0, behavior: 'smooth' });
                }}
              />
            </div>
          </details>
        </div>
      )}

      {tab === 'journal' && (
        <div>
          <div className="mb-6 flex flex-wrap gap-2">
            {[
              ['paper', '가상매매 이력'],
              ['live', '실전 매매노트·복기'],
              ['record', '실제 체결 수동 등록'],
              ['account', '실제 계좌 조회'],
            ].map(([id, label]) => (
              <button
                key={id}
                className={`rounded-lg border px-4 py-2 text-sm font-medium transition ${
                  journalTab === id
                    ? 'border-zinc-500 bg-[#181E2B] text-zinc-100'
                    : 'border-[#1E2536] bg-[#121620] text-zinc-400 hover:text-zinc-200'
                }`}
                onClick={() => setJournalTab(id)}
              >
                {label}
              </button>
            ))}
          </div>
          {journalTab === 'paper' && (
            <QuietBoard
              board={board}
              onTrade={selectTrade}
              onHolding={selectHolding}
              onBuyPage={setBuyPage}
              onSellPage={setSellPage}
            />
          )}
          {journalTab === 'live' && (
            <TradingWorkspace mode="journal" symbol={symbol} ordersEnabled={false} onOrder={() => {}} />
          )}
          {journalTab === 'record' && (
            <ManualFillPanel followTrade={followTrade} onUnlink={() => setFollowTrade(null)} />
          )}
          {journalTab === 'account' && <PrivateAccount />}
        </div>
      )}

      {tab === 'strategy' && (
        <div className="space-y-6">
          <StrategyComparison />
          <StrategyLab />
        </div>
      )}

      {tab === 'settings' && (
        <QuietSettings
          board={board}
          onSaved={() => {
            setBuyPage(1);
            setSellPage(1);
            setRevision((v) => v + 1);
          }}
        />
      )}
    </main>
  );
}
