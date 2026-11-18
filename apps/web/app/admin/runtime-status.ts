'use client';
import { createContext, useContext } from 'react';
import { api, Policy } from '../trading-types';

export type LoadPhase = 'loading' | 'ready' | 'error';

export type UnifiedAdminStatus = {
  phase: LoadPhase;
  headline: string;
  headlineTone: 'ok' | 'warn' | 'danger' | 'neutral';
  actionMessage: string | null;
  orderAllowed: boolean;
  autoBuyOnly: boolean;
  controlState: string;
  controlMode: string;
  followVersion: string | null;
  marketConnected: boolean;
  freshCount: number;
  watchCount: number;
  freshnessOk: boolean;
  privateConnected: boolean;
  privateCode: string | null;
  lastAnalysisAt: string | null;
  policy: Policy | null;
};

export const INITIAL_ADMIN_STATUS: UnifiedAdminStatus = {
  phase: 'loading',
  headline: '운영 상태 확인 중',
  headlineTone: 'neutral',
  actionMessage: null,
  orderAllowed: false,
  autoBuyOnly: false,
  controlState: 'UNKNOWN',
  controlMode: 'UNKNOWN',
  followVersion: null,
  marketConnected: false,
  freshCount: 0,
  watchCount: 0,
  freshnessOk: false,
  privateConnected: false,
  privateCode: null,
  lastAnalysisAt: null,
  policy: null,
};

export const AdminStatusContext = createContext<{
  status: UnifiedAdminStatus;
  refreshStatus: () => Promise<void>;
}>({
  status: INITIAL_ADMIN_STATUS,
  refreshStatus: async () => {},
});

export function useAdminStatus() {
  return useContext(AdminStatusContext);
}

/**
 * Priority order (Section 5):
 * 1. EMERGENCY_STOP
 * 2. Market stale or disconnected
 * 3. Control STOPPED / MANUAL_APPROVAL
 * 4. Auto-buy OFF
 * 5. No active live strategy
 * 6. Order allowed / Normal monitoring
 */
export async function fetchUnifiedAdminStatus(): Promise<UnifiedAdminStatus> {
  const [policy, ctrl, mkt, priv, strat] = await Promise.all([
    api<Policy>('settings').catch(() => null),
    api<{ state?: string; mode?: string }>('trading/control').catch(() => null),
    api<{
      connected_markets?: number;
      last_received_at?: string | null;
      scanner?: { watch_count?: number; fresh_count?: number };
    }>('market/status').catch(() => null),
    api<{ configured?: boolean; code?: string | null }>('private/status').catch(() => null),
    api<{ follow_version?: string | null }>('strategies/overview').catch(() => null),
  ]);

  if (!policy && !ctrl && !mkt) {
    return {
      ...INITIAL_ADMIN_STATUS,
      phase: 'error',
      headline: '운영 상태를 가져올 수 없음 — 실전 주문 상태 미확인',
      headlineTone: 'danger',
      actionMessage: '백엔드 API 응답이 없습니다. 프로세스 상태와 로그를 확인하세요.',
    };
  }

  const autoBuyOnly = Boolean(policy?.auto_buy_only);
  const controlState = ctrl?.state ?? 'STOPPED';
  const controlMode = ctrl?.mode ?? 'MANUAL_APPROVAL';
  const followVersion = strat?.follow_version ?? null;
  const watchCount = Number(mkt?.scanner?.watch_count ?? 0);
  const freshCount = Number(mkt?.scanner?.fresh_count ?? 0);
  const marketConnected = Number(mkt?.connected_markets ?? 0) > 0;
  const freshnessOk = marketConnected && (watchCount === 0 || freshCount >= watchCount * 0.5);
  const privateConnected = Boolean(priv?.configured);

  let headline = '실전 주문 차단됨';
  let headlineTone: UnifiedAdminStatus['headlineTone'] = 'warn';
  let actionMessage: string | null = null;
  let orderAllowed = false;

  if (controlState === 'EMERGENCY_STOP') {
    headline = '실전 주문 차단됨 · 긴급 정지';
    headlineTone = 'danger';
    actionMessage = '긴급 정지(EMERGENCY_STOP)가 활성화되어 모든 실전 주문이 차단되었습니다.';
  } else if (!marketConnected || !freshnessOk) {
    headline = '실전 주문 차단됨 · 시세 지연';
    headlineTone = 'danger';
    actionMessage =
      watchCount > 0
        ? `시세 신선도가 ${freshCount}/${watchCount}로 낮아 신규 실전매수가 차단됐습니다.`
        : '시세 기준 데이터 수집 중 · 신규 실전매수 차단';
  } else if (controlState !== 'RUNNING' || controlMode === 'MANUAL_APPROVAL') {
    headline = autoBuyOnly
      ? `자동매수 대기 · 관제 ${controlState}로 주문 차단`
      : `실전 주문 차단됨 · 관제 ${controlState}`;
    headlineTone = 'warn';
    actionMessage = `자동매매 관제가 ${controlState} (${controlMode}) 상태이므로 신규 실전 주문이 실행되지 않습니다.`;
  } else if (!autoBuyOnly) {
    headline = '실전 주문 차단됨 · 자동매수 OFF';
    headlineTone = 'neutral';
    actionMessage = '자동 실전매수가 꺼져 있습니다. 설정에서 활성화할 수 있습니다.';
  } else if (!followVersion) {
    headline = '실전 주문 차단됨 · 활성 전략 없음';
    headlineTone = 'warn';
    actionMessage = '실전 활성 전략(follow_version)이 지정되지 않아 신규 자동매수가 차단됩니다.';
  } else {
    headline = '주문 가능 · 정상 감시';
    headlineTone = 'ok';
    orderAllowed = true;
  }

  return {
    phase: 'ready',
    headline,
    headlineTone,
    actionMessage,
    orderAllowed,
    autoBuyOnly,
    controlState,
    controlMode,
    followVersion,
    marketConnected,
    freshCount,
    watchCount,
    freshnessOk,
    privateConnected,
    privateCode: priv?.code ?? null,
    lastAnalysisAt: mkt?.last_received_at ?? null,
    policy,
  };
}
