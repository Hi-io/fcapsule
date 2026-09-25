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

test('labels wrap at word boundaries and stay within two bounded lines',()=>{
  assert.deepEqual(map.labelLines('Checkout Latency High'),['Checkout Latency High']);
  assert.deepEqual(map.labelLines('pod container restarts total'),['pod container restarts','total']);
  for (const value of ['x'.repeat(120),'inventory response schema mismatch across multiple services']) {
    const lines=map.labelLines(value);
    assert.equal(lines.length,2);
    assert.ok(lines.every(line=>line.length<=23));
  }
});

test('case marks stay centered and do not overlap while preserving relationships',()=>{
  const patterns=Array.from({length:20},(_,i)=>pattern('p'+i,'timeout',i));
  const records=Array.from({length:120},(_,i)=>record('c'+i,[{key:'timeout',value:i%20}]));
  const source=map.buildGraph(patterns,records);
  const placed=map.positionGraph(source);
  const cases=placed.nodes.filter(n=>n.kind==='case');
  assert.ok(Math.abs(cases.reduce((sum,n)=>sum+n.x,0)/cases.length-500)<0.01);
  assert.ok(Math.abs(cases.reduce((sum,n)=>sum+n.y,0)/cases.length-350)<0.01);
  for(let i=0;i<cases.length;i++) for(let j=i+1;j<cases.length;j++) {
    assert.ok(Math.hypot(cases[i].x-cases[j].x,cases[i].y-cases[j].y)>11.8);
  }
  assert.deepEqual(placed.edges,source.edges);
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
