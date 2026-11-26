// SPDX-License-Identifier: GPL-2.0-or-later
// Copyright (C) 2026 Jonathan Mills
// Number formatting. Sizes use binary units (KiB, MiB, ..., PiB).

const UNITS = ["B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB"];

export function bytes(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return "0 B";
  const i = Math.min(UNITS.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  const v = n / 1024 ** i;
  return `${v >= 100 || i === 0 ? v.toFixed(0) : v.toFixed(1)} ${UNITS[i]}`;
}

const intFmt = new Intl.NumberFormat("en-US");
export const int = (n: number) => intFmt.format(n);

export function ago(seconds: number | undefined): string {
  if (seconds === undefined) return "never";
  if (seconds < 90) return "just now";
  const m = seconds / 60, h = m / 60, d = h / 24;
  if (m < 90) return `${Math.round(m)} min ago`;
  if (h < 36) return `${Math.round(h)} h ago`;
  return `${Math.round(d)} days ago`;
}

export function duration(seconds: number | undefined): string {
  if (seconds === undefined) return "—";
  if (seconds < 60) return `${seconds} s`;
  const m = Math.floor(seconds / 60), s = seconds % 60;
  if (m < 60) return s ? `${m} min ${s} s` : `${m} min`;
  const h = Math.floor(m / 60);
  if (h >= 48 && h % 24 === 0) return `${h / 24} days`;
  if (h >= 48) return `${Math.round(h / 24)} days`;
  return m % 60 ? `${h} h ${m % 60} min` : `${h} h`;
}

export const dateTime = (epoch: number | undefined) =>
  epoch === undefined ? "—" : new Date(epoch * 1000).toLocaleString();
