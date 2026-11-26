// SPDX-License-Identifier: GPL-2.0-or-later
// Copyright (C) 2026 Jonathan Mills
// Bar chart on ECharts (SVG renderer). Colors come from design tokens,
// so the chart redraws in the right palette when the theme changes.
// Marks follow the dataviz rules: 4px rounded data ends, thin gaps,
// recessive grid, direct value labels, hover tooltip.
import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { BarChart as EBar } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import { SVGRenderer } from "echarts/renderers";
import { token, useTheme } from "../theme";

echarts.use([EBar, GridComponent, TooltipComponent, SVGRenderer]);

interface Props {
  categories: string[];
  values: number[];
  /** CSS token per bar (ordinal ramp), or one token for a single series. */
  colorTokens: string[] | string;
  horizontal?: boolean;
  /** Values are bytes: place ticks on round binary multiples (1, 2, 5 GiB...). */
  binary?: boolean;
  format: (v: number) => string;
  ariaLabel: string;
}

/** Round tick step in binary units, so labels read 2 TiB, 4 TiB... not 1.8, 3.6. */
function binaryTicks(max: number, splits: number): { interval: number; max: number } | undefined {
  if (!(max > 0)) return undefined;
  const unit = 1024 ** Math.floor(Math.log(max) / Math.log(1024));
  const steps = [1, 2, 2.5, 5, 10, 20, 25, 50, 100, 200, 250, 500];
  const step = (steps.find((s) => max / (s * unit) <= splits) ?? 1000) * unit;
  return { interval: step, max: Math.ceil(max / step) * step };
}

export default function BarChart({ categories, values, colorTokens, horizontal, binary, format, ariaLabel }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const theme = useTheme();

  useEffect(() => {
    if (!el.current) return;
    const chart = echarts.init(el.current, undefined, { renderer: "svg" });
    const text2 = token("--text-2"), text = token("--text"), grid = token("--chart-grid");
    const colors = Array.isArray(colorTokens) ? colorTokens.map(token) : categories.map(() => token(colorTokens));
    const radius = horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0];
    const catAxis = {
      type: "category" as const, data: categories, inverse: horizontal,
      axisTick: { show: false }, axisLine: { lineStyle: { color: grid } },
      // Show every category label; wrap rather than hide them.
      axisLabel: { color: text2, fontFamily: "inherit", interval: 0,
                   ...(horizontal ? {} : { width: Math.max(48, (el.current.clientWidth - 60) / categories.length - 8), overflow: "break" as const }) },
    };
    const narrow = el.current.clientWidth < 480;
    const ticks = binary ? binaryTicks(Math.max(...values), narrow ? 2 : 4) : undefined;
    const valAxis = {
      type: "value" as const, splitLine: { lineStyle: { color: grid } }, min: 0,
      ...(ticks ?? { splitNumber: narrow ? 3 : 5 }),
      axisLabel: { color: text2, hideOverlap: true, formatter: (v: number) => format(v) },
    };
    chart.setOption({
      animation: !matchMedia("(prefers-reduced-motion: reduce)").matches,
      grid: { left: 8, right: horizontal ? 72 : 8, top: horizontal ? 4 : 24, bottom: 4, containLabel: true },
      tooltip: {
        trigger: "item", backgroundColor: token("--panel"), borderColor: token("--border"),
        textStyle: { color: text }, formatter: (p: { name: string; value: number }) => `${p.name}<br><b>${format(p.value)}</b>`,
      },
      xAxis: horizontal ? valAxis : catAxis,
      yAxis: horizontal ? catAxis : valAxis,
      series: [{
        type: "bar", barCategoryGap: "28%", barMaxWidth: horizontal ? 28 : 96,
        data: values.map((v, i) => ({ value: v, itemStyle: { color: colors[i], borderRadius: radius } })),
        label: { show: true, position: horizontal ? "right" : "top", color: text, formatter: (p: { value: number }) => format(p.value) },
        emphasis: { itemStyle: { opacity: 0.85 } },
      }],
    });
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(el.current);
    return () => { ro.disconnect(); chart.dispose(); };
  }, [categories, values, colorTokens, horizontal, binary, format, theme]);

  return <div className="chart" ref={el} role="img" aria-label={ariaLabel} />;
}
