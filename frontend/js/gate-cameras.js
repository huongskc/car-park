'use strict';
// AI service owns the webcam; this panel shows live preview and requests captures.
window.GateCameras = (() => {
  const state = {IN:{}, OUT:{}};
  let config = null, previewTimer = null;
  const panel = lane => document.getElementById('gate-camera-' + lane);

  function view(lane) {
    return `<article id="gate-camera-${lane}" class="card camera-card"><div class="camera-heading"><h3>Làn ${lane==='IN'?'vào':'ra'}</h3><span data-role="session" class="mt"></span></div>
      <div class="camera-screen"><img data-role="preview" class="camera-preview" alt="Camera làn ${lane==='IN'?'vào':'ra'}" hidden><div class="camera-placeholder">Đang kết nối camera…</div></div>
      <div class="camera-toolbar"><button type="button" class="sec sm" data-action="capture" disabled>Chụp ảnh</button><span data-role="status" role="status" class="camera-status"></span></div></article>`;
  }

  function paint(lane) {
    const p = panel(lane), s = state[lane];
    if (!p) return;
    p.querySelector('[data-role="session"]').textContent = s.session || '';
    const button = p.querySelector('[data-action="capture"]');
    button.textContent = s.imageUrl ? 'Chụp lại' : 'Chụp ảnh';
    button.disabled = !!s.busy || !s.session || (lane==='IN' ? s.status!=='ENTRY_PENDING' : !['ACTIVE','PENDING_REVIEW'].includes(s.status));
    p.querySelector('[data-role="status"]').textContent = s.message || '';
    p.querySelector('[data-role="preview"]').hidden = !s.previewUrl;
    p.querySelector('.camera-placeholder').hidden = !!s.previewUrl;
  }

  function setSession(lane, sessionCode, imageUrl, _ocrPlate, _confidence, status) {
    const s = state[lane];
    if (s.session !== sessionCode) {
      s.captureSeq = (s.captureSeq || 0) + 1;
      s.busy = false;
      s.message = '';
    }
    s.session = sessionCode;
    s.status = status;
    s.imageUrl = imageUrl;
    paint(lane);
  }

  function clearSession(lane) {
    const s = state[lane];
    if (!s.session) return;
    s.captureSeq = (s.captureSeq || 0) + 1;
    s.session = null;
    s.status = null;
    s.imageUrl = null;
    s.busy = false;
    s.message = '';
    paint(lane);
  }

  async function capture(lane) {
    const s = state[lane], session = s.session;
    if (s.busy || !session || (lane==='IN' ? s.status!=='ENTRY_PENDING' : !['ACTIVE','PENDING_REVIEW'].includes(s.status))) return;
    const seq = s.captureSeq;
    s.busy = true;
    s.message = 'Đang chụp và nhận diện…';
    paint(lane);
    try {
      const base = config.apiBase.replace(/\/$/, '');
      const response = await fetch(base+'/alpr/capture', {method:'POST', headers:{Authorization:'Bearer '+config.session.access_token,'Content-Type':'application/json'}, body:JSON.stringify({lane,session_id:session})});
      if (response.status===401) { config.onUnauthorized?.(); return; }
      const result = await response.json();
      if (s.captureSeq!==seq || s.session!==session) return;
      if (!response.ok) throw new Error(typeof result.detail==='string' ? result.detail : 'Không chụp được ảnh.');
      if (result.lane!==lane || result.session_id!==session) throw new Error('Ảnh không khớp phiên hoặc làn.');
      s.message = 'Đã cập nhật ảnh.';
      config.onCapture?.();
    } catch (error) {
      if (s.captureSeq===seq && s.session===session) s.message = error.message || 'Không kết nối được camera.';
    } finally {
      if (s.captureSeq===seq && s.session===session) { s.busy = false; paint(lane); }
    }
  }

  async function refreshPreview(lane) {
    const s = state[lane];
    if (!config || s.previewBusy || document.hidden) return;
    const p = panel(lane);
    if (!p) return;
    s.previewBusy = true;
    const controller = typeof AbortController!=='undefined' ? new AbortController() : null;
    const timeoutId = controller ? setTimeout(() => controller.abort(), 2000) : null;
    try {
      const base = config.apiBase.replace(/\/$/, '');
      const response = await fetch(base+'/alpr/preview?lane='+lane, {headers:{Authorization:'Bearer '+config.session.access_token},cache:'no-store',...(controller?{signal:controller.signal}:{})});
      if (response.status===401) { config.onUnauthorized?.(); return; }
      if (!response.ok) throw new Error('preview');
      const blob = await response.blob();
      if (state[lane]!==s || !config) return;
      const url = URL.createObjectURL(blob);
      const img = p.querySelector('[data-role="preview"]'), oldUrl = s.previewUrl;
      s.previewUrl = url;
      img.onload = img.onerror = () => { if (oldUrl) URL.revokeObjectURL(oldUrl); };
      img.src = url;
      img.hidden = false;
      p.querySelector('.camera-placeholder').hidden = true;
    } catch (_) {
      if (!s.previewUrl) p.querySelector('.camera-placeholder').textContent = 'Không xem được camera.';
    } finally {
      if (timeoutId) clearTimeout(timeoutId);
      s.previewBusy = false;
    }
  }

  function mount(options) {
    if (config) unmount();
    config = options;
    for (const lane of ['IN','OUT']) {
      const p = panel(lane);
      if (!p) continue;
      p.querySelector('[data-action="capture"]').onclick = () => capture(lane);
      paint(lane);
      refreshPreview(lane);
    }
    previewTimer = setInterval(() => { refreshPreview('IN'); refreshPreview('OUT'); }, 350);
  }

  function unmount() {
    if (previewTimer) clearInterval(previewTimer);
    previewTimer = null;
    for (const lane of ['IN','OUT']) {
      state[lane].captureSeq = (state[lane].captureSeq || 0) + 1;
      if (state[lane].previewUrl) URL.revokeObjectURL(state[lane].previewUrl);
      state[lane] = {};
    }
    config = null;
  }

  return {view,mount,unmount,setSession,clearSession};
})();
