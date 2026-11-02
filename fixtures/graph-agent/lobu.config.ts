import { defineConfig, defineEntityType, defineRelationshipType } from "@lobu/cli/config";

const node = defineEntityType({
  key: "bench-node", name: "Benchmark node",
  required: ["node_ref", "episode"],
  properties: {
    node_ref: { type: "string" }, episode: { type: "string" },
    risk: { type: "string", enum: ["open"] },
    risk_assertion_id: { type: "string" },
  },
});
const issuer = defineRelationshipType({
  key: "bench_issuer", name: "Benchmark issuer",
  rules: [{ source: node, target: node }],
});
const contains = defineRelationshipType({
  key: "bench_contains", name: "Benchmark directed containment",
  rules: [{ source: node, target: node }],
});
export default defineConfig({
  org: "graph-agent-benchmark", orgName: "Synthetic graph agent benchmark",
  agents: [], entities: [node], relationships: [issuer, contains],
});
