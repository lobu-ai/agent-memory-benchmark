#!/usr/bin/env node
/* Claude-judge regrade via the Claude Code CLI (`claude -p`) — runs on a
 * Claude subscription, no Anthropic API key required. Cross-family judge:
 * the shared answerer is glm-5.1, so a Claude judge removes the
 * self-grading caveat the glm-5.1 board carries.
 *
 * Grading rubric is byte-identical per item to scripts/regrade-zai-judge.mjs;
 * items are batched per CLI call to amortize process startup. Judges the SAME
 * way on every file passed, so cross-file deltas are fair. Usage:
 *   node scripts/regrade-claude-judge.mjs results/A.json results/B.json
 *
 * Persists verdicts to results/judges/<basename>.judge-<MODEL>.json (same
 * format as the other judge scripts; build-data.mjs discovers it as a board).
 */
import { execFile } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { basename, dirname, join } from "node:path";

const MODEL = process.env.JUDGE_MODEL || "claude-sonnet-4-6";
const BATCH = Number(process.env.JUDGE_BATCH || 15);
const CONCURRENCY = Number(process.env.JUDGE_CONCURRENCY || 3);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function itemRubric(i, question, answer, expected) {
  return (
    `ITEM ${i}\n` +
    `QUESTION: ${question}\nMODEL ANSWER: ${answer}\nGOLD ANSWERS: ${JSON.stringify(expected)}`
  );
}

function claudeCall(prompt) {
  return new Promise((resolve, reject) => {
    execFile(
      "claude",
      ["-p", prompt, "--model", MODEL, "--output-format", "json"],
      { maxBuffer: 16 * 1024 * 1024, timeout: 300_000 },
      (err, stdout) => {
        if (err) return reject(err);
        try {
          const events = JSON.parse(stdout);
          const res = (Array.isArray(events) ? events : [events]).find(
            (e) => e && e.type === "result"
          );
          if (!res || res.is_error) return reject(new Error("claude error"));
          resolve(String(res.result ?? ""));
        } catch (e) {
          reject(e);
        }
      }
    );
  });
}

function parseBatchVerdicts(txt, n) {
  let t = String(txt || "").trim();
  if (t.startsWith("```")) t = t.replace(/^```[a-z]*\n?|\n?```$/g, "").trim();
  const m = t.match(/\[[\s\S]*\]/);
  const out = new Array(n).fill(null);
  if (!m) return out;
  try {
    for (const v of JSON.parse(m[0])) {
      const i = Number(v?.i);
      if (Number.isInteger(i) && i >= 0 && i < n)
        out[i] = v?.correct === true ? 1 : 0;
    }
  } catch {}
  return out;
}

async function judgeBatch(items) {
  const header =
    `You are grading a long-term-memory QA benchmark. For EACH item below, decide if ` +
    `the MODEL ANSWER is correct, i.e. semantically equivalent to ANY of the gold answers ` +
    `(same fact/value; paraphrase, formatting, number-word vs digit, extra/missing filler ` +
    `words are all fine; "unknown"/refusal is WRONG). Judge each item independently.\n` +
    `Reply with strict JSON only — an array with one entry per item: ` +
    `[{"i":<item number>,"correct":true|false}, ...]. No other text.\n\n`;
  const prompt =
    header +
    items
      .map((q, i) =>
        itemRubric(i, q.prompt, q.answer ?? "", q.expectedAnswers)
      )
      .join("\n\n");
  for (let attempt = 0; attempt < 4; attempt++) {
    try {
      const verdicts = parseBatchVerdicts(await claudeCall(prompt), items.length);
      if (verdicts.some((v) => v != null)) return verdicts;
    } catch {}
    await sleep(3000 * (attempt + 1));
  }
  return new Array(items.length).fill(null); // unjudgeable, not wrong
}

async function mapLimit(items, limit, fn) {
  const out = new Array(items.length);
  let i = 0;
  await Promise.all(
    Array.from({ length: limit }, async () => {
      while (i < items.length) {
        const idx = i++;
        out[idx] = await fn(items[idx], idx);
      }
    })
  );
  return out;
}
const pct = (xs) => {
  const v = xs.filter((x) => typeof x === "number");
  return v.length ? (v.reduce((a, b) => a + b, 0) / v.length) * 100 : null;
};

for (const file of process.argv.slice(2)) {
  const data = JSON.parse(readFileSync(file, "utf8"));
  const judgedSystems = [];
  for (const sys of data.systems) {
    const tasks = [];
    for (const t of sys.trials) for (const q of t.questions) tasks.push(q);
    // Empty answers are wrong by definition — don't spend a judge call.
    const batches = [];
    for (let b = 0; b < tasks.length; b += BATCH)
      batches.push(tasks.slice(b, b + BATCH));
    const batchVerdicts = await mapLimit(batches, CONCURRENCY, (batch) =>
      judgeBatch(batch)
    );
    const verdicts = batchVerdicts.flat().map((v, i) => {
      const a = tasks[i].answer;
      return a == null || String(a).trim() === "" ? 0 : v;
    });
    let k = 0,
      nullN = 0;
    const allNew = [],
      cat = {},
      outTrials = [];
    for (const t of sys.trials) {
      const questions = [];
      for (const q of t.questions) {
        const nv = verdicts[k++];
        if (nv == null) nullN++;
        allNew.push(nv);
        (cat[q.category] ??= []).push(nv);
        questions.push({
          scenarioId: q.scenarioId,
          questionId: q.questionId,
          category: q.category,
          verdict: nv,
        });
      }
      outTrials.push({ questions });
    }
    const frac = (xs) => {
      const p = pct(xs);
      return p == null ? null : p / 100;
    };
    judgedSystems.push({
      systemId: sys.systemId,
      systemLabel: sys.systemLabel,
      trials: outTrials,
      summary: {
        answerAccuracy: frac(allNew),
        perCategory: Object.fromEntries(
          Object.keys(cat)
            .sort()
            .map((c) => [c, frac(cat[c])])
        ),
      },
    });
    console.log(`\n## ${sys.systemLabel}  (${file.split("/").pop()})`);
    console.log(`  trials=${sys.trials.length}  unjudgeable=${nullN}`);
    console.log(`  OVERALL claude-judge: ${pct(allNew)?.toFixed(1)}%`);
    for (const c of Object.keys(cat).sort())
      console.log(
        `    ${c.padEnd(28)} ${pct(cat[c])?.toFixed(0).padStart(4)}%`
      );
  }
  const outFile = join(
    dirname(file),
    "judges",
    `${basename(file, ".json")}.judge-${MODEL}.json`
  );
  mkdirSync(dirname(outFile), { recursive: true });
  writeFileSync(
    outFile,
    JSON.stringify(
      {
        judgeModel: MODEL,
        judgedAt: process.env.JUDGED_AT || new Date().toISOString(),
        systems: judgedSystems,
      },
      null,
      2
    ) + "\n"
  );
  console.error(`wrote ${outFile}`);
}
