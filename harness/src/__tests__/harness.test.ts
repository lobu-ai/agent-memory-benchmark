import { afterEach, expect, test } from 'bun:test';
import { mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { runBenchmark } from '../runner';
import { scoreQuestion } from '../scoring';
import type { BenchmarkRunConfig } from '../types';

const dirs: string[] = [];
afterEach(() => { for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true }); });

function fixture(fail = false): BenchmarkRunConfig {
  const dir = mkdtempSync(join(tmpdir(), 'memory-bench-'));
  dirs.push(dir);
  const suitePath = join(dir, 'suite.json');
  writeFileSync(suitePath, JSON.stringify({ id: 'synthetic', version: '1', entityTypes: [{ slug: 'subject', name: 'Subject' }], scenarios: [{
    id: 'one', category: 'correction', entities: [{ ref: 'subject', entityType: 'subject', name: 'Synthetic subject' }], steps: [{ kind: 'memory', id: 'fact', content: 'The answer is blue.', entityRefs: ['subject'], semanticType: 'fact' }],
    questions: [{ id: 'q', prompt: 'What color?', expectedAnswers: ['blue'], expectedSourceStepIds: ['fact'] }],
  }] }));
  const adapter = join(dir, 'adapter.mjs');
  writeFileSync(adapter, `import readline from 'node:readline';
for await (const line of readline.createInterface({input:process.stdin})) {
 const {id,action,payload}=JSON.parse(line);
 const leaked=JSON.stringify(payload).includes('expectedAnswers') || JSON.stringify(payload).includes('expectedSourceStepIds');
 const failed=leaked || (process.env.FAIL==='1' && action==='retrieve');
 console.log(JSON.stringify({id,ok:!failed,error:leaked?'gold labels exposed':'synthetic outage',result:action==='retrieve'?{items:[{id:'fact',text:'The answer is blue.'}],latencyMs:1}:null}));
}`);
  return { suitePath, outputDir: join(dir, 'reports'), answerer: { type: 'none' }, systems: [
    { id: 'working', label: 'Working', type: 'command', argv: [process.execPath, adapter] },
    ...(fail ? [{ id: 'failed', label: 'Failed', type: 'command' as const, argv: [process.execPath, adapter], env: { FAIL: '1' } }] : []),
  ] };
}

test('gold labels never cross the real subprocess boundary', async () => {
  const report = await runBenchmark(fixture());
  expect(report.systems[0]?.summary.retrievalRecall).toBe(1);
});

test('CLI writes a partial report and fails visibly when one system is unavailable', async () => {
  const config = fixture(true);
  const path = join(dirs.at(-1)!, 'config.json');
  writeFileSync(path, JSON.stringify(config));
  const child = Bun.spawn([process.execPath, resolve('scripts/run.ts'), '--config', path], { stdout: 'pipe', stderr: 'pipe' });
  const [exitCode] = await Promise.all([child.exited, new Response(child.stdout).text(), new Response(child.stderr).text()]);
  expect(exitCode).toBe(1);
  const reportPath = readdirSync(config.outputDir!).find(name => name.endsWith('.json'));
  expect(reportPath).toBeDefined();
  const report = JSON.parse(readFileSync(join(config.outputDir!, reportPath!), 'utf8'));
  expect(report.systems.map((s: { systemId: string }) => s.systemId)).toEqual(['working']);
  expect(report.failures).toEqual([{ systemId: 'failed', systemLabel: 'Failed', error: "Command adapter 'failed' action 'retrieve' failed: synthetic outage" }]);
});

test('all-system failure still leaves a diagnostic report', async () => {
  const config = fixture(true);
  config.systems = config.systems.slice(1);
  const report = await runBenchmark(config);
  expect(report.systems).toEqual([]);
  expect(report.failures).toHaveLength(1);
});

test('missing gold is unscored and empty normalized answers cannot pass', () => {
  const base = { expectedAnswers: ['blue'], expectedSourceStepIds: ['fact'], answer: '!!!', citedIds: [], retrievedIds: [] };
  expect(scoreQuestion(base).answerCorrect).toBe(0);
  expect(scoreQuestion({ ...base, answer: 'the' }).answerCorrect).toBe(0);
  expect(scoreQuestion({ ...base, answer: '' }).answerCorrect).toBe(0);
  const noGold = scoreQuestion({ ...base, expectedSourceStepIds: [], answer: 'blue' });
  expect(noGold.retrievalRecall).toBeNull();
  expect(noGold.citationRecall).toBeNull();
  expect(noGold.citationPrecision).toBeNull();
});

test('exact pilot scoring distinguishes an affirmative recommendation from its negation', () => {
  const base = { expectedAnswers: ['eligible'], expectedSourceStepIds: [], citedIds: [], retrievedIds: [], answerMatch: 'normalized-exact' as const };
  expect(scoreQuestion({ ...base, answer: 'not eligible' }).answerCorrect).toBe(0);
  expect(scoreQuestion({ ...base, answer: 'Eligible.' }).answerCorrect).toBe(1);
});
