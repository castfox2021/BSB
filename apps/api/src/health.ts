import { Pool } from 'pg';
import { createClient } from 'redis';
export async function dependencies() {
  const pool = new Pool({ connectionString: process.env.DATABASE_URL, connectionTimeoutMillis: 2000 });
  const redis = createClient({ url: process.env.REDIS_URL, socket: { connectTimeout: 2000, reconnectStrategy: false } });
  redis.on('error', () => undefined);
  try {
    await pool.query('SELECT 1');
    await redis.connect();
    await redis.ping();
    return { postgres: 'ok', redis: 'ok' };
  } finally {
    await Promise.allSettled([pool.end(), redis.isOpen ? redis.quit() : Promise.resolve()]);
  }
}
export function mode(source = process.env.DATA_SOURCE ?? 'mock') {
  if (source !== 'mock') throw new Error('Task 01 supports DATA_SOURCE=mock only');
  return { mode: 'paper', dataSource: source, tradingEnabled: false };
}
