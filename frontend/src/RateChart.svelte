<script>
  // 速率专页中断面散点+拟合图：样本点蓝、窗口外点灰空心，拟合线 2px。
  export let view = null; // {points:[{id,t,y,inSample}], line:{t0,y0,t1,y1}|null, wStart,wEnd, fit}

  const W = 680, H = 270, L = 50, R = 18, T = 18, B = 32;
  const PW = W - L - R, PH = H - T - B;

  const BLUE = "#3987e5";
  const GRAY = "#78716c";
  const SURFACE = "#292524";
  const INK2 = "#c3c2b7";
  const MUTED = "#898781";
  const GRID = "#3a3735";

  let tip = null; // {x,y,rows:[{y,t,inSample}]}

  function pad(n) { return String(n).padStart(2, "0"); }
  function fmtDay(t) {
    const d = new Date(t);
    return `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  }
  function fmtFull(t) {
    const d = new Date(t);
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }
  function fmtMm(v) { return (Math.round(v * 100) / 100).toString(); }

  $: points = view ? view.points : [];
  $: ts = points.map((p) => p.t);
  $: d0 = ts.length ? Math.min(...ts) : 0;
  $: d1 = ts.length ? Math.max(...ts) : 1;
  $: xspan = d1 - d0 || 1;

  $: ys = points.map((p) => p.y);
  $: lineYs = view && view.line ? [view.line.y0, view.line.y1] : [];
  $: allY = [...ys, ...lineYs];
  $: ymin0 = allY.length ? Math.min(...allY) : 0;
  $: ymax0 = allY.length ? Math.max(...allY) : 1;
  $: ypad = ymax0 - ymin0 || 1;
  $: ymin = ymin0 - ypad * 0.15;
  $: ymax = ymax0 + ypad * 0.15;

  function sx(t) { return L + ((t - d0) / xspan) * PW; }
  function sy(y) { return T + PH - ((y - ymin) / (ymax - ymin)) * PH; }

  $: yTicks = [0, 1, 2, 3, 4].map((i) => {
      const y = ymin + ((ymax - ymin) * i) / 4;
      return { y, py: sy(y) };
    });
  $: xTicks = [0, 0.5, 1].map((f) => {
      const t = d0 + xspan * f;
      return { t, px: sx(t) };
    });

  $: wash = view && points.length ? {
      x0: sx(view.wStart ?? d0),
      x1: sx(view.wEnd ?? d1),
    } : null;

  function showTip(ev, p) {
    tip = { x: ev.clientX, y: ev.clientY, p };
  }
  function moveTip(ev) { if (tip) tip = { x: ev.clientX, y: ev.clientY, p: tip.p }; }
  function hideTip() { tip = null; }
</script>

<div class="chart" on:pointermove={moveTip} on:pointerleave={hideTip}>
  {#if points.length === 0}
    <div class="empty">该断面还没有已办结读数，暂无可拟合样本。</div>
  {:else}
    <svg viewBox="0 0 {W} {H}" role="img" aria-label="断面收敛随办结时刻散点与拟合直线">
      <!-- y 网格 + 刻度 -->
      {#each yTicks as g}
        <line x1={L} y1={g.py} x2={W - R} y2={g.py} stroke={GRID} stroke-width="1" />
        <text x={L - 8} y={g.py + 4} text-anchor="end" font-size="11" fill={MUTED}>{fmtMm(g.y)}</text>
      {/each}
      <!-- x 刻度 -->
      {#each xTicks as g}
        <text x={g.px} y={H - 10} text-anchor="middle" font-size="11" fill={MUTED}>{fmtDay(g.t)}</text>
      {/each}
      <line x1={L} y1={T} x2={L} y2={T + PH} stroke={GRID} stroke-width="1" />
      <line x1={L} y1={T + PH} x2={W - R} y2={T + PH} stroke={GRID} stroke-width="1" />

      <!-- 取点窗口带 -->
      {#if wash}
        <rect x={wash.x0} y={T} width={Math.max(0, wash.x1 - wash.x0)} height={PH}
              fill={BLUE} opacity="0.08" />
      {/if}

      <!-- 拟合直线（仅样本） -->
      {#if view.line}
        <line x1={sx(view.line.t0)} y1={sy(view.line.y0)}
              x2={sx(view.line.t1)} y2={sy(view.line.y1)}
              stroke={BLUE} stroke-width="2" stroke-linecap="round" />
      {/if}

      <!-- 窗口外点：灰空心（先画） -->
      {#each points.filter((p) => !p.inSample) as p}
        <circle cx={sx(p.t)} cy={sy(p.y)} r="4" fill={SURFACE} stroke={GRAY} stroke-width="2" />
      {/each}
      <!-- 样本点：蓝实心 + 表面环 -->
      {#each points.filter((p) => p.inSample) as p}
        <circle cx={sx(p.t)} cy={sy(p.y)} r="4.5" fill={BLUE} stroke={SURFACE} stroke-width="2" />
      {/each}
      <!-- 命中区（≥24px），只挂事件，不涂数据色 -->
      {#each points as p}
        <circle cx={sx(p.t)} cy={sy(p.y)} r="13" fill="transparent"
                on:pointerenter={(e) => showTip(e, p)}
                on:pointermove={(e) => showTip(e, p)}
                on:pointerleave={hideTip} />
      {/each}
    </svg>

    <div class="legend">
      <span><i style="background:{BLUE}"></i>窗口内样本点</span>
      <span><i class="out"></i>窗口外（不入拟合）</span>
      <span><i class="ln"></i>最小二乘拟合线</span>
    </div>

    {#if (!view.fit || view.fit.n < 2)}
      <div class="note">窗口内有效样本不足 2 个，成不了一条直线——速率记为“空”。</div>
    {/if}

    {#if tip}
      <div class="tip" style="left:{tip.x + 14}px;top:{tip.y + 12}px">
        <strong>{fmtMm(tip.p.y)} mm</strong>
        <span>{fmtFull(tip.p.t)} 办结</span>
        <span class="tag-{tip.p.inSample ? 'in' : 'out'}">{tip.p.inSample ? '入样' : '窗口外'}</span>
      </div>
    {/if}
  {/if}
</div>

<style>
  .chart { position: relative; }
  .empty {
    height: 180px; display: flex; align-items: center; justify-content: center;
    color: #898781; font-size: 0.9rem; border: 1px dashed #44403c; border-radius: 8px;
  }
  .note { color: #a8a29e; font-size: 0.82rem; margin-top: 0.35rem; }
  .legend { display: flex; gap: 1.1rem; flex-wrap: wrap; font-size: 0.8rem; color: #d6d3d1; margin-top: 0.2rem; }
  .legend i {
    display: inline-block; width: 10px; height: 10px; border-radius: 50%;
    margin-right: 0.35rem; vertical-align: -1px;
  }
  .legend .out { background: #292524; border: 2px solid #78716c; width: 6px; height: 6px; }
  .legend .ln {
    background: none; border-radius: 0; width: 14px; height: 0;
    border-top: 2px solid #3987e5; vertical-align: 3px;
  }
  .tip {
    position: fixed; z-index: 30; pointer-events: none; display: flex; flex-direction: column;
    gap: 2px; background: #1c1917; border: 1px solid #57534e; border-radius: 6px;
    padding: 0.45rem 0.6rem; font-size: 0.8rem; box-shadow: 0 6px 18px rgba(0,0,0,0.4);
  }
  .tip strong { color: #fafaf9; }
  .tip span { color: #a8a29e; }
  .tip .tag-in { color: #7cb7f5; }
  .tip .tag-out { color: #a8a29e; }
</style>
