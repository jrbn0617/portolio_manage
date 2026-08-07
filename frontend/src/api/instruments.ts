import { apiClient } from "./client";
import type { Instrument, InstrumentInput } from "../types";

export async function fetchInstruments(assetType?: string): Promise<Instrument[]> {
  const { data } = await apiClient.get<Instrument[]>("/instruments", {
    params: assetType ? { asset_type: assetType } : undefined,
  });
  return data;
}

export async function createInstrument(input: InstrumentInput): Promise<Instrument> {
  const { data } = await apiClient.post<Instrument>("/instruments", input);
  return data;
}

export async function updateInstrument(
  id: number,
  input: Partial<InstrumentInput>
): Promise<Instrument> {
  const { data } = await apiClient.patch<Instrument>(`/instruments/${id}`, input);
  return data;
}

export async function deleteInstrument(id: number): Promise<void> {
  await apiClient.delete(`/instruments/${id}`);
}
