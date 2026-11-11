"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api/client";

export type Loadable<T> = {
  data: T | null;
  error: string | null;
  loading: boolean;
  status?: number;
  reload: () => Promise<void>;
};

/** Load data with explicit loading / error / empty handling; optional polling for live screens. */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[] = [], pollMs?: number): Loadable<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<number | undefined>();
  const [loading, setLoading] = useState(true);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const load = useCallback(async () => {
    try {
      const d = await fnRef.current();
      setData(d);
      setError(null);
      setStatus(undefined);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setStatus(e instanceof ApiError ? e.status : undefined);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    setLoading(true);
    void load();
    if (!pollMs) return;
    const t = setInterval(() => void load(), pollMs);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { data, error, loading, reload: load, status };
}

export function fmtMoney(n?: number | null, hourly = false): string {
  if (n === null || n === undefined) return "—";
  return `$${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}${hourly ? "/h" : ""}`;
}

export function pct(n?: number | null): string {
  return n === null || n === undefined ? "—" : `${Math.round(n * 100)}%`;
}

export function ago(iso?: string | null): string {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}
