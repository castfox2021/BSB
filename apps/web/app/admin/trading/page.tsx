'use client';
import { useEffect, useState } from 'react';
import AdminShell from '../admin-shell';
import TradingWorkspace from '../../trading-review';
import PrivateAccount from '../../private-account';
import ManualFillPanel from '../../manual-fill-panel';
import { api, kst } from '../../trading-types';

type AuditItem = { id: number; type: string; details: Record<string, unknown>; created_at: string };

export default function AdminTradingPage() {
  const [sub, setSub] = useState<'account' | 'review' | 'manual' | 'audit'>('account');
  const [symbol] = useState('KRW-BTC');
  const [audits, setAudits] = useState<AuditItem[]>([]);

  useEffect(() => {
    if (sub !== 'audit') return;
    api<{ items: AuditItem[] }>('admin/audit-logs?limit=60')
      .then((d) => setAudits(d.items ?? []))
      .catch(() => setAudits([]));
  }, [sub]);

  return (
    <AdminShell>
      <div className="space-y-6">
        <div className="flex flex-wrap gap-2">
          {[
            ['account', '개인 계좌 · 체결 내역'],
            ['review', '실거래 복기'],
            ['manual', '수동 체결 기록'],
            ['audit', '자동매수 · 보안 감사 로그'],
          ].map(([id, label]) => (
            <button
              key={id}
              type="button"
              onClick={() => setSub(id as 'account' | 'review' | 'manual' | 'audit')}
              className={`rounded-lg px-4 py-2 text-xs font-medium transition ${
                sub === id ? 'bg-zinc-100 text-zinc-950' : 'border border-[#1E2536] bg-[#121620] text-zinc-400 hover:text-zinc-200'
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {sub === 'account' && <PrivateAccount />}
        {sub === 'review' && <TradingWorkspace mode="journal" symbol={symbol} ordersEnabled={false} onOrder={() => {}} />}
        {sub === 'manual' && <ManualFillPanel followTrade={null} onUnlink={() => {}} />}
        {sub === 'audit' && (
          <section className="rounded-xl border border-[#1E2536] bg-[#121620] p-5 text-xs">
            <h2 className="mb-3 text-sm font-semibold text-zinc-100">자동매수 · 보안 감사 로그 (최근 60건)</h2>
            <div className="space-y-2 font-mono">
              {audits.map((a) => (
                <div key={a.id} className="rounded-lg border border-[#1E2536] bg-[#0E1119] px-3.5 py-2.5">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="rounded bg-[#181E2B] px-2 py-0.5 text-emerald-300">{a.type}</span>
                    <span className="text-[11px] text-zinc-500">{kst(a.created_at)}</span>
                  </div>
                  <p className="mt-1.5 break-all text-[11px] text-zinc-400">{JSON.stringify(a.details)}</p>
                </div>
              ))}
              {!audits.length && <p className="py-6 text-center text-zinc-500">기록된 감사 로그가 없습니다.</p>}
            </div>
          </section>
        )}
      </div>
    </AdminShell>
  );
}
