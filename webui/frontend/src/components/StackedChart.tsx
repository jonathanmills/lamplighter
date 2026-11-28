// SPDX-License-Identifier: GPL-2.0-or-later
// Copyright (C) 2026 Jonathan Mills
// Several series over time, stacked (or as separate lines): categorical
// colours in fixed order (--cat-1..8, then --cat-other for "Other"), 2px
// lines, a legend always, a crosshair tooltip, and a table view (some light
// theme colours are under 3:1 against the panel, so the numbers are always
// one click away).
import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { LineChart as ELine } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { SVGRenderer } from "echarts/renderers";
import { token, useTheme } from "../theme";

echarts.use([ELine, GridComponent, LegendComponent, TooltipComponent, SVGRenderer]);

export interface Series { name: string; values: number[] }

export default function StackedChart({ times, series, format, stacked = true, ariaLabel }: {
  times: number[]; series: Series[]; format: (v: number) => string; stacked?: boolean; ariaLabel: string;
}) {
  const el = useRef<HTMLDivElement>(null);
  const theme = useTheme();
  useEffect(() => {
    if (!el.current) return;
    const chart = echarts.init(el.current, undefined, { renderer: "svg" });
    const text2 = token("--text-2"), grid = token("--chart-grid");
    const colour = (s: Series, i: number) => token(s.name === "Other" ? "--cat-other" : `--cat-${Math.min(i + 1, 8)}`);
    chart.setOption({
      animation: !matchMedia("(prefers-reduced-motion: reduce)").matches,
      grid: { left: 8, right: 16, top: 40, bottom: 4, containLabel: true },
      legend: { top: 0, left: 0, type: "scroll", textStyle: { color: token("--text") }, itemWidth: 14, itemHeight: 8,
        pageTextStyle: { color: text2 } },
      tooltip: { trigger: "axis", backgroundColor: token("--panel"), borderColor: token("--border"),
        textStyle: { color: token("--text") }, axisPointer: { type: "line", lineStyle: { color: grid } },
        valueFormatter: (v: number) => format(v), order: "seriesDesc" },
      xAxis: { type: "time", axisLine: { lineStyle: { color: grid } }, axisLabel: { color: text2, hideOverlap: true },
        splitLine: { show: false } },
      yAxis: { type: "value", min: 0, splitLine: { lineStyle: { color: grid } },
        axisLabel: { color: text2, formatter: (v: number) => format(v) } },
      series: series.map((s, i) => ({
        type: "line", name: s.name, data: s.values.map((v, j) => [times[j] * 1000, v]),
        stack: stacked ? "total" : undefined, symbol: "none",
        lineStyle: { width: 2, color: colour(s, i) }, itemStyle: { color: colour(s, i) },
        areaStyle: stacked ? { color: colour(s, i), opacity: 0.35 } : undefined,
        emphasis: { focus: "series" },
      })),
    });
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(el.current);
    return () => { ro.disconnect(); chart.dispose(); };
  }, [times, series, format, stacked, theme]);

  const when = (t: number) => new Date(t * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  return (
    <>
      <div className="chart chart-tall" ref={el} role="img" aria-label={ariaLabel} />
      <details className="chart-table">
        <summary>Show as table</summary>
        <div style={{ overflowX: "auto" }}>
          <table className="data-table">
            <thead><tr><th style={{ textAlign: "left" }}>Time</th>{series.map((s) => <th key={s.name}>{s.name}</th>)}</tr></thead>
            <tbody>{times.map((t, j) => (
              <tr key={`${t}-${j}`}><td style={{ textAlign: "left" }}>{when(t)}</td>
                {series.map((s) => <td key={s.name}>{format(s.values[j] ?? 0)}</td>)}</tr>
            ))}</tbody>
          </table>
        </div>
      </details>
    </>
  );
}
