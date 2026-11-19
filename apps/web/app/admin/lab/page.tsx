'use client';
import { useState } from 'react';
import AdminShell from '../admin-shell';
import { RiskBanner, SectionHeader, StatusBadge } from '../components/admin-ui';
import { TOKENS } from '../design-tokens';
import { useAdminStatus } from '../runtime-status';
import ReversionPanel from '../../reversion-panel';
import StrategyComparison from '../../strategy-comparison';
import StrategyLab from '../../strategy-lab';

type LabTab = 'live' | 'experimental' | 'backtest';

const TABS: { id: LabTab; label: string }[] = [
  { id: 'live', label: '운영 전략' },
  { id: 'experimental', label: '실험 전략' },
  { id: 'backtest', label: '백테스트 결과' },
];

export default function AdminLabPage() {
  const { status } = useAdminStatus();
  const [tab, setTab] = useState<LabTab>('live');

  return (
    <AdminShell>
      <div className="space-y-6">
        <div role="tablist" aria-label="전략 메뉴 탭" className="flex flex-wrap gap-2">
          {TABS.map((t) => (
            <button
              key={t.id}
              role="tab"
              type="button"
              aria-selected={tab === t.id}
              onClick={() => setTab(t.id)}
              className={`rounded-lg px-4 py-2 text-xs font-medium transition ${
                tab === t.id
                  ? 'bg-zinc-100 text-zinc-950'
                  : 'border border-[#1E2536] bg-[#121620] text-zinc-400 hover:text-zinc-200'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        {/* 1. 운영 전략 */}
        {tab === 'live' && (
          <div className="space-y-6">
            {!status.followVersion ? (
              <RiskBanner
                tone="danger"
                title="실전 활성 전략 없음 — 신규 자동매수 차단"
                message="현재 실전 신호에 연결된 활성 전략(follow_version)이 없습니다. 가상 추종 전략이 동작하더라도 실전 자동매수는 실행되지 않습니다."
              />
            ) : (
              <RiskBanner
                tone="ok"
                title={`실전 활성 전략 연결됨: ${status.followVersion}`}
                message="실전 자동매수는 관제 상태(RUNNING)와 시세 신선도 조건이 함께 충족될 때만 동작합니다."
              />
            )}

            <section className={`${TOKENS.card} space-y-4 p-5`}>
              <SectionHeader
                title="실전 전략 · 가상 추종 전략 구분"
                subtitle="가상 추종 전략의 성과 비교 결과는 참고용이며, 실전 주문은 활성 전략이 지정된 경우에만 허용됩니다."
                actions={
                  <StatusBadge
                    tone={status.followVersion ? 'ok' : 'warn'}
                    label={status.followVersion ? `실전 활성: ${status.followVersion}` : '실전 활성 전략 없음'}
                  />
                }
              />
              <StrategyComparison />
            </section>
          </div>
        )}

        {/* 2. 실험 전략 */}
        {tab === 'experimental' && (
          <div className="space-y-6">
            <section className={`${TOKENS.card} space-y-4 p-5 text-xs`}>
              <SectionHeader
                title="급락 반등 전략 현재 정책 (Structure Trailing)"
                subtitle="고정 익절 전략(+1.2%)과는 별도로 동작하는 구조 손절·트레일링 정책입니다. 긴급 정지(EMERGENCY_STOP)가 아닌 한 급락 반등 매수는 상시 허용됩니다."
                actions={
                  <StatusBadge
                    tone={status.reversionBuyAllowed ? 'buy' : 'danger'}
                    label={status.reversionBuyAllowed ? '급락 반등 매수 허용' : '급락 반등 매수 차단'}
                  />
                }
              />
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
                <div className={`${TOKENS.subCard} p-3`}>
                  <p className="text-[11px] text-zinc-400">손절 방식</p>
                  <p className="mt-1 font-semibold text-zinc-100">구조 손절</p>
                </div>
                <div className={`${TOKENS.subCard} p-3`}>
                  <p className="text-[11px] text-zinc-400">최대 손절</p>
                  <p className="mt-1 font-mono font-semibold text-sky-400">-1.5%</p>
                </div>
                <div className={`${TOKENS.subCard} p-3`}>
                  <p className="text-[11px] text-zinc-400">트레일링 활성화</p>
                  <p className="mt-1 font-mono font-semibold text-rose-400">+2.0% 도달 시</p>
                </div>
                <div className={`${TOKENS.subCard} p-3`}>
                  <p className="text-[11px] text-zinc-400">트레일링 청산</p>
                  <p className="mt-1 font-mono font-semibold text-zinc-100">최고가 대비 -1.0%</p>
                </div>
                <div className={`${TOKENS.subCard} p-3`}>
                  <p className="text-[11px] text-zinc-400">시간 청산 예외</p>
                  <p className="mt-1 font-semibold text-emerald-300">상승 추세 중 시간 청산 제외</p>
                </div>
              </div>
            </section>
            <ReversionPanel />
          </div>
        )}

        {/* 3. 백테스트 결과 */}
        {tab === 'backtest' && (
          <div className="space-y-4">
            <RiskBanner
              tone="warn"
              title="백테스트 실행과 실전 전략 승격은 분리되어 있습니다"
              message="백테스트 결과는 과거 시세 기반 검증이며, 실전 전략 반영은 운영 설정 및 관제 승인 절차를 거쳐야 합니다."
            />
            <StrategyLab />
          </div>
        )}
      </div>
    </AdminShell>
  );
}
