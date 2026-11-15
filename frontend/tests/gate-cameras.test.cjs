const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const source = fs.readFileSync(require('node:path').join(__dirname,'../js/gate-cameras.js'),'utf8');
const flush = async () => {for (let i=0;i<12;i++) await new Promise(setImmediate);};
const frame = bytes => Buffer.concat([
  Buffer.from('--frame\r\nContent-Type: image/jpeg\r\nContent-Length: '+bytes.length+'\r\n\r\n'),
  Buffer.from(bytes),Buffer.from('\r\n')
]);
const jsonResponse = (data,status=200) => ({ok:status===200,status,json:async () => data});

function harness(factory) {
  const panels={},requests=[],timers=new Map(),blobs=new Map(),revoked=[],notifications=[];
  let timerId=0,blobId=0;
  for (const lane of ['IN','OUT']) {
    const elements=Object.fromEntries(['[data-role="session"]','[data-action="capture"]',
      '[data-role="status"]','[data-role="preview"]','.camera-placeholder'].map(key => [key,{
        setAttribute(name,value){this[name]=value;},removeAttribute(name){delete this[name];}
      }]));
    panels[lane]={elements,querySelector:key => elements[key]};
  }
  const context={window:{},document:{getElementById:id => panels[id.replace('gate-camera-','')]},
    AbortController,Blob,TextDecoder,Uint8Array,Image:class{async decode(){}},
    URL:{createObjectURL:blob => {const url='blob:'+(++blobId);blobs.set(url,blob);return url;},
      revokeObjectURL:url => revoked.push(url)},
    setTimeout:(fn,ms) => {const id=++timerId;timers.set(id,{fn,ms});return id;},
    clearTimeout:id => timers.delete(id),
    setInterval:(fn,ms) => {const id=++timerId;timers.set(id,{fn,ms,repeat:true});return id;},
    clearInterval:id => timers.delete(id),
    fetch:async (url,options) => {
      const r={url,options,chunks:[],pending:null,cancelled:false};
      r.push=value => r.pending ? (r.pending.resolve({value,done:false}),r.pending=null) : r.chunks.push(value);
      const reader={read:() => r.chunks.length ? Promise.resolve({value:r.chunks.shift(),done:false}) :
        new Promise((resolve,reject) => {r.pending={resolve,reject};}),cancel:async () => {r.cancelled=true;}};
      options.signal?.addEventListener('abort',() => {
        if(r.pending){r.pending.reject(new DOMException('Aborted','AbortError'));r.pending=null;}
      });
      requests.push(r);
      return (factory && await factory(r)) || {ok:true,status:200,
        headers:new Headers({'content-type':'multipart/x-mixed-replace; boundary=frame'}),body:{getReader:() => reader}};
    }
  };
  vm.runInNewContext(source,context);
  const cameras=context.window.GateCameras;
  const mount=() => cameras.mount({apiBase:'http://localhost:8000/api/v1',session:{access_token:'test-jwt'},onCapture:() => notifications.push('capture')});
  return {cameras,mount,panels,requests,timers,blobs,revoked,notifications};
}
async function makeLive(h){
  await flush();
  for(const r of h.requests.filter(r => r.url.includes('/stream?')))r.push(frame([255,216,255,217]));
  await flush();
}

test('keeps authenticated video connections and decodes fragmented/coalesced MJPEG',async () => {
  const h=harness();h.mount();await flush();
  assert.equal(h.requests.length,2);
  assert.ok(h.requests.every(r => r.url.includes('/alpr/stream?lane=')&&!r.url.includes('test-jwt')));
  assert.equal(h.requests[0].options.headers.Authorization,'Bearer test-jwt');
  const req=h.requests[0],jpeg=[255,216,255,217];
  for(const byte of frame(jpeg))req.push(Uint8Array.of(byte));
  await flush();
  const image=h.panels.IN.elements['[data-role="preview"]'],old=image.src;
  assert.equal(image.hidden,false);
  assert.deepEqual([...new Uint8Array(await h.blobs.get(old).arrayBuffer())],jpeg);
  req.push(Buffer.concat([frame([1,2]),frame([3,4])]));await flush();
  assert.deepEqual([...new Uint8Array(await h.blobs.get(image.src).arrayBuffer())],[3,4]);
  assert.ok(h.revoked.includes(old));
  assert.equal(h.requests.length,2);
  h.cameras.unmount();await flush();
  assert.ok(h.requests.every(r => r.options.signal.aborted&&r.cancelled));
  assert.equal(h.timers.size,0);
});

test('reports stream errors and cancels reconnect timers on navigation',async () => {
  const h=harness(() => jsonResponse({detail:'Camera chưa sẵn sàng.'},503));
  h.mount();await flush();
  assert.match(h.panels.IN.elements['.camera-placeholder'].textContent,/Camera chưa sẵn sàng/);
  assert.equal([...h.timers.values()].filter(t => t.ms===2000).length,2);
  h.cameras.unmount();assert.equal(h.timers.size,0);
});

test('rejects malformed frames and ignores late video after remount',async () => {
  const h=harness();h.mount();await flush();
  h.requests[0].push(Buffer.from('--frame\r\nContent-Length: 99999999\r\n\r\n'));await flush();
  assert.match(h.panels.IN.elements['.camera-placeholder'].textContent,/không hợp lệ/);
  const old=h.requests[1];h.mount();await flush();
  old.push(frame([9,9]));h.requests[2].push(frame([7,8]));await flush();
  const url=h.panels.IN.elements['[data-role="preview"]'].src;
  assert.deepEqual([...new Uint8Array(await h.blobs.get(url).arrayBuffer())],[7,8]);
  assert.ok(old.options.signal.aborted);
  h.cameras.unmount();await flush();assert.equal(h.timers.size,0);
});

test('live viewing never triggers OCR and capture retains the original session states',async () => {
  const h=harness(r => {
    if(r.url.endsWith('/alpr/capture'))return jsonResponse(JSON.parse(r.options.body));
  });
  h.mount();await makeLive(h);
  assert.equal(h.panels.IN.elements['[data-action="capture"]'].disabled,true);
  await h.panels.IN.elements['[data-action="capture"]'].onclick();
  assert.equal(h.requests.filter(r => r.options.method==='POST').length,0);
  assert.equal([...h.timers.values()].filter(t => t.repeat).length,0);
  h.cameras.setSession('IN','SES_entry',null,null,null,'ENTRY_PENDING');
  assert.equal(h.panels.IN.elements['[data-action="capture"]'].disabled,false);
  await h.panels.IN.elements['[data-action="capture"]'].onclick();
  const capture=h.requests.find(r => r.url.endsWith('/alpr/capture'));
  assert.deepEqual(JSON.parse(capture.options.body),{lane:'IN',session_id:'SES_entry'});
  assert.equal(h.notifications.length,1);
  h.cameras.setSession('IN','SES_entry',null,null,null,'ENTRY_ACKED_WAITING_PASS');
  assert.equal(h.panels.IN.elements['[data-action="capture"]'].disabled,true);
  h.cameras.setSession('OUT','SES_exit',null,null,null,'ACTIVE');
  assert.equal(h.panels.OUT.elements['[data-action="capture"]'].disabled,false);
  h.cameras.setSession('OUT','SES_exit',null,null,null,'PAID_WAITING_EXIT');
  assert.equal(h.panels.OUT.elements['[data-action="capture"]'].disabled,true);
  h.cameras.clearSession('OUT');
  assert.equal(h.panels.OUT.elements['[data-action="capture"]'].disabled,true);
  assert.ok(!h.requests.some(r => r.url.includes('/alpr/scan')));
  h.cameras.unmount();await flush();assert.equal(h.timers.size,0);
});

test('original capture ignores an old session result when another session is selected',async () => {
  let resolveCapture;
  const h=harness(r => r.url.endsWith('/alpr/capture') ? new Promise(resolve => {resolveCapture=resolve;}) : undefined);
  h.mount();await makeLive(h);
  h.cameras.setSession('IN','SES_old',null,null,null,'ENTRY_PENDING');
  const pending=h.panels.IN.elements['[data-action="capture"]'].onclick();await flush();
  h.cameras.setSession('IN','SES_new',null,null,null,'ENTRY_PENDING');
  resolveCapture(jsonResponse({lane:'IN',session_id:'SES_old'}));await pending;
  assert.equal(h.notifications.length,0);
  assert.equal(h.panels.IN.elements['[data-role="session"]'].textContent,'SES_new');
  assert.equal(h.panels.IN.elements['[data-role="status"]'].textContent,'');
  h.cameras.unmount();await flush();assert.equal(h.timers.size,0);
});

