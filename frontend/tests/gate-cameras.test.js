const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

test('both lanes keep live preview while a captured image arrives before OCR', async () => {
  const panels = {}, requests = [], revoked = [], cleared = [];
  let previewTick, releaseOcr, captureCount = 0, captureDone = 0, nextBlob = 0;
  const context = {
    window: {},
    URL: Object.assign(URL, {
      createObjectURL: () => `blob:${++nextBlob}`,
      revokeObjectURL: url => revoked.push(url),
    }),
    document: {hidden: false, getElementById: id => panels[id]},
    setInterval: callback => { previewTick = callback; return 1; },
    clearInterval: id => cleared.push(id),
    fetch: async (url, options) => {
      const target = String(url);
      requests.push({target, options});
      assert.equal(options.headers.Authorization, 'Bearer token-test');
      assert(!target.includes('token='));
      if (target.includes('/alpr/capture')) {
        captureCount++;
        const body = JSON.parse(options.body);
        if (captureCount === 1) await new Promise(resolve => { releaseOcr = resolve; });
        return {ok: true, status: 200, json: async () => ({...body, image_url: '/api/v1/alpr/images/new.jpg'})};
      }
      return {ok: true, status: 200, blob: async () => Buffer.from('preview')};
    },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../js/gate-cameras.js'), 'utf8'), context);
  const cameras = context.window.GateCameras;
  for (const lane of ['IN', 'OUT']) {
    const html = cameras.view(lane);
    assert.doesNotMatch(html, /Mã phiên đã tạo|Dùng ảnh cho phiên|Tiếp tục quay cam|camera-result/);
    const nodes = {};
    for (const [, kind, name] of html.matchAll(/data-(role|action)="([^"]+)"/g)) {
      nodes[`[data-${kind}="${name}"]`] = {hidden: false, disabled: false, textContent: '', removeAttribute() {}};
    }
    nodes['.camera-placeholder'] = {hidden: false, textContent: ''};
    panels[`gate-camera-${lane}`] = {querySelector: selector => nodes[selector] || null};
  }
  cameras.mount({apiBase: 'http://localhost/api/v1', session: {access_token: 'token-test'}, onCapture: () => captureDone++});
  await new Promise(resolve => setImmediate(resolve));
  assert(requests.some(x => x.target.includes('preview?lane=IN')));
  assert(requests.some(x => x.target.includes('preview?lane=OUT')));

  const entry = panels['gate-camera-IN'];
  const capture = entry.querySelector('[data-action="capture"]');
  assert.equal(capture.disabled, true);
  cameras.setSession('IN', 'SES_1', null, null, null, 'ENTRY_PENDING');
  assert.equal(capture.disabled, false);
  const pending = capture.onclick();
  await new Promise(resolve => setImmediate(resolve));
  cameras.setSession('IN', 'SES_1', '/api/v1/alpr/images/new.jpg', null, null, 'ENTRY_PENDING');
  assert.equal(capture.textContent, 'Chụp lại');
  const previewsBefore = requests.filter(x => x.target.includes('/alpr/preview')).length;
  previewTick();
  await new Promise(resolve => setImmediate(resolve));
  assert(requests.filter(x => x.target.includes('/alpr/preview')).length > previewsBefore);
  assert.equal(entry.querySelector('[data-role="preview"]').hidden, false);
  releaseOcr();
  await pending;
  assert.equal(captureDone, 1);
  assert.equal(capture.disabled, false);
  await capture.onclick();
  assert.equal(captureCount, 2);
  cameras.setSession('OUT', 'SES_1', '/api/v1/alpr/images/out.jpg', '30A12345', null, 'PENDING_REVIEW');
  const exitCapture = panels['gate-camera-OUT'].querySelector('[data-action="capture"]');
  assert.equal(exitCapture.textContent, 'Chụp lại');
  assert.equal(exitCapture.disabled, false);
  await exitCapture.onclick();
  assert.equal(captureCount, 3);
  cameras.unmount();
  assert(cleared.includes(1));
  assert(revoked.some(url => url.startsWith('blob:')));
});
