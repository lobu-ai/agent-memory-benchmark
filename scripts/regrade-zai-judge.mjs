#!/usr/bin/env node
/* Alternate-judge regrade using z.ai/GLM (OpenAI-compatible) because the gemini
 * judge hit its monthly spend cap. Judges the SAME way on every file passed, so
 * the cross-file DELTA is fair even though the absolute number isn't the
 * gemini-judged headline. Usage:
 *   node scripts/regrade-zai-judge.mjs results/A.json results/B.json
 *
 * Besides the stdout report, persists the verdicts per input file to
 * results/judges/<basename>.judge-<MODEL>.json (a subdirectory because
 * .gitignore excludes results/*.json — verdict files must be committable).
 */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { basename, dirname, join } from "node:path";

const KEY = process.env.Z_AI_API_KEY;
if (!KEY) throw new Error("Z_AI_API_KEY required");
const MODEL = process.env.JUDGE_MODEL || "glm-4.6";
const URL =
  process.env.JUDGE_URL ||
  "https://api.z.ai/api/coding/paas/v4/chat/completions";
const CONCURRENCY = Number(process.env.JUDGE_CONCURRENCY || 4);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function parseVerdict(txt) {
  let t = String(txt || "").trim();
  if (t.startsWith("```")) t = t.replace(/^```[a-z]*\n?|\n?```$/g, "").trim();
  const m = t.match(/\{[^}]*\}/);
  if (m) {
    try {
      return JSON.parse(m[0]).correct === true ? 1 : 0;
    } catch {}
  }
  return /"?correct"?\s*[:=]\s*true/i.test(t) ? 1 : 0;
}

async function judge(question, answer, expected) {
  if (answer == null || String(answer).trim() === "") return 0;
  const prompt =
    `You are grading a long-term-memory QA benchmark. Decide if the MODEL ANSWER is ` +
    `correct, i.e. semantically equivalent to ANY of the gold answers (same fact/value; ` +
    `paraphrase, formatting, number-word vs digit, extra/missing filler words are all fine; ` +
    `"unknown"/refusal is WRONG). Reply with strict JSON {"correct": true|false} only.\n\n` +
    `QUESTION: ${question}\nMODEL ANSWER: ${answer}\nGOLD ANSWERS: ${JSON.stringify(expected)}`;
  for (let attempt = 0; attempt < 5; attempt++) {
    try {
      const res = await fetch(URL, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          authorization: `Bearer ${KEY}`,
        },
        body: JSON.stringify({
          model: MODEL,
          temperature: 0,
          // Same convention as the shared answerer's z.ai calls: reasoning
          // models burn seconds "thinking" about a binary verdict.
          thinking: { type: "disabled" },
          messages: [{ role: "user", content: prompt }],
        }),
      });
      if (!res.ok) {
        await sleep(1500 * (attempt + 1));
        continue;
      }
      const doc = await res.json();
      return parseVerdict(doc?.choices?.[0]?.message?.content);
    } catch {
      await sleep(1500 * (attempt + 1));
    }
  }
  return null; // unjudgeable (don't silently count as wrong)
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
    const verdicts = await mapLimit(tasks, CONCURRENCY, (q) =>
      judge(q.prompt, q.answer, q.expectedAnswers)
    );
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
    console.log(`  OVERALL z.ai-judge: ${pct(allNew)?.toFixed(1)}%`);
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
