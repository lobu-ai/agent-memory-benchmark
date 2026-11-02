import { defineConfig, defineEntityType, defineRelationshipType } from "@lobu/cli/config";

// A synthetic fixture owned by this benchmark, never a production workspace.
const company = defineEntityType({
  key: "bench-company", name: "Benchmark company",
  required: ["registration", "jurisdiction"],
  properties: {
    registration: { type: "string", minLength: 1 },
    jurisdiction: { type: "string", enum: ["GB", "US"] },
  },
});
const invoice = defineEntityType({
  key: "bench-invoice", name: "Benchmark invoice",
  required: ["reference", "amount", "status"],
  properties: {
    reference: { type: "string", minLength: 1 },
    amount: { type: "number", minimum: 0 },
    status: { type: "string", enum: ["pending", "held", "released"] },
  },
  eventKinds: {
    bench_assessment: {
      description: "Benchmark assessment",
      metadataSchema: {
        type: "object", required: ["decision"],
        properties: { decision: { type: "string", enum: ["hold", "release"] } },
      },
    },
  },
});
const risk = defineEntityType({
  key: "bench-risk", name: "Benchmark risk",
  required: ["reference", "severity", "state"],
  properties: {
    reference: { type: "string", minLength: 1 },
    severity: { type: "string", enum: ["low", "high"] },
    state: { type: "string", enum: ["open", "resolved"] },
  },
});
const billedTo = defineRelationshipType({
  key: "bench_billed_to", name: "Billed to",
  rules: [{ source: invoice, target: company }],
});
const controlledBy = defineRelationshipType({
  key: "bench_controlled_by", name: "Controlled by",
  rules: [{ source: company, target: company }],
});
const hasRisk = defineRelationshipType({
  key: "bench_has_risk", name: "Has risk",
  rules: [{ source: company, target: risk }],
});
export default defineConfig({
  org: "ontology-benchmark", orgName: "Synthetic ontology benchmark",
  agents: [],
  entities: [company, invoice, risk],
  relationships: [billedTo, controlledBy, hasRisk],
});
