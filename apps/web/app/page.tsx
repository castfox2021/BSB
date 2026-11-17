'use client';
import { useEffect, useState } from 'react';
import Link from 'next/link';
import { PublicFooter, PublicHeader } from './components/public-nav';
import { priceText, pnlColor, signedMoney } from './quiet-types';

type MarketSummary = {
  connected_markets: number;
  ready_markets: number;
  top_drops: { symbol: string; coin_name: string; price: string | null; change_1m: string | null }[];
  top_gainers: { symbol: string; coin_name: string; price: string | null; change_1m: string | null }[];
};

type PerfSummary = {
  policies: { policy: string; trades: number; realized: string; win_rate: string | null; profit_factor: string | null }[];
};

export default function LandingPage() {
  const [mkt, setMkt] = useState<MarketSummary | null>(null);
  const [perf, setPerf] = useState<PerfSummary | null>(null);

  useEffect(() => {
    fetch('/api/manual/public/market/summary')
      .then((r) => (r.ok ? r.json() : null))
      .then(setMkt)
      .catch(() => {});
    fetch('/api/manual/public/performance/summary')
      .then((r) => (r.ok ? r.json() : null))
      .then(setPerf)
      .catch(() => {});
  }, []);

  const primary = perf?.policies?.find((p) => p.policy === 'partial_tp_trailing') ?? perf?.policies?.[0];

  return (
    <div className="min-h-screen bg-[#0B0E14] text-zinc-100">
      <PublicHeader />
      <main className="mx-auto max-w-6xl space-y-16 px-4 py-12 sm:px-6">
        {/* Hero */}
        <section className="rounded-2xl border border-[#1E2536] bg-[#121620] p-8 sm:p-12">
          <span className="inline-flex items-center gap-2 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-3 py-1 text-xs font-medium text-emerald-300">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
            실시간 KRW 마켓 {mkt?.connected_markets ?? '—'}종목 감시 중
          </span>
          <h1 className="mt-5 text-3xl font-bold tracking-tight text-zinc-100 sm:text-5xl">
            단기 급락 포착부터 분할 익절 검증까지,
            <br />
            데이터 기반 크립토 퀀트 플랫폼
          </h1>
          <p className="mt-4 max-w-2xl text-sm leading-relaxed text-zinc-400 sm:text-base">
            빗썸 전 종목 실시간 호가·체결 스트림을 분석해 급락 후 반등 구간을 포착하고, Task 16 구조적 손절과 Task 17 분할 익절·동적 트레일링 성과를 투명한 가상 시뮬레이션 원장으로 검증합니다.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link href="/market" className="rounded-lg bg-zinc-100 px-5 py-2.5 text-sm font-semibold text-zinc-950 hover:bg-white">
              실시간 시장 보기
            </Link>
            <Link href="/strategies" className="rounded-lg border border-[#2A344B] bg-[#181E2B] px-5 py-2.5 text-sm font-medium text-zinc-100 hover:border-zinc-400">
              전략 관찰하기
            </Link>
            <Link href="/performance" className="rounded-lg border border-[#1E2536] px-5 py-2.5 text-sm font-medium text-zinc-300 hover:border-zinc-500">
              공개 성과 분석
            </Link>
          </div>
        </section>

        {/* 실시간 시장 & 공개 성과 미리보기 */}
        <section className="grid gap-6 lg:grid-cols-2">
          <div className="rounded-xl border border-[#1E2536] bg-[#121620] p-6">
            <div className="flex items-center justify-between border-b border-[#1E2536] pb-3">
              <h2 className="text-base font-semibold">실시간 시장 급락/상승 TOP</h2>
              <Link href="/market" className="text-xs text-zinc-400 hover:text-zinc-200">전체 보기 →</Link>
            </div>
            <div className="mt-4 space-y-2.5 text-xs font-mono tabular-nums">
              {(mkt?.top_drops ?? []).map((item) => (
                <div key={item.symbol} className="flex items-center justify-between rounded-lg border border-[#1E2536] bg-[#0E1119] px-3.5 py-2.5">
                  <span className="font-sans font-medium text-zinc-200">{item.coin_name} ({item.symbol.replace('KRW-', '')})</span>
                  <div className="flex items-center gap-3">
                    <span className="text-zinc-300">{priceText(item.price)}원</span>
                    <span className="text-sky-400">{item.change_1m ? `${(Number(item.change_1m) * 100).toFixed(2)}%` : '—'}</span>
                  </div>
                </div>
              ))}
              {!mkt?.top_drops?.length && <p className="py-4 text-center text-zinc-500">시세 연결 중...</p>}
            </div>
          </div>

          <div className="rounded-xl border border-[#1E2536] bg-[#121620] p-6">
            <div className="flex items-center justify-between border-b border-[#1E2536] pb-3">
              <h2 className="text-base font-semibold">공개 가상 전략 성과 요약</h2>
              <Link href="/performance" className="text-xs text-zinc-400 hover:text-zinc-200">상세 통계 →</Link>
            </div>
            <div className="mt-4 grid grid-cols-2 gap-3">
              <div className="rounded-lg border border-[#1E2536] bg-[#0E1119] p-4">
                <p className="text-xs text-zinc-400">검증 거래 수</p>
                <p className="mt-1 font-mono text-xl font-semibold">{primary?.trades ?? 0}건</p>
              </div>
              <div className="rounded-lg border border-[#1E2536] bg-[#0E1119] p-4">
                <p className="text-xs text-zinc-400">가상 승률</p>
                <p className="mt-1 font-mono text-xl font-semibold">
                  {primary?.win_rate ? `${(Number(primary.win_rate) * 100).toFixed(1)}%` : '—'}
                </p>
              </div>
              <div className="rounded-lg border border-[#1E2536] bg-[#0E1119] p-4">
                <p className="text-xs text-zinc-400">가상 순손익</p>
                <p className={`mt-1 font-mono text-xl font-semibold ${pnlColor(primary?.realized)}`}>
                  {signedMoney(primary?.realized ?? '0')}
                </p>
              </div>
              <div className="rounded-lg border border-[#1E2536] bg-[#0E1119] p-4">
                <p className="text-xs text-zinc-400">Profit Factor</p>
                <p className="mt-1 font-mono text-xl font-semibold">{primary?.profit_factor ?? '—'}</p>
              </div>
            </div>
          </div>
        </section>

        {/* 전략 소개 & 작동 방식 */}
        <section className="grid gap-4 md:grid-cols-3">
          {[
            ['1. 전 종목 실시간 스캔', '빗썸 KRW 마켓 전 종목의 1·3·5분 급락과 거래량, 호가 스프레드를 1초 주기로 감시합니다.'],
            ['2. Task 16/17 다층 리스크 관리', '최대 -1.5% 구조적 손절과 TP1(+1.2% 25%) · TP2(+2.5% 25%) · 잔여 50% 동적 트레일링을 가상 검증합니다.'],
            ['3. Task 18 통합 원장 & AI 리포트', 'Decimal 정밀 이동평균 원장과 10가지 분석 뷰, ChatGPT 연동 ZIP 리포트를 제공합니다.'],
          ].map(([title, desc]) => (
            <div key={title} className="rounded-xl border border-[#1E2536] bg-[#121620] p-6">
              <h3 className="text-sm font-semibold text-zinc-100">{title}</h3>
              <p className="mt-2 text-xs leading-relaxed text-zinc-400">{desc}</p>
            </div>
          ))}
        </section>

        {/* 로드맵 & FAQ */}
        <section className="grid gap-6 md:grid-cols-2">
          <div className="rounded-xl border border-[#1E2536] bg-[#121620] p-6">
            <h2 className="text-base font-semibold">플랫폼 로드맵</h2>
            <ul className="mt-3 space-y-2 text-xs text-zinc-400">
              <li>• <strong className="text-zinc-200">Phase 1 (완료)</strong>: 공개 대시보드, 운영자 세션 인증, Task 16/17/18 통합</li>
              <li>• <strong className="text-zinc-200">Phase 2 (예정)</strong>: 업비트(Upbit) 어댑터 연동 및 다중 거래소 비교</li>
              <li>• <strong className="text-zinc-200">Phase 3 (예정)</strong>: 회원별 계좌 격리, 암호화 API 키 금고 및 SaaS 확장</li>
            </ul>
          </div>
          <div className="rounded-xl border border-[#1E2536] bg-[#121620] p-6">
            <h2 className="text-base font-semibold">자주 묻는 질문 (FAQ)</h2>
            <div className="mt-3 space-y-2 text-xs text-zinc-400">
              <p><strong className="text-zinc-200">Q. 공개 화면에 실제 계좌 정보가 노출되나요?</strong><br />아니요. 공개 API는 허용된 시세와 익명화된 가상 시뮬레이션 통계만 반환합니다.</p>
              <p><strong className="text-zinc-200">Q. 운영자 기능은 어디에서 접근하나요?</strong><br />우측 상단「운영자 로그인」을 통해 세션 인증 후 `/admin` 관제 화면을 이용합니다.</p>
            </div>
          </div>
        </section>
      </main>
      <PublicFooter />
    </div>
  );
}
