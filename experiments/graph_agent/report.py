"""Build a source-checked, standalone report from existing benchmark artifacts.

No model calls. Retrieval and native graph boards remain separate. Semantic
review labels are displayed as same-reader adjudication, not independent truth.
"""
import argparse
import hashlib
import json
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_retrieval(folder):
    comparison = json.loads((folder/"comparison.json").read_text())
    review = json.loads((folder/"semantic-review.json").read_text())
    suite = json.loads((folder/"suite.json").read_text())
    if digest(folder/"suite.json") != comparison["suiteSha256"]:
        raise ValueError("Retrieval suite hash mismatch")
    tasks = {(t["system"], t["question_id"]): t for t in review["tasks"]}
    scenarios = {s["id"]: s for s in suite["scenarios"]}
    questions = {}
    systems = []
    for system in comparison["systems"]:
        path = folder/system["sourceReport"]
        if path.name != system["sourceReport"] or digest(path) != system["sourceSha256"]:
            raise ValueError("Retrieval source hash mismatch")
        raw = json.loads(path.read_text())
        if raw["provenance"]["suiteSha256"] != comparison["suiteSha256"]:
            raise ValueError("Retrieval run used another suite")
        trials = raw["systems"][0]["trials"]
        if len(trials) != 1:
            raise ValueError("This report currently expects a single trial")
        rows = trials[0]["questions"]
        systems.append({**system, "strictCorrect": sum(q["score"]["answerCorrect"] for q in rows)})
        for row in rows:
            task = tasks[(system["system"], row["questionId"])]
            if task["source_sha256"] != system["sourceSha256"] or task["source_file"] != path.name:
                raise ValueError("Semantic review does not match source bytes")
            if task["answer"] != row["answer"] or task["expected"] != row["expectedAnswers"]:
                raise ValueError("Semantic review answer mismatch")
            names = {}
            for step in scenarios[row["scenarioId"]]["steps"]:
                if step["kind"] == "memory":
                    record = json.loads(step["content"])
                    names[step["id"]] = record.get("name", step["id"])
            item = questions.setdefault(row["questionId"], {"id": row["questionId"], "prompt": row["prompt"],
                "category": row["category"], "expected": row["expectedAnswers"], "systems": {}})
            item["systems"][system["system"]] = {"answer": row["answer"], "strict": bool(row["score"]["answerCorrect"]),
                "semantic": task["semantic_correct"], "review_reason": task["reason"],
                "retrieved": [{"id": key, "name": names.get(key, key)} for key in row["retrievedIds"]],
                "missing_reference_records": [{"id": key, "name": names.get(key, key)} for key in row["expectedSourceStepIds"] if key not in row["retrievedIds"]],
                "cited": row["citedIds"], "retrieval_ms": row["latencyMs"], "record_recall": row["score"]["retrievalRecall"]}
        if sum(tasks[(system["system"], q["questionId"])]["semantic_correct"] for q in rows) != system["semanticCorrect"]:
            raise ValueError("Semantic summary disagrees with question labels")
    rows = list(questions.values())
    paired = {"both_pass": 0, "lobu_only": 0, "supermemory_only": 0, "both_fail": 0}
    for q in rows:
        a, b = q["systems"]["lobu"]["semantic"], q["systems"]["supermemory"]["semantic"]
        paired["both_pass" if a and b else "lobu_only" if a else "supermemory_only" if b else "both_fail"] += 1
    return {"questions": rows, "systems": systems, "paired": paired, "model": review["model"],
            "semantic_judge": review["judge"], "suite_sha256": comparison["suiteSha256"]}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--retrieval-dir", type=Path, required=True)
    p.add_argument("--native-summary", type=Path, required=True)
    p.add_argument("--generated-suite", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    data = {"retrieval": load_retrieval(args.retrieval_dir), "native": json.loads(args.native_summary.read_text()),
            "generated": json.loads(args.generated_suite.read_text()), "generated_reference": json.loads(args.generated_suite.with_suffix(".reference.json").read_text()),
            "report_builder_sha256": digest(Path(__file__))}
    if digest(args.generated_suite) != data["generated_reference"]["fixture_sha256"]:
        raise ValueError("Generated reference fixture hash mismatch")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.with_suffix(".json").write_text(json.dumps(data, indent=2)+"\n")
    payload = json.dumps(data).replace("<", "\\u003c")
    args.output.write_text(HTML.replace("__PAYLOAD__", payload))
    print(json.dumps({"retrieval_questions": len(data["retrieval"]["questions"]),
                      "native_checkpoints_per_system": {key: value["denominator"] for key,value in data["native"]["systems"].items()},
                      "generated_not_run": data["generated"]["checkpoint_queries"]}))


HTML = r'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Benchmark evidence and next tests</title>
<style>*{box-sizing:border-box}body{margin:0;color:var(--foreground,#17212e);font:14px/1.55 var(--font-sans,system-ui)}h2{font-size:19px;margin:26px 0 8px}h3{font-size:15px;margin:16px 0 6px}p{margin:8px 0}small,.muted{color:var(--muted-foreground,#657184)}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.card{padding:16px;background:var(--surface-raised,#f3f6f9);border-radius:var(--radius,10px)}.big{font-size:25px;font-weight:650}.scroll{overflow:auto}table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:9px 8px;text-align:left;border-bottom:1px solid var(--border,#dce1e8);vertical-align:top}th{color:var(--muted-foreground,#657184);font-weight:600}select,input{padding:7px;color:inherit;background:var(--surface-raised,#f3f6f9);border:1px solid var(--border,#cdd6e0);border-radius:6px;max-width:100%}.controls{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}details{border-bottom:1px solid var(--border,#dce1e8);padding:10px 0}summary{cursor:pointer}.pill{font-size:11px;padding:2px 6px;border-radius:4px;background:var(--surface-recessed,#e8edf3);white-space:nowrap}.answer{overflow-wrap:anywhere}.pass{color:var(--success,#147354)}.fail{color:var(--destructive-text,#bd3b33)}.reason{padding:14px;border-left:3px solid var(--brand,#2770cf);background:var(--surface-raised,#f3f6f9)}code{font:12px var(--font-mono,monospace);overflow-wrap:anywhere}@media(max-width:500px){.grid{grid-template-columns:1fr}.big{font-size:22px}}</style>
<div class="grid"><div class="card"><small>RUN · RETRIEVAL</small><div class="big">24 questions</div><div>Lobu 20/24 · Supermemory 20/24</div><small>Semantic review; strict scores below</small></div><div class="card"><small>RUN · NATIVE GRAPH</small><div class="big">6/6 each</div><div>Lobu · PostgreSQL control</div><small>2 worlds · 1 matched pair · 3 checkpoints</small></div><div class="card"><small>GENERATED · NOT RUN</small><div class="big">24 more queries</div><div>8 worlds · 4 structural pairs</div><small>24/24 independent reference checks passed</small></div></div>
<h2>What the evidence says</h2><p class="reason">The current runs show a working retrieval integration and successful graph-state maintenance in a bounded smoke. They establish no accuracy advantage over the tested controls. The next useful comparison needs additional structures and a graph-product baseline.</p>
<p><strong>Muse is the executor</strong>: <code>opencode-go/muse-spark-1.3-contributor</code>. Native answers, proof citations and frontier certificates are graded deterministically. Earlier retrieval semantic labels came from a blinded batch reviewed by the same Muse model as the reader, with manual inspection of disagreements; that judge is not independent. No Claude judge was used for these runs.</p>
<h2>Retrieval: tied totals, different failures</h2><div class="scroll"><table><thead><tr><th>System</th><th>Strict exact</th><th>Semantic review</th><th>Mean visited-record recall</th></tr></thead><tbody id="retrievalScores"></tbody></table></div><p id="paired"></p><p class="muted">One trial, top-K 4, oracle-selected subgraphs flattened to text. These are not native ontology/graph scores. The full-context control answers all 24 semantically. Estimated context size is not billed token usage.</p>
<h3>Breakdown by question category</h3><div class="scroll"><table><thead><tr><th>Category</th><th>Lobu</th><th>Supermemory</th><th>Full context</th></tr></thead><tbody id="categories"></tbody></table></div>
<h3>Question explorer</h3><p>Open a question to inspect each answer and the records retrieved. Missing visited records are a diagnostic; this report does not claim every visited record was required for every sufficient proof.</p><div class="controls"><select id="filter"><option value="misses">Product misses only</option><option value="all">All 24 questions</option><option value="lobu">Lobu misses</option><option value="supermemory">Supermemory misses</option><option value="disagreement">Strict / semantic disagreement</option></select><select id="category"><option value="all">All categories</option></select><input id="search" aria-label="Search questions" placeholder="Search question or ID"></div><small id="count"></small><div id="questions"></div>
<h2>Native graph: measured separately</h2><div class="scroll"><table><thead><tr><th>System</th><th>Answer</th><th>Grounded proof</th><th>Frontier certificate</th><th>Reader calls</th><th>Tool calls</th><th>Tool bytes</th></tr></thead><tbody id="nativeScores"></tbody></table></div><p class="muted">Six queries are correlated observations from two worlds, not six independent scenarios. Source-update feeds and snapshot bindings are benchmark application glue. PostgreSQL is a SQL correctness/navigation control, not a memory-product competitor. Tool granularity differs. No native OWL inference or SQL expression-lineage proof is claimed.</p><p>Fresh native reads verified all assertions at each checkpoint: 72 → 71 → 70 in both worlds. SQL role, transaction and file-access guards passed. Green GitHub CI includes 26 tests, typecheck and fixture validation. The saved runs used 34 completed comparison responses plus 13 diagnostic responses and one interrupted-call reservation. Billed tokens and cost were unavailable.</p>
<h2>New development set: generated, not executed</h2><p>This extends graph maintenance only. Each pair changes one edge direction while preserving its topology and source schedule. IDs and record order are seeded. Expected answers are checked by simple-path enumeration and an independent recursive SQL reference; the agent never receives the oracle.</p><div class="scroll"><table><thead><tr><th>Episode</th><th>Structure</th><th>Answer counts q0 → q1 → q2</th><th>Product execution</th></tr></thead><tbody id="generated"></tbody></table></div>
<h2>Next runs that would change the conclusion</h2><ol><li>Run the four new pairs as bounded batches, with the same Muse model and limits for both systems. Keep budget exhaustion, setup failure, abstention and unsupported capability distinct. Do not treat a partial batch as full-suite coverage.</li><li>Add Graphiti as a graph-product baseline; report its model, embeddings, database, ingestion costs and capability limits. Keep PostgreSQL as the correctness control.</li><li>Implement the separate temporal, source-completeness/pagination, authorization and approval/dispatch families from the reviewed design. These new graph questions do not cover them.</li><li>Freeze adapters, then generate the planned 24 structurally held-out episodes. Report scenario/pair counts, coverage, unsafe actions, accuracy, proof validity, reader calls, tool bytes and latency. Keep retrieval and agent boards separate.</li></ol><p class="muted">A convincing result is a reproducible difference on a complete, held-out comparison. More renamed copies do not add independent evidence. No p-values or confidence intervals are presented for this single development smoke.</p><details><summary>Provenance and reproducibility</summary><p id="provenance"></p><p>KQA Pro questions and data: Cao et al., <em>KQA Pro: A Dataset with Explicit Compositional Programs for Complex Question Answering over Knowledge Base</em>, CC-BY-SA-4.0. <a href="https://github.com/shijx12/KQAPro_Baselines">Upstream source</a>. Synthetic graph fixtures follow the benchmark repository's MIT license.</p><p><a href="https://github.com/lobu-ai/agent-memory-benchmark/pull/6">Benchmark PR #6</a> · <a href="../native-graph-report/index.html">Native traces, storage and cleanup receipts</a> · <a href="../public-ontology-report/index.html">Original retrieval report</a> · <a href="dev-graph-24.json">New controller fixture</a> · <a href="dev-graph-24.reference.json">Development reference checks</a> · <a href="index.json">Combined machine-readable evidence</a></p></details>
<script>const D=__PAYLOAD__;const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const label=s=>({'lobu':'Lobu','supermemory':'Supermemory','postgres':'PostgreSQL','full-context-control':'Full context'}[s]||s);const pct=n=>(n*100).toFixed(1)+'%';const verdict=b=>'<span class="'+(b?'pass':'fail')+'">'+(b?'Pass':'Miss')+'</span>';
document.getElementById('retrievalScores').innerHTML=D.retrieval.systems.map(s=>'<tr><td>'+esc(s.label)+'</td><td>'+s.strictCorrect+'/'+s.questions+'</td><td>'+s.semanticCorrect+'/'+s.questions+'</td><td>'+pct(s.visitedRecordRecall)+'</td></tr>').join('');const p=D.retrieval.paired;document.getElementById('paired').textContent=`Both products pass ${p.both_pass}; Lobu alone passes ${p.lobu_only}; Supermemory alone passes ${p.supermemory_only}; both fail ${p.both_fail}. All eight product misses are abstentions (“unknown”). Lobu's comparison questions deserve the first retrieval investigation.`;
const cats=[...new Set(D.retrieval.questions.map(q=>q.category))];document.getElementById('category').innerHTML+=[...cats].map(c=>'<option>'+esc(c)+'</option>').join('');document.getElementById('categories').innerHTML=cats.map(c=>'<tr><td>'+esc(c)+'</td>'+['lobu','supermemory','full-context-control'].map(s=>{let qs=D.retrieval.questions.filter(q=>q.category===c);return '<td>'+qs.filter(q=>q.systems[s].semantic).length+'/'+qs.length+'</td>'}).join('')+'</tr>').join('');
function render(){let f=document.getElementById('filter').value,c=document.getElementById('category').value,term=document.getElementById('search').value.toLowerCase();let qs=D.retrieval.questions.filter(q=>(c==='all'||q.category===c)&&(!term||(q.id+' '+q.prompt).toLowerCase().includes(term))&&(f==='all'||f==='misses'&&(!q.systems.lobu.semantic||!q.systems.supermemory.semantic)||f==='lobu'&&!q.systems.lobu.semantic||f==='supermemory'&&!q.systems.supermemory.semantic||f==='disagreement'&&Object.values(q.systems).some(r=>r.strict!==r.semantic)));document.getElementById('count').textContent=qs.length+' of '+D.retrieval.questions.length+' questions';document.getElementById('questions').innerHTML=qs.map(q=>'<details><summary><span class="pill">'+esc(q.category)+'</span> '+esc(q.prompt)+' <small> Lobu: '+(q.systems.lobu.semantic?'pass':'miss')+' · SM: '+(q.systems.supermemory.semantic?'pass':'miss')+'</small></summary><p><strong>Expected:</strong> '+esc(q.expected.join(' / '))+' <small>'+esc(q.id)+'</small></p>'+['lobu','supermemory','full-context-control'].map(s=>{let r=q.systems[s];return '<div class="card"><strong>'+label(s)+'</strong> · strict '+verdict(r.strict)+' · semantic '+verdict(r.semantic)+'<p class="answer">Answer: '+esc(r.answer)+'</p><small>'+esc(r.review_reason)+'</small><p>Retrieved: '+(r.retrieved.map(x=>esc(x.name)+' <code>'+esc(x.id)+'</code>').join(', ')||'none')+'</p><p>Missing visited records: '+(r.missing_reference_records.map(x=>esc(x.name)+' <code>'+esc(x.id)+'</code>').join(', ')||'none')+'</p><small>Citations: '+esc(r.cited.join(', ')||'none')+' · visited-record recall '+pct(r.record_recall)+' · retrieval '+r.retrieval_ms.toFixed(0)+' ms</small></div>'}).join('')+'</details>').join('')};['filter','category','search'].forEach(id=>document.getElementById(id).addEventListener(id==='search'?'input':'change',render));render();
document.getElementById('nativeScores').innerHTML=Object.entries(D.native.systems).map(([s,v])=>{let ends=D.native.rows.filter(r=>r.system===s&&r.checkpoint==='q2'),sum=k=>ends.reduce((a,r)=>a+(r[k]||0),0);return '<tr><td>'+label(s)+'</td><td>'+v.answer_correct+'/'+v.denominator+'</td><td>'+v.proof_grounded+'/'+v.denominator+'</td><td>'+v.exact_set_certified+'/'+v.denominator+'</td><td>'+sum('cumulative_model_calls')+'</td><td>'+sum('cumulative_tool_calls')+'</td><td>'+sum('cumulative_tool_bytes').toLocaleString()+'</td></tr>'}).join('');
document.getElementById('generated').innerHTML=D.generated.episodes.map(e=>{let checks=D.generated_reference.rows.filter(r=>r.episode===e.id);return '<tr><td>'+esc(e.id)+'</td><td>'+esc(e.development_family.replaceAll('_',' '))+'</td><td>'+checks.map(r=>r.expected.length).join(' → ')+'</td><td>Not run</td></tr>'}).join('');document.getElementById('provenance').innerHTML='Retrieval suite SHA-256: <code>'+esc(D.retrieval.suite_sha256)+'</code><br>Original native fixture: <code>'+esc(D.native.fixture_sha256)+'</code><br>New development fixture: <code>'+esc(D.generated_reference.fixture_sha256)+'</code><br>Native source commit: <code>'+esc(D.native.source_commit)+'</code>';
</script></html>'''


if __name__ == "__main__":
    main()
