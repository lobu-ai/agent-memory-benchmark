import type {
  AggregateSummary,
  AggregateSystemResult,
  CategorySummary,
  QuestionResult,
  QuestionScore,
  TrialResult,
  TrialSummary,
} from './types';

function average(values: Array<number | null | undefined>): number | null {
  const filtered = values.filter((value): value is number => typeof value === 'number');
  if (filtered.length === 0) return null;
  return filtered.reduce((sum, value) => sum + value, 0) / filtered.length;
}

function percentile(values: number[], p: number): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const index = Math.min(sorted.length - 1, Math.max(0, Math.ceil((p / 100) * sorted.length) - 1));
  return sorted[index] ?? 0;
}

const NUMBER_WORDS: Record<string, string> = {
  zero: '0',
  one: '1',
  two: '2',
  three: '3',
  four: '4',
  five: '5',
  six: '6',
  seven: '7',
  eight: '8',
  nine: '9',
  ten: '10',
  eleven: '11',
  twelve: '12',
  thirteen: '13',
  fourteen: '14',
  fifteen: '15',
  sixteen: '16',
  seventeen: '17',
  eighteen: '18',
  nineteen: '19',
  twenty: '20',
  thirty: '30',
};

function normalizeText(value: string): string {
  return value
    .toLowerCase()
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(
      /\b(zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty)\b/g,
      (match) => NUMBER_WORDS[match] ?? match
    )
    .replace(/[$€,]/g, '')
    .replace(/[^a-z0-9\s.-]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function normalizeLooseText(value: string): string {
  return normalizeText(value)
    .replace(/\b(the|a|an|my|your|their|his|her|our|narrator|user)\b/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function expandExpectedAnswers(values: string[]): string[] {
  return dedupe(
    values
      .flatMap((value) => value.split(/\.\s+/))
      .map((value) => value.trim())
      .map((value) => value.replace(/\s*\([^)]*\)\s*is also acceptable\.?$/i, ''))
      .map((value) => value.replace(/\s*is also acceptable\.?$/i, ''))
      .map((value) => value.trim())
      .filter(Boolean)
  );
}

function dedupe(values: string[]): string[] {
  return [...new Set(values)];
}

export function scoreQuestion(args: {
  expectedAnswers: string[];
  expectedSourceStepIds: string[];
  answer: string | null;
  citedIds: string[];
  retrievedIds: string[];
  answerMatch?: "substring-proxy" | "normalized-exact";
}): QuestionScore {
  const expectedAnswers = expandExpectedAnswers(args.expectedAnswers);
  const normalizedExpectedAnswers = dedupe(expectedAnswers.map(normalizeText).filter(Boolean));
  const looseExpectedAnswers = dedupe(expectedAnswers.map(normalizeLooseText).filter(Boolean));
  const expectedSources = new Set(args.expectedSourceStepIds);
  const citedIds = dedupe(args.citedIds);
  const retrievedIds = dedupe(args.retrievedIds);
  const normalizedAnswer = args.answer !== null ? normalizeText(args.answer) : null;
  const looseAnswer = args.answer !== null ? normalizeLooseText(args.answer) : null;

  function matches(answer: string, expected: string): boolean {
    return answer.length > 0 && (answer === expected || answer.includes(expected) || expected.includes(answer));
  }
  let answerCorrect: number | null = null;
  if (normalizedAnswer !== null) {
    const correct = args.answerMatch === 'normalized-exact'
      ? normalizedExpectedAnswers.some(expected => normalizedAnswer.replace(/\.+$/, '') === expected.replace(/\.+$/, ''))
      : normalizedExpectedAnswers.some(expected => matches(normalizedAnswer, expected)) ||
        (looseAnswer !== null && looseExpectedAnswers.some(expected => matches(looseAnswer, expected)));
    answerCorrect = correct ? 1 : 0;
  }

  const retrievedHits = retrievedIds.filter((id) => expectedSources.has(id)).length;
  const citedHits = citedIds.filter((id) => expectedSources.has(id)).length;

  return {
    answerCorrect,
    retrievalRecall: expectedSources.size > 0 ? retrievedHits / expectedSources.size : null,
    citationRecall: normalizedAnswer === null || expectedSources.size === 0
      ? null : citedHits / expectedSources.size,
    citationPrecision: normalizedAnswer === null || expectedSources.size === 0
      ? null : citedIds.length > 0 ? citedHits / citedIds.length : 0,
  };
}

export function summarizeQuestions(questions: QuestionResult[]): TrialSummary {
  const latencies = questions.map((question) => question.latencyMs);
  const promptTokens = questions.map((question) => question.answererPromptTokens);
  const completionTokens = questions.map((question) => question.answererCompletionTokens);
  const totalPromptTokens = promptTokens.reduce((sum, value) => sum + value, 0);
  const totalCompletionTokens = completionTokens.reduce((sum, value) => sum + value, 0);
  return {
    questionCount: questions.length,
    answerAccuracy: average(questions.map((question) => question.score.answerCorrect)),
    retrievalRecall: average(questions.map((question) => question.score.retrievalRecall)),
    citationRecall: average(questions.map((question) => question.score.citationRecall)),
    citationPrecision: average(questions.map((question) => question.score.citationPrecision)),
    averageLatencyMs: average(latencies) ?? 0,
    p95LatencyMs: percentile(latencies, 95),
    averageContextTokensApprox:
      average(questions.map((question) => question.contextTokensApprox)) ?? 0,
    averageAnswererPromptTokens: average(promptTokens) ?? 0,
    averageAnswererCompletionTokens: average(completionTokens) ?? 0,
    totalAnswererPromptTokens: totalPromptTokens,
    totalAnswererCompletionTokens: totalCompletionTokens,
  };
}

function sampleStandardDeviation(values: number[]): number {
  if (values.length < 2) return 0;
  const mean = values.reduce((sum, value) => sum + value, 0) / values.length;
  const variance =
    values.reduce((sum, value) => sum + (value - mean) ** 2, 0) / (values.length - 1);
  return Math.sqrt(variance);
}

function confidence95(values: Array<number | null | undefined>): number | undefined {
  const filtered = values.filter((value): value is number => typeof value === 'number');
  if (filtered.length < 2) return undefined;
  const sd = sampleStandardDeviation(filtered);
  return 1.96 * (sd / Math.sqrt(filtered.length));
}

function computeOverallScore(summary: TrialSummary | AggregateSummary): number {
  const parts: Array<{ value: number | null; weight: number }> = [
    { value: summary.answerAccuracy, weight: 0.5 },
    { value: summary.retrievalRecall, weight: 0.25 },
    { value: summary.citationRecall, weight: 0.15 },
    { value: summary.citationPrecision, weight: 0.05 },
    { value: 'latencyScore' in summary ? summary.latencyScore : null, weight: 0.05 },
  ];

  const available = parts.filter((part) => typeof part.value === 'number') as Array<{
    value: number;
    weight: number;
  }>;
  const totalWeight = available.reduce((sum, part) => sum + part.weight, 0);
  if (totalWeight === 0) return 0;
  return available.reduce((sum, part) => sum + part.value * (part.weight / totalWeight), 0);
}

export function summarizeTrial(trial: TrialResult): TrialSummary {
  return summarizeQuestions(trial.questions);
}

function summarizeByCategory(questions: QuestionResult[]): CategorySummary[] {
  const grouped = new Map<string, QuestionResult[]>();
  for (const question of questions) {
    const existing = grouped.get(question.category) ?? [];
    existing.push(question);
    grouped.set(question.category, existing);
  }

  return [...grouped.entries()]
    .map(([category, categoryQuestions]) => ({
      category,
      ...summarizeQuestions(categoryQuestions),
    }))
    .sort((a, b) => a.category.localeCompare(b.category));
}

function compareAggregateSystems(a: AggregateSystemResult, b: AggregateSystemResult): number {
  const primaryA = a.summary.answerAccuracy ?? a.summary.retrievalRecall ?? -1;
  const primaryB = b.summary.answerAccuracy ?? b.summary.retrievalRecall ?? -1;

  return (
    primaryB - primaryA ||
    (b.summary.retrievalRecall ?? -1) - (a.summary.retrievalRecall ?? -1) ||
    (b.summary.citationRecall ?? -1) - (a.summary.citationRecall ?? -1) ||
    a.summary.averageLatencyMs - b.summary.averageLatencyMs
  );
}

export function aggregateSystemResults(
  systems: Array<{
    systemId: string;
    systemLabel: string;
    trials: TrialResult[];
  }>
): AggregateSystemResult[] {
  const baseSummaries = systems.map((system) => ({
    systemId: system.systemId,
    systemLabel: system.systemLabel,
    trials: system.trials,
    baseSummary: summarizeQuestions(system.trials.flatMap((trial) => trial.questions)),
  }));

  const fastestLatency = Math.min(
    ...baseSummaries.map((system) => Math.max(system.baseSummary.averageLatencyMs, 1))
  );

  return baseSummaries
    .map((system) => {
      const latencyScore = fastestLatency / Math.max(system.baseSummary.averageLatencyMs, 1);
      const summary: AggregateSummary = {
        ...system.baseSummary,
        latencyScore,
        overallScore: 0,
        confidence95: {
          answerAccuracy: confidence95(system.trials.map((trial) => trial.summary.answerAccuracy)),
          retrievalRecall: confidence95(
            system.trials.map((trial) => trial.summary.retrievalRecall)
          ),
          citationRecall: confidence95(system.trials.map((trial) => trial.summary.citationRecall)),
          overallScore: confidence95(
            system.trials.map((trial) =>
              computeOverallScore({
                ...trial.summary,
                latencyScore,
                overallScore: 0,
              })
            )
          ),
        },
      };
      summary.overallScore = computeOverallScore(summary);
      return {
        systemId: system.systemId,
        systemLabel: system.systemLabel,
        trials: system.trials,
        summary,
        byCategory: summarizeByCategory(system.trials.flatMap((trial) => trial.questions)),
      } satisfies AggregateSystemResult;
    })
    .sort(compareAggregateSystems);
}
