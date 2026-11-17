'use client';
import AdminShell from '../admin-shell';
import StrategyComparison from '../../strategy-comparison';
import StrategyLab from '../../strategy-lab';

export default function AdminLabPage() {
  return (
    <AdminShell>
      <div className="space-y-6">
        <StrategyComparison />
        <StrategyLab />
      </div>
    </AdminShell>
  );
}
