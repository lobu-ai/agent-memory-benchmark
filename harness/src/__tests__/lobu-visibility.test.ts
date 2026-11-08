import { test } from 'bun:test';
import { resolve } from 'node:path';

test('Lobu visibility errors remain distinct from valid empty ingestion', async () => {
  const child = Bun.spawn(['python3', resolve('adapters/test_lobu_visibility.py')], {
    stdout: 'pipe', stderr: 'pipe',
  });
  const [status, stdout, stderr] = await Promise.all([
    child.exited, new Response(child.stdout).text(), new Response(child.stderr).text(),
  ]);
  if (status !== 0) throw new Error(stdout + stderr);
});
