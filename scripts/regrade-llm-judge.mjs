#!/usr/bin/env node
/* Re-grade a benchmark result JSON with an LLM judge instead of the substring
 * matcher in scoring.ts. The substring grader marks correct answers wrong when
 * they paraphrase the gold ("three and a half weeks" vs "3.5 weeks", "GPS system
 * issue" vs "GPS system not functioning correctly") — this is the standard
 * LongMemEval failure mode and the original benchmark uses an LLM judge for
 * exactly this reason. Re-grades EVERY answered question (so it can lower a
 * spurious substring match as well as rescue a paraphrase), recomputes
 * per-trial / per-category / overall accuracy, and prints old vs new.
 *
 * Judge: gemini-2.5-flash (fast, off the z.ai answerer path). Set GEMINI_API_KEY.
 *
 *   node scripts/regrade-llm-judge.mjs results/<file>.json
 */
import { readFileSync, writeFileSync } from "node:fs";

const KEY = process.env.GEMINI_API_KEY;
if (!KEY) throw new Error("GEMINI_API_KEY required");
const MODEL = process.env.JUDGE_MODEL || "gemini-2.5-flash";
const CONCURRENCY = Number(process.env.JUDGE_CONCURRENCY || 6);

async function judge(question, answer, expected) {
  if (answer == null || String(answer).trim() === "") return 0;
  const prompt =
    `You are grading a long-term-memory QA benchmark. Decide if the MODEL ANSWER is ` +
    `correct, i.e. semantically equivalent to ANY of the gold answers (same fact/value; ` +
    `paraphrase, formatting, number-word vs digit, extra/missing filler words are all fine; ` +
    `"unknown"/refusal is WRONG). Reply with strict JSON {"correct": true|false} only.\n\n` +
    `QUESTION: ${question}\nMODEL ANSWER: ${answer}\nGOLD ANSWERS: ${JSON.stringify(expected)}`;
  const url = `https://generativelanguage.googleapis.com/v1beta/models/${MODEL}:generateContent?key=${KEY}`;
  for (let attempt = 0; attempt < 4; attempt++) {
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          contents: [{ parts: [{ text: prompt }] }],
          generationConfig: {
            temperature: 0,
            responseMimeType: "application/json",
          },
        }),
      });
      if (!res.ok) {
        if ([429, 500, 503].includes(res.status)) {
          await sleep(1500 * (attempt + 1));
          continue;
        }
        throw new Error(`judge ${res.status}`);
      }
      const doc = await res.json();
      const txt = doc?.candidates?.[0]?.content?.parts?.[0]?.text ?? "{}";
      return JSON.parse(txt).correct === true ? 1 : 0;
    } catch (e) {
      if (attempt === 3) throw e;
      await sleep(1500 * (attempt + 1));
    }
  }
  return 0;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

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

function pct(xs) {
  const v = xs.filter((x) => typeof x === "number");
  return v.length ? (v.reduce((a, b) => a + b, 0) / v.length) * 100 : null;
}

const file = process.argv[2];
const data = JSON.parse(readFileSync(file, "utf8"));

for (const sys of data.systems) {
  // gather every answered question across trials
  const tasks = [];
  for (const t of sys.trials) for (const q of t.questions) tasks.push(q);
  const verdicts = await mapLimit(tasks, CONCURRENCY, (q) =>
    judge(q.prompt, q.answer, q.expectedAnswers)
  );
  let k = 0;
  const oldByTrial = [],
    newByTrial = [];
  const catOld = {},
    catNew = {};
  for (const t of sys.trials) {
    const oldS = [],
      newS = [];
    for (const q of t.questions) {
      const nv = verdicts[k++];
      const ov = q.score.answerCorrect;
      q.score.answerCorrectSubstring = ov; // keep the original for the record
      q.score.answerCorrect = nv; // PROMOTE the LLM judge to the canonical grade
      q.score.answerCorrectLLM = nv;
      oldS.push(ov);
      newS.push(nv);
      (catOld[q.category] ??= []).push(ov);
      (catNew[q.category] ??= []).push(nv);
    }
    // Recompute this trial's answerAccuracy from the judge so build-data /
    // aggregates read the proper number.
    t.summary.answerAccuracy = newS.reduce((a, b) => a + b, 0) / newS.length;
    oldByTrial.push(pct(oldS));
    newByTrial.push(pct(newS));
  }
  // Recompute the system summary (cross-trial mean) + per-category from the judge.
  const allNew = sys.trials.flatMap((t) =>
    t.questions.map((q) => q.score.answerCorrect)
  );
  sys.summary.answerAccuracy =
    allNew.reduce((a, b) => a + b, 0) / allNew.length;
  if (Array.isArray(sys.byCategory)) {
    for (const c of sys.byCategory) {
      const vals = catNew[c.category];
      if (vals && vals.length)
        c.answerAccuracy = vals.reduce((a, b) => a + b, 0) / vals.length;
    }
  }
  const mean = (xs) => xs.reduce((a, b) => a + b, 0) / xs.length;
  console.log(`\n## ${sys.systemLabel} (${sys.systemId})`);
  console.log(
    `  overall  substring: ${mean(oldByTrial).toFixed(1)}%  ->  LLM-judge: ${mean(newByTrial).toFixed(1)}%`
  );
  console.log(
    `  per-trial substring: [${oldByTrial.map((x) => x.toFixed(1)).join(", ")}]`
  );
  console.log(
    `  per-trial LLM-judge: [${newByTrial.map((x) => x.toFixed(1)).join(", ")}]`
  );
  console.log(
    `  ${"category".padEnd(28)} ${"substr".padStart(7)} ${"judge".padStart(7)} ${"delta".padStart(7)}`
  );
  for (const c of Object.keys(catOld).sort()) {
    const o = pct(catOld[c]),
      n = pct(catNew[c]);
    console.log(
      `  ${c.padEnd(28)} ${o.toFixed(0).padStart(6)}% ${n.toFixed(0).padStart(6)}% ${(n - o >= 0 ? "+" : "") + (n - o).toFixed(0).padStart(6)}`
    );
  }
}
const out = file.replace(/\.json$/, ".regraded.json");
writeFileSync(out, JSON.stringify(data, null, 2));
console.log(`\nwrote ${out} (answerCorrectLLM annotated)`);
