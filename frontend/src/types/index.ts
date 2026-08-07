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
  cron: string;
  timezone: string;
}

export type BatchRunStatus = "running" | "success" | "failed";

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
