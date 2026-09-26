/* Collective's read-only explorer. Relationships come from retained typed observations. */
(function (global) {
  'use strict';
  const GROUPS = [
    {id:'alerts', label:'Fault signals', color:'#bd7940'},
    {id:'configuration', label:'Configuration', color:'#8968b5'},
    {id:'performance', label:'Performance & health', color:'#308c9c'},
    {id:'observations', label:'Other observations', color:'#478666'},
  ];
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const identifier = item => String(item?.id ?? item?.pattern_id ?? item?.case_id ?? '');
  const count = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
  const list = value => Array.isArray(value) ? value : [];
  const human = value => String(value ?? '').replace(/([a-z0-9])([A-Z])/g, '$1 $2').replace(/[_-]+/g, ' ').trim();
  const shortened = (value, length = 30) => value.length > length ? value.slice(0, length - 1) + '\u2026' : value;
  function labelLines(text, width = 23) {
    if (text.length <= width) return [text];
    const boundary = text.lastIndexOf(' ', width);
    const split = boundary > width / 3 ? boundary : width;
    return [text.slice(0,split),shortened(text.slice(split).trim(),width)];
  }
  const dateText = value => Number.isFinite(typeof value === 'number' ? value : Date.parse(value)) ? new Date(value).toLocaleString([], {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}) : 'Time unavailable';
  function groupFor(pattern) {
    const key = String(pattern.key || '');
    if (key === 'alert_family' || pattern.kind === 'fm') return GROUPS[0];
    if (/^(pod_|node_|container_)/.test(key) || pattern.kind === 'pm') return GROUPS[2];
    if (/config|timeout|retries|schema|revision|max_|limit|requests_per_second/.test(key) || pattern.kind === 'cm') return GROUPS[1];
    return GROUPS[3];
  }
  function patternTitle(pattern) {
    if (pattern.key === 'alert_family') return human(pattern.value).replace(/^Lab /, '');
    return human(pattern.key || pattern.title || identifier(pattern));
  }
  function caseTitle(record) {
    const alert = list(record.observations).find(o => o.key === 'alert_family');
    return human(alert?.value || record.scope?.service || record.scope?.workload || 'Retained case').replace(/^Lab /, '');
  }
  function scalarKey(item) {
    return JSON.stringify([item.kind || null, item.key, typeof item.value, item.value, item.unit || null]);
  }
  function uniqueCases(records) {
    const episodes = new Map();
    for (const record of records) {
      if (!identifier(record)) continue;
      const key = record.episode_id ? JSON.stringify([record.instance_id, record.episode_id]) : identifier(record);
      const previous = episodes.get(key);
      if (!previous || Number(record.revision || 0) > Number(previous.revision || 0)) episodes.set(key, record);
    }
    return [...episodes.values()].sort((a,b) => identifier(a).localeCompare(identifier(b)));
  }
  function buildGraph(patterns, records) {
    const cases = uniqueCases(records);
    const nodes = patterns.filter(p => identifier(p)).map(p => ({id:'p:' + identifier(p), kind:'pattern', data:p, group:groupFor(p).id}));
    const edges = [];
    const byObservation = new Map();
    for (const node of nodes) {
      const key = scalarKey(node.data);
      if (!byObservation.has(key)) byObservation.set(key, []);
      byObservation.get(key).push(node.id);
    }
    for (const record of cases) {
      const node = {id:'c:' + identifier(record), kind:'case', data:record, group:'case'};
      nodes.push(node);
      const seen = new Set();
      for (const observation of list(record.observations)) {
        for (const target of byObservation.get(scalarKey(observation)) || []) {
          if (seen.has(target)) continue;
          seen.add(target);
          edges.push({source:node.id, target, observation});
        }
      }
    }
    return {nodes,edges,cases};
  }
  function positionGraph(graph) {
    const groups = GROUPS.map(group => ({...group, nodes:graph.nodes.filter(n => n.kind === 'pattern' && n.group === group.id)})).filter(g => g.nodes.length);
    const placed = new Map();
    let index = 0;
    const patternCount = graph.nodes.filter(n => n.kind === 'pattern').length;
    const labels = [];
    for (const group of groups) {
      const ranked = [...group.nodes].sort((a,b) => Number(b.data.key === 'alert_family')-Number(a.data.key === 'alert_family') || Number(b.data.case_count || 0)-Number(a.data.case_count || 0));
      const prominent = [ranked[0].id];
      const first = index;
      for (const node of group.nodes) {
        const angle = -Math.PI / 2 + 2 * Math.PI * (index++ + .5) / Math.max(1, patternCount);
        placed.set(node.id, {...node, x:500 + Math.cos(angle) * 300, y:350 + Math.sin(angle) * 220,
          r:6.5 + Math.min(7, Math.sqrt(Math.max(0,Number(node.data.case_count) || 0)) * .65), color:group.color, prominent:prominent.includes(node.id)});
      }
      const primary=placed.get(prominent[0]);
      const peers=group.nodes.filter(n=>n.id!==primary.id);
      const distinct=peers.filter(n=>primary.data.key==='alert_family' ? n.data.key==='alert_family' : n.data.key!==primary.data.key);
      const secondary=(distinct.length ? distinct : peers).map(n=>placed.get(n.id))
        .sort((a,b)=>Math.hypot(b.x-primary.x,b.y-primary.y)-Math.hypot(a.x-primary.x,a.y-primary.y))[0];
      if(secondary) secondary.prominent=true;
      const angle = -Math.PI / 2 + 2 * Math.PI * (first + group.nodes.length / 2) / Math.max(1,patternCount);
      labels.push({...group, x:500 + Math.cos(angle) * 430, y:350 + Math.sin(angle) * 310});
    }
    const cases = graph.nodes.filter(n => n.kind === 'case');
    for (let i = 0; i < cases.length; i++) {
      const node = cases[i];
      const angle = i * Math.PI * (3 - Math.sqrt(5));
      const related=graph.edges.filter(e=>e.source===node.id).map(e=>placed.get(e.target));
      const cx=related.length ? related.reduce((s,n)=>s+n.x,0)/related.length : 500;
      const cy=related.length ? related.reduce((s,n)=>s+n.y,0)/related.length : 350;
      const radius = 25 + Math.sqrt((i + .5) / Math.max(1,cases.length)) * 110;
      placed.set(node.id, {...node, x:500+(cx-500)*.5+Math.cos(angle)*radius, y:350+(cy-350)*.5+Math.sin(angle)*radius*.82, r:4,color:'#6984a2'});
    }
    // Keep the case cloud optically centered, then separate coincident marks.
    // This changes presentation only; edge membership stays untouched.
    const marks=cases.map(node=>placed.get(node.id));
    if(marks.length) {
      const dx=500-marks.reduce((sum,n)=>sum+n.x,0)/marks.length;
      const dy=350-marks.reduce((sum,n)=>sum+n.y,0)/marks.length;
      marks.forEach(n=>{n.x+=dx;n.y+=dy;});
      for(let pass=0;pass<32;pass++) for(let i=0;i<marks.length;i++) for(let j=i+1;j<marks.length;j++) {
        const a=marks[i],b=marks[j],vx=b.x-a.x,vy=b.y-a.y,distance=Math.hypot(vx,vy);
        if(distance>=12) continue;
        const ux=distance ? vx/distance : 1,uy=distance ? vy/distance : 0,push=(12-distance)/2;
        a.x-=ux*push;a.y-=uy*push;b.x+=ux*push;b.y+=uy*push;
      }
    }
    return {...graph,nodes:[...placed.values()],positions:placed,groups:labels};
  }
  let current = null;
  function start(root, options) {
    if (current) current.dispose();
    current = explorer(root, options);
    return current.load();
  }
  function explorer(root, options) {
    const icons = options.icon;
    const state = {patterns:[],cases:[],browseCases:[],nextCursor:null,stats:null,statsUnavailable:false,
      browseUnavailable:false,browseLoading:false,browseError:'',cache:new Map(),selected:null,mode:'map',instance:'',time:100,
      query:options.query || '',scope:options.scope || '',phase:'loading',message:'',partial:0,loaded:0,
      detailError:'',detailLoading:false,epoch:0,zoom:1,pan:{x:0,y:0}};
    const controllers = new Set();
    let disposed = false, detailSequence = 0;
    let graph = positionGraph(buildGraph([], []));
    async function request(path, body) {
      const controller = new AbortController(); controllers.add(controller);
      const timer = setTimeout(() => controller.abort(), 15000);
      try {
        const response = await fetch(path, {cache:'no-store',signal:controller.signal,
          ...(body ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)} : {})});
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || data.detail || 'Collective is unavailable. Check the connection in Settings.');
        return data;
      } finally {clearTimeout(timer); controllers.delete(controller);}
    }
    function visibleCases() {
      let records = uniqueCases(state.cases).filter(c => !state.instance || c.instance_id === state.instance);
      const dates = records.map(c => Date.parse(c.observed_at)).filter(Number.isFinite).sort((a,b)=>a-b);
      if (state.time < 100 && dates.length) {
        const cutoff = dates[0] + (dates[dates.length - 1]-dates[0])*state.time/100;
        records = records.filter(c => Date.parse(c.observed_at) <= cutoff);
      }
      return records;
    }
    function makeGraph() {
      const records = visibleCases();
      let patterns = state.patterns;
      if (state.instance || state.time < 100) {
        const keys = new Set(records.flatMap(c => list(c.observations).map(scalarKey)));
        patterns = patterns.filter(p => keys.has(scalarKey(p)));
      }
      return positionGraph(buildGraph(patterns,records));
    }
    function controlButton(id, symbol, label) {
      return `<button type="button" id="${id}" class="em-icon-button" title="${label}" aria-label="${label}">${icons(symbol)}</button>`;
    }
    function header() {
      return `<header class="em-header"><div><div class="eyebrow">FCAPSule investigates. Collective remembers.</div><h1>Collective <span>Shared knowledge</span></h1></div><div class="em-connection"><span class="status ${state.phase === 'error' ? 'error' : state.phase === 'loading' ? 'running' : 'healthy'}">${state.phase === 'error' ? 'Unavailable' : state.phase === 'loading' ? 'Connecting' : 'Connected'}</span>${controlButton('em-refresh','refresh-cw','Refresh memory')}</div></header>`;
    }
    function toolbar() {
      return `<div class="em-toolbar"><form id="em-search" class="em-search"><label class="sr-only" for="em-query">Search memory</label><input id="em-query" type="search" value="${esc(state.query)}" placeholder="Search collective memory" autocomplete="off"><label class="sr-only" for="em-scope">Cluster</label><input id="em-scope" value="${esc(state.scope)}" placeholder="All clusters" autocomplete="off"><button type="submit" title="Search memory" aria-label="Search memory">${icons('scan-line')}</button></form><div class="em-modes" role="group" aria-label="Memory view"><button type="button" id="em-map-mode" aria-pressed="${state.mode === 'map'}">${icons('network')}Map</button><button type="button" id="em-list-mode" aria-pressed="${state.mode === 'list'}">${icons('logs')}List</button></div></div>`;
    }
    function nodeMarkup(node) {
      const pattern = node.kind === 'pattern';
      const title = pattern ? patternTitle(node.data) : caseTitle(node.data);
      const label = pattern ? `${title}: ${String(node.data.value)}. ${count(node.data.case_count || 0,'case')}.` : `${title}. ${dateText(node.data.observed_at)}. ${node.data.instance_id || ''}`;
      const anchor = node.x > 530 ? 'start' : node.x < 470 ? 'end' : 'middle';
      const dx = anchor === 'start' ? node.r + 9 : anchor === 'end' ? -node.r - 9 : 0;
      const lines=labelLines(title);
      const dy = anchor === 'middle' ? -node.r - 14 - (lines.length-1)*17 : lines.length>1 ? -10 : -3;
      return `<g class="em-node em-node-${node.kind} ${node.prominent ? 'is-prominent' : ''}" data-node="${esc(node.id)}" role="button" tabindex="0" aria-label="${esc(label)}" aria-pressed="false" transform="translate(${node.x},${node.y})" style="--node-color:${node.color}"><title>${esc(label)}</title><circle class="em-hit" r="${Math.max(18,node.r+7)}"/><circle class="em-node-ring" r="${node.r+5}"/><circle class="em-dot" r="${node.r}"/>${pattern ? `<text x="${dx}" y="${dy}" text-anchor="${anchor}" class="em-node-title">${lines.map((line,index)=>`<tspan x="${dx}" dy="${index ? 17 : 0}">${esc(line)}</tspan>`).join('')}</text><text x="${dx}" y="${dy+(lines.length-1)*17+16}" text-anchor="${anchor}" class="em-node-meta">${esc(node.data.key === 'alert_family' ? count(node.data.case_count || 0,'case') : shortened(String(node.data.value),18) + ' · ' + count(node.data.case_count || 0,'case'))}</text>` : ''}</g>`;
    }
    function mapMarkup() {
      const edges = graph.edges.map(edge => {
        const a=graph.positions.get(edge.source), b=graph.positions.get(edge.target);
        return `<line class="em-edge" data-source="${esc(edge.source)}" data-target="${esc(edge.target)}" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}"/>`;
      }).join('');
      return `<div class="em-map" id="em-map"><div class="em-map-heading"><div><span class="em-overline">KNOWLEDGE MAP</span><h2>Experience, connected.</h2></div><span class="em-map-count" id="em-map-count">${count(graph.nodes.filter(n=>n.kind==='pattern').length,'pattern')}<br>${count(graph.cases.length,'case')} in map${state.stats && !state.scope && !state.query ? ` / ${esc(state.stats.episodes)} total` : ''}</span></div><svg id="em-network" viewBox="0 0 1000 700" role="group" aria-label="Shared observations and retained cases. Select a node to inspect its connections."><g id="em-viewport"><g class="em-edges">${edges}</g><g class="em-nodes">${graph.nodes.map(nodeMarkup).join('')}</g></g></svg><div id="em-map-empty" class="em-map-empty" ${graph.nodes.length ? 'hidden' : ''}>${state.phase === 'loading' ? 'Connecting to collective memory...' : state.phase === 'error' ? 'Memory is unavailable' : 'No memories in this view'}</div><div class="em-map-footer"><div class="em-legend">${GROUPS.filter(g=>graph.nodes.some(n=>n.group===g.id)).map(g=>`<span><i style="background:${g.color}"></i>${g.label}</span>`).join('')}<span><i class="em-case-dot"></i>Retained case</span></div><div class="em-map-tools">${controlButton('em-zoom-in','zoom-in','Zoom in')}${controlButton('em-zoom-out','zoom-out','Zoom out')}${controlButton('em-fit','scan-line','Fit map')}</div></div></div>`;
    }
    function listMarkup() {
      const records=state.browseUnavailable ? graph.cases : state.browseCases;
      const more=state.nextCursor ? `<button type="button" id="em-load-more" class="em-load-more" ${state.browseLoading ? 'disabled' : ''}>${state.browseLoading ? 'Loading cases...' : 'Load more cases'}</button>` : '';
      return `<div class="em-list"><h2>Shared observations <span>${graph.nodes.filter(n=>n.kind==='pattern').length} in map</span></h2>${graph.nodes.filter(n=>n.kind==='pattern').map(node=>`<button type="button" data-node="${esc(node.id)}" class="em-list-row"><i style="background:${node.color}"></i><span><strong>${esc(patternTitle(node.data))}</strong><small>${esc(node.data.key)}: ${esc(node.data.value)}</small></span><span>${count(node.data.case_count || 0,'case')}</span>${icons('chevron-right')}</button>`).join('') || '<p class="em-muted">No shared observations in this view.</p>'}<h2>Retained cases <span>${records.length} shown${state.stats && !state.scope && !state.query ? ` of ${esc(state.stats.episodes)}` : ''}</span></h2>${records.map(c=>caseButton(c)).join('') || '<p class="em-muted">No retained cases in this view.</p>'}${more}${state.browseError ? `<p role="alert" class="em-muted">${esc(state.browseError)}</p>` : ''}${state.browseUnavailable ? '<p class="em-muted">The case list is unavailable; showing cases from the map.</p>' : ''}</div>`;
    }
    function caseButton(record) {
      return `<button type="button" class="em-case-row" data-node="c:${esc(identifier(record))}"><span><strong>${esc(caseTitle(record))}</strong><small>${esc([record.scope?.service || record.scope?.workload, dateText(record.observed_at)].filter(Boolean).join(' · '))}</small></span>${icons('chevron-right')}</button>`;
    }
    function overview() {
      const instances = new Set(graph.cases.map(c=>c.instance_id).filter(Boolean));
      const recent = [...graph.cases].sort((a,b)=>Date.parse(b.observed_at)-Date.parse(a.observed_at)).slice(0,3);
      const total=state.stats && !state.scope && !state.query ? state.stats.episodes : null;
      return `<div class="em-panel-intro"><span class="em-overline">RETAINED KNOWLEDGE</span><h2>Every case leaves<br>a memory.</h2><p>Explore the observations that connect past investigations.</p></div><div class="em-stat-pair"><div><strong>${total == null ? graph.cases.length : esc(total)}</strong><span>${total == null ? 'Cases in this map' : 'Unique episodes retained'}</span></div><div><strong>${graph.cases.length}</strong><span>Cases in this map</span></div></div><div class="em-panel-section"><h3>Recent in this view</h3>${recent.map(caseButton).join('') || `<p class="em-muted">${state.phase === 'loading' || state.phase === 'connections' ? 'Retrieving retained cases...' : 'Select a pattern or search for a case.'}</p>`}</div><div class="em-panel-section em-reading-key"><h3>Read the connections</h3><p>A line means a case contains that exact observation. Shared observations do not establish a shared cause.</p><p>${instances.size} ${instances.size === 1 ? 'instance is' : 'instances are'} represented in this map. The map is a loaded selection; use List to browse more cases.</p></div>`;
    }
    function panelContent() {
      const selection = state.selected;
      if (!selection) return overview();
      const close = `<div class="em-detail-nav"><button type="button" id="em-back">${icons('chevron-right')}Memory overview</button><button type="button" id="em-copy-link" class="em-permalink" title="Copy record link" aria-label="Copy record link">${icons('copy')}</button></div><span id="em-copy-status" class="em-copy-status" role="status" aria-live="polite"></span>`;
      if (state.detailLoading) return close + '<div class="em-detail-loading" role="status">Loading retained evidence...</div>';
      if (state.detailError) return close + `<p role="alert" class="target-error">${esc(state.detailError)}</p><button type="button" id="em-retry-detail">Retry</button>`;
      const data = selection.data;
      if (!data) return close;
      if (selection.kind === 'pattern') {
        const pattern = data.pattern;
        const members = list(data.cases).filter(c=>!state.scope || c.scope?.cluster === state.scope);
        const group = groupFor(pattern);
        const first=pattern.first_seen_at || pattern.first_seen, last=pattern.last_seen_at || pattern.last_seen;
        return close + `<div class="em-selected-heading"><span class="em-domain-label" style="--node-color:${group.color}">${esc(group.label)}</span><h2>${esc(patternTitle(pattern))}</h2><div class="em-value">${esc(String(pattern.value))}${pattern.unit ? ' ' + esc(pattern.unit) : ''}</div><code>${esc(pattern.key)}</code></div><div class="em-stat-pair"><div><strong>${esc(pattern.case_count ?? members.length)}</strong><span>Cases across memory</span></div><div><strong>${esc(pattern.instance_count ?? '--')}</strong><span>Contributing instances</span></div></div><div class="em-panel-section"><h3>What connects these cases</h3><p>Each retained case contains <strong>${esc(pattern.key)} = ${esc(String(pattern.value))}</strong>${pattern.unit ? ' ' + esc(pattern.unit) : ''}. This is a repeated observation, not a confirmed cause.</p></div>${first || last ? `<div class="em-time-span"><span>First observed<strong>${esc(dateText(first))}</strong></span><span>Last observed<strong>${esc(dateText(last))}</strong></span></div>` : ''}<div class="em-panel-section"><h3>Linked cases <span>${members.length} loaded${state.scope ? ' in cluster' : ''}</span></h3>${members.map(caseButton).join('') || '<p class="em-muted">No linked cases returned for this view.</p>'}${data.has_more ? '<p class="em-muted">Recent members shown. More cases are retained by Collective.</p>' : ''}</div>`;
      }
      const record = data.case;
      const matches = graph.edges.filter(e=>e.source==='c:'+identifier(record));
      const facts = list(record.observations), hypotheses=list(record.hypotheses);
      return close + `<div class="em-selected-heading"><span class="em-overline">RETAINED CASE</span><h2>${esc(caseTitle(record))}</h2><p class="em-case-location">${esc([record.scope?.cluster,record.scope?.namespace,record.scope?.service || record.scope?.workload].filter(Boolean).join(' / '))}</p><p>${esc(record.summary)}</p><div class="em-record-meta">${esc(dateText(record.observed_at))}<br>${esc(record.instance_id)}</div></div><div class="em-panel-section"><h3>Shared observations <span>${matches.length} in this map</span></h3>${matches.map(e=>{const p=graph.positions.get(e.target).data;return `<button type="button" class="em-observation-link" data-node="${esc(e.target)}"><span>${esc(patternTitle(p))}<small>${esc(String(p.value))}</small></span>${icons('chevron-right')}</button>`;}).join('') || '<p class="em-muted">No matching observations in the loaded patterns.</p>'}</div><details class="em-disclosure" open><summary>Retained observations <span>${facts.length}</span></summary><ul class="em-facts">${facts.map(f=>`<li><span>${esc(human(f.key))}</span><strong>${esc(typeof f.value === 'object' ? JSON.stringify(f.value) : f.value)}${f.unit ? ' ' + esc(f.unit) : ''}</strong>${f.source || f.reference || f.observed_at ? `<small>${esc([f.source,f.reference,f.observed_at && 'Observed ' + dateText(f.observed_at)].filter(Boolean).join(' · '))}</small>` : ''}</li>`).join('')}</ul></details><details class="em-disclosure"><summary>Unverified hypotheses <span>${hypotheses.length}</span></summary>${hypotheses.map(h=>`<p>${esc(h.statement)}</p>${h.supporting_refs?.length ? `<small>References: ${esc(h.supporting_refs.join(', '))}</small>` : ''}`).join('') || '<p>No hypothesis was retained.</p>'}</details><details class="em-disclosure"><summary>Provenance</summary><dl class="em-provenance"><dt>Instance</dt><dd>${esc(record.instance_id)}</dd><dt>Episode</dt><dd>${esc(record.episode_id)}</dd><dt>Record</dt><dd>${esc(identifier(record))}</dd><dt>Revision</dt><dd>${esc(record.revision)}</dd>${Object.entries(record.scope || {}).filter(([,v])=>v!=null).map(([k,v])=>`<dt>${esc(human(k))}</dt><dd>${esc(v)}</dd>`).join('')}</dl></details>`;
    }
    function renderPanel() {
      const panel=root.querySelector('#em-panel');
      if (!panel) return;
      panel.innerHTML=panelContent();
      highlight();
    }
    function footer() {
      const records=uniqueCases(state.cases);
      const instances=[...new Set(records.map(c=>c.instance_id).filter(Boolean))].sort();
      const visible=graph.cases.map(c=>Date.parse(c.observed_at)).filter(Number.isFinite);
      const text=state.time===100 ? 'All loaded events' : visible.length ? 'Events through ' + dateText(Math.max(...visible)) : 'No dated events';
      return `<div class="em-explore-controls"><div class="em-time-control"><label for="em-time">${esc(text)}</label><input type="range" id="em-time" min="0" max="100" value="${state.time}" step="1" ${records.length<2 ? 'disabled' : ''} aria-label="Filter loaded cases by event time"></div><div class="em-instance-control"><label for="em-instance">Contribution</label><select id="em-instance"><option value="">All instances</option>${instances.map(i=>`<option value="${esc(i)}" ${state.instance===i ? 'selected' : ''}>${esc(i)}</option>`).join('')}</select></div></div>`;
    }
    function statusText() {
      if (state.phase==='loading') return 'Connecting to Collective...';
      if (state.phase==='connections') return `Loading connections · ${state.loaded} / ${state.patterns.length} patterns`;
      if (state.message) return state.message;
      return `${count(graph.edges.length,'connection')} in this view · Shared observations, not causal links${state.partial ? ' · Some records could not be loaded; refresh to retry.' : ''}`;
    }
    function render() {
      if(disposed) return;
      graph=makeGraph();
      root.innerHTML=`<div class="em-explorer">${header()}${toolbar()}<div class="em-workspace"><div class="em-main">${state.mode==='map' ? mapMarkup() : listMarkup()}${state.mode==='map' ? footer() : ''}<div class="em-status" role="status" aria-live="polite">${esc(statusText())}</div></div><aside id="em-panel" class="em-panel" aria-label="Memory details">${panelContent()}</aside></div></div>`;
      wireControls(); highlight(); transform();
    }
    function transform() {
      const element=root.querySelector('#em-viewport');
      element?.setAttribute('transform',`translate(${500*(1-state.zoom)+state.pan.x} ${350*(1-state.zoom)+state.pan.y}) scale(${state.zoom})`);
    }
    function highlight(hovered) {
      const id=hovered || (state.selected ? (state.selected.kind==='pattern' ? 'p:' : 'c:')+state.selected.id : null);
      const linked=new Set(id ? [id] : []);
      for(const edge of graph.edges) if(edge.source===id || edge.target===id) {linked.add(edge.source);linked.add(edge.target);}
      root.querySelectorAll('.em-node').forEach(el=>{el.classList.toggle('is-muted',Boolean(id)&&!linked.has(el.dataset.node));el.classList.toggle('is-selected',el.dataset.node===id);el.setAttribute('aria-pressed',String(el.dataset.node===id));});
      root.querySelectorAll('.em-edge').forEach(el=>{el.classList.toggle('is-active',el.dataset.source===id||el.dataset.target===id);el.classList.toggle('is-muted',Boolean(id)&&el.dataset.source!==id&&el.dataset.target!==id);});
    }
    async function selectNode(kind,id,updateUrl=true) {
      const sequence=++detailSequence;
      state.selected={kind,id,data:null};state.detailLoading=true;state.detailError='';
      if(updateUrl) history.replaceState(null,'',options.url(kind,id));
      renderPanel();
      root.querySelector('#em-back')?.focus({preventScroll:true});
      if (matchMedia('(max-width: 900px)').matches) root.querySelector('#em-panel')?.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',block:'start'});
      try {
        let data;
        if(kind==='pattern' && state.cache.has(id)) data=state.cache.get(id);
        else data=await request(`/api/collective/${kind==='pattern' ? 'patterns' : 'cases'}/${encodeURIComponent(id)}`);
        if(disposed||sequence!==detailSequence) return;
        state.selected.data=data;
        if(kind==='pattern') {
          state.cache.set(id,data);
          if(!state.patterns.some(p=>identifier(p)===id)) state.patterns.push(data.pattern);
          state.cases.push(...list(data.cases).filter(c=>!state.scope||c.scope?.cluster===state.scope));
        } else if(data.case) state.cases.push(data.case);
        state.detailLoading=false;render();root.querySelector('#em-back')?.focus({preventScroll:true});
      } catch(error) {
        if(disposed||sequence!==detailSequence) return;
        state.detailLoading=false;state.detailError=error.name==='AbortError' ? 'The request timed out. Retry to load this record.' : error.message;renderPanel();
      }
    }
    function clearSelection() {
      detailSequence++;state.selected=null;state.detailLoading=false;state.detailError='';
      options.selectedPattern='';options.selectedCase='';
      history.replaceState(null,'',options.url());renderPanel();
    }
    function wireControls() {
      root.querySelector('#em-refresh').onclick=()=>load();
      root.querySelector('#em-search').onsubmit=event=>{
        event.preventDefault();state.query=root.querySelector('#em-query').value.trim();state.scope=root.querySelector('#em-scope').value.trim();
        options.setSearch(state.query,state.scope);clearSelection();state.instance='';state.time=100;load();
      };
      for(const mode of ['map','list']) root.querySelector(`#em-${mode}-mode`).onclick=()=>{state.mode=mode;render();root.querySelector(`#em-${mode}-mode`).focus();};
      root.querySelector('#em-time')?.addEventListener('change',event=>{
        const value=Number(event.target.value);state.time=value;clearSelection();render();root.querySelector('#em-time').focus({preventScroll:true});
      });
      root.querySelector('#em-instance')?.addEventListener('change',event=>{state.instance=event.target.value;clearSelection();render();root.querySelector('#em-instance').focus({preventScroll:true});});
      root.querySelector('#em-load-more')?.addEventListener('click',loadMoreCases);
      root.querySelector('#em-zoom-in')?.addEventListener('click',()=>{state.zoom=Math.min(3,state.zoom+.25);transform();});
      root.querySelector('#em-zoom-out')?.addEventListener('click',()=>{state.zoom=Math.max(.75,state.zoom-.25);transform();});
      root.querySelector('#em-fit')?.addEventListener('click',()=>{state.zoom=1;state.pan={x:0,y:0};transform();});
      const svg=root.querySelector('#em-network');
      if(svg) {
        let drag=null;
        svg.addEventListener('pointerdown',event=>{if(event.target.closest('[data-node]')) return;drag={x:event.clientX,y:event.clientY,pan:{...state.pan}};svg.setPointerCapture(event.pointerId);});
        svg.addEventListener('pointermove',event=>{if(!drag) return;const scale=1000/svg.getBoundingClientRect().width;state.pan={x:Math.max(-900,Math.min(900,drag.pan.x+(event.clientX-drag.x)*scale)),y:Math.max(-650,Math.min(650,drag.pan.y+(event.clientY-drag.y)*scale))};transform();});
        svg.addEventListener('pointerup',()=>{drag=null;});svg.addEventListener('pointercancel',()=>{drag=null;});
      }
    }
    function click(event) {
      const node=event.target.closest('[data-node]');
      if(node) {const key=node.dataset.node;selectNode(key.startsWith('p:') ? 'pattern' : 'case',key.slice(2));}
      if(event.target.closest('#em-back')) {clearSelection();root.querySelector('#em-map-mode')?.focus({preventScroll:true});}
      if(event.target.closest('#em-retry-detail') && state.selected) selectNode(state.selected.kind,state.selected.id,false);
      if(event.target.closest('#em-copy-link') && state.selected) {
        const url=new URL(options.url(state.selected.kind,state.selected.id),location.origin).href;
        const button=root.querySelector('#em-copy-link');
        const feedback=root.querySelector('#em-copy-status');
        Promise.resolve().then(()=>navigator.clipboard.writeText(url)).then(()=>{
          button.title='Link copied';feedback.textContent='Link copied';
        }).catch(()=>{feedback.textContent='Copy this page\'s URL from your address bar.';});
      }
    }
    function keydown(event) {
      if(event.key==='Escape') {clearSelection();root.querySelector('#em-map-mode')?.focus({preventScroll:true});}
      const node=event.target.closest('.em-node');
      if(node && ['Enter',' '].includes(event.key)) {event.preventDefault();node.dispatchEvent(new MouseEvent('click',{bubbles:true}));}
    }
    function hover(event) {const node=event.target.closest('.em-node');if(node) highlight(node.dataset.node);}
    function out(event) {if(event.target.closest('.em-node')) highlight();}
    root.addEventListener('click',click);root.addEventListener('keydown',keydown);root.addEventListener('mouseover',hover);root.addEventListener('mouseout',out);
    root.addEventListener('focusin',hover);root.addEventListener('focusout',out);
    async function loadMoreCases() {
      if(state.browseLoading || !state.nextCursor) return;
      const epoch=state.epoch;
      const cursor=state.nextCursor;
      state.browseLoading=true;state.browseError='';render();
      const params=new URLSearchParams({limit:'20',cursor});
      if(state.query) params.set('query',state.query);
      if(state.scope) params.set('cluster',state.scope);
      try {
        const page=await request('/api/collective/cases?'+params);
        if(disposed || epoch!==state.epoch) return;
        state.browseCases=uniqueCases([...state.browseCases,...list(page.cases)]);
        state.nextCursor=page.next_cursor || null;
      } catch(error) {
        if(disposed || epoch!==state.epoch) return;
        state.browseError=error.name==='AbortError' ? 'The case list timed out. Retry.' : error.message;
      }
      state.browseLoading=false;render();root.querySelector('#em-load-more')?.focus({preventScroll:true});
    }
    async function load() {
      const epoch=++state.epoch;
      detailSequence++;
      for(const c of controllers) c.abort();
      state.detailLoading=Boolean(state.selected);state.detailError='';
      if(state.selected) state.selected.data=null;
      state.phase='loading';state.message='';state.partial=0;state.loaded=0;state.patterns=[];state.cases=[];
      state.browseCases=[];state.nextCursor=null;state.stats=null;state.statsUnavailable=false;
      state.browseUnavailable=false;state.browseLoading=false;state.browseError='';state.cache.clear();render();
      const params=new URLSearchParams({limit:'20'});
      if(state.query) params.set('query',state.query);if(state.scope) params.set('scope',state.scope);
      const caseParams=new URLSearchParams({limit:'20'});
      if(state.query) caseParams.set('query',state.query);
      if(state.scope) caseParams.set('cluster',state.scope);
      const results=await Promise.allSettled([
        request('/api/collective/patterns?'+params),
        state.query ? request('/api/collective/search',{query:state.query,scope:state.scope ? {cluster:state.scope} : null,limit:10}) : Promise.resolve({cases:[]}),
        request('/api/collective/stats'),
        request('/api/collective/cases?'+caseParams),
      ]);
      if(disposed||epoch!==state.epoch) return;
      if(results[0].status==='fulfilled') state.patterns=list(results[0].value.patterns);
      if(results[1].status==='fulfilled') state.cases=list(results[1].value.cases);
      if(results[2].status==='fulfilled') state.stats=results[2].value;
      else state.statsUnavailable=true;
      if(results[3].status==='fulfilled') {
        state.browseCases=list(results[3].value.cases);
        state.nextCursor=results[3].value.next_cursor || null;
      } else state.browseUnavailable=true;
      const failed=results.slice(0,2).filter(r=>r.status==='rejected');
      if(failed.length) {
        state.message=failed.map(r=>r.reason.name==='AbortError' ? 'Collective request timed out. Refresh to retry.' : r.reason.message).join(' ');
        state.partial=failed.length;
        if(!state.patterns.length&&!state.cases.length&&!state.browseCases.length) {state.phase='error';render();return;}
      }
      state.phase=state.patterns.length ? 'connections' : 'ready';render();
      let cursor=0;
      // Bounded, read-only expansion of existing endpoints; no global totals are inferred.
      async function worker() {
        while(cursor<state.patterns.length && epoch===state.epoch && !disposed) {
          const pattern=state.patterns[cursor++];
          try {
            const data=await request('/api/collective/patterns/'+encodeURIComponent(identifier(pattern)));
            if(disposed||epoch!==state.epoch) return;
            state.cache.set(identifier(pattern),data);
            state.cases.push(...list(data.cases).filter(c=>!state.scope||c.scope?.cluster===state.scope));
          } catch(error) {if(disposed||epoch!==state.epoch) return;state.partial++;}
          state.loaded++;
          const status=root.querySelector('.em-status');if(status) status.textContent=statusText();
        }
      }
      await Promise.all([worker(),worker(),worker()]);
      if(disposed||epoch!==state.epoch) return;
      state.phase='ready';render();
      const initial=state.selected || (options.selectedPattern ? {kind:'pattern',id:options.selectedPattern} : options.selectedCase ? {kind:'case',id:options.selectedCase} : null);
      options.selectedPattern='';options.selectedCase='';
      if(initial) await selectNode(initial.kind,initial.id,false);
    }
    return {load,dispose(){disposed=true;state.epoch++;detailSequence++;for(const c of controllers)c.abort();root.removeEventListener('click',click);root.removeEventListener('keydown',keydown);root.removeEventListener('mouseover',hover);root.removeEventListener('mouseout',out);root.removeEventListener('focusin',hover);root.removeEventListener('focusout',out);}};
  }
  const api={start,buildGraph,positionGraph,uniqueCases,scalarKey,groupFor,patternTitle,caseTitle,labelLines};
  if(typeof module!=='undefined' && module.exports) module.exports=api;
  else global.EstimaExplorer=api;
})(typeof window!=='undefined' ? window : globalThis);
