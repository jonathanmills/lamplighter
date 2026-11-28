// SPDX-License-Identifier: GPL-2.0-or-later
// Copyright (C) 2026 Jonathan Mills
import { useEffect, useState } from "react";

export type Theme = "dark" | "light" | "blue";
export const THEMES: { key: Theme; label: string }[] = [
  { key: "dark", label: "Dark" }, { key: "light", label: "Light" }, { key: "blue", label: "Blue" }];

export function currentTheme(): Theme {
  const t = document.documentElement.getAttribute("data-theme");
  return t === "light" || t === "blue" ? t : "dark";
}

/** Read a CSS custom property (design token) from the root element. */
export function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function setTheme(t: Theme): void {
  document.documentElement.setAttribute("data-theme", t);
  try { localStorage.setItem("lamplighter-theme", t); } catch { /* storage blocked: still switch */ }
  window.dispatchEvent(new CustomEvent("lamplighter-theme", { detail: t }));
}

/** Re-render on theme change (charts read tokens at draw time). */
export function useTheme(): Theme {
  const [t, setT] = useState<Theme>(currentTheme());
  useEffect(() => {
    const on = () => setT(currentTheme());
    window.addEventListener("lamplighter-theme", on);
    return () => window.removeEventListener("lamplighter-theme", on);
  }, []);
  return t;
}
