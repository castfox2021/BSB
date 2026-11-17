'use client';
import { useEffect, useState } from 'react';
import AdminShell from '../admin-shell';
import QuietSettings from '../../quiet-settings';
import { api } from '../../trading-types';
import { Board } from '../../quiet-types';

export default function AdminSettingsPage() {
  const [board, setBoard] = useState<Board | null>(null);
  const [rev, setRev] = useState(0);

  useEffect(() => {
    api<Board>('board?buy_page=1&sell_page=1')
      .then(setBoard)
      .catch(() => {});
  }, [rev]);

  return (
    <AdminShell>
      <QuietSettings board={board} onSaved={() => setRev((r) => r + 1)} />
    </AdminShell>
  );
}
