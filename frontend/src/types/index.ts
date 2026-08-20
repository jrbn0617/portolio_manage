export type AssetType = "stock" | "etf" | "fund" | "index";

export interface Instrument {
  id: number;
  ticker: string;
  name: string;
  asset_type: AssetType;
  market: string | null;
  sector: string | null;
  industry: string | null;
  created_at: string;
}

export interface InstrumentInput {
  ticker: string;
  name: string;
  asset_type: AssetType;
  market: string | null;
}

export interface Price {
  id: number;
  instrument_id: number;
  date: string;
  period: "D" | "M";
  open: number | null;
  high: number | null;
  low: number | null;
  close: number;
  volume: number | null;
}

export interface DividendAdjustedPrice {
  id: number;
  instrument_id: number;
  date: string;
  period: "D" | "M";
  adj_close: number;
}

export interface Dividend {
  id: number;
  instrument_id: number;
  ex_date: string;
  pay_date: string | null;
  amount: number;
}

export interface MacroIndicator {
  id: number;
  indicator_name: string;
  date: string;
  value: number;
}

export interface IndexMembership {
  id: number;
  index_name: string;
  as_of_date: string;
  instrument_id: number;
}

export interface IndexConstituent {
  instrument_id: number;
  ticker: string;
  name: string;
  market: string | null;
  sector: string | null;
  krx_sector: string | null;
  close: number | null;
  market_cap: number | null;
}

export type UploadDataType = "prices" | "dividends" | "macro" | "index_memberships";

export interface MonthlyFundamental {
  id: number;
  instrument_id: number;
  date: string;
  metric: string;
  value: number;
  ticker: string;
  name: string;
}

export interface MonthlyFundamentalInput {
  ticker: string;
  date: string;
  metric: string;
  value: number;
}

export interface BatchSchedule {
  job_name: string;
  description: string;
  source: string;
  cron: string | null; // 수동 업로드 항목은 cron이 없다
  schedule: string;
  timezone: string;
  runnable: boolean;
}

export type BatchRunStatus = "running" | "success" | "failed" | "holiday";

export interface BatchRun {
  id: number;
  job_name: string;
  trigger: "cron" | "manual";
  status: BatchRunStatus;
  started_at: string;
  finished_at: string | null;
  summary: string | null;
  log: string | null;
  error: string | null;
}

export interface UploadResult {
  id: number;
  data_type: UploadDataType;
  file_name: string;
  uploaded_at: string;
  row_count: number;
  error_count: number;
  status: "success" | "partial" | "failed";
  errors: string[];
}

export interface MonthlyFundamentalBulkUploadMetricResult {
  metric: string;
  sheets: number;
  rows: number;
  unknown_tickers: number;
}

export interface MonthlyFundamentalBulkUploadResult {
  status: "success" | "partial" | "failed";
  file_name: string;
  total_rows: number;
  metrics: MonthlyFundamentalBulkUploadMetricResult[];
  errors: string[];
}

export interface DataSourceRun {
  status: BatchRunStatus;
  started_at: string;
  finished_at: string | null;
  error: string | null;
}

export type DataSourceCadence = "daily" | "monthly" | "manual";

export interface DataSource {
  key: string;
  label: string;
  source: string;
  schedule: string;
  job_name: string | null;
  last_date: string | null;
  date_label: string;
  cadence: DataSourceCadence;
  note: string | null;
  last_run: DataSourceRun | null;
  stale: boolean;
  pending: boolean;
  stale_reason: string | null;
}

export interface Fund {
  id: number;
  fund_code: string;
  name: string;
  master_fund_code: string | null;
  is_manage_fund: boolean;
  class_str: string | null;
  special: boolean;
  pension_type: string | null;
  manage_company: string | null;
  category: string | null;
  region: string | null;
  incept_dt: string | null;
}

export interface FundClass {
  fund_code: string;
  name: string;
  class_str: string | null;
  special: boolean;
  pension_type: string | null;
  incept_dt: string | null;
  last_nav: number | null;
  last_dt: string | null;
}

export interface FundDetail extends Fund {
  custodian: string | null;
  lead_dist: string | null;
  term_dt: string | null;
  nav_from: string | null;
  nav_to: string | null;
  nav_count: number;
  settlement_count: number;
  classes: FundClass[];
}

export interface FundNavPoint {
  base_dt: string;
  nav: number | null;
  adj_nav: number | null;
  adj_factor: number | null;
  aum: number | null;
}

export interface FundSettlement {
  period_start_value: string | null;
  period_end_value: string;
  settlement_type: string;
  nav: number | null;
  post_settlement_nav: number | null;
  ex_dividend_dt: string | null;
}

export interface FundStats {
  total: number;
  manage_funds: number;
  class_funds: number;
  unmapped: number;
  nav_rows: number;
  nav_from: string | null;
  nav_to: string | null;
  updated_at: string | null;
}

export type FundKind = "manage" | "class" | "all";

export interface BbgIndex {
  id: number;
  bbg_ticker: string;
  ticker: string;
  name: string;
  note: string | null;
  refresh_mode: "daily" | "full";
  fields: string;
  compute_tr: boolean;
  start_date: string | null;
  enabled: boolean;
  sort_order: number;
  rows: number;
  first_dt: string | null;
  last_dt: string | null;
  last_value: number | null;
  updated_at: string | null;
}

export interface BbgIndexUpdate {
  name?: string;
  note?: string | null;
  refresh_mode?: "daily" | "full";
  start_date?: string | null;
  enabled?: boolean;
  sort_order?: number;
}

export interface BbgIndexCreate {
  bbg_ticker: string;
  ticker: string;
  name: string;
  note?: string | null;
  refresh_mode?: "daily" | "full";
  start_date?: string | null;
  sort_order?: number;
}
