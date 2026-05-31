// Build a category-balanced LongMemEval suite so the leaderboard isn't skewed
// toward one question type. The prebuilt oracle-N suites take the first-N
// records, which are all `temporal-reasoning` (the hardest category) — that
// structurally punishes extraction-based memory systems. This stratifies across
// all LongMemEval categories instead.
//
//   bun run scripts/build-mixed-suite.ts [perCategory=5]
import { writeFileSync } from "node:fs";
import {
  downloadLongMemEvalDataset,
  convertLongMemEvalToBenchmarkSuite,
} from "../harness/src/datasets/longmemeval.ts";

const perCategory = Number(process.argv[2] ?? 5);
const all = await downloadLongMemEvalDataset("oracle");
// Drop abstention questions (`_abs`, no gold answer) and any record missing a
// string answer — they aren't cleanly scoreable on a single-answer board.
const records = all.filter(
  (r) =>
    !(r as { question_id?: string }).question_id?.endsWith("_abs") &&
    typeof (r as { answer?: unknown }).answer === "string"
);

const byType = new Map<string, typeof records>();
for (const r of records) {
  const t = (r as { question_type?: string }).question_type ?? "unknown";
  if (!byType.has(t)) byType.set(t, []);
  byType.get(t)!.push(r);
}

const picked: typeof records = [];
for (const [, rs] of [...byType.entries()].sort()) {
  picked.push(...rs.slice(0, perCategory));
}

const suite = convertLongMemEvalToBenchmarkSuite(picked, {
  variant: "oracle",
  suiteId: `longmemeval-mixed-${picked.length}`,
});
const out = `suites/longmemeval-mixed-${picked.length}.json`;
writeFileSync(out, JSON.stringify(suite));
console.log(
  `wrote ${out}: ${picked.length} scenarios across ${byType.size} categories ` +
    `(${[...byType.keys()].sort().join(", ")})`
);
