"""Structurally held-out graph maintenance templates, generated after adapter freeze.

This is a graph-only holdout, not the broader temporal/authorization/action plan.
Only the controller reads this fixture; the reference sidecar stays evaluator-only.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import random
from generate import generate, verify

FAMILIES = ['joined_braid', 'dual_issuer_zero_hop', 'nested_cycles', 'irrelevant_then_relevant']


def generate_holdout(seed=2026110207):
    scaffold = generate(seed)['episodes'][0]
    episodes = []
    for index, family in enumerate(FAMILIES):
        for reverse in [False, True]:
            rng = random.Random(seed+index)
            aliases = ['invoice','issuer','issuer2','a','b','c','d','e','risk','risk2','clone']+['x'+str(i) for i in range(50)]
            refs = dict(zip(aliases, ['n'+format(x,'08x') for x in rng.sample(range(2**31),len(aliases))]))
            facts = []
            def fact(s,p,o):
                ident='s'+format(rng.getrandbits(48),'012x')
                facts.append({'id':ident,'subject':refs[s],'predicate':p,'object':refs.get(o,o)})
                return ident
            def edge(a,b):return fact(a,'org:subOrganizationOf',b)
            def alternate(a,b):return edge(b,a) if reverse else edge(a,b)
            issuer = fact('invoice','bench:issuer','issuer')
            if family=='joined_braid':
                edge('issuer','a');edge('issuer','b');edge('a','c');edge('b','c')
                edge('c','d');edge('c','e');first=edge('d','risk');second=alternate('e','risk')
            elif family=='dual_issuer_zero_hop':
                fact('invoice','bench:issuer','issuer2');fact('issuer','bench:risk','open')
                edge('issuer','a');edge('a','risk');edge('issuer2','b');second=alternate('b','risk')
                first=issuer
            elif family=='nested_cycles':
                edge('issuer','a');edge('a','b');edge('b','c');edge('c','a')
                edge('c','d');edge('d','e');edge('e','d')
                first=edge('b','risk');second=alternate('e','risk2');fact('risk2','bench:risk','open')
            else:
                edge('issuer','a');second=edge('a','risk')
                edge('issuer','b');edge('b','c');edge('c','b')
                alternate('risk2','c');fact('risk2','bench:risk','open')
                first=None
            fact('risk','bench:risk','open');fact('clone','bench:risk','open')
            for i in range(50):
                distractor=edge('x'+str(i),'x'+str((i+1)%50))
                if family=='irrelevant_then_relevant' and i==17:first=distractor
                if i%7==0:fact('x'+str(i),'bench:risk','open')
            rng.shuffle(facts)
            world=copy.deepcopy(scaffold)
            world.update(id=f'graph-holdout-{len(episodes)+1:02}',pair_id=f'holdout-pair-{index+1}',
                         development_family=family,split='structural_holdout',invoice=refs['invoice'],facts=facts,
                         names=[{'id':refs[key],'label':label} for key,label in [
                             ('risk','Synthetic Meridian'),('clone','Synthetic Meridian'),
                             ('a','Synthetic Junction'),('b','Synthetic Junction')]])
            for cp,withdrawn in zip(world['checkpoints'],[[],[first],[first,second]]):cp['withdraw']=sorted(withdrawn)
            episodes.append(world)
    return {'status':'Sealed graph-only structural holdout; no product/model run at generation',
            'seed':seed,'base_episodes':8,'matched_pairs':4,'checkpoint_queries':24,
            'adapter_freeze_commit':'bab999b6fe3ab5301b43cf8334a35c6af72708c6',
            'boundary':'Four new structural templates; renaming is not the holdout. Oracle never reaches executor.',
            'episodes':episodes}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--seed',type=int,default=2026110207)
    args=parser.parse_args()
    suite=generate_holdout(args.seed);checks=verify(suite)
    data=(json.dumps(suite,indent=2)+'\n').encode()
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_bytes(data)
    receipt={'fixture_sha256':hashlib.sha256(data).hexdigest(),'generated_queries':len(checks),
             'generator_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
             'reference_checks_passed':len(checks),'product_runs_at_generation':0,'model_calls_at_generation':0,'rows':checks}
    args.output.with_suffix('.reference.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps({k:v for k,v in receipt.items() if k!='rows'}))


if __name__=='__main__':main()
