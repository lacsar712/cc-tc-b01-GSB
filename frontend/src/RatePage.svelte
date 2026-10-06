<script>
  import { createEventDispatcher, onDestroy, onMount } from "svelte";

  export let session; // { token, username, role }

  const dispatch = createEventDispatcher();
  const WEEK_MS = 7 * 24 * 3600 * 1000;
  const TREND_TEXT = { up: "上升", down: "下降", flat: "平", none: "空" };

  let sections = [];
  let selected = null;
  let draft = null; // 本地拖取中的窗口 { start: ms, end: ms }
  let dirty = false;
  let dragging = null;
  let trackEl = null;
  let err = "";
  let notice = "";
  let timer = null;

  $: isWriter = session?.role === "writer";
  $: sel = sections.find((s) => s.chainage === selected) || null;
  $: sampleIds = new Set((sel?.points ?? []).map((p) => p.id));
  $: domain = computeDomain(sel, draft);

  function headers() {
    return { Authorization: "Bearer " + session.token };
  }

  async function load() {
    let res;
    try {
      res = await fetch("/api/rates", { headers: headers() });
    } catch {
      return;
    }
    if (res.status === 401) {
      dispatch("unauthorized");
      return;
    }
    if (!res.ok) return;
    const data = await res.json();
    sections = data.sections || [];
    if (!selected && sections.length) selected = sections[0].chainage;
    if (!dirty && !dragging) syncDraft();
  }

  function syncDraft() {
    const s = sections.find((x) => x.chainage === selected);
    const w = s?.window;
    draft = w ? { start: Date.parse(w.start_at), end: Date.parse(w.end_at) } : null;
  }

  function selectSection(c) {
    selected = c;
    dirty = false;
    err = "";
    notice = "";
    syncDraft();
  }

  function computeDomain(s, d) {
    const pts = s?.done_points ?? [];
    let lo, hi;
    if (pts.length) {
      lo = Math.min(...pts.map((p) => Date.parse(p.processed_at)));
      hi = Math.max(...pts.map((p) => Date.parse(p.processed_at)));
    } else {
      hi = Date.now();
      lo = hi - 7 * 86400000;
    }
    if (hi - lo < 3600000) {
      const m = (lo + hi) / 2;
      lo = m - 1800000;
      hi = m + 1800000;
    }
    const pad = (hi - lo) * 0.06;
    let d0 = lo - pad;
    let d1 = hi + pad;
    if (d) {
      d0 = Math.min(d0, d.start);
      d1 = Math.max(d1, d.end);
    }
    return [d0, d1];
  }

  function pct(t) {
    return ((t - domain[0]) / (domain[1] - domain[0])) * 100;
  }

  function xToMs(e) {
    const r = trackEl.getBoundingClientRect();
    const frac = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
    return domain[0] + frac * (domain[1] - domain[0]);
  }

  function onTrackDown(e) {
    if (!isWriter || !trackEl) return;
    e.preventDefault();
    const t = xToMs(e);
    const handle = e.target?.dataset?.handle;
    if (handle && draft) {
      dragging = { mode: handle };
    } else if (draft && t >= draft.start && t <= draft.end) {
      dragging = { mode: "move", off: t - draft.start };
    } else {
      dragging = { mode: "new", anchor: t };
      draft = { start: t, end: t };
      dirty = true;
    }
    window.addEventListener("pointermove", onTrackMove);
    window.addEventListener("pointerup", onTrackUp, { once: true });
  }

  function onTrackMove(e) {
    if (!dragging) return;
    const t = xToMs(e);
    const minW = (domain[1] - domain[0]) * 0.003;
    if (dragging.mode === "new") {
      draft = { start: Math.min(dragging.anchor, t), end: Math.max(dragging.anchor, t) };
    } else if (dragging.mode === "left") {
      draft = { start: Math.min(t, draft.end - minW), end: draft.end };
    } else if (dragging.mode === "right") {
      draft = { start: draft.start, end: Math.max(t, draft.start + minW) };
    } else if (dragging.mode === "move") {
      const w = draft.end - draft.start;
      const s = Math.max(domain[0], Math.min(domain[1] - w, t - dragging.off));
      draft = { start: s, end: s + w };
    }
    dirty = true;
  }

  function onTrackUp() {
    dragging = null;
    window.removeEventListener("pointermove", onTrackMove);
  }

  async function saveWindow() {
    if (!sel || !draft) return;
    err = "";
    notice = "";
    let res;
    try {
      res = await fetch(`/api/sections/${encodeURIComponent(sel.chainage)}/window`, {
        method: "PUT",
        headers: { "Content-Type": "application/json", ...headers() },
        body: JSON.stringify({
          start_at: new Date(draft.start).toISOString(),
          end_at: new Date(draft.end).toISOString(),
          version: sel.window?.version ?? 0,
        }),
      });
    } catch {
      err = "保存时网络异常";
      return;
    }
    const data = await res.json().catch(() => ({}));
    if (res.status === 409) {
      err = (data.detail || "窗口已被他人修改") + "，已载入最新窗口";
      dirty = false;
      await load();
      return;
    }
    if (!res.ok) {
      err = data.detail || "保存失败";
      return;
    }
    notice = `窗口已保存（版本 ${data.version}，${data.updated_by}）`;
    dirty = false;
    await load();
  }

  function chartGeom(sec) {
    const pts = sec.done_points;
    if (!pts || !pts.length) return null;
    const ts = pts.map((p) => Date.parse(p.processed_at));
    const vs = pts.map((p) => p.delta_mm);
    let t0 = Math.min(...ts);
    let t1 = Math.max(...ts);
    if (t1 - t0 < 60000) {
      const m = (t0 + t1) / 2;
      t0 = m - 30000;
      t1 = m + 30000;
    }
    let v0 = Math.min(...vs);
    let v1 = Math.max(...vs);
    const vpad = Math.max(0.2, (v1 - v0) * 0.2);
    v0 -= vpad;
    v1 += vpad;
    const X = (t) => 6 + ((t - t0) / (t1 - t0)) * 308;
    const Y = (v) => 114 - ((v - v0) / (v1 - v0)) * 104;
    const ids = new Set(sec.points.map((p) => p.id));
    const dots = pts.map((p) => ({
      x: X(Date.parse(p.processed_at)),
      y: Y(p.delta_mm),
      inSample: ids.has(p.id),
    }));
    let line = null;
    if (sec.rate_mm_per_week != null && sec.points.length >= 2) {
      const sts = sec.points.map((p) => Date.parse(p.processed_at));
      const svs = sec.points.map((p) => p.delta_mm);
      const tm = sts.reduce((a, b) => a + b, 0) / sts.length;
      const vm = svs.reduce((a, b) => a + b, 0) / svs.length;
      const k = sec.rate_mm_per_week / WEEK_MS;
      const a = Math.min(...sts);
      const b = Math.max(...sts);
      const cl = (y) => Math.max(2, Math.min(118, y));
      line = { x1: X(a), y1: cl(Y(vm + k * (a - tm))), x2: X(b), y2: cl(Y(vm + k * (b - tm))) };
    }
    return { dots, line };
  }

  const fmtT = (iso) => (iso ? new Date(iso).toLocaleString("zh-CN", { hour12: false }) : "—");
  const fmtMs = (ms) => new Date(ms).toLocaleString("zh-CN", { hour12: false });
  const fmtRate = (r) => (r == null ? "—" : (r > 0 ? "+" : "") + r.toFixed(2));

  onMount(() => {
    load();
    timer = setInterval(load, 3000);
  });
  onDestroy(() => clearInterval(timer));
</script>

<style>
  .block {
    background: #292524; border: 1px solid #44403c; border-radius: 8px;
    padding: 1rem 1.25rem; margin-bottom: 1rem;
  }
  h2 { font-size: 1rem; color: #fbbf24; margin: 0 0 0.75rem; }
  h2 .hint { color: #a8a29e; font-weight: 400; font-size: 0.8rem; }
  .tabs { display: flex; flex-wrap: wrap; gap: 0.4rem; margin-bottom: 0.75rem; }
  .tab {
    background: #44403c; color: #e7e5e4; padding: 0.3rem 0.7rem;
    border-radius: 999px; font-size: 0.85rem; font-weight: 500;
  }
  .tab.active { background: #d97706; color: #fff; }
  .track {
    position: relative; height: 64px; border-radius: 6px; touch-action: none;
    background: #0c0a09; border: 1px solid #44403c; user-select: none;
  }
  .track.readonly { cursor: default; }
  .track:not(.readonly) { cursor: crosshair; }
  .axis {
    position: absolute; left: 0; right: 0; top: 50%; height: 1px; background: #44403c;
  }
  .win {
    position: absolute; top: 6px; bottom: 6px; border-radius: 4px;
    background: rgba(217, 119, 6, 0.22); border: 1px solid #d97706;
  }
  .handle {
    position: absolute; top: -2px; bottom: -2px; width: 10px; cursor: ew-resize;
    background: #fbbf24; border-radius: 3px;
  }
  .handle.left { left: -5px; }
  .handle.right { right: -5px; }
  .dot {
    position: absolute; top: 50%; width: 9px; height: 9px; margin: -4.5px 0 0 -4.5px;
    border-radius: 50%; background: #78716c; pointer-events: none;
  }
  .dot.insample { background: #fbbf24; box-shadow: 0 0 0 2px rgba(251, 191, 36, 0.25); }
  .scale { display: flex; justify-content: space-between; color: #78716c; font-size: 0.75rem; margin-top: 0.25rem; }
  .wininfo { color: #d6d3d1; font-size: 0.85rem; margin: 0.5rem 0; }
  button {
    cursor: pointer; padding: 0.5rem 1rem; border: none; border-radius: 6px;
    background: #d97706; color: #fff; font-weight: 600;
  }
  button:disabled { opacity: 0.45; cursor: not-allowed; }
  .err { color: #fb7185; }
  .okmsg { color: #86efac; }
  .card { border: 1px solid #44403c; border-radius: 8px; padding: 0.75rem 1rem; margin-bottom: 0.75rem; background: #1c1917; }
  .card.sel { border-color: #d97706; }
  .card-head { display: flex; align-items: baseline; gap: 0.75rem; flex-wrap: wrap; }
  .chainage { font-weight: 700; }
  .rate { font-size: 1.05rem; font-weight: 700; }
  .rate.up { color: #fca5a5; }
  .rate.down { color: #93c5fd; }
  .rate.flat, .rate.none { color: #a8a29e; }
  .meta { color: #a8a29e; font-size: 0.8rem; }
  .chart { width: 100%; height: auto; background: #0c0a09; border-radius: 6px; margin: 0.5rem 0; }
  table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
  th, td { text-align: left; padding: 0.35rem 0.45rem; border-bottom: 1px solid #44403c; }
  th { color: #a8a29e; font-weight: 500; }
  .empty { color: #78716c; font-size: 0.85rem; }
  ul.spec { margin: 0; padding-left: 1.2rem; color: #d6d3d1; font-size: 0.88rem; line-height: 1.7; }
</style>

<!-- 上块：拖取点窗口 -->
<div class="block">
  <h2>
    上块 · 拖取点窗口
    {#if !isWriter}<span class="hint">（巡检员只读：不能调整窗口，也不能提交读数）</span>{/if}
  </h2>
  {#if sections.length === 0}
    <p class="empty">还没有任何断面数据。</p>
  {:else}
    <div class="tabs">
      {#each sections as s}
        <button class="tab" class:active={s.chainage === selected} on:click={() => selectSection(s.chainage)}>
          {s.chainage}
        </button>
      {/each}
    </div>
    {#if sel}
      <div
        class="track"
        class:readonly={!isWriter}
        bind:this={trackEl}
        on:pointerdown={onTrackDown}
      >
        <div class="axis"></div>
        {#if draft}
          <div class="win" style="left:{pct(draft.start)}%;width:{Math.max(0.4, pct(draft.end) - pct(draft.start))}%">
            {#if isWriter}
              <div class="handle left" data-handle="left"></div>
              <div class="handle right" data-handle="right"></div>
            {/if}
          </div>
        {/if}
        {#each sel.done_points as p (p.id)}
          <div
            class="dot"
            class:insample={sampleIds.has(p.id)}
            style="left:{pct(Date.parse(p.processed_at))}%"
            title="{fmtT(p.processed_at)} · {p.delta_mm} mm"
          ></div>
        {/each}
      </div>
      <div class="scale"><span>{fmtMs(domain[0])}</span><span>{fmtMs(domain[1])}</span></div>
      <p class="wininfo">
        {#if draft}
          当前窗口：{fmtMs(draft.start)} ~ {fmtMs(draft.end)}
          {#if sel.window}
            （已存版本 v{sel.window.version}：{fmtT(sel.window.start_at)} ~ {fmtT(sel.window.end_at)}，{sel.window.updated_by} 保存）
          {:else}
            （尚未保存过窗口，拖动后需保存才生效）
          {/if}
        {:else}
          未设窗口：使用全部已办结点（{sel.done_points.length} 个）{#if isWriter}，在轨道上拖动即可框选窗口{/if}
        {/if}
      </p>
      {#if isWriter}
        <button on:click={saveWindow} disabled={!dirty || !draft || draft.end <= draft.start}>
          保存窗口
        </button>
      {/if}
      {#if err}<p class="err">{err}</p>{/if}
      {#if notice}<p class="okmsg">{notice}</p>{/if}
    {/if}
  {/if}
</div>

<!-- 中块：按断面列出拟合速率和样本点 -->
<div class="block">
  <h2>中块 · 各断面拟合速率与样本点</h2>
  {#each sections as sec (sec.chainage)}
    {@const g = chartGeom(sec)}
    <div class="card" class:sel={sec.chainage === selected}>
      <div class="card-head">
        <span class="chainage">{sec.chainage}</span>
        <span class="rate {sec.trend}">
          {#if sec.rate_mm_per_week == null}
            速率空（{TREND_TEXT[sec.trend]}）
          {:else}
            速率 {fmtRate(sec.rate_mm_per_week)} mm/周 · {TREND_TEXT[sec.trend]}
          {/if}
        </span>
        <span class="meta">
          样本 {sec.point_count}/{sec.done_points.length} 点
          {#if sec.window}
            · 窗口 v{sec.window.version}（{fmtT(sec.window.start_at)} ~ {fmtT(sec.window.end_at)}）
          {:else}
            · 未设窗口（全部办结点）
          {/if}
        </span>
      </div>
      {#if g}
        <svg class="chart" viewBox="0 0 320 120">
          {#if g.line}
            <line x1={g.line.x1} y1={g.line.y1} x2={g.line.x2} y2={g.line.y2} stroke="#fbbf24" stroke-width="1.5" />
          {/if}
          {#each g.dots as d}
            <circle cx={d.x} cy={d.y} r="3" fill={d.inSample ? "#fbbf24" : "#57534e"} />
          {/each}
        </svg>
      {/if}
      {#if sec.points.length}
        <table>
          <thead><tr><th>编号</th><th>办结时刻</th><th>收敛mm</th></tr></thead>
          <tbody>
            {#each sec.points as p (p.id)}
              <tr><td>{p.id}</td><td>{fmtT(p.processed_at)}</td><td>{p.delta_mm}</td></tr>
            {/each}
          </tbody>
        </table>
      {:else}
        <p class="empty">窗口内没有样本点，速率为空。</p>
      {/if}
    </div>
  {/each}
</div>

<!-- 下块：计算口径 -->
<div class="block">
  <h2>下块 · 计算口径</h2>
  <ul class="spec">
    <li>样本：只取状态为「已完成」（已办结）的测点；待认领（pending）的测点不进入样本。</li>
    <li>取点：以办结时刻 processed_at 为横轴、收敛值 delta_mm（mm）为纵轴。</li>
    <li>拟合：对窗口内样本做最小二乘一元线性回归，斜率即该断面速率，单位 mm/周；速率为正表示收敛读数在抬高（越挤越快）。</li>
    <li>窗口：按断面保存；只统计办结时刻落在窗口内（含端点）的点；未设窗口时使用该断面全部已办结点。</li>
    <li>空值：窗口内样本不足 2 个、或样本时刻全相同时速率为空，页面显示「空 / —」。</li>
    <li>并发：保存窗口需带版本号；两人几乎同时改同一断面窗口时只有一笔生效，另一笔返回 409，需刷新后重试。</li>
    <li>权限：测量员（surveyor）可拖取并保存窗口、可提交读数；巡检员（inspector）只读，不能改窗口也不能提交读数。</li>
  </ul>
</div>
