#!/usr/bin/env python3
"""Verify two public development worlds, not product performance.
Two independent reference implementations: simple-path enumeration and a
recursive relational query. No model, Lobu service or external action is used.
"""
from pathlib import Path
import hashlib,json,random,sqlite3

def worlds():
    cases=[]
    for reverse in (False,True):
        rng=random.Random(2026110205)
        names=["invoice","issuer","left1","left2","left3","right1","right2","right3","riskroot","cycle1","cycle2","namesake"]
        ids=dict(zip(names,["n"+x for x in rng.sample([f"{i:04d}" for i in range(100,9999)],len(names))]))
        facts=[]
        def fact(s,p,o):
            fid="s"+str(len(facts)).zfill(4)
            facts.append({"id":fid,"subject":ids.get(s,s),"predicate":p,"object":ids.get(o,o)})
            return fid
        issuer=fact("invoice","bench:issuer","issuer")
        left=[fact(a,"org:subOrganizationOf",b) for a,b in zip(["issuer","left1","left2","left3"],["left1","left2","left3","riskroot"])]
        right=[fact(a,"org:subOrganizationOf",b) for a,b in zip(["issuer","right1","right2"],["right1","right2","right3"])]
        right.append(fact("riskroot" if reverse else "right3","org:subOrganizationOf","right3" if reverse else "riskroot"))
        fact("issuer","org:subOrganizationOf","cycle1")
        fact("cycle1","org:subOrganizationOf","cycle2")
        fact("cycle2","org:subOrganizationOf","issuer")
        risk=fact("riskroot","bench:risk","open")
        fact("namesake","bench:risk","open")
        # Connected irrelevant records and same-predicate distractors; no
        # answer-program subgraph selection or marked support in public input.
        for k in range(50):
            fact("d"+str(k),"org:subOrganizationOf","d"+str((k+1)%50))
            if k%7==0: fact("d"+str(k),"bench:risk","open")
        public={"id":"parallel-proof-"+("reverse" if reverse else "survives"),"task":"Return all current open-risk organizations reachable from the invoice issuer via zero or more org:subOrganizationOf links. Include sufficient assertion IDs.","invoice":ids["invoice"],"facts":facts,"names":[{"id":ids["riskroot"],"label":"Synthetic Meridian"},{"id":ids["namesake"],"label":"Synthetic Meridian"}],"schema":{"org:subOrganizationOf":"directed containment, not automatically transitive as an asserted RDF predicate","bench:risk":"custom benchmark risk fact; no legal or financial inference"},"coverage":{"status":"complete","predicates":["bench:issuer","org:subOrganizationOf","bench:risk"],"scope":"all entities in this bounded world","version":"v1"},"checkpoints":[{"id":"q0","withdraw":[]},{"id":"q1","withdraw":[left[-1]]},{"id":"q2","withdraw":[left[-1],right[-1]]}]}
        public['inferenceProfile']='Directed reachability over asserted org:subOrganizationOf only; no inverse, subproperty or other entailment inputs are enabled in this seed.'
        for index,checkpoint in enumerate(public['checkpoints']):
            checkpoint.update(snapshot_id='snapshot-'+str(index),required_watermark=index,principal='fixture-reader',authorization_scope='bounded-world',source_id='fixture-source',source_health='healthy',valid_at=index,observation_cutoff=index)
        expected=[{"ids":[ids["riskroot"]],"proofCount":1 if reverse else 2},{"ids":[] if reverse else [ids["riskroot"]],"proofCount":0 if reverse else 1},{"ids":[],"proofCount":0}]
        cases.append((public,expected))
    return cases

def path_oracle(public,withdrawn):
    facts=[f for f in public["facts"] if f["id"] not in withdrawn]
    roots=[f for f in facts if f["subject"]==public["invoice"] and f["predicate"]=="bench:issuer"]
    edges={}
    risks={}
    for f in facts:
        if f["predicate"]=="org:subOrganizationOf": edges.setdefault(f["subject"],[]).append(f)
        elif f["predicate"]=="bench:risk" and f["object"]=="open": risks.setdefault(f["subject"],[]).append(f)
    proofs={}
    def walk(node,seen,proof):
        for r in risks.get(node,[]): proofs.setdefault(node,[]).append(proof+[r["id"]])
        for e in edges.get(node,[]):
            if e["object"] not in seen:
                walk(e["object"],seen|{e["object"]},proof+[e["id"]])
    for root in roots: walk(root["object"],{root["object"]},[root["id"]])
    return proofs

def relational_oracle(public,withdrawn):
    db=sqlite3.connect(":memory:")
    try:
        db.execute("CREATE TABLE assertions(subject TEXT,predicate TEXT,object TEXT)")
        db.executemany("INSERT INTO assertions VALUES(?,?,?)",[(f["subject"],f["predicate"],f["object"]) for f in public["facts"] if f["id"] not in withdrawn])
        return sorted(r[0] for r in db.execute("""
        WITH RECURSIVE reachable(id) AS (
          SELECT object FROM assertions WHERE subject=? AND predicate='bench:issuer'
          UNION
          SELECT a.object FROM reachable r JOIN assertions a ON a.subject=r.id
            WHERE a.predicate='org:subOrganizationOf'
        )
        SELECT DISTINCT r.id FROM reachable r JOIN assertions a ON a.subject=r.id
          WHERE a.predicate='bench:risk' AND a.object='open'
        """,(public["invoice"],)))
    finally: db.close()

def sufficient_positive_proof(public,withdrawn,answer,assertion_ids):
    """Check a positive claim against currently visible assertions.

    Accept any sufficient subset, not one designated gold path. Negative and
    temporal certificates are outside this bounded development checker.
    """
    active={f['id']:f for f in public['facts'] if f['id'] not in withdrawn}
    if not assertion_ids or not set(assertion_ids)<=active.keys(): return False
    subset={**public,'facts':[active[k] for k in set(assertion_ids)]}
    return answer in relational_oracle(subset,set())

def certificate_bindings(checkpoint):
    return {k:checkpoint[k] for k in ['snapshot_id','required_watermark','principal','authorization_scope','source_id','valid_at','observation_cutoff']}

def make_frontier_certificate(public,checkpoint):
    """Evaluator-side example builder; never provided as an agent solve tool."""
    facts=[f for f in public['facts'] if f['id'] not in checkpoint['withdraw']]
    roots=[f for f in facts if f['subject']==public['invoice'] and f['predicate']=='bench:issuer']
    frontier={};todo=[f['object'] for f in roots]
    while todo:
        node=todo.pop()
        if node in frontier: continue
        edges=[f for f in facts if f['subject']==node and f['predicate']=='org:subOrganizationOf']
        risks=[f for f in facts if f['subject']==node and f['predicate']=='bench:risk']
        frontier[node]={'outbound_assertions':sorted(f['id'] for f in edges),'risk_assertions':sorted(f['id'] for f in risks),'terminal_cursor':None,'pages_complete':True}
        todo.extend(f['object'] for f in edges)
    return {**certificate_bindings(checkpoint),'health':checkpoint['source_health'],'issuer_assertions':sorted(f['id'] for f in roots),'issuer_pages_complete':True,'issuer_terminal_cursor':None,'frontier':frontier}

def verify_exact_set(public,checkpoint,claimed,certificate):
    """Verify bounded exact-set coverage independently of path enumeration.

    This seed has one complete source and one page per subject/predicate. It
    does not implement real product pagination, temporal or authz enforcement.
    """
    if checkpoint['source_health']!='healthy' or certificate.get('health')!='healthy': return False
    if any(certificate.get(k)!=v for k,v in certificate_bindings(checkpoint).items()): return False
    if certificate.get('issuer_pages_complete') is not True or certificate.get('issuer_terminal_cursor') is not None: return False
    facts=[f for f in public['facts'] if f['id'] not in checkpoint['withdraw']]
    roots=[f for f in facts if f['subject']==public['invoice'] and f['predicate']=='bench:issuer']
    if certificate.get('issuer_assertions')!=sorted(f['id'] for f in roots): return False
    frontier=certificate.get('frontier',{})
    visited=set();todo=[f['object'] for f in roots];answers=set()
    while todo:
        node=todo.pop()
        if node in visited: continue
        visited.add(node)
        row=frontier.get(node)
        if not isinstance(row,dict) or row.get('pages_complete') is not True or row.get('terminal_cursor') is not None: return False
        edges=[f for f in facts if f['subject']==node and f['predicate']=='org:subOrganizationOf']
        risks=[f for f in facts if f['subject']==node and f['predicate']=='bench:risk']
        if row.get('outbound_assertions')!=sorted(f['id'] for f in edges): return False
        if row.get('risk_assertions')!=sorted(f['id'] for f in risks): return False
        if any(f['object']=='open' for f in risks): answers.add(node)
        todo.extend(f['object'] for f in edges)
    return len(claimed)==len(set(claimed)) and set(claimed)==answers
