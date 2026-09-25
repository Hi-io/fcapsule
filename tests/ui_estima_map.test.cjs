const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const map = require('../fcapsule/ui/assets/estima.js');

const pattern = (id,key,value,unit=null) => ({id,key,value,unit,case_count:5});
const record = (id,observations,extras={}) => ({id,instance_id:'north',episode_id:id,revision:1,observations,...extras});

test('connections require exact typed observations, including units',()=>{
  const graph=map.buildGraph([pattern('a','limit',40),pattern('b','limit','40'),pattern('c','limit',40,'MB')], [
    record('x',[{key:'limit',value:40},{key:'limit',value:40}]),
    record('y',[{key:'limit',value:40,unit:'MiB'}]),
  ]);
  assert.equal(graph.nodes.length,5);
  assert.equal(graph.edges.length,1);
  assert.deepEqual([graph.edges[0].source,graph.edges[0].target],['c:x','p:a']);
});

test('a newer revision replaces an older record without inflating the case count',()=>{
  const old=record('old',[{key:'state',value:'old'}],{episode_id:'ep',revision:1});
  const fresh=record('new',[{key:'state',value:'new'}],{episode_id:'ep',revision:2});
  const other=record('other',[],{instance_id:'south',episode_id:'ep',revision:1});
  const graph=map.buildGraph([pattern('p','state','old')],[old,fresh,other,fresh]);
  assert.equal(graph.cases.length,2);
  assert.equal(graph.edges.length,0);
  assert.deepEqual(graph.cases.map(c=>c.id),['new','other']);
});

test('hypotheses and matching alert names in summaries do not become evidence edges',()=>{
  const graph=map.buildGraph([pattern('p','alert_family','Unavailable')],[record('x',[],{
    summary:'Unavailable',hypotheses:[{key:'alert_family',value:'Unavailable'}],
  })]);
  assert.equal(graph.edges.length,0);
});

test('observation domains remain part of the relationship identity',()=>{
  const p={...pattern('p','status',0),kind:'pm'};
  const graph=map.buildGraph([p],[record('a',[{kind:'fm',key:'status',value:0}])]);
  assert.equal(graph.edges.length,0);
});

test('different values do not merge and no pattern-to-pattern causal links are created',()=>{
  const graph=map.buildGraph([pattern('p','pod_ready',0),pattern('q','pod_ready',1)],[record('a',[{key:'pod_ready',value:0}])]);
  assert.equal(graph.edges.length,1);
  assert.equal(graph.edges[0].target,'p:p');
  assert.ok(graph.edges.every(e=>e.source.startsWith('c:')&&e.target.startsWith('p:')));
});

test('layout is deterministic, bounded and has finite coordinates for empty and populated maps',()=>{
  assert.equal(map.positionGraph(map.buildGraph([],[])).nodes.length,0);
  const patterns=Array.from({length:20},(_,i)=>pattern('p'+i,'timeout',i));
  const records=Array.from({length:200},(_,i)=>record('c'+i,[{key:'timeout',value:i%20}]));
  const graph=map.buildGraph(patterns,records);
  const a=map.positionGraph(graph),b=map.positionGraph(graph);
  assert.deepEqual(a.nodes,b.nodes);
  assert.ok(a.nodes.every(n=>Number.isFinite(n.x)&&Number.isFinite(n.y)&&n.x>0&&n.x<1000&&n.y>0&&n.y<700));
});

test('human-facing titles retain the technical meaning without asserting root cause',()=>{
  assert.equal(map.patternTitle(pattern('a','alert_family','LabCheckoutLatencyHigh')),'Checkout Latency High');
  assert.equal(map.patternTitle(pattern('a','mysql_max_connections',40)),'mysql max connections');
  assert.equal(map.caseTitle(record('a',[{key:'alert_family',value:'KubePodNotReady'}])),'Kube Pod Not Ready');
});

test('explorer is read-only and uses bounded existing retrieval routes, not model APIs',()=>{
  const source=fs.readFileSync(path.join(__dirname,'../fcapsule/ui/assets/estima.js'),'utf8');
  assert.match(source,/limit:'20'/);
  assert.match(source,/limit:10/);
  assert.match(source,/Promise.all\(\[worker\(\),worker\(\),worker\(\)\]\)/);
  assert.doesNotMatch(source,/\/api\/(?:investigations|briefing|settings)|openrouter|api\.deepseek|method:'(?:DELETE|PUT|PATCH)'/);
  assert.match(source,/Cases loaded/);
  assert.match(source,/loaded selection, not the entire memory/);
  assert.match(source,/Unverified hypotheses/);
});

test('new explorer assets are included in the existing bundled UI endpoints',()=>{
  const source=fs.readFileSync(path.join(__dirname,'../fcapsule/ui/app.py'),'utf8');
  assert.match(source,/"estima.js", "app.js"/);
  assert.match(source,/"visual.css", "estima.css"/);
});
