/* Agent Memory Benchmark — static leaderboard renderer.
 * Loads data.json (produced by build-data.mjs) and renders a ClickBench-style
 * sortable leaderboard. Selectors pick a (benchmark, answerer model) run;
 * system checkboxes filter rows; per-column heatmap colors best->worst.
 * Plain vanilla JS, no dependencies, no CDN. */

(function () {
  "use strict";

  // ---- Column definitions ----------------------------------------------
  // key: metric key on system.summary
  // higherIsBetter: drives heatmap direction (green=best)
  // fmt: value -> display string
  const COLUMNS = [
    {
      key: "answerAccuracy",
      label: "Answer %",
      higherIsBetter: true,
      fmt: pct,
    },
    {
      key: "retrievalRecall",
      label: "Retrieval %",
      higherIsBetter: true,
      fmt: pct,
    },
    {
      key: "citationRecall",
      label: "Citation %",
      higherIsBetter: true,
      fmt: pct,
    },
    {
      key: "averageLatencyMs",
      label: "Avg latency",
      higherIsBetter: false,
      fmt: ms,
    },
    {
      key: "averageContextTokensApprox",
      label: "Avg context tok.",
      higherIsBetter: false,
      fmt: tokens,
    },
  ];

  // ---- App state --------------------------------------------------------
  const state = {
    data: null,
    suiteId: null,
    answererModel: null,
    enabledSystems: new Set(), // systemIds currently checked
    sortKey: "answerAccuracy",
    sortDir: "desc",
  };

  // ---- Formatters -------------------------------------------------------
  function pct(v) {
    if (v == null) return null;
    return (v * 100).toFixed(1) + "%";
  }
  function ms(v) {
    if (v == null) return null;
    if (v >= 1000) return (v / 1000).toFixed(2) + " s";
    return v.toFixed(1) + " ms";
  }
  function tokens(v) {
    if (v == null) return null;
    return Math.round(v).toLocaleString("en-US");
  }

  // ---- Data lookup ------------------------------------------------------
  function findRun(suiteId, answererModel) {
    if (!state.data) return null;
    return (
      state.data.runs.find(
        (r) => r.suiteId === suiteId && r.answererModel === answererModel
      ) || null
    );
  }

  // Models available for the currently selected benchmark.
  function modelsForSuite(suiteId) {
    const set = new Set();
    for (const r of state.data.runs) {
      if (r.suiteId === suiteId && r.answererModel) set.add(r.answererModel);
    }
    return [...set].sort();
  }

  // ---- Heatmap ----------------------------------------------------------
  // Linear green->yellow->red gradient. t=1 is best (green), t=0 worst (red).
  function heatColor(t) {
    // Clamp.
    t = Math.max(0, Math.min(1, t));
    // Interpolate hue from red (0) to green (120) through yellow (60).
    const hue = 120 * t;
    // Keep it pale so monospace text stays readable.
    return `hsl(${hue}, 72%, 88%)`;
  }

  // Given the visible systems + a column, compute background color per cell.
  function columnHeat(systems, col) {
    let vals = systems.map((s) => s.summary[col.key]).filter((v) => v != null);
    // For lower-is-better columns (latency, context tokens), a 0 means the
    // system retrieved nothing / made no real call — a failure, not "leanest".
    // Exclude zeros from the best-end of the scale so a failed system isn't
    // painted green; they're colored worst below.
    if (!col.higherIsBetter) vals = vals.filter((v) => v > 0);
    if (vals.length < 2) return () => null; // no contrast to show
    const min = Math.min(...vals);
    const max = Math.max(...vals);
    if (max === min) return () => null;
    return (v) => {
      if (v == null) return null;
      if (!col.higherIsBetter && v <= 0) return heatColor(0); // failed => worst
      let t = (v - min) / (max - min); // 0..1, higher value => 1
      t = Math.max(0, Math.min(1, t));
      if (!col.higherIsBetter) t = 1 - t; // invert for latency / tokens
      return heatColor(t);
    };
  }

  // ---- Rendering --------------------------------------------------------
  function render() {
    renderControls();
    renderBoard();
    renderFooter();
    renderReproduce();
  }

  function renderControls() {
    const benchEl = document.getElementById("benchmark-pills");
    const modelEl = document.getElementById("model-pills");
    const togEl = document.getElementById("system-toggles");

    // Benchmark pills.
    benchEl.innerHTML = "";
    for (const b of state.data.benchmarks) {
      const btn = pillButton(b.suiteLabel, b.suiteId === state.suiteId, () => {
        state.suiteId = b.suiteId;
        // Re-pick a valid model for this suite.
        const models = modelsForSuite(b.suiteId);
        if (!models.includes(state.answererModel)) {
          state.answererModel = models[0] || null;
        }
        resetSystemsForRun();
        render();
      });
      benchEl.appendChild(btn);
    }

    // Model pills (scoped to current benchmark).
    modelEl.innerHTML = "";
    const models = modelsForSuite(state.suiteId);
    for (const m of models) {
      const btn = pillButton(m, m === state.answererModel, () => {
        state.answererModel = m;
        resetSystemsForRun();
        render();
      });
      modelEl.appendChild(btn);
    }

    // System toggles (scoped to current run).
    togEl.innerHTML = "";
    const run = findRun(state.suiteId, state.answererModel);
    const systems = run ? run.systems : [];
    for (const s of systems) {
      const label = document.createElement("label");
      label.className = "toggle";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = state.enabledSystems.has(s.systemId);
      cb.addEventListener("change", () => {
        if (cb.checked) state.enabledSystems.add(s.systemId);
        else state.enabledSystems.delete(s.systemId);
        renderBoard();
      });
      label.appendChild(cb);
      label.appendChild(document.createTextNode(s.systemLabel));
      togEl.appendChild(label);
    }
  }

  function pillButton(text, pressed, onClick) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "pill";
    btn.textContent = text;
    btn.setAttribute("aria-pressed", pressed ? "true" : "false");
    btn.addEventListener("click", onClick);
    return btn;
  }

  // When the selected run changes, default all its systems to enabled.
  function resetSystemsForRun() {
    const run = findRun(state.suiteId, state.answererModel);
    state.enabledSystems = new Set(
      run ? run.systems.map((s) => s.systemId) : []
    );
  }

  function renderBoard() {
    const board = document.getElementById("board");
    board.innerHTML = "";

    const run = findRun(state.suiteId, state.answererModel);
    if (!run) {
      board.innerHTML =
        '<div class="empty-state">No results for this benchmark and model yet.</div>';
      return;
    }

    // Caption.
    const caption = document.createElement("p");
    caption.className = "run-caption";
    const parts = [];
    if (run.questionCount != null) parts.push(`${run.questionCount} questions`);
    if (run.trials != null) parts.push(`${run.trials} trial(s)`);
    if (run.topK != null) parts.push(`top-K ${run.topK}`);
    if (run.answererModel) parts.push(`answerer ${run.answererModel}`);
    caption.innerHTML =
      `<strong>${escapeHtml(run.suiteLabel)}</strong> · ` +
      parts.map(escapeHtml).join(" · ");
    board.appendChild(caption);

    // Visible (checked) systems.
    let systems = run.systems.filter((s) =>
      state.enabledSystems.has(s.systemId)
    );

    if (systems.length === 0) {
      const note = document.createElement("div");
      note.className = "empty-state";
      note.textContent = "No systems selected — enable one above.";
      board.appendChild(note);
      return;
    }

    // Sort.
    systems = sortSystems(systems);

    // Precompute heatmap fns over the visible set.
    const heatFns = {};
    for (const col of COLUMNS) heatFns[col.key] = columnHeat(systems, col);

    // Build table.
    const scroll = document.createElement("div");
    scroll.className = "table-scroll";
    const table = document.createElement("table");
    table.className = "leaderboard";

    // Head.
    const thead = document.createElement("thead");
    const hrow = document.createElement("tr");
    hrow.appendChild(headCell("System", "system", "col-system"));
    for (const col of COLUMNS) {
      hrow.appendChild(headCell(col.label, col.key, "num"));
    }
    thead.appendChild(hrow);
    table.appendChild(thead);

    // Body.
    const tbody = document.createElement("tbody");
    for (const s of systems) {
      const tr = document.createElement("tr");

      const nameTd = document.createElement("td");
      nameTd.className = "col-system";
      nameTd.textContent = s.systemLabel;
      tr.appendChild(nameTd);

      for (const col of COLUMNS) {
        const td = document.createElement("td");
        td.className = "heat";
        const v = s.summary[col.key];
        const disp = col.fmt(v);
        if (disp == null) {
          td.classList.add("na");
          td.textContent = "—";
        } else {
          td.textContent = disp;
          const bg = heatFns[col.key](v);
          if (bg) td.style.backgroundColor = bg;
        }
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    scroll.appendChild(table);
    board.appendChild(scroll);
  }

  function headCell(label, sortKey, cls) {
    const th = document.createElement("th");
    th.className = cls;
    th.textContent = label;
    const arrow = document.createElement("span");
    arrow.className = "sort-arrow";
    th.appendChild(arrow);

    if (state.sortKey === sortKey) {
      th.setAttribute(
        "aria-sort",
        state.sortDir === "asc" ? "ascending" : "descending"
      );
    }
    th.addEventListener("click", () => {
      if (state.sortKey === sortKey) {
        state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
      } else {
        state.sortKey = sortKey;
        // Default direction: name asc, metrics desc.
        state.sortDir = sortKey === "system" ? "asc" : "desc";
      }
      renderBoard();
    });
    return th;
  }

  function sortSystems(systems) {
    const key = state.sortKey;
    const dir = state.sortDir === "asc" ? 1 : -1;
    const copy = systems.slice();
    copy.sort((a, b) => {
      if (key === "system") {
        const av = a.systemLabel.toLowerCase();
        const bv = b.systemLabel.toLowerCase();
        return av < bv ? -dir : av > bv ? dir : 0;
      }
      const av = a.summary[key];
      const bv = b.summary[key];
      // Nulls always sort last regardless of direction.
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      return (av - bv) * dir;
    });
    return copy;
  }

  function renderReproduce() {
    const el = document.getElementById("reproduce-cmd");
    if (!el) return;
    const suite = state.suiteId || "longmemeval-oracle-10";
    el.textContent = `bun run scripts/run.ts --config configs/${suite}.all.json`;
  }

  function renderFooter() {
    const el = document.getElementById("footer-meta");
    const run = findRun(state.suiteId, state.answererModel);
    const bits = [];
    if (run && run.generatedAt) {
      const d = new Date(run.generatedAt);
      bits.push(
        `Run generated ${isNaN(d) ? run.generatedAt : d.toUTCString()}`
      );
    }
    if (run && run.answererModel) bits.push(`answerer ${run.answererModel}`);
    if (state.data && state.data.builtAt) {
      const d = new Date(state.data.builtAt);
      bits.push(
        `data built ${isNaN(d) ? state.data.builtAt : d.toUTCString()}`
      );
    }
    el.textContent = bits.join(" · ");
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  // ---- Boot -------------------------------------------------------------
  function boot(data) {
    state.data = data;
    if (!data.runs || data.runs.length === 0) {
      document.getElementById("board").innerHTML =
        '<div class="empty-state">No benchmark results yet. Run a suite and rebuild <code>data.json</code>.</div>';
      return;
    }
    // Default selection: first benchmark, first model for it.
    state.suiteId = data.benchmarks[0] ? data.benchmarks[0].suiteId : null;
    const models = modelsForSuite(state.suiteId);
    state.answererModel = models[0] || null;
    resetSystemsForRun();
    render();
  }

  fetch("./data.json")
    .then((r) => {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    })
    .then(boot)
    .catch((err) => {
      document.getElementById("board").innerHTML =
        '<div class="empty-state">Failed to load data.json: ' +
        escapeHtml(err.message) +
        "</div>";
    });
})();
