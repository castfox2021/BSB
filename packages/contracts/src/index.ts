/** Prices and quantities are decimal strings; UTC timestamps end in Z. */
export type DecimalString = string;
export interface MarketEventBase {
 schema_version: '1'; event_id: string; source: 'mock' | 'bithumb_public';
 symbol: string; exchange_at: string; received_at: string;
}
export interface MarketStreamMetadata {
 connection_id: string; stream_type: 'SNAPSHOT' | 'REALTIME' | 'UNKNOWN';
 exchange_timestamp_unit: 'milliseconds' | 'microseconds'; raw_exchange_timestamp: string;
}
export interface MarketTradeEvent extends MarketEventBase, Partial<MarketStreamMetadata> {
 event_type: 'market.trade'; price: DecimalString; quantity: DecimalString;
 side: 'BUY' | 'SELL' | 'UNKNOWN';
}
export interface MarketTickerEvent extends MarketEventBase, MarketStreamMetadata {
 event_type: 'market.ticker'; price: DecimalString; turnover_24h: DecimalString;
 volume_24h: DecimalString; market_state: string; market_warning: string;
 is_trading_suspended: boolean | null;
}
export interface MarketOrderbookEvent extends MarketEventBase, MarketStreamMetadata {
 event_type: 'market.orderbook'; asks: [DecimalString, DecimalString][];
 bids: [DecimalString, DecimalString][];
}
export type MarketEvent = MarketTradeEvent | MarketTickerEvent | MarketOrderbookEvent;

export interface CollectorControlBase {
 schema_version: '1'; event_id: string; source: 'mock' | 'bithumb_public';
 exchange_at: string; received_at: string;
}
export interface CollectorStatusEvent extends CollectorControlBase {
 event_type: 'collector.status'; invalid_messages: number;
 shards: {symbols: string[]; connection_id: string; connected: boolean; last_data_at?: string | null}[];
}
export interface CollectorUniverseEvent extends CollectorControlBase {
 event_type: 'collector.universe';
 markets: {symbol: string; korean_name?: string; english_name?: string; market_warning?: string; source?: 'mock' | 'bithumb_public'}[];
}
export type CollectorEvent = MarketEvent | CollectorStatusEvent | CollectorUniverseEvent;
export interface Candle {
 source: 'mock' | 'bithumb_public'; symbol: string; timeframe: 60 | 300;
 bucket_start: string; bucket_end: string; first_at: string; last_at: string; updated_at: string;
 open: DecimalString; high: DecimalString; low: DecimalString; close: DecimalString;
 volume: DecimalString; turnover: DecimalString; trade_count: number;
 first_event_id: string; last_event_id: string;
 closed: boolean; complete: boolean; revised_after_close: boolean;
 revision: number; quality_flags: string[];
}
