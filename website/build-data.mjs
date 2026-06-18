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
// Judge boards are verdict-file-driven: results/judges/*.judge-<model>.json
// verdict files (scripts/regrade-{zai,claude,gemini}-judge.mjs) override
// answerAccuracy on their source report; recall/latency/tokens come from the
// run itself. One board per distinct judge model.
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

// Judge metadata, in display order.
const JUDGE_META = {
  "claude-sonnet-4-6": {
    label: "claude-sonnet-4-6 (independent)",
    note: "cross-family judge — independent of the glm-5.1 answerer, so no self-grading. Same rubric as the other judges; runs via the Claude Code CLI (scripts/regrade-claude-judge.mjs).",
  },
  "gemini-2.5-flash": {
    label: "gemini-2.5-flash (independent)",
    note: "cross-family judge — independent of the glm-5.1 answerer. Same rubric as the other judges; runs via the Gemini CLI (scripts/regrade-gemini-judge.mjs).",
  },
  "glm-5.1": {
    label: "glm-5.1 (same family as answerer)",
    note: "judge is the same model family as the shared answerer (self-grading); applied identically to every system, so deltas are fair. Judge script committed (scripts/regrade-zai-judge.mjs).",
  },
};

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

/**
 * Coarse model family, used to flag same-family (self-)judging. A judge from
 * the same family as the answerer tends to grade its own outputs leniently, so
 * those cells are a leniency reference, not a headline number.
 */
function modelFamily(model) {
  const m = String(model || "").toLowerCase();
  if (!m) return null;
  if (m.includes("grok")) return "grok";
  if (m.includes("glm")) return "glm";
  if (m.includes("claude")) return "claude";
  if (m.includes("gemini")) return "gemini";
  if (m.includes("gpt") || m.includes("o1") || m.includes("o3"))
    return "openai";
  return m.split(/[-\s]/)[0] || null;
}

/** Human label for a suite id. Falls back to a title-cased id. */
function suiteLabel(suiteId) {
  const known = {
    "longmemeval-mixed-30": "LongMemEval (all 6 categories, 30)",
    "longmemeval-oracle-10": "LongMemEval (temporal-only, 10)",
    "longmemeval-oracle": "LongMemEval (oracle)",
    "longmemeval-mixed-60": "LongMemEval (all 6 categories, 60)",
    "xafs-5": "xAFS (agentic filesystem, 5 docs / 115 files)",
    "xafs-7": "xAFS (agentic filesystem, 7 docs / 415 files)",
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

/**
 * Canonical systemId. Different configs name the same system differently
 * (the original board used "lobu-plain"/"sm-plain"; the answerer-matrix configs
 * used "lobu"/"supermemory"). Collapse aliases so the same system across answerer
 * runs merges into one row — without this the cross-answerer aggregate counts
 * "lobu" and "lobu-plain" as two separate systems (n=1 each).
 */
const SYSTEM_ALIASES = {
  lobu: "lobu-plain",
  "lobu-plain": "lobu-plain",
  supermemory: "sm-plain",
  sm: "sm-plain",
  "sm-plain": "sm-plain",
};
function canonicalSystemId(id) {
  return SYSTEM_ALIASES[id] || id;
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
    // Ingest (write-time) cost — its own benchmark dimension. Average the
    // per-trial ingest wall-clock; null for older reports without it.
    const ingestMsVals = trials
      .map((t) => num(t && t.ingestMs))
      .filter((x) => x != null);
    const ingestSeconds = ingestMsVals.length
      ? ingestMsVals.reduce((a, b) => a + b, 0) / ingestMsVals.length / 1000
      : null;
    return {
      systemId: canonicalSystemId(sys.systemId || "(unknown)"),
      systemLabel: sys.systemLabel || sys.systemId || "(unknown)",
      ingestSeconds,
      version:
        typeof sys.version === "string" && sys.version ? sys.version : null,
      mode: typeof sys.mode === "string" && sys.mode ? sys.mode : null,
      summary: { ...pickSummary(sys.summary), ingestSeconds },
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

  // Real ingest (write-time) cost is system-intrinsic but only the FIRST
  // (cold-cache) run measures it; later cache-replay runs record ~0 (ingest
  // skipped). The latest report per system is often a replay, so its ingest is
  // misleadingly low. Recover the true cost as the MAX ingestSeconds seen for
  // each (suite, system) across every report, and apply it during materialize.
  const maxIngest = new Map(); // "suiteId systemId" -> max ingestSeconds
  for (const r of reports) {
    for (const sys of r.systems) {
      if (sys.ingestSeconds == null) continue;
      const k = `${r.suiteId} ${sys.systemId}`;
      const prev = maxIngest.get(k);
      if (prev == null || sys.ingestSeconds > prev)
        maxIngest.set(k, sys.ingestSeconds);
    }
  }

  // Each board entry is a report tagged with the judge that graded it.
  const boardReports = [];

  // Judge boards: verdict files override answerAccuracy on their
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
        canonicalSystemId(s.systemId),
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
    const systems = [...combo.systemsLatest.values()]
      .map((e) => e.system)
      .map((sys) => {
        // Override cache-replay ingest with the true (max) measured ingest.
        const trueIngest = maxIngest.get(`${combo.suiteId} ${sys.systemId}`);
        if (trueIngest == null || trueIngest === sys.ingestSeconds) return sys;
        return {
          ...sys,
          ingestSeconds: trueIngest,
          summary: { ...sys.summary, ingestSeconds: trueIngest },
        };
      });
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
      selfJudged:
        modelFamily(combo.answererModel) != null &&
        modelFamily(combo.answererModel) === modelFamily(combo.judge),
      generatedAt: combo.generatedAt,
      generatedAtMs: combo.generatedAtMs,
      trials: combo.trials,
      topK: combo.topK,
      questionCount,
      systems,
    });
  }

  // Cross-answerer aggregate: for each (suiteId, judge) that has >=2 distinct
  // answerer models, synthesize an "all answerers" run where each system's
  // answerAccuracy is the MEAN across answerers and accuracySpread carries the
  // across-answerer min/max/stdev. This makes answerer-robustness visible at a
  // glance (the existing spread renderer shows the range) instead of forcing the
  // reader to flip the answerer selector. Recall/latency/tokens are answerer-
  // independent, so they're taken from one constituent run.
  const AGG_MODEL = "all answerers (mean±range)";
  {
    const bySuiteJudge = new Map();
    for (const r of runs) {
      const k = `${r.suiteId} ${r.judge}`;
      if (!bySuiteJudge.has(k)) bySuiteJudge.set(k, []);
      bySuiteJudge.get(k).push(r);
    }
    for (const group of bySuiteJudge.values()) {
      const models = new Set(group.map((r) => r.answererModel).filter(Boolean));
      if (models.size < 2) continue;
      // systemId -> { accs: number[], any: systemObj }
      const bySystem = new Map();
      for (const r of group) {
        for (const s of r.systems) {
          const acc = s.summary.answerAccuracy;
          if (acc == null) continue;
          if (!bySystem.has(s.systemId))
            bySystem.set(s.systemId, { accs: [], any: s });
          bySystem.get(s.systemId).accs.push(acc);
        }
      }
      const aggSystems = [];
      for (const { accs, any } of bySystem.values()) {
        const sp = spread(accs);
        if (!sp) continue;
        aggSystems.push({
          ...any,
          summary: { ...any.summary, answerAccuracy: sp.mean },
          trialAccuracies: accs,
          accuracySpread: sp,
        });
      }
      if (!aggSystems.length) continue;
      const head = group[0];
      runs.push({
        suiteId: head.suiteId,
        suiteLabel: head.suiteLabel,
        answererModel: AGG_MODEL,
        judge: head.judge,
        selfJudged: false,
        generatedAt: head.generatedAt,
        generatedAtMs: head.generatedAtMs,
        trials: head.trials,
        topK: head.topK,
        questionCount: head.questionCount,
        systems: aggSystems,
      });
    }
  }

  // Sort runs: suiteId asc, then answererModel asc, then judge in
  // JUDGE_META order (independent judges first).
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
