const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

test('camera panels mount, preview both lanes, capture and clear a session image', async () => {
  const panels = {};
  const revoked = [];
  const requests = [];
  let nextBlob = 0;
  let pauseCapture = false, releaseCapture, failCapture = false;
  const context = {
    window: {},
    URL: Object.assign(URL, {
      createObjectURL: () => `blob:${++nextBlob}`,
      revokeObjectURL: url => revoked.push(url),
    }),
    document: { getElementById: id => panels[id] },
    setInterval: () => 1,
    clearInterval: () => {},
    fetch: async (url, options) => {
      const target = String(url);
      requests.push(target);
      if (target.includes('/alpr/capture')) {
        if (pauseCapture) await new Promise(resolve => { releaseCapture = resolve; });
        if (failCapture) return { ok: false, status: 503, json: async () => ({detail: 'OCR timeout'}) };
        const body = JSON.parse(options.body);
        return { ok: true, status: 200, json: async () => ({
          ...body, plate_number: '30A12345', needs_confirmation: true,
          confidence: 0.9, image_url: '/api/v1/alpr/images/demo_12345678.jpg',
        }) };
      }
      return { ok: true, status: 200, blob: async () => Buffer.from('demo') };
    },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../js/gate-cameras.js'), 'utf8'), context);
  const cameras = context.window.GateCameras;
  for (const lane of ['IN', 'OUT']) {
    const html = cameras.view(lane);
    const nodes = {};
    for (const [, kind, name] of html.matchAll(/data-(role|action)="([^"]+)"/g)) {
      nodes[`[data-${kind}="${name}"]`] = { hidden: false, disabled: false, value: '', style: {}, removeAttribute() {} };
    }
    nodes['.camera-placeholder'] = { hidden: false, textContent: '' };
    panels[`gate-camera-${lane}`] = { querySelector: selector => nodes[selector] || null };
  }
  cameras.mount({ apiBase: 'http://localhost/api/v1', session: { access_token: 'demo' } });
  await new Promise(resolve => setImmediate(resolve));
  assert(requests.some(url => url.includes('preview?lane=IN')));
  assert(requests.some(url => url.includes('preview?lane=OUT')));
  assert.equal(panels['gate-camera-OUT'].querySelector('[data-action="capture"]').disabled, true);

  const panel = panels['gate-camera-IN'];
  const session = panel.querySelector('[data-role="session"]');
  session.value = 'SES_1';
  session.oninput();
  await panel.querySelector('[data-action="capture"]').onclick();
  assert.equal(panel.querySelector('[data-role="plate"]').textContent, '30A12345');
  assert.equal(panel.querySelector('[data-action="use"]').disabled, false);
  assert.equal(panel.querySelector('[data-role="image"]').hidden, false);
  session.value = 'SES_2';
  session.oninput();
  assert.equal(panel.querySelector('[data-action="use"]').disabled, true);
  assert.equal(panel.querySelector('[data-role="plate"]').textContent, 'Chưa chụp ảnh');
  assert(revoked.some(url => url.startsWith('blob:')));
  await cameras.setSession('OUT', 'SES_EXIT', '/api/v1/alpr/images/out.jpg', '30A12345', 0.9, 'ACTIVE');
  const exitPanel = panels['gate-camera-OUT'];
  assert.equal(exitPanel.querySelector('[data-action="capture"]').disabled, false);
  await cameras.setSession('OUT', 'SES_EXIT', '/api/v1/alpr/images/out.jpg', '30A12345', 0.9, 'PENDING_REVIEW');
  assert.equal(exitPanel.querySelector('[data-action="capture"]').disabled, true);
  assert.equal(exitPanel.querySelector('[data-action="use"]').disabled, false);
  assert.equal(exitPanel.querySelector('[data-action="use"]').textContent, 'Xác nhận biển ra từ ảnh');
  assert.equal(requests.filter(url => url.includes('out.jpg')).length, 1);
  await cameras.setSession('IN', 'SES_RACE', '/api/v1/alpr/images/old.jpg', 'OLD_PLATE', 0.1, 'ENTRY_PENDING');
  pauseCapture = true;
  const pendingCapture = panel.querySelector('[data-action="capture"]').onclick();
  await new Promise(resolve => setImmediate(resolve));
  await cameras.setSession('IN', 'SES_RACE', '/api/v1/alpr/images/old.jpg', 'OLD_PLATE', 0.1, 'ENTRY_PENDING');
  assert.equal(panel.querySelector('[data-role="image"]').hidden, true);
  assert.equal(requests.filter(url => url.includes('old.jpg')).length, 1);
  await cameras.setSession('IN', 'SES_RACE', '/api/v1/alpr/images/demo_12345678.jpg', '30A12345', 0.1, 'ENTRY_PENDING');
  const imageLoads=requests.filter(url => url.includes('/alpr/images/demo_12345678.jpg')).length;
  releaseCapture();
  await pendingCapture;
  pauseCapture = false;
  assert.equal(requests.filter(url => url.includes('/alpr/images/demo_12345678.jpg')).length, imageLoads);
  assert.equal(panel.querySelector('[data-role="details"]').textContent, ' · Độ tin cậy OCR: 90%');
  assert.equal(panel.querySelector('[data-role="image"]').hidden, false);
  await cameras.setSession('IN', 'SES_FAILED', '/api/v1/alpr/images/prior.jpg', 'PRIOR', 0.5, 'ENTRY_PENDING');
  failCapture = true;
  await panel.querySelector('[data-action="capture"]').onclick();
  await cameras.setSession('IN', 'SES_FAILED', '/api/v1/alpr/images/prior.jpg', 'PRIOR', 0.5, 'ENTRY_PENDING');
  assert.equal(panel.querySelector('[data-role="image"]').hidden, true);
  assert.equal(panel.querySelector('[data-role="status"]').textContent, 'OCR timeout');
  await cameras.setSession('IN', 'SES_FAILED', '/api/v1/alpr/images/fresh.jpg', null, null, 'ENTRY_PENDING');
  assert.equal(panel.querySelector('[data-role="image"]').hidden, false);
  cameras.clearSession('OUT');
  assert.equal(exitPanel.querySelector('[data-role="session"]').value, '');
  assert.equal(exitPanel.querySelector('[data-action="use"]').disabled, true);
  cameras.unmount();
});
