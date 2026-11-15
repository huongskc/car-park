'use strict';
// AI service owns the webcam; authenticated MJPEG supplies the live preview.
window.GateCameras = (() => {
  const state = {IN:{}, OUT:{}};
  let config = null;
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

  async function startStream(lane) {
    const s = state[lane];
    const owner = config, p = panel(lane);
    if (!owner || s.controller) return;
    if (!p) return;
    const current = () => config===owner && state[lane]===s;
    const controller = new AbortController();
    s.controller = controller;
    let reader, watchdog;
    const armTimeout = () => {
      clearTimeout(watchdog);
      watchdog = setTimeout(() => controller.abort(), 8000);
    };
    try {
      armTimeout();
      const base = owner.apiBase.replace(/\/$/, '');
      const response = await fetch(base+'/alpr/stream?lane='+lane, {
        headers:{Authorization:'Bearer '+owner.session.access_token},
        cache:'no-store', signal:controller.signal
      });
      if (!current()) return;
      if (response.status===401) { owner.onUnauthorized?.(); return; }
      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(typeof error.detail==='string' ? error.detail : 'Không mở được camera (HTTP '+response.status+').');
      }
      if (!response.headers.get('content-type')?.startsWith('multipart/x-mixed-replace') || !response.body) throw new Error('Luồng camera không đúng định dạng.');
      reader = response.body.getReader();
      let buffer = new Uint8Array(0), frameLength = null;
      const decoder = new TextDecoder();
      while (current()) {
        armTimeout();
        const {value, done} = await reader.read();
        if (done) throw new Error('Luồng camera đã ngắt.');
        if (!current()) return;
        const merged = new Uint8Array(buffer.length + value.length);
        merged.set(buffer); merged.set(value, buffer.length); buffer = merged;
        if (buffer.length > 4*1024*1024+1024) throw new Error('Khung hình camera quá lớn.');
        let latestFrame;
        while (true) {
          if (frameLength===null) {
            let end = -1;
            for (let i=0; i+3<buffer.length; i++) {
              if (buffer[i]===13 && buffer[i+1]===10 && buffer[i+2]===13 && buffer[i+3]===10) { end=i; break; }
            }
            if (end<0) {
              if (buffer.length>1024) throw new Error('Header luồng camera không hợp lệ.');
              break;
            }
            const length = /(?:^|\r\n)Content-Length:\s*(\d+)\r?$/im.exec(decoder.decode(buffer.subarray(0,end)));
            frameLength = length ? Number(length[1]) : 0;
            if (!frameLength || frameLength>4*1024*1024) throw new Error('Kích thước khung hình không hợp lệ.');
            buffer = buffer.subarray(end+4);
          }
          if (buffer.length<frameLength) break;
          latestFrame = buffer.slice(0,frameLength);
          buffer = buffer.subarray(frameLength);
          frameLength = null;
        }
        if (latestFrame) {
          const url = URL.createObjectURL(new Blob([latestFrame],{type:'image/jpeg'}));
          try {
            const image = new Image();
            image.src = url;
            await image.decode();
            if (!current()) { URL.revokeObjectURL(url); return; }
            const oldUrl = s.previewUrl;
            s.previewUrl = url;
            p.querySelector('[data-role="preview"]').src = url;
            paint(lane);
            if (oldUrl) URL.revokeObjectURL(oldUrl);
          } catch (error) { URL.revokeObjectURL(url); throw error; }
        }
      }
    } catch (error) {
      if (current()) {
        if (s.previewUrl) URL.revokeObjectURL(s.previewUrl);
        s.previewUrl = null;
        paint(lane);
        p.querySelector('.camera-placeholder').textContent =
          (error.name==='AbortError' ? 'Camera không phản hồi.' : error.message || 'Không xem được camera.')+' Đang kết nối lại…';
        s.retryTimer = setTimeout(() => startStream(lane), 2000);
      }
    } finally {
      clearTimeout(watchdog);
      controller.abort();
      if (reader) { try { await reader.cancel(); } catch (_) {} }
      s.controller = null;
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
      startStream(lane);
    }
  }

  function unmount() {
    for (const lane of ['IN','OUT']) {
      clearTimeout(state[lane].retryTimer);
      state[lane].controller?.abort();
      state[lane].captureSeq = (state[lane].captureSeq || 0) + 1;
      if (state[lane].previewUrl) URL.revokeObjectURL(state[lane].previewUrl);
      state[lane] = {};
    }
    config = null;
  }

  return {view,mount,unmount,setSession,clearSession};
})();
