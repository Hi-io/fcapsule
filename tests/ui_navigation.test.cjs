const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '../fcapsule/ui/assets/app.js'), 'utf8');
const setup = source.slice(0, source.indexOf('let lastState = null;'));

function navigation(pathname = '/console') {
  const listeners = {};
  const frames = [];
  const assigned = [];
  const classes = new Set();
  const linkClasses = new Set();
  const attributes = new Map();
  const link = {
    href:'https://fcapsule.test/targets', target:'',
    hasAttribute:()=>false,
    classList:{add:name=>linkClasses.add(name),remove:name=>linkClasses.delete(name)},
  };
  const app = {
    setAttribute:(name,value)=>attributes.set(name,value),
    removeAttribute:name=>attributes.delete(name),
  };
  const nav = {addEventListener:(name,callback)=>{listeners[name]=callback;}};
  const document = {
    querySelector:selector=>selector === '#app' ? app : selector === '.product-bar nav' ? nav :
      selector === '.product-bar nav .is-pending' ? link :
      selector === '#boot-label' ? {textContent:''} : selector.startsWith('[data-nav=') ? {classList:{add(){}},setAttribute(){}} : null,
    body:{dataset:{},classList:{add:name=>classes.add(name),remove:name=>classes.delete(name)}},
    title:'',
  };
  const location = {href:`https://fcapsule.test${pathname}`,pathname,origin:'https://fcapsule.test',assign:href=>assigned.push(href)};
  const window = {addEventListener:(name,callback)=>{listeners[name]=callback;}};
  vm.runInNewContext(setup,{document,location,window,URL,Intl,requestAnimationFrame:callback=>frames.push(callback)});
  return {listeners,frames,assigned,classes,linkClasses,attributes,link,app,document};
}

function click(fixture, overrides = {}) {
  const event = {target:{closest:()=>fixture.link},button:0,defaultPrevented:false,
    ctrlKey:false,metaKey:false,shiftKey:false,altKey:false,preventDefault(){this.defaultPrevented=true;},...overrides};
  fixture.listeners.click(event);
  return event;
}

test('primary navigation signals loading before changing pages', () => {
  const fixture = navigation();
  const event = click(fixture);
  assert.equal(event.defaultPrevented,true);
  assert.ok(fixture.classes.has('navigation-pending'));
  assert.ok(fixture.linkClasses.has('is-pending'));
  assert.equal(fixture.attributes.get('aria-busy'),'true');
  assert.equal(fixture.assigned.length,0);
  fixture.frames.shift()();
  fixture.frames.shift()();
  assert.equal(fixture.assigned[0],'https://fcapsule.test/targets');
});

test('modified clicks and the current destination keep native link behavior', () => {
  for (const options of [{ctrlKey:true},{metaKey:true},{shiftKey:true},{altKey:true},{button:1}]) {
    const fixture = navigation();
    assert.equal(click(fixture,options).defaultPrevented,false);
    assert.equal(fixture.frames.length,0);
  }
  const fixture = navigation('/targets');
  assert.equal(click(fixture).defaultPrevented,false);
  assert.equal(fixture.classes.size,0);
});

test('keyboard activation and back-forward restoration remain usable', () => {
  const fixture = navigation();
  click(fixture,{detail:0});
  assert.ok(fixture.classes.has('navigation-pending'));
  fixture.listeners.pageshow({persisted:true});
  assert.equal(fixture.classes.has('navigation-pending'),false);
  assert.equal(fixture.linkClasses.has('is-pending'),false);
  assert.equal(fixture.attributes.has('aria-busy'),false);
});

test('the initial document exposes a named loading state', () => {
  const html = fs.readFileSync(path.join(__dirname, '../fcapsule/ui/app.py'), 'utf8');
  assert.match(html, /<main id="app" tabindex="-1" aria-busy="true">/);
  assert.match(html, /class="boot-heading" role="status" aria-live="polite"/);
  assert.match(html, /id="boot-label"/);
});
