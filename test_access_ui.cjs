const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function fixture(devices) {
  const nodes = {}, requests = [], dates = [];
  function node() {
    return { children: [], textContent: '', handlers: {},
      append(...values) { this.children.push(...values); },
      replaceChildren() { this.children = []; },
      addEventListener(name, handler) { this.handlers[name] = handler; } };
  }
  const context = {
    el: id => nodes[id] ||= node(),
    document: { createElement: node },
    csrf: 'fixture-csrf',
    shortTime: timestamp => { dates.push(timestamp); return 'fixture date'; },
    api: async (path, options) => { requests.push({path, options}); return {devices}; },
  };
  const source = fs.readFileSync(__dirname + '/web/app.js', 'utf8');
  vm.runInNewContext(source.slice(source.indexOf('async function showDevices(){'), source.indexOf('el("phone-setup").addEventListener')), context);
  return {context, nodes, requests, dates};
}

test('browser access shows never-expiring tokens without a 1970 date and retains finite expiry', async () => {
  const f = fixture([{id:'never', name:'My phone <script>', expires:0}, {id:'finite', name:'Tablet', expires:12345}]);
  await f.context.showDevices();
  assert.equal(f.nodes.devices.children[0].children[0].textContent, 'My phone <script> · Never expires');
  assert.equal(f.nodes.devices.children[1].children[0].textContent, 'Tablet · Expires fixture date');
  assert.deepEqual(f.dates, [12345000]);
  await f.nodes.devices.children[0].children[1].handlers.click();
  assert.equal(f.requests[1].path, '/api/devices/revoke');
  assert.equal(f.requests[1].options.headers['X-Companion-CSRF'], 'fixture-csrf');
  assert.deepEqual(JSON.parse(f.requests[1].options.body), {device_id:'never'});
});

test('empty browser access remains explicit', async () => {
  const f = fixture([]);
  await f.context.showDevices();
  assert.equal(f.nodes.devices.children[0].textContent, 'No browsers are currently paired.');
});
