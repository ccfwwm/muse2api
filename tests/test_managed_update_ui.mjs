import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const html = readFileSync(new URL('../admin.html', import.meta.url), 'utf8');
const source = html.slice(html.indexOf('function showManagedUpdate('), html.indexOf('function toggleRepoPanel('));
const elements = new Map();
const element = () => ({style:{}, children:[], setAttribute(){}, append(item){this.children.push(item);}});
for (const id of ['btnRepoPull', 'btnBannerUpgrade', 'updateBanner']) elements.set(id, element());
elements.get('updateBanner').insertAdjacentElement = (_, item) => elements.set(item.id, item);
let nextStatus, timer, reloads = 0, removals = 0;
const context = vm.createContext({
  $: id => elements.get(id),
  document: {createElement: element},
  _managedUpdateTimer: null,
  clearTimeout(){},
  setTimeout: callback => {timer=callback; return 1;},
  api: async () => nextStatus,
  localStorage: {removeItem(){removals++;}},
  location: {reload(){reloads++;}},
  toast(){},
});
vm.runInContext(source, context);
nextStatus = {request_id:'test', state:'building', message:'Building image', workflow_url:'javascript:alert(1)'};
await context.pollManagedUpdate('test');
assert.equal(reloads, 0, 'queued builds must not reload as if already deployed');
assert.equal(elements.get('btnRepoPull').disabled, true);
assert.equal(elements.get('managedUpdateStatus').children.length, 0, 'unsafe status links must be rejected');
nextStatus = {request_id:'test', state:'failed', message:'Build failed'};
await context.pollManagedUpdate('test');
assert.equal(elements.get('btnRepoPull').disabled, false);
assert.equal(reloads, 0, 'failed updates must remain visible');
assert.equal(removals, 1);
nextStatus = {request_id:'test', state:'completed', message:'Healthy deployment'};
await context.pollManagedUpdate('test');
assert.equal(reloads, 0);
timer();
assert.equal(reloads, 1, 'reload only after host confirms deployment health');
console.log('PASS managed update UI: pending, failure, confirmed completion and safe links');
