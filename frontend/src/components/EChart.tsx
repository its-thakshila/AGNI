import { useEffect, useRef } from 'react';
import * as echarts from 'echarts/core';
import { BarChart, CustomChart, LineChart } from 'echarts/charts';
import { GridComponent, MarkAreaComponent, MarkLineComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';

echarts.use([BarChart, CustomChart, LineChart, GridComponent, MarkAreaComponent, MarkLineComponent, TooltipComponent, CanvasRenderer]);

export const AXIS_FONT = { color: '#84838c', fontSize: 11, fontFamily: 'Inter' };
export const TOOLTIP = {
  backgroundColor: '#1a1a1e',
  borderColor: '#26262c',
  padding: [8, 10],
  textStyle: { color: '#f0efec', fontFamily: 'Inter', fontSize: 12 },
};

interface Props {
  option: echarts.EChartsCoreOption;
  /** px; omit to fill the parent's height */
  height?: number;
  className?: string;
  onClick?: (p: { seriesIndex?: number; dataIndex: number; data: unknown }) => void;
  ariaLabel: string;
}

/** Minimal ECharts wrapper: init once, resize with the container, replace option on change. */
export default function EChart({ option, height, className, onClick, ariaLabel }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  const clickRef = useRef(onClick);
  clickRef.current = onClick;

  useEffect(() => {
    const c = echarts.init(el.current!, undefined, { renderer: 'canvas' });
    chart.current = c;
    c.on('click', (p) => clickRef.current?.(p as never));
    const ro = new ResizeObserver(() => c.resize());
    ro.observe(el.current!);
    return () => {
      ro.disconnect();
      c.dispose();
    };
  }, []);

  useEffect(() => {
    chart.current?.setOption(option, { notMerge: true });
    chart.current?.resize();
  }, [option, height]);

  return <div ref={el} role="img" aria-label={ariaLabel} className={className} style={height ? { height } : { position: 'absolute', inset: 0 }} />;
}
