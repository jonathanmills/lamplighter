// SPDX-License-Identifier: GPL-2.0-or-later
// Copyright (C) 2026 Jonathan Mills
// One-series line over time (2px line, >= 8px end marker, crosshair tooltip).
import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { LineChart as ELine } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import { SVGRenderer } from "echarts/renderers";
import { token, useTheme } from "../theme";
import { binaryTicks } from "./BarChart";

echarts.use([ELine, GridComponent, TooltipComponent, SVGRenderer]);

export default function LineChart({ points, format, binary, ariaLabel }: {
  points: { at: number; value: number }[]; format: (v: number) => string; binary?: boolean; ariaLabel: string;
}) {
  const el = useRef<HTMLDivElement>(null);
  const theme = useTheme();
  useEffect(() => {
    if (!el.current) return;
    const chart = echarts.init(el.current, undefined, { renderer: "svg" });
    const line = token("--chart-1"), text2 = token("--text-2"), grid = token("--chart-grid");
    chart.setOption({
      animation: !matchMedia("(prefers-reduced-motion: reduce)").matches,
      grid: { left: 8, right: 16, top: 16, bottom: 4, containLabel: true },
      tooltip: { trigger: "axis", backgroundColor: token("--panel"), borderColor: token("--border"),
        textStyle: { color: token("--text") }, axisPointer: { type: "line", lineStyle: { color: grid } },
        valueFormatter: (v: number) => format(v) },
      xAxis: { type: "time", axisLine: { lineStyle: { color: grid } }, axisLabel: { color: text2, hideOverlap: true },
        splitLine: { show: false } },
      yAxis: { type: "value", min: 0, splitLine: { lineStyle: { color: grid } },
        ...(binary ? binaryTicks(Math.max(...points.map((p) => p.value)), 4) : {}),
        axisLabel: { color: text2, formatter: (v: number) => format(v) } },
      series: [{
        type: "line", name: "Volume", data: points.map((p) => [p.at * 1000, p.value]),
        lineStyle: { width: 2, color: line }, itemStyle: { color: line },
        symbol: "circle", symbolSize: (_: unknown, p: { dataIndex: number }) => (p.dataIndex === points.length - 1 ? 8 : 0),
        showSymbol: true, areaStyle: { color: line, opacity: 0.08 },
      }],
    });
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(el.current);
    return () => { ro.disconnect(); chart.dispose(); };
  }, [points, format, binary, theme]);
  return <div className="chart" ref={el} role="img" aria-label={ariaLabel} />;
}
