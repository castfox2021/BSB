'use client';
import { useEffect, useState } from 'react';
import AdminShell from '../admin-shell';
import {
  ConfirmActionDialog,
  RiskBanner,
  SectionHeader,
  StatusBadge,
} from '../components/admin-ui';
import { formatKrw, TOKENS } from '../design-tokens';
import { useAdminStatus } from '../runtime-status';
import QuietSettings from '../../quiet-settings';
import { api } from '../../trading-types';
import { Board } from '../../quiet-types';

export default function AdminSettingsPage() {
  const { status, refreshStatus } = useAdminStatus();
  const [board, setBoard] = useState<Board | null>(null);
  const [rev, setRev] = useState(0);
  const [actionMsg, setActionMsg] = useState('');
  const [busy, setBusy] = useState(false);

  // Dialog states
  const [confirmAutoBuyOpen, setConfirmAutoBuyOpen] = useState(false);
  const [confirmSafetyResetOpen, setConfirmSafetyResetOpen] = useState(false);
  const [confirmEmergencyStopOpen, setConfirmEmergencyStopOpen] = useState(false);

  // Security & MFA states
  const [mfaEnabled, setMfaEnabled] = useState(false);
  const [mfaSecret, setMfaSecret] = useState('');
  const [mfaCode, setMfaCode] = useState('');
  const [disableCode, setDisableCode] = useState('');
  const [mfaMsg, setMfaMsg] = useState('');
  const [isHttps, setIsHttps] = useState(true);

  // Advanced system states (collapsed by default)
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [pgStatus, setPgStatus] = useState<{
    configured: boolean;
    identity_mode?: string;
    audit_mode?: string;
    analytics_mode?: string;
    driver_available: boolean;
    connected: boolean;
    pg_version?: string | null;
    parity_ok: boolean;
    error?: string | null;
  } | null>(null);
  const [pgMsg, setPgMsg] = useState('');
  const [pgBusy, setPgBusy] = useState(false);
  const [memStats, setMemStats] = useState<{
    vm_rss_mb: number | null;
    vm_hwm_mb: number | null;
    vm_swap_mb: number | null;
    threads: number;
    trade_queue_size: number;
    latest_symbols_count: number;
    postgres_identity_enabled: boolean;
  } | null>(null);
  const [members, setMembers] = useState<
    { id: string; email: string; role: string; mfa_enabled: boolean; exchanges: string[]; credentials_count: number; created_at: string }[]
  >([]);

  function refreshMemory() {
    api<NonNullable<typeof memStats>>('admin/system/memory').then(setMemStats).catch(() => {});
  }

  useEffect(() => {
    setIsHttps(typeof window !== 'undefined' ? window.location.protocol === 'https:' : true);
    api<Board>('board?buy_page=1&sell_page=1').then(setBoard).catch(() => {});
    fetch('/api/manual/auth/session')
      .then((r) => r.json())
      .then((s) => setMfaEnabled(Boolean(s.mfa_enabled)))
      .catch(() => {});
  }, [rev]);

  useEffect(() => {
    if (!showAdvanced) return;
    api<NonNullable<typeof pgStatus>>('admin/pg-migration/status').then(setPgStatus).catch(() => {});
    api<{ items: typeof members }>('admin/members').then((d) => setMembers(d.items ?? [])).catch(() => {});
    refreshMemory();
  }, [showAdvanced, rev]);

  async function toggleAutoBuy(enable: boolean) {
    setBusy(true);
    setActionMsg('');
    try {
      const res = await fetch('/api/manual/settings', {
        method: 'PATCH',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ auto_buy_only: enable }),
      });
      if (res.ok) {
        setActionMsg(enable ? '자동 실전매수 설정을 켰습니다.' : '자동 실전매수를 껐습니다.');
        setRev((v) => v + 1);
        await refreshStatus();
      }
    } finally {
      setBusy(false);
      setConfirmAutoBuyOpen(false);
    }
  }

  async function runControlAction(action: 'stop' | 'emergency-stop') {
    setBusy(true);
    setActionMsg('');
    try {
      const res = await fetch('/api/manual/trading/control', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ action }),
      });
      if (res.ok) {
        setActionMsg(action === 'emergency-stop' ? '긴급 정지(EMERGENCY_STOP)를 발동했습니다.' : '관제를 STOPPED 상태로 전환했습니다.');
        await refreshStatus();
      }
    } finally {
      setBusy(false);
      setConfirmEmergencyStopOpen(false);
    }
  }

  async function runSafetyReset() {
    setBusy(true);
    setActionMsg('');
    try {
      const res = await fetch('/api/manual/admin/safety-reset', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: '{}',
      });
      if (res.ok) {
        setActionMsg('긴급 안전 초기화 완료: 자동매수 OFF · 150,000원 / 50,000원 / 3,000원 복원');
        setRev((v) => v + 1);
        await refreshStatus();
      }
    } finally {
      setBusy(false);
      setConfirmSafetyResetOpen(false);
    }
  }

  async function setupMfa() {
    const res = await fetch('/api/manual/auth/mfa/setup', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: '{}',
    });
    const d = await res.json().catch(() => ({}));
    if (d.secret) setMfaSecret(d.secret);
  }

  async function enableMfa() {
    const res = await fetch('/api/manual/auth/mfa/enable', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ code: mfaCode }),
    });
    if (res.ok) {
      setMfaSecret('');
      setMfaCode('');
      setMfaEnabled(true);
      setMfaMsg('운영자 2단계 인증(TOTP)이 활성화되었습니다.');
    } else {
      setMfaMsg('인증 코드가 올바르지 않습니다.');
    }
  }

  async function disableMfa() {
    const res = await fetch('/api/manual/auth/mfa/disable', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ code: disableCode }),
    });
    if (res.ok) {
      setDisableCode('');
      setMfaEnabled(false);
      setMfaMsg('운영자 2단계 인증(TOTP)이 비활성화되었습니다.');
    } else {
      setMfaMsg('유효하지 않은 TOTP 코드입니다.');
    }
  }

  async function applyPgMigration() {
    if (!window.confirm('현재 SQLite 데이터를 PostgreSQL(DATABASE_URL)에 멱등 이관할까요?')) return;
    setPgBusy(true);
    setPgMsg('');
    try {
      const res = await fetch('/api/manual/admin/pg-migration/apply', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: '{}',
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) {
        setPgMsg(`이관 실패: ${d.detail || d.error || res.status}`);
        return;
      }
      if (d.status) setPgStatus(d.status);
      setPgMsg(`PostgreSQL 이관 완료 (정합성: ${d.status?.parity_ok ? '일치' : '확인 필요'})`);
    } finally {
      setPgBusy(false);
    }
  }

  async function pruneArchive() {
    if (!window.confirm('7일 이전의 replay_candles 및 market_events 아카이브를 정리할까요?')) return;
    const res = await fetch('/api/manual/admin/system/prune-archive', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ retention_days: 7 }),
    });
    const d = await res.json().catch(() => ({}));
    if (res.ok) {
      setPgMsg(`아카이브 정리 완료: replay_candles ${d.replay_candles_deleted ?? 0}건, market_events ${d.market_events_deleted ?? 0}건 삭제`);
      refreshMemory();
    }
  }

  return (
    <AdminShell>
      <div className="space-y-6">
        {actionMsg && <RiskBanner tone="ok" title="설정 반영" message={actionMsg} />}

        {/* 1. 안전 및 주문 (가장 상단) */}
        <section aria-label="안전 및 주문" className={`${TOKENS.card} space-y-5 p-6 text-xs`}>
          <SectionHeader
            title="1. 안전 및 주문"
            subtitle="자동 실전매수 활성화는 4단계 확인 절차를 거쳐야 하며, 안전 초기화로 즉시 기본 한도를 복원할 수 있습니다."
            actions={<StatusBadge tone={status.headlineTone} label={status.headline} />}
          />

          <div className="grid gap-4 lg:grid-cols-3">
            {/* 자동 실전매수 제어 */}
            <div className={`${TOKENS.subCard} flex flex-col justify-between gap-3 p-4`}>
              <div>
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-zinc-100">자동 실전매수</span>
                  <StatusBadge tone={status.autoBuyOnly ? 'warn' : 'neutral'} label={status.autoBuyOnly ? 'ON' : 'OFF'} />
                </div>
                <p className="mt-1.5 text-zinc-400">
                  활성화 시 위험 한도 요약 및 확인 문구 입력을 요구합니다.
                </p>
              </div>
              <div className="flex gap-2">
                {status.autoBuyOnly ? (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => toggleAutoBuy(false)}
                    className="rounded-lg border border-amber-500/40 bg-amber-500/15 px-3 py-2 font-semibold text-amber-300 hover:bg-amber-500/25 disabled:opacity-40"
                  >
                    자동매수 즉시 끄기
                  </button>
                ) : (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => setConfirmAutoBuyOpen(true)}
                    className="rounded-lg border border-emerald-500/40 bg-emerald-500/15 px-3 py-2 font-semibold text-emerald-300 hover:bg-emerald-500/25 disabled:opacity-40"
                  >
                    자동매수 활성화 (확인 절차)
                  </button>
                )}
              </div>
            </div>

            {/* 관제 상태 & 긴급 정지 */}
            <div className={`${TOKENS.subCard} flex flex-col justify-between gap-3 p-4`}>
              <div>
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-zinc-100">관제 상태</span>
                  <StatusBadge
                    tone={status.controlState === 'RUNNING' ? 'ok' : status.controlState === 'EMERGENCY_STOP' ? 'danger' : 'warn'}
                    label={status.controlState}
                  />
                </div>
                <p className="mt-1.5 text-zinc-400">모드: {status.controlMode}</p>
              </div>
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => runControlAction('stop')}
                  className="rounded-lg border border-[#1E2536] bg-[#121620] px-3 py-2 text-zinc-200 hover:border-zinc-500 disabled:opacity-40"
                >
                  관제 STOPPED 전환
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => setConfirmEmergencyStopOpen(true)}
                  className="rounded-lg border border-red-500/50 bg-red-500/15 px-3 py-2 font-semibold text-red-300 hover:bg-red-500/25 disabled:opacity-40"
                >
                  긴급 정지 (EMERGENCY)
                </button>
              </div>
            </div>

            {/* 긴급 안전 초기화 (주황색 보호 버튼) */}
            <div className={`${TOKENS.subCard} flex flex-col justify-between gap-3 p-4`}>
              <div>
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-zinc-100">안전 초기화</span>
                  <StatusBadge tone="warn" label="보호 동작" />
                </div>
                <p className="mt-1.5 text-zinc-400">
                  자동매수 OFF · 150,000원 / 50,000원 / 3,000원 안전 한도로 복원합니다.
                </p>
              </div>
              <button
                type="button"
                disabled={busy}
                onClick={() => setConfirmSafetyResetOpen(true)}
                className="w-fit rounded-lg border border-amber-500/40 bg-amber-500/15 px-3.5 py-2 font-semibold text-amber-300 hover:bg-amber-500/25 disabled:opacity-40"
              >
                안전 초기화 (자동매수 OFF · 15만/5만/3천원)
              </button>
            </div>
          </div>

          {/* 실전 주문 가능 조건 체크리스트 */}
          <div className={`${TOKENS.subCard} p-4`}>
            <p className="mb-2.5 font-semibold text-zinc-200">실전 주문 가능 조건 (모두 충족 시 허용)</p>
            <div className="flex flex-wrap gap-2">
              <StatusBadge tone={status.controlState === 'RUNNING' ? 'ok' : 'danger'} label={`1. 관제 RUNNING (${status.controlState})`} />
              <StatusBadge tone={status.autoBuyOnly ? 'ok' : 'neutral'} label={`2. 자동매수 (${status.autoBuyOnly ? 'ON' : 'OFF'})`} />
              <StatusBadge tone={status.freshnessOk ? 'ok' : 'danger'} label={`3. 시세 신선도 (${status.freshCount}/${status.watchCount})`} />
              <StatusBadge tone={status.followVersion ? 'ok' : 'warn'} label={`4. 활성 전략 (${status.followVersion ?? '없음'})`} />
              <StatusBadge tone={status.privateConnected ? 'ok' : 'warn'} label={`5. Private API (${status.privateConnected ? '연결' : '미연결'})`} />
            </div>
          </div>
        </section>

        {/* 2. 운용 자본 / 3. 전략 조건 / 4. 데이터 수집 (기존 QuietSettings 보존) */}
        <QuietSettings
          key={rev}
          board={board}
          onSaved={() => {
            setRev((v) => v + 1);
            refreshStatus();
          }}
        />

        {/* 5. 계정 및 보안 */}
        <section aria-label="계정 및 보안" className={`${TOKENS.card} space-y-4 p-6 text-xs`}>
          <SectionHeader
            title="5. 계정 및 보안"
            subtitle="운영자 인증·TOTP·API 연결·전송 암호화 상태를 점검합니다. 민감 키는 화면에 노출하지 않습니다."
          />

          {!isHttps && (
            <RiskBanner
              tone="warn"
              title="HTTP 접속 감지 — HTTPS 적용 권장"
              message="현재 브라우저가 HTTPS가 아닌 HTTP로 접속 중입니다. 로그인 세션과 계좌 화면 보호를 위해 리버스 프록시(Nginx/Caddy) 기반 HTTPS를 적용하세요."
            />
          )}

          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <div className={`${TOKENS.subCard} p-3`}>
              <p className="text-[11px] text-zinc-400">운영자 비밀번호</p>
              <div className="mt-1.5">
                <StatusBadge tone="ok" label="환경변수 설정됨 (기본값 차단)" />
              </div>
            </div>
            <div className={`${TOKENS.subCard} p-3`}>
              <p className="text-[11px] text-zinc-400">TOTP 2단계 인증</p>
              <div className="mt-1.5">
                <StatusBadge tone={mfaEnabled ? 'ok' : 'warn'} label={mfaEnabled ? 'TOTP 활성' : 'TOTP 미설정'} />
              </div>
            </div>
            <div className={`${TOKENS.subCard} p-3`}>
              <p className="text-[11px] text-zinc-400">Private API 키</p>
              <div className="mt-1.5">
                <StatusBadge tone={status.privateConnected ? 'ok' : 'warn'} label={status.privateConnected ? '연결됨' : '미연결'} />
              </div>
            </div>
            <div className={`${TOKENS.subCard} p-3`}>
              <p className="text-[11px] text-zinc-400">전송 보안</p>
              <div className="mt-1.5">
                <StatusBadge tone={isHttps ? 'ok' : 'warn'} label={isHttps ? 'HTTPS' : 'HTTP (주의)'} />
              </div>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3 pt-1">
            {!mfaEnabled && !mfaSecret && (
              <button
                type="button"
                onClick={setupMfa}
                className="rounded-lg bg-zinc-100 px-3.5 py-2 font-semibold text-zinc-950"
              >
                TOTP 2단계 인증 설정하기
              </button>
            )}
            {mfaSecret && (
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded border border-[#1E2536] bg-[#0E1119] px-2.5 py-1.5 font-mono text-zinc-200">
                  시크릿 발급됨 (앱 등록 후 6자리 입력)
                </span>
                <input
                  value={mfaCode}
                  onChange={(e) => setMfaCode(e.target.value)}
                  placeholder="6자리 코드"
                  className="w-28 rounded-lg border border-[#1E2536] bg-[#0E1119] px-3 py-1.5 font-mono text-zinc-100"
                />
                <button
                  type="button"
                  onClick={enableMfa}
                  className="rounded-lg border border-emerald-500/40 bg-emerald-500/15 px-3 py-1.5 font-semibold text-emerald-300"
                >
                  활성화
                </button>
              </div>
            )}
            {mfaEnabled && (
              <div className="flex flex-wrap items-center gap-2">
                <input
                  value={disableCode}
                  onChange={(e) => setDisableCode(e.target.value)}
                  placeholder="해제할 TOTP 6자리"
                  className="w-36 rounded-lg border border-[#1E2536] bg-[#0E1119] px-3 py-1.5 font-mono text-zinc-100"
                />
                <button
                  type="button"
                  onClick={disableMfa}
                  className="rounded-lg border border-amber-500/40 bg-amber-500/15 px-3 py-1.5 font-semibold text-amber-300"
                >
                  TOTP 해제
                </button>
              </div>
            )}
            {mfaMsg && <span className="text-emerald-400">{mfaMsg}</span>}
          </div>
        </section>

        {/* 6. 고급 시스템 (접힘) */}
        <section aria-label="고급 시스템" className={`${TOKENS.card} p-6 text-xs`}>
          <SectionHeader
            title="6. 고급 시스템"
            subtitle="PostgreSQL 이관 상태, 메모리 점유율, 회원 목록 등 시스템 관리 기능입니다."
            actions={
              <button
                type="button"
                onClick={() => setShowAdvanced((v) => !v)}
                className="rounded-lg border border-[#1E2536] bg-[#0E1119] px-3 py-1.5 text-zinc-200 hover:border-zinc-500"
              >
                {showAdvanced ? '접기' : '고급 시스템 펼치기'}
              </button>
            }
          />
          {showAdvanced && (
            <div className="mt-4 space-y-5">
              {pgStatus && (
                <div className={`${TOKENS.subCard} space-y-3 p-4 font-mono`}>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-sans font-semibold text-zinc-200">PostgreSQL 전환 상태</span>
                    <StatusBadge
                      tone={pgStatus.parity_ok ? 'ok' : pgStatus.configured ? 'warn' : 'neutral'}
                      label={pgStatus.connected ? (pgStatus.parity_ok ? '연결 · 정합성 일치' : '연결 · 이관 필요') : '미연결'}
                    />
                  </div>
                  <p className="text-[11px] text-zinc-400">
                    Identity: {pgStatus.identity_mode ?? 'sqlite'} · Audit: {pgStatus.audit_mode ?? 'sqlite'} · Analytics: {pgStatus.analytics_mode ?? 'sqlite'}
                  </p>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      disabled={pgBusy || !pgStatus.connected}
                      onClick={applyPgMigration}
                      className="rounded-lg border border-emerald-500/40 bg-emerald-500/15 px-3 py-1.5 font-sans font-semibold text-emerald-300 disabled:opacity-40"
                    >
                      {pgBusy ? '이관 중...' : 'SQLite → PostgreSQL 이관'}
                    </button>
                    <button
                      type="button"
                      onClick={pruneArchive}
                      className="rounded-lg border border-[#1E2536] bg-[#121620] px-3 py-1.5 font-sans text-zinc-300"
                    >
                      7일 이전 아카이브 정리
                    </button>
                  </div>
                  {pgMsg && <p className="font-sans text-emerald-400">{pgMsg}</p>}
                </div>
              )}

              {memStats && (
                <div className="grid grid-cols-2 gap-2 font-mono sm:grid-cols-4">
                  <div className={`${TOKENS.subCard} p-3`}>
                    <p className="text-zinc-400">RSS / HWM</p>
                    <p className="mt-1 text-zinc-100">{memStats.vm_rss_mb ?? '—'} / {memStats.vm_hwm_mb ?? '—'} MB</p>
                  </div>
                  <div className={`${TOKENS.subCard} p-3`}>
                    <p className="text-zinc-400">Swap</p>
                    <p className="mt-1 text-zinc-100">{memStats.vm_swap_mb ?? '—'} MB</p>
                  </div>
                  <div className={`${TOKENS.subCard} p-3`}>
                    <p className="text-zinc-400">스레드 / 큐</p>
                    <p className="mt-1 text-zinc-100">{memStats.threads} / {memStats.trade_queue_size}</p>
                  </div>
                  <div className={`${TOKENS.subCard} p-3`}>
                    <p className="text-zinc-400">수신 종목</p>
                    <p className="mt-1 text-zinc-100">{memStats.latest_symbols_count}</p>
                  </div>
                </div>
              )}

              <div className={`${TOKENS.subCard} p-4`}>
                <p className="mb-2 font-semibold text-zinc-200">가입 회원 ({members.length}명)</p>
                {members.length === 0 ? (
                  <p className="text-zinc-500">가입된 회원이 없습니다.</p>
                ) : (
                  <div className="space-y-1.5 font-mono text-[11px]">
                    {members.map((m) => (
                      <div key={m.id} className="flex flex-wrap justify-between gap-2 border-b border-[#1E2536]/60 py-1.5 last:border-0">
                        <span className="text-zinc-200">{m.email} ({m.role})</span>
                        <span className="text-zinc-400">
                          TOTP {m.mfa_enabled ? 'ON' : 'OFF'} · 키 {m.credentials_count}개
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
        </section>
      </div>

      {/* 4단계 확인: 자동 실전매수 활성화 */}
      <ConfirmActionDialog
        open={confirmAutoBuyOpen}
        title="자동 실전매수 활성화 확인"
        description="현재 설정된 위험 한도를 확인한 뒤 확인 문구를 입력해야 자동 실전매수가 켜집니다."
        confirmPhrase="실전매수활성화"
        confirmLabel="자동 실전매수 활성화"
        tone="warn"
        busy={busy}
        onCancel={() => setConfirmAutoBuyOpen(false)}
        onConfirm={() => toggleAutoBuy(true)}
      >
        <div className={`${TOKENS.subCard} space-y-1.5 p-3 font-mono text-[11px]`}>
          <div className="flex justify-between">
            <span className="text-zinc-400">운용 기준 자본</span>
            <span>{formatKrw(status.policy?.initial_capital_krw)}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-zinc-400">1회 최대 매수</span>
            <span>{formatKrw(status.policy?.max_entry_krw)}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-zinc-400">일일 손실 한도</span>
            <span>{formatKrw(status.policy?.daily_loss_alert_krw)}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-zinc-400">현재 관제 상태</span>
            <span>{status.controlState} ({status.controlMode})</span>
          </div>
        </div>
      </ConfirmActionDialog>

      {/* 안전 초기화 전/후 비교 표 */}
      <ConfirmActionDialog
        open={confirmSafetyResetOpen}
        title="안전 초기화 확인 (자동매수 OFF · 15만/5만/3천원)"
        description="실행 시 아래 표와 같이 자본 한도와 자동매수 설정이 안전한 기본값으로 변경됩니다."
        confirmLabel="안전 초기화 실행"
        tone="warn"
        busy={busy}
        onCancel={() => setConfirmSafetyResetOpen(false)}
        onConfirm={runSafetyReset}
      >
        <div className="overflow-hidden rounded-lg border border-[#1E2536] bg-[#0E1119] font-mono text-[11px]">
          <table className="w-full text-left">
            <thead className="border-b border-[#1E2536] text-zinc-400">
              <tr>
                <th className="px-3 py-2">항목</th>
                <th className="px-3 py-2 text-right">현재</th>
                <th className="px-3 py-2 text-right">초기화 후</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#1E2536]/60">
              <tr>
                <td className="px-3 py-2 text-zinc-300">운용 자본</td>
                <td className="px-3 py-2 text-right text-zinc-400">{formatKrw(status.policy?.initial_capital_krw)}</td>
                <td className="px-3 py-2 text-right font-semibold text-emerald-300">150,000원</td>
              </tr>
              <tr>
                <td className="px-3 py-2 text-zinc-300">1회 매수</td>
                <td className="px-3 py-2 text-right text-zinc-400">{formatKrw(status.policy?.max_entry_krw)}</td>
                <td className="px-3 py-2 text-right font-semibold text-emerald-300">50,000원</td>
              </tr>
              <tr>
                <td className="px-3 py-2 text-zinc-300">일일 손실</td>
                <td className="px-3 py-2 text-right text-zinc-400">{formatKrw(status.policy?.daily_loss_alert_krw)}</td>
                <td className="px-3 py-2 text-right font-semibold text-emerald-300">3,000원</td>
              </tr>
              <tr>
                <td className="px-3 py-2 text-zinc-300">자동매수</td>
                <td className="px-3 py-2 text-right text-zinc-400">{status.autoBuyOnly ? 'ON' : 'OFF'}</td>
                <td className="px-3 py-2 text-right font-semibold text-emerald-300">OFF</td>
              </tr>
            </tbody>
          </table>
        </div>
      </ConfirmActionDialog>

      {/* 긴급 정지 확인 */}
      <ConfirmActionDialog
        open={confirmEmergencyStopOpen}
        title="긴급 정지(EMERGENCY_STOP) 발동"
        description="즉시 모든 자동 실전 주문을 차단하고 관제를 EMERGENCY_STOP 상태로 전환합니다."
        confirmLabel="긴급 정지 실행"
        tone="danger"
        busy={busy}
        onCancel={() => setConfirmEmergencyStopOpen(false)}
        onConfirm={() => runControlAction('emergency-stop')}
      />
    </AdminShell>
  );
}
