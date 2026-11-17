'use client';
import { useState } from 'react';
import AdminShell from '../admin-shell';
import TradingWorkspace from '../../trading-review';
import PrivateAccount from '../../private-account';
import ManualFillPanel from '../../manual-fill-panel';

export default function AdminTradingPage() {
  const [sub, setSub] = useState<'account' | 'review' | 'manual'>('account');
  const [symbol] = useState('KRW-BTC');

  return (
    <AdminShell>
      <div className="space-y-6">
        <div className="flex flex-wrap gap-2">
          {[
            ['account', '개인 계좌 · 체결 내역'],
            ['review', '실거래 복기'],
            ['manual', '수동 체결 기록'],
          ].map(([id, label]) => (
            <button
              key={id}
              type="button"
              onClick={() => setSub(id as 'account' | 'review' | 'manual')}
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
      </div>
    </AdminShell>
  );
}
