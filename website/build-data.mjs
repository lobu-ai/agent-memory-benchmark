#!/usr/bin/env node
// Build website/public/data.json from ../results/*.json
//
// Run from the repo root (or anywhere):
//   bun website/build-data.mjs
//   node website/build-data.mjs
//
// Reads every results/*.json report and emits a leaderboard-ready payload
// shaped for the ClickBench-style selectors: a list of distinct benchmarks
// (suites), a list of distinct answerer models, a list of judges, and one
// `run` per (suiteId, answererModel, judge) combination whose `systems`
// array is the latest result for each memory system in that combination.
//
// Two judge boards:
//   - gemini-2.5-flash (original): the *.regraded.json reports carry gemini
//     verdicts (scripts/regrade-llm-judge.mjs). Gemini is quota-capped, so
//     this board is frozen to the systems judged before the cap.
//   - alternate judges (e.g. glm-5.1): results/judges/*.judge-<model>.json
//     verdict files (scripts/regrade-zai-judge.mjs) override answerAccuracy
//     on their source report; recall/latency/tokens come from the run itself.
//
// Defensive about missing/renamed fields so a malformed or partial report
// never crashes the build (non-finite numbers -> null).

import {
  readdirSync,
  readFileSync,
  writeFileSync,
  mkdirSync,
  existsSync,
} from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const RESULTS_DIR = resolve(__dirname, "..", "results");
const JUDGES_DIR = join(RESULTS_DIR, "judges");
const OUT_DIR = join(__dirname, "public");
const OUT_FILE = join(OUT_DIR, "data.json");

// Judge metadata, in display order (the original gemini board first).
const JUDGE_META = {
  "gemini-2.5-flash": {
    label: "gemini-2.5-flash (original)",
    note: "gemini quota-capped; the query-rewrite configuration is not yet judged by gemini",
  },
  "glm-5.1": {
    label: "glm-5.1 (consistent, incl. query-rewrite config)",
    note: "judge is the same model family as the shared answerer (self-grading); applied identically to every system, so deltas are fair. Judge script committed.",
  },
  "claude-sonnet-4-6": {
    label: "claude-sonnet-4-6 (independent)",
    note: "cross-family judge — independent of the glm-5.1 answerer, so no self-grading. Same rubric as the glm judge; runs via the Claude Code CLI (scripts/regrade-claude-judge.mjs).",
  },
};
const ORIGINAL_JUDGE = "gemini-2.5-flash";
// The gemini board is frozen to the systems gemini actually judged before the
// quota cap; later experimental configurations only have alternate-judge
// verdicts and must not change the published gemini numbers. Supermemory's
// hosted-API runs were removed from the published boards (2026-06-11) —
// only the reproducible self-hosted binary is benchmarked now, and it has
// no gemini verdicts yet.
const GEMINI_JUDGED_SYSTEM_IDS = new Set(["lobu", "letta"]);

/** Coerce to a finite number or return null. */
function num(v) {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/**
 * Per-trial spread for a metric. Single-trial LongMemEval swings ~±14 points,
 * so the leaderboard must show variance, not a single point. Returns
 * { mean, min, max, stdev, n } or null.
 */
function spread(xs) {
  const v = xs.filter((x) => typeof x === "number" && Number.isFinite(x));
  if (!v.length) return null;
  const mean = v.reduce((a, b) => a + b, 0) / v.length;
  const variance = v.reduce((a, b) => a + (b - mean) ** 2, 0) / v.length;
  return {
    mean,
    min: Math.min(...v),
    max: Math.max(...v),
    stdev: Math.sqrt(variance),
    n: v.length,
  };
}

/** Pull the metric keys we care about out of a summary-like object. */
function pickSummary(s) {
  const o = s && typeof s === "object" ? s : {};
  return {
    questionCount: num(o.questionCount),
    answerAccuracy: num(o.answerAccuracy),
    retrievalRecall: num(o.retrievalRecall),
    citationRecall: num(o.citationRecall),
    citationPrecision: num(o.citationPrecision),
    averageLatencyMs: num(o.averageLatencyMs),
    p95LatencyMs: num(o.p95LatencyMs),
    averageContextTokensApprox: num(o.averageContextTokensApprox),
    averageAnswererPromptTokens: num(o.averageAnswererPromptTokens),
    averageAnswererCompletionTokens: num(o.averageAnswererCompletionTokens),
    overallScore: num(o.overallScore),
  };
}

/**
 * The answerer model can be a string ("glm-5.1 via https://…") or an object
 * ({ model, baseUrl }). Extract a short human model label.
 */
function answererModel(config) {
  const a = config && config.answerer;
  if (!a) return null;
  if (typeof a === "string") {
    // "glm-5.1 via https://api.z.ai/..." -> "glm-5.1"
    const m = a.split(/\s+via\s+/i)[0];
    return m ? m.trim() : a;
  }
  if (typeof a === "object") {
    return a.model || a.name || null;
  }
  return null;
}

/** Human label for a suite id. Falls back to a title-cased id. */
function suiteLabel(suiteId) {
  const known = {
    "longmemeval-mixed-30": "LongMemEval (all 6 categories, 30)",
    "longmemeval-oracle-10": "LongMemEval (temporal-only, 10)",
    "longmemeval-oracle": "LongMemEval (oracle)",
    locomo: "LoCoMo",
  };
  if (known[suiteId]) return known[suiteId];
  // longmemeval-oracle-10 -> "Longmemeval Oracle 10"
  return String(suiteId)
    .split(/[-_]/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

/** Parse one raw report into the systems it reports on, plus run metadata. */
function parseReport(json, filename) {
  const suiteId = json.suiteId || "(unknown suite)";
  const generatedAt = json.generatedAt || null;
  const config =
    json.config && typeof json.config === "object" ? json.config : {};

  const systemsRaw = Array.isArray(json.systems) ? json.systems : [];
  const systems = systemsRaw.map((sys) => {
    const trials = Array.isArray(sys.trials) ? sys.trials : [];
    const trialAccuracies = trials.map((t) =>
      num(t && t.summary ? t.summary.answerAccuracy : null)
    );
    return {
      systemId: sys.systemId || "(unknown)",
      systemLabel: sys.systemLabel || sys.systemId || "(unknown)",
      version:
        typeof sys.version === "string" && sys.version ? sys.version : null,
      mode: typeof sys.mode === "string" && sys.mode ? sys.mode : null,
      summary: pickSummary(sys.summary),
      // Per-trial accuracies + their spread (mean/min/max/stdev). The summary's
      // answerAccuracy is the across-trial mean; this exposes the variance.
      trialAccuracies: trialAccuracies.filter((x) => x != null),
      accuracySpread: spread(trialAccuracies),
    };
  });

  return {
    sourceFile: filename,
    suiteId,
    suiteLabel: suiteLabel(suiteId),
    answererModel: answererModel(config),
    generatedAt,
    generatedAtMs: generatedAt ? Date.parse(generatedAt) || 0 : 0,
    topK: num(config.topK),
    trials: num(config.trials),
    systems,
  };
}

function main() {
  let files = [];
  if (existsSync(RESULTS_DIR)) {
    files = readdirSync(RESULTS_DIR)
      .filter((f) => f.endsWith(".json"))
      .sort();
  }

  const reports = [];
  for (const f of files) {
    const full = join(RESULTS_DIR, f);
    try {
      const json = JSON.parse(readFileSync(full, "utf8"));
      reports.push(parseReport(json, f));
    } catch (err) {
      console.warn(`[build-data] skipping ${f}: ${err.message}`);
    }
  }

  // Each board entry is a report tagged with the judge that graded it.
  const boardReports = [];

  // Original board: *.regraded.json reports carry the gemini verdicts.
  for (const r of reports) {
    if (!r.sourceFile.endsWith(".regraded.json")) continue;
    const systems = r.systems.filter((s) =>
      GEMINI_JUDGED_SYSTEM_IDS.has(s.systemId)
    );
    if (!systems.length) continue;
    boardReports.push({ ...r, judge: ORIGINAL_JUDGE, systems });
  }

  // Alternate-judge boards: verdict files override answerAccuracy on their
  // source report; every other metric stays the source run's own number.
  const reportByFile = new Map(reports.map((r) => [r.sourceFile, r]));
  let judgeFiles = [];
  if (existsSync(JUDGES_DIR)) {
    judgeFiles = readdirSync(JUDGES_DIR)
      .filter((f) => /\.judge-.+\.json$/.test(f))
      .sort();
  }
  for (const f of judgeFiles) {
    let doc;
    try {
      doc = JSON.parse(readFileSync(join(JUDGES_DIR, f), "utf8"));
    } catch (err) {
      console.warn(`[build-data] skipping judges/${f}: ${err.message}`);
      continue;
    }
    const sourceName = f.replace(/\.judge-.+\.json$/, ".json");
    const src = reportByFile.get(sourceName);
    if (!src || !doc.judgeModel) {
      console.warn(`[build-data] skipping judges/${f}: no source report`);
      continue;
    }
    const verdictsBySystem = new Map(
      (Array.isArray(doc.systems) ? doc.systems : []).map((s) => [
        s.systemId,
        s,
      ])
    );
    const trialAccuracy = (t) => {
      const v = (t && Array.isArray(t.questions) ? t.questions : [])
        .map((q) => q.verdict)
        .filter((x) => typeof x === "number" && Number.isFinite(x));
      return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
    };
    const systems = src.systems.flatMap((sys) => {
      const jv = verdictsBySystem.get(sys.systemId);
      if (!jv) return [];
      const trialAccuracies = (Array.isArray(jv.trials) ? jv.trials : []).map(
        trialAccuracy
      );
      return [
        {
          ...sys,
          summary: {
            ...sys.summary,
            answerAccuracy: num(jv.summary && jv.summary.answerAccuracy),
          },
          trialAccuracies: trialAccuracies.filter((x) => x != null),
          accuracySpread: spread(trialAccuracies),
        },
      ];
    });
    if (systems.length) {
      boardReports.push({ ...src, judge: doc.judgeModel, systems });
    }
  }

  // Group board entries by (suiteId, answererModel, judge). Within a
  // combination, keep the LATEST report per systemId so a 5-way run and a
  // later single-system rerun merge into one leaderboard with each system's
  // freshest numbers.
  const comboKey = (r) => `${r.suiteId} ${r.answererModel ?? ""} ${r.judge}`;
  const combos = new Map();

  for (const r of boardReports) {
    const key = comboKey(r);
    let combo = combos.get(key);
    if (!combo) {
      combo = {
        suiteId: r.suiteId,
        suiteLabel: r.suiteLabel,
        answererModel: r.answererModel,
        judge: r.judge,
        generatedAtMs: 0,
        generatedAt: null,
        trials: r.trials,
        topK: r.topK,
        questionCount: null,
        // systemId -> { generatedAtMs, system }
        systemsLatest: new Map(),
      };
      combos.set(key, combo);
    }

    // Freshest run metadata for the combo as a whole.
    if (r.generatedAtMs >= combo.generatedAtMs) {
      combo.generatedAtMs = r.generatedAtMs;
      combo.generatedAt = r.generatedAt;
      combo.trials = r.trials;
      combo.topK = r.topK;
    }

    for (const sys of r.systems) {
      const cur = combo.systemsLatest.get(sys.systemId);
      if (!cur || r.generatedAtMs >= cur.generatedAtMs) {
        combo.systemsLatest.set(sys.systemId, {
          generatedAtMs: r.generatedAtMs,
          system: sys,
        });
      }
    }
  }

  // Materialize combos into runs.
  const runs = [];
  for (const combo of combos.values()) {
    const systems = [...combo.systemsLatest.values()].map((e) => e.system);
    // questionCount: take from any system that reports one (they should agree).
    let questionCount = null;
    for (const s of systems) {
      if (s.summary.questionCount != null) {
        questionCount = s.summary.questionCount;
        break;
      }
    }
    runs.push({
      suiteId: combo.suiteId,
      suiteLabel: combo.suiteLabel,
      answererModel: combo.answererModel,
      judge: combo.judge,
      generatedAt: combo.generatedAt,
      generatedAtMs: combo.generatedAtMs,
      trials: combo.trials,
      topK: combo.topK,
      questionCount,
      systems,
    });
  }

  // Sort runs: suiteId asc, then answererModel asc, then judge in
  // JUDGE_META order (original gemini board first).
  const judgeOrder = Object.keys(JUDGE_META);
  const judgeRank = (j) => {
    const i = judgeOrder.indexOf(j);
    return i === -1 ? judgeOrder.length : i;
  };
  runs.sort((a, b) => {
    if (a.suiteId !== b.suiteId) return a.suiteId < b.suiteId ? -1 : 1;
    const am = String(a.answererModel ?? "");
    const bm = String(b.answererModel ?? "");
    if (am !== bm) return am < bm ? -1 : 1;
    return judgeRank(a.judge) - judgeRank(b.judge);
  });

  // Distinct benchmarks (suites), sorted by label.
  const benchmarkMap = new Map();
  for (const r of runs) {
    if (!benchmarkMap.has(r.suiteId)) {
      benchmarkMap.set(r.suiteId, {
        suiteId: r.suiteId,
        suiteLabel: r.suiteLabel,
      });
    }
  }
  const benchmarks = [...benchmarkMap.values()].sort((a, b) =>
    a.suiteLabel < b.suiteLabel ? -1 : a.suiteLabel > b.suiteLabel ? 1 : 0
  );

  // Distinct answerer models, sorted.
  const modelSet = new Set();
  for (const r of runs) if (r.answererModel) modelSet.add(r.answererModel);
  const models = [...modelSet].sort();

  // Distinct judges present in runs, in JUDGE_META display order.
  const judgeSet = new Set(runs.map((r) => r.judge));
  const judges = [...judgeSet]
    .sort((a, b) => judgeRank(a) - judgeRank(b))
    .map((id) => ({
      judgeId: id,
      judgeLabel: (JUDGE_META[id] && JUDGE_META[id].label) || id,
      note: (JUDGE_META[id] && JUDGE_META[id].note) || null,
    }));

  const payload = {
    builtAt: new Date().toISOString(),
    benchmarks,
    models,
    judges,
    runs,
  };

  if (!existsSync(OUT_DIR)) mkdirSync(OUT_DIR, { recursive: true });
  writeFileSync(OUT_FILE, JSON.stringify(payload, null, 2));

  console.log(
    `[build-data] wrote ${OUT_FILE} — ${runs.length} run(s), ` +
      `${benchmarks.length} benchmark(s), ${models.length} model(s), ` +
      `${judges.length} judge(s)`
  );
}

main();
