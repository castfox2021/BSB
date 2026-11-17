'use client';
import { useState } from 'react';
import AdminShell from '../admin-shell';
import QuietDetail from '../../quiet-details';
import FullMarketScanner from '../../full-market-scanner';

export default function AdminMarketPage() {
  const [symbol, setSymbol] = useState('KRW-BTC');

  return (
    <AdminShell>
      <div className="space-y-6">
        <QuietDetail symbol={symbol} trade={null} onRecord={() => {}} />
        <FullMarketScanner onPick={setSymbol} />
      </div>
    </AdminShell>
  );
}
