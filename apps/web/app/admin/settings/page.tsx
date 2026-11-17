'use client';
import { useEffect, useState } from 'react';
import AdminShell from '../admin-shell';
import QuietSettings from '../../quiet-settings';
import { api } from '../../trading-types';
import { Board } from '../../quiet-types';

export default function AdminSettingsPage() {
  const [board, setBoard] = useState<Board | null>(null);
  const [rev, setRev] = useState(0);
  const [mfaEnabled, setMfaEnabled] = useState(false);
  const [mfaSecret, setMfaSecret] = useState('');
  const [mfaCode, setMfaCode] = useState('');
  const [disableCode, setDisableCode] = useState('');
  const [mfaMsg, setMfaMsg] = useState('');
  const [pgPreview, setPgPreview] = useState<{ counts: Record<string, number>; ddl_preview: string } | null>(null);
  const [pgStatus, setPgStatus] = useState<{
    configured: boolean;
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
    seen_events_count: number;
    detail_cache_count: number;
    latest_symbols_count: number;
    postgres_identity_enabled: boolean;
  } | null>(null);
  const [members, setMembers] = useState<{ id: string; email: string; role: string; mfa_enabled: boolean; exchanges: string[]; credentials_count: number; created_at: string }[]>([]);

  function refreshMemory() {
    api<{
      vm_rss_mb: number | null;
      vm_hwm_mb: number | null;
      vm_swap_mb: number | null;
      threads: number;
      trade_queue_size: number;
      seen_events_count: number;
      detail_cache_count: number;
      latest_symbols_count: number;
      postgres_identity_enabled: boolean;
    }>('admin/system/memory')
      .then(setMemStats)
      .catch(() => {});
  }

  useEffect(() => {
    api<Board>('board?buy_page=1&sell_page=1')
      .then(setBoard)
      .catch(() => {});
    fetch('/api/manual/auth/session')
      .then((r) => r.json())
      .then((s) => setMfaEnabled(Boolean(s.mfa_enabled)))
      .catch(() => {});
    api<{ counts: Record<string, number>; ddl_preview: string }>('admin/pg-migration/preview')
      .then(setPgPreview)
      .catch(() => {});
    api<{ configured: boolean; driver_available: boolean; connected: boolean; pg_version?: string | null; parity_ok: boolean; error?: string | null }>('admin/pg-migration/status')
      .then(setPgStatus)
      .catch(() => {});
    api<{ items: { id: string; email: string; role: string; mfa_enabled: boolean; exchanges: string[]; credentials_count: number; created_at: string }[] }>('admin/members')
      .then((d) => setMembers(d.items ?? []))
      .catch(() => {});
    refreshMemory();
  }, [rev]);

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
    if (!window.confirm('현재 SQLite 데이터를 PostgreSQL(DATABASE_URL)에 멱등 이관(ON CONFLICT DO NOTHING)할까요?')) {
      return;
    }
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

  return (
    <AdminShell>
      <div className="space-y-6">
        <QuietSettings board={board} onSaved={() => setRev((r) => r + 1)} />
        <section className="rounded-xl border border-[#1E2536] bg-[#121620] p-6 text-xs">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 className="text-base font-semibold text-zinc-100">운영자 2단계 인증 (TOTP MFA)</h3>
              <p className="mt-1 text-zinc-400">상태: {mfaEnabled ? '활성화됨' : '미설정 (권장)'}</p>
            </div>
            <button
              type="button"
              onClick={setupMfa}
              className="rounded-lg border border-[#2A344B] bg-[#181E2B] px-3.5 py-2 font-medium text-zinc-200 hover:border-zinc-400"
            >
              TOTP 시크릿 발급
            </button>
          </div>
          {mfaSecret && (
            <div className="mt-4 space-y-3 rounded-lg border border-[#1E2536] bg-[#0E1119] p-4">
              <p className="font-mono text-emerald-300">시크릿 키 (Google Authenticator / 1Password 등록): {mfaSecret}</p>
              <div className="flex gap-2">
                <input
                  placeholder="6자리 코드 입력"
                  value={mfaCode}
                  onChange={(e) => setMfaCode(e.target.value)}
                  className="rounded-lg border border-[#1E2536] bg-[#0B0E14] px-3 py-1.5 font-mono text-zinc-100"
                />
                <button
                  type="button"
                  onClick={enableMfa}
                  className="rounded-lg bg-zinc-100 px-3.5 py-1.5 font-semibold text-zinc-950"
                >
                  MFA 활성화
                </button>
              </div>
            </div>
          )}
          {mfaEnabled && (
            <div className="mt-4 flex flex-wrap items-center gap-2 rounded-lg border border-[#1E2536] bg-[#0E1119] p-3">
              <input
                placeholder="비활성화용 6자리 TOTP"
                value={disableCode}
                onChange={(e) => setDisableCode(e.target.value)}
                className="rounded-lg border border-[#1E2536] bg-[#0B0E14] px-3 py-1.5 font-mono text-zinc-100"
              />
              <button
                type="button"
                onClick={disableMfa}
                className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-3.5 py-1.5 font-semibold text-rose-300"
              >
                MFA 비활성화
              </button>
            </div>
          )}
          {mfaMsg && <p className="mt-3 font-medium text-emerald-400">{mfaMsg}</p>}
        </section>

        {pgPreview && (
          <details className="rounded-xl border border-[#1E2536] bg-[#121620] p-6 text-xs">
            <summary className="cursor-pointer font-semibold text-zinc-200">
              PostgreSQL 다중 회원제 마이그레이션 준비 현황
            </summary>
            <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap gap-2 font-mono text-[11px] text-zinc-400">
                {Object.entries(pgPreview.counts).map(([tbl, cnt]) => (
                  <span key={tbl} className="rounded border border-[#1E2536] bg-[#0E1119] px-2.5 py-1">
                    {tbl}: {cnt}건
                  </span>
                ))}
              </div>
              <div className="flex flex-wrap gap-2">
                <a
                  href="/api/manual/admin/pg-migration/export"
                  className="rounded-lg border border-emerald-500/40 bg-emerald-500/15 px-3 py-1.5 font-mono text-[11px] font-semibold text-emerald-300"
                >
                  이관 SQL 다운로드 (.sql)
                </a>
                <button
                  type="button"
                  disabled={pgBusy}
                  onClick={applyPgMigration}
                  className="rounded-lg bg-zinc-100 px-3 py-1.5 font-mono text-[11px] font-semibold text-zinc-950 disabled:opacity-50"
                >
                  {pgBusy ? '이관 실행 중...' : 'PostgreSQL 직접 이관 실행'}
                </button>
              </div>
            </div>
            {pgMsg && <p className="mt-2 font-mono text-[11px] text-emerald-400">{pgMsg}</p>}
            {pgStatus && (
              <p className="mt-2 font-mono text-[11px] text-zinc-400">
                PG 연결:{' '}
                <span className={pgStatus.connected ? 'text-emerald-400' : 'text-amber-300'}>
                  {pgStatus.connected ? `연결됨 (${pgStatus.pg_version?.split(' ').slice(0, 2).join(' ')})` : '미연결'}
                </span>
                {' · '}정합성: {pgStatus.parity_ok ? '일치' : pgStatus.error ?? '대기'}
              </p>
            )}
            <pre className="mt-3 max-h-64 overflow-auto rounded-lg border border-[#1E2536] bg-[#0B0E14] p-3 font-mono text-[11px] text-zinc-400">
              {pgPreview.ddl_preview}
            </pre>
          </details>
        )}

        {memStats && (
          <section className="rounded-xl border border-[#1E2536] bg-[#121620] p-6 text-xs">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-base font-semibold text-zinc-100">백엔드 메모리 · 스레드 · 큐 상태 (Task 20 모니터)</h3>
                <p className="mt-1 text-zinc-400">FastAPI 프로세스 RSS·스왑·스레드 및 인메모리 캐시 상한을 실시간으로 점검합니다.</p>
              </div>
              <button
                type="button"
                onClick={refreshMemory}
                className="rounded-lg border border-[#2A344B] bg-[#181E2B] px-3 py-1.5 font-mono text-[11px] text-zinc-200 hover:border-zinc-400"
              >
                새로고침
              </button>
            </div>
            <div className="mt-4 grid grid-cols-2 gap-3 font-mono sm:grid-cols-4">
              <div className="rounded-lg border border-[#1E2536] bg-[#0E1119] p-3">
                <p className="text-zinc-400">VmRSS / HWM</p>
                <p className="mt-1 text-sm font-semibold text-zinc-100">
                  {memStats.vm_rss_mb ?? '—'} MB {memStats.vm_hwm_mb != null ? `/ ${memStats.vm_hwm_mb} MB` : ''}
                </p>
              </div>
              <div className="rounded-lg border border-[#1E2536] bg-[#0E1119] p-3">
                <p className="text-zinc-400">VmSwap / Threads</p>
                <p className="mt-1 text-sm font-semibold text-zinc-100">
                  {memStats.vm_swap_mb != null ? `${memStats.vm_swap_mb} MB` : '—'} · {memStats.threads}개
                </p>
              </div>
              <div className="rounded-lg border border-[#1E2536] bg-[#0E1119] p-3">
                <p className="text-zinc-400">큐 / seen 캐시</p>
                <p className="mt-1 text-sm font-semibold text-zinc-100">
                  {memStats.trade_queue_size} / {memStats.seen_events_count.toLocaleString('ko-KR')}
                </p>
              </div>
              <div className="rounded-lg border border-[#1E2536] bg-[#0E1119] p-3">
                <p className="text-zinc-400">detail / 인증 저장소</p>
                <p className="mt-1 text-sm font-semibold text-zinc-100">
                  {memStats.detail_cache_count}종목 · {memStats.postgres_identity_enabled ? 'PostgreSQL' : 'SQLite'}
                </p>
              </div>
            </div>
          </section>
        )}

        <section className="rounded-xl border border-[#1E2536] bg-[#121620] p-6 text-xs">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-base font-semibold text-zinc-100">가입 회원 현황 (보안 메타데이터)</h3>
              <p className="mt-1 text-zinc-400">회원의 API 키 평문이나 개인 잔고는 조회되지 않으며 연결 거래소·MFA 상태만 표시됩니다.</p>
            </div>
            <span className="font-mono text-zinc-400">총 {members.length}명</span>
          </div>
          {members.length === 0 ? (
            <p className="mt-3 text-zinc-500">가입된 회원이 없습니다.</p>
          ) : (
            <div className="mt-3 overflow-x-auto rounded-lg border border-[#1E2536] bg-[#0E1119]">
              <table className="w-full text-left font-mono text-[11px]">
                <thead className="border-b border-[#1E2536] text-zinc-400">
                  <tr>
                    <th className="px-3 py-2">이메일</th>
                    <th className="px-3 py-2">역할</th>
                    <th className="px-3 py-2">2FA(MFA)</th>
                    <th className="px-3 py-2">연동 거래소</th>
                    <th className="px-3 py-2 text-right">가입일</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#1E2536]/60">
                  {members.map((m) => (
                    <tr key={m.id}>
                      <td className="px-3 py-2 text-zinc-200">{m.email}</td>
                      <td className="px-3 py-2 text-zinc-300">{m.role}</td>
                      <td className={`px-3 py-2 ${m.mfa_enabled ? 'text-emerald-400' : 'text-zinc-500'}`}>
                        {m.mfa_enabled ? '활성' : '미설정'}
                      </td>
                      <td className="px-3 py-2 text-zinc-300">
                        {m.exchanges.length > 0 ? `${m.exchanges.join(', ')} (${m.credentials_count})` : '—'}
                      </td>
                      <td className="px-3 py-2 text-right text-zinc-500">{m.created_at.slice(0, 10)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </AdminShell>
  );
}
