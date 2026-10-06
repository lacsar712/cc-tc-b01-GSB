<script>
  import { onDestroy } from "svelte";
  import RateChart from "./RateChart.svelte";

  export let session = null;
  export let onUnauth = () => {};

  let sections = [];
  let loading = false;
  let loadErr = "";
  let notice = "";
  let conflict = false;
  let selected = "";
  let draft = { start: 0, end: 1, d0: 0, d1: 1 };
  let dragging = null; // "start" | "end" | null
  let stripEl;
  let pollTimer;

  $: isWriter = session?.role === "writer";
  $: current = sections.find((s) => s.chainage === selected) || null;
  $: canDrag = isWriter && current && current.domain;
  // 仅在切换断面、或服务器窗口版本变化（自己保存成功 / 他人并发落库）时用服务器值重置草稿，
  // 拖拽过程中与同版本的后台轮询都不去打断手柄位置（用 appliedKey 去重，不把 dragging 列进依赖）。
  $: sectionKey = current ? `${current.chainage}#${current.window?.version ?? 0}` : "";
  let appliedKey = "";
  $: if (current && current.domain && sectionKey && sectionKey !== appliedKey) {
    appliedKey = sectionKey;
    draft = {
      d0: current.d0ms,
      d1: current.d1ms,
      start: current.wStart ?? current.d0ms,
      end: current.wEnd ?? current.d1ms,
    };
  }

  function headers() {
    return session ? { Authorization: "Bearer " + session.token } : {};
  }
  function iso(ms) { return new Date(ms).toISOString(); }
  function pad(n) { return String(n).padStart(2, "0"); }
  function fmtDay(t) {
    const d = new Date(t);
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  }
  function fmtFull(t) {
    const d = new Date(t);
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }
  function fmtSlope(v) { return (v >= 0 ? "+" : "") + (Math.round(v * 1000) / 1000).toFixed(3); }

  async function load() {
    if (!session) return;
    loading = true;
    try {
      const res = await fetch("/api/rate/sections", { headers: headers() });
      if (res.status === 401) { onUnauth(); return; }
      if (!res.ok) { loadErr = "速率数据加载失败"; return; }
      const data = await res.json();
      sections = data.map((s) => ({
        ...s,
        wStart: s.window?.start_at ? Date.parse(s.window.start_at) : null,
        wEnd: s.window?.end_at ? Date.parse(s.window.end_at) : null,
        d0ms: s.domain ? Date.parse(s.domain.start_at) : null,
        d1ms: s.domain ? Date.parse(s.domain.end_at) : null,
        pts: (s.points || []).map((p) => ({ ...p, t: Date.parse(p.x) })),
      }));
      if (!selected && sections.length) selected = sections[0].chainage;
    } catch {
      loadErr = "无法连接接口";
    } finally {
      loading = false;
    }
  }

  function pct(ms) {
    const span = draft.d1 - draft.d0 || 1;
    return Math.max(0, Math.min(1, (ms - draft.d0) / span)) * 100;
  }
  const MIN_GAP = 0.02;

  function fracFromEvent(ev) {
    const r = stripEl.getBoundingClientRect();
    return Math.max(0, Math.min(1, (ev.clientX - r.left) / r.width));
  }
  function onDown(which, ev) {
    if (!canDrag) return;
    dragging = which;
    conflict = false;
    notice = "";
    ev.currentTarget.setPointerCapture?.(ev.pointerId);
  }
  function onMove(ev) {
    if (!dragging) return;
    const f = fracFromEvent(ev);
    const span = draft.d1 - draft.d0 || 1;
    let t = draft.d0 + f * span;
    if (dragging === "start") {
      t = Math.min(t, draft.end - span * MIN_GAP);
      draft = { ...draft, start: t };
    } else {
      t = Math.max(t, draft.start + span * MIN_GAP);
      draft = { ...draft, end: t };
    }
  }
  async function onUp() {
    if (!dragging) return;
    dragging = null;
    if (!canDrag) return;
    await commit(draft.start, draft.end);
  }
  async function resetWindow() {
    if (!canDrag) return;
    draft = { ...draft, start: draft.d0, end: draft.d1 };
    await commit(draft.d0, draft.d1, true);
  }

  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

  // 键盘可达：左右方向键每次移动全程 1%。
  async function nudge(which, dir) {
    if (!canDrag) return;
    const span = draft.d1 - draft.d0 || 1;
    const step = span * 0.01;
    conflict = false;
    notice = "";
    if (which === "start") {
      draft = { ...draft, start: clamp(draft.start + dir * step, draft.d0, draft.end - span * MIN_GAP) };
    } else {
      draft = { ...draft, end: clamp(draft.end + dir * step, draft.start + span * MIN_GAP, draft.d1) };
    }
    await commit(draft.start, draft.end);
  }

  async function commit(startMs, endMs, isReset = false) {
    loadErr = "";
    const version = current.window ? current.window.version : null;
    // 边界贴到域端点时按“不限”处理，避免无谓的端点漂移。
    const startAt = !isReset && Math.abs(startMs - current.d0ms) > 1 ? iso(startMs) : null;
    const endAt = !isReset && Math.abs(endMs - current.d1ms) > 1 ? iso(endMs) : null;
    try {
      const res = await fetch("/api/rate/window", {
        method: "PUT",
        headers: { "Content-Type": "application/json", ...headers() },
        body: JSON.stringify({ chainage: current.chainage, start_at: startAt, end_at: endAt, version }),
      });
      if (res.status === 401) { onUnauth(); return; }
      const data = await res.json().catch(() => ({}));
      if (res.status === 403) {
        loadErr = "巡检员为只读，不能调整取点窗口";
        await load();
        return;
      }
      if (res.status === 409) {
        conflict = true;
        notice = `该断面窗口刚被 ${data?.current?.updated_by || "他人"} 更新，已只保留那一笔并为你加载。`;
      } else if (!res.ok) {
        loadErr = data.detail || "窗口保存失败";
      } else {
        conflict = false;
        notice = `取点窗口已保存（第 ${data.version} 笔）。`;
      }
      await load(); // 以服务器结果为准重新拟合
    } catch {
      loadErr = "保存窗口时网络异常";
    }
  }

  // 轻轮询：5s 拉一次，能看到别人改窗；拖拽中不抢草稿。
  pollTimer = setInterval(() => { if (!dragging) load(); }, 5000);
  onDestroy(() => clearInterval(pollTimer));
  load();
</script>

<section class="page">
  <!-- 上块：取点窗口（拖拽） -->
  <div class="card">
    <div class="card-head">
      <h2>取点窗口</h2>
      <select bind:value={selected} disabled={!sections.length}>
        {#each sections as s}
          <option value={s.chainage}>{s.chainage}</option>
        {/each}
      </select>
    </div>

    {#if !isWriter}
      <p class="ro-banner">巡检员为只读：可查看各断面拟合速率与样本点，但不能调整取点窗口，也不能提交。</p>
    {/if}

    {#if current && current.domain}
      <div class="strip-wrap" class:disabled={!canDrag}>
        <div class="strip" bind:this={stripEl}
             on:pointermove={onMove} on:pointerup={onUp} on:pointercancel={onUp}>
          <div class="band" style="left:{pct(draft.start)}%;width:{pct(draft.end) - pct(draft.start)}%"></div>
          <div class="handle h-start" style="left:{pct(draft.start)}%"
               on:pointerdown={(e) => onDown("start", e)}
               on:keydown={(e) => { if (e.key === "ArrowLeft") nudge("start", -1); if (e.key === "ArrowRight") nudge("start", 1); }}
               role="slider" tabindex={canDrag ? 0 : -1}
               aria-label="窗口起点（办结时刻）"
               aria-valuemin="0" aria-valuemax="100" aria-valuenow={Math.round(pct(draft.start))}
               aria-valuetext={fmtDay(draft.start)} aria-disabled={!canDrag}></div>
          <div class="handle h-end" style="left:{pct(draft.end)}%"
               on:pointerdown={(e) => onDown("end", e)}
               on:keydown={(e) => { if (e.key === "ArrowLeft") nudge("end", -1); if (e.key === "ArrowRight") nudge("end", 1); }}
               role="slider" tabindex={canDrag ? 0 : -1}
               aria-label="窗口终点（办结时刻）"
               aria-valuemin="0" aria-valuemax="100" aria-valuenow={Math.round(pct(draft.end))}
               aria-valuetext={fmtDay(draft.end)} aria-disabled={!canDrag}></div>
        </div>
        <div class="strip-labels">
          <span>{fmtDay(draft.start)}</span>
          <span class="hint">{canDrag ? "拖动两端手柄选取办结时刻范围，松手即按该窗口重新拟合" : "只读视图"}</span>
          <span>{fmtDay(draft.end)}</span>
        </div>
        {#if isWriter}
          <button class="ghost" on:click={resetWindow}>重置为全程</button>
        {/if}
      </div>
    {:else if current}
      <p class="muted">该断面还没有已办结读数，暂无可选时间范围。</p>
    {/if}
  </div>

  {#if loadErr}<p class="err">{loadErr}</p>{/if}
  {#if notice}<p class={conflict ? "warn" : "ok-msg"}>{notice}</p>{/if}

  <!-- 中块：按断面列出拟合速率与样本点 -->
  <div class="card">
    <h2>各断面周进尺速率</h2>
    {#if loading && !sections.length}<p class="muted">加载中…</p>{/if}
    {#each sections as s (s.chainage)}
      <div class="section" class:selected={s.chainage === selected}>
        <div class="sec-head">
          <button class="chainage-btn" on:click={() => (selected = s.chainage)}>断面 {s.chainage}</button>
          <div class="stat">
            {#if s.fit}
              <span class="state state-{s.fit.state}">
                {s.fit.state === "正" ? "▲" : s.fit.state === "负" ? "▼" : "→"} {s.fit.state === "正" ? "上升" : s.fit.state === "负" ? "回落" : "走平"}
              </span>
              <strong>{fmtSlope(s.fit.slope)}</strong><em>mm/周</em>
              <span class="muted small">样本 {s.fit.n} / 已办结 {s.total_done}</span>
              {#if s.fit.r2 != null}<span class="muted small">R² {(Math.round(s.fit.r2 * 100) / 100).toFixed(2)}</span>{/if}
            {:else}
              <span class="state state-空">— 空</span>
              <span class="muted small">窗口内有效样本 {s.sample_count} 个（不足 2 个不成线）</span>
            {/if}
          </div>
        </div>

        <RateChart view={{
          points: s.pts,
          line: s.line ? { t0: Date.parse(s.line.x0), y0: s.line.y0, t1: Date.parse(s.line.x1), y1: s.line.y1 } : null,
          wStart: s.wStart,
          wEnd: s.wEnd,
          fit: s.fit,
        }} />

        <details>
          <summary>办结读数（{s.pts.length}）— 实心为入样，空心为窗口外</summary>
          <table class="pts">
            <thead><tr><th>编号</th><th>办结时刻</th><th>收敛 mm</th><th>是否入样</th></tr></thead>
            <tbody>
              {#each s.pts as p}
                <tr class:out={!p.inSample}>
                  <td>{p.id}</td>
                  <td>{fmtFull(p.t)}</td>
                  <td>{p.y}</td>
                  <td>{p.inSample ? "入样" : "窗口外"}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </details>
      </div>
    {/each}
  </div>

  <!-- 下块：口径 -->
  <div class="card caliber">
    <h2>速率口径</h2>
    <ul>
      <li><strong>取点时刻用“办结时刻”</strong>，不是登记时刻：每个读数以后台判结完成的时间作为横轴。</li>
      <li><strong>只有已办结（status=done）且有办结时刻的读数才进样本</strong>；待认领 / 未办结的点一律排除，不计入拟合。</li>
      <li>按断面分组，对窗口内样本做<strong>一元最小二乘拟合</strong> y = a + b·x，x 以“周”（7 天）计，b 即周进尺速率，单位 mm/周。</li>
      <li>b 明显为正记“上升”（拱顶持续抬高，越挤越快方向），接近 0 记“走平”，为负记“回落”；<strong>窗口内有效样本不足 2 个时记“空”</strong>，不编造数字。</li>
      <li>取点窗口只认<strong>已办结时间落在闭区间 [起, 止] 内</strong>的点；把窗口缩到样本之外，速率即变为走平/空，图上这些点转为灰色空心。</li>
      <li>窗口由<strong>测量员</strong>在上方拖拽调整并落库；<strong>巡检员只读，不能改、也不能提交</strong>。同一断面并发调整以办结时间戳先后只保留一笔，落败方收到冲突并自动对齐到最新一笔。</li>
    </ul>
  </div>
</section>

<style>
  .page { display: flex; flex-direction: column; gap: 1rem; }
  .card {
    background: #292524; border: 1px solid #44403c; border-radius: 8px;
    padding: 1rem 1.25rem;
  }
  .card-head { display: flex; align-items: center; justify-content: space-between; gap: 1rem; flex-wrap: wrap; }
  h2 { font-size: 1.05rem; margin: 0 0 0.75rem; color: #fbbf24; }
  .card-head h2 { margin: 0; }
  select {
    background: #0c0a09; color: #fafaf9; border: 1px solid #57534e;
    border-radius: 6px; padding: 0.4rem 0.6rem;
  }
  .ro-banner {
    background: #422006; border: 1px solid #854d0e; color: #fde68a;
    border-radius: 6px; padding: 0.5rem 0.7rem; font-size: 0.85rem; margin: 0.7rem 0 0;
  }
  .muted { color: #a8a29e; }
  .small { font-size: 0.78rem; }
  .err { color: #fb7185; margin: 0; }
  .warn { color: #fbbf24; margin: 0; }
  .ok-msg { color: #86efac; margin: 0; }

  .strip-wrap { margin-top: 0.8rem; user-select: none; }
  .strip-wrap.disabled { opacity: 0.85; }
  .strip {
    position: relative; height: 34px; border-radius: 6px;
    background: #0c0a09; border: 1px solid #57534e; touch-action: none;
  }
  .band {
    position: absolute; top: 0; bottom: 0; background: rgba(57,135,229,0.22);
    border-left: 1px solid rgba(57,135,229,0.5); border-right: 1px solid rgba(57,135,229,0.5);
  }
  .handle {
    position: absolute; top: -4px; width: 14px; height: 42px; margin-left: -7px;
    background: #3987e5; border: 2px solid #0c0a09; border-radius: 4px; cursor: ew-resize;
  }
  .disabled .handle { cursor: not-allowed; pointer-events: none; }
  .strip-labels {
    display: flex; justify-content: space-between; align-items: center;
    font-size: 0.78rem; color: #a8a29e; margin-top: 0.45rem;
  }
  .strip-labels .hint { color: #78716c; }
  .ghost {
    margin-top: 0.5rem; cursor: pointer; padding: 0.35rem 0.8rem; border-radius: 6px;
    background: #57534e; color: #fff; border: none; font-size: 0.82rem;
  }

  .section { border-top: 1px solid #44403c; padding: 0.9rem 0; }
  .section.selected { background: rgba(57,135,229,0.06); border-radius: 6px; padding-left: 0.5rem; padding-right: 0.5rem; }
  .sec-head { display: flex; align-items: center; justify-content: space-between; gap: 1rem; flex-wrap: wrap; margin-bottom: 0.5rem; }
  .chainage-btn {
    background: none; border: none; color: #fafaf9; font-weight: 700; font-size: 1rem;
    cursor: pointer; padding: 0;
  }
  .stat { display: flex; align-items: baseline; gap: 0.5rem; flex-wrap: wrap; }
  .stat strong { font-size: 1.15rem; color: #fafaf9; font-variant-numeric: tabular-nums; }
  .stat em { color: #a8a29e; font-style: normal; font-size: 0.8rem; }
  .state { font-size: 0.8rem; padding: 0.12rem 0.5rem; border-radius: 4px; font-weight: 600; }
  .state-正 { background: #7c2d12; color: #fed7aa; }
  .state-负 { background: #1e3a5f; color: #bfdbfe; }
  .state-平 { background: #44403c; color: #d6d3d1; }
  .state-空 { background: #292524; color: #a8a29e; border: 1px solid #57534e; }

  details { margin-top: 0.6rem; }
  summary { cursor: pointer; color: #d6d3d1; font-size: 0.85rem; }
  table.pts { width: 100%; border-collapse: collapse; font-size: 0.83rem; margin-top: 0.5rem; }
  table.pts th, table.pts td { text-align: left; padding: 0.35rem 0.45rem; border-bottom: 1px solid #44403c; }
  table.pts tr.out { color: #78716c; }

  .caliber ul { margin: 0; padding-left: 1.1rem; color: #d6d3d1; font-size: 0.86rem; line-height: 1.65; }
  .caliber li { margin-bottom: 0.35rem; }
</style>
