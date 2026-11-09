/* Live browser cameras. OCR goes through the authenticated backend. */
window.GateCameras = (() => {
  const LANES = ['IN', 'OUT'];
  const name = lane => lane === 'IN' ? 'làn vào' : 'làn ra';
  const state = Object.fromEntries(LANES.map(lane => [lane, {
    stream: null, deviceId: '', opening: false, version: 0,
    auto: false, result: null, imageUrl: '', capturedAt: null, error: ''
  }]));
  let mounted = false, generation = 0, config, devices = [], job = null, timer, nextLane = 0;
  const root = lane => document.getElementById('gate-camera-' + lane);
  const find = (lane, role) => root(lane)?.querySelector(`[data-role="${role}"]`);
  const supported = () => !!navigator.mediaDevices?.getUserMedia;
  const canRead = () => !!config?.session?.access_token && !config.session.demo;
  const remember = (lane, id) => {
    try { localStorage.setItem('gate-camera-' + lane, id); } catch { /* Storage is optional. */ }
  };

  function view(lane) {
    return `<article id="gate-camera-${lane}" class="card camera-card" aria-label="Camera ${name(lane)}">
      <div class="camera-heading"><h3>Camera ${name(lane)}</h3><span class="b mute" data-role="badge">Đã tắt</span></div>
      <label>Thiết bị camera<select data-role="source" aria-label="Chọn camera ${name(lane)}"><option value="">Camera mặc định</option></select></label>
      <div class="camera-screen">
        <video data-role="video" autoplay muted playsinline hidden aria-label="Video trực tiếp ${name(lane)}"></video>
        <div class="camera-placeholder" data-role="placeholder"><span>Camera ${name(lane)}</span><small>Chọn camera rồi nhấn Bật camera</small></div>
        <span class="camera-live" data-role="live" hidden>● TRỰC TIẾP</span>
      </div>
      <div class="row camera-controls">
        <button type="button" data-action="start" class="sec sm">Bật camera</button>
        <button type="button" data-action="stop" class="sec sm" disabled>Tắt camera</button>
        <button type="button" data-action="recognize" class="go sm" disabled>Đọc biển số</button>
      </div>
      <label class="camera-auto"><input type="checkbox" data-role="auto" disabled>Tự đọc biển số khi camera đang bật</label>
      <p class="camera-status" data-role="status" role="status" aria-live="polite">Camera chưa bật.</p>
      <div class="camera-result">
        <img data-role="image" alt="Ảnh biển số ${name(lane)}" hidden>
        <div><small>Kết quả gần nhất · ${name(lane)}</small><strong data-role="plate">Chưa đọc biển số</strong><span data-role="details">Nhấn Đọc biển số để nhận diện.</span></div>
      </div>
      ${lane === 'IN' ? '<button type="button" class="sec sm camera-use" data-action="use" disabled>Dùng biển số cho lượt vào</button>' : ''}
    </article>`;
  }

  function paint(lane) {
    if (!mounted || !root(lane)) return;
    const s = state[lane], live = !!s.stream;
    const video = find(lane, 'video');
    if (video.srcObject !== s.stream) {
      video.srcObject = s.stream;
      if (s.stream) video.play().catch(() => {});
    }
    video.hidden = !live;
    find(lane, 'placeholder').hidden = live;
    find(lane, 'live').hidden = !live;
    const badge = find(lane, 'badge');
    badge.textContent = s.opening ? 'Đang mở…' : live ? 'Đang phát' : 'Đã tắt';
    badge.className = 'b ' + (s.opening ? 'warn' : live ? 'ok' : 'mute');
    root(lane).querySelector('[data-action="start"]').disabled = s.opening || live || !supported();
    root(lane).querySelector('[data-action="stop"]').disabled = !live && !s.opening;
    root(lane).querySelector('[data-action="recognize"]').disabled = !live || s.opening || !!job || !canRead();
    find(lane, 'source').disabled = s.opening;
    const auto = find(lane, 'auto');
    auto.checked = s.auto;
    auto.disabled = !live || !canRead();
    const status = find(lane, 'status');
    status.classList.toggle('camera-error', !!s.error);
    status.textContent = s.error || (s.opening ? 'Đang mở camera…' : job?.lane === lane
      ? 'Đang đọc biển số từ khung hình vừa chụp…'
      : !supported() ? 'Trình duyệt chưa hỗ trợ camera. Hãy mở bằng Chrome/Edge tại localhost hoặc HTTPS.'
      : !canRead() ? 'Có thể xem camera thật. Nhận diện biển số cần đăng nhập backend và dịch vụ AI đang chạy.'
      : live ? 'Đối chiếu kết quả với ảnh trước khi sử dụng biển số.' : 'Camera chưa bật.');
    const r = s.result;
    find(lane, 'plate').textContent = r ? r.plate_number || 'Không nhận diện được' : 'Chưa đọc biển số';
    find(lane, 'details').textContent = r
      ? [r.plate_number ? (r.needs_confirmation !== false ? 'Cần đối chiếu ảnh' : 'Đã nhận diện') : 'Thử lại khi biển số rõ hơn',
        Number.isFinite(r.confidence) ? 'Tin cậy ' + Math.round(r.confidence * 100) + '%' : '',
        s.capturedAt ? new Date(s.capturedAt).toLocaleTimeString('vi-VN') : ''].filter(Boolean).join(' · ')
      : 'Nhấn Đọc biển số để nhận diện.';
    const image = find(lane, 'image');
    image.hidden = !s.imageUrl;
    if (s.imageUrl && image.getAttribute('src') !== s.imageUrl) image.src = s.imageUrl;
    if (!s.imageUrl) image.removeAttribute('src');
    const use = root(lane).querySelector('[data-action="use"]');
    if (use) use.disabled = !r?.plate_number;
    const [a, b] = LANES.map(key => state[key].stream?.getVideoTracks()[0]?.getSettings().deviceId);
    const shared = document.getElementById('gate-camera-shared');
    if (shared) shared.hidden = !(a && b && a === b);
  }

  async function refreshDevices() {
    if (!supported() || !mounted) return;
    const currentGeneration = generation;
    try {
      const found = await navigator.mediaDevices.enumerateDevices();
      if (!mounted || currentGeneration !== generation) return;
      devices = found.filter(device => device.kind === 'videoinput');
      for (const [index, lane] of LANES.entries()) {
        const s = state[lane], select = find(lane, 'source');
        if (!select) continue;
        if (!s.deviceId) {
          let saved = '';
          try { saved = localStorage.getItem('gate-camera-' + lane) || ''; } catch { /* Optional. */ }
          s.deviceId = devices.some(d => d.deviceId && d.deviceId === saved)
            ? saved : (devices[index] || devices[0])?.deviceId || '';
        }
        const options = devices.map((device, i) => new Option(device.label || 'Camera ' + (i + 1), device.deviceId));
        if (!options.length) options.push(new Option('Camera mặc định', ''));
        if (s.deviceId && !options.some(option => option.value === s.deviceId)) {
          options.push(new Option('Camera đã chọn (chưa tìm thấy)', s.deviceId));
        }
        select.replaceChildren(...options);
        select.value = s.deviceId;
      }
    } catch {
      // The default camera can still be requested if device enumeration is blocked.
    }
  }

  function stop(lane) {
    const s = state[lane];
    ++s.version;
    s.opening = false;
    s.auto = false;
    if (job?.lane === lane) job.controller.abort();
    const stream = s.stream;
    s.stream = null;
    stream?.getTracks().forEach(track => track.stop());
    LANES.forEach(paint);
  }

  function resetResult(lane) {
    const s = state[lane];
    if (s.imageUrl) URL.revokeObjectURL(s.imageUrl);
    s.imageUrl = '';
    s.result = null;
    s.capturedAt = null;
  }

  async function start(lane) {
    if (!mounted || !supported() || state[lane].opening || state[lane].stream) return;
    const s = state[lane], version = ++s.version, currentGeneration = generation;
    s.opening = true;
    s.error = '';
    resetResult(lane);
    paint(lane);
    let stream;
    try {
      const selected = find(lane, 'source').value;
      const shared = LANES.filter(key => key !== lane).map(key => state[key].stream).find(candidate =>
        candidate && selected && candidate.getVideoTracks()[0]?.getSettings().deviceId === selected);
      stream = shared ? shared.clone() : await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: { width: { ideal: 1280 }, height: { ideal: 720 }, ...(selected ? { deviceId: { exact: selected } } : {}) }
      });
      if (!mounted || currentGeneration !== generation || version !== s.version) {
        stream.getTracks().forEach(track => track.stop());
        return;
      }
      s.stream = stream;
      s.deviceId = stream.getVideoTracks()[0]?.getSettings().deviceId || selected;
      remember(lane, s.deviceId);
      const video = find(lane, 'video');
      video.srcObject = stream;
      await video.play();
      if (!mounted || currentGeneration !== generation || version !== s.version) return;
      for (const track of stream.getVideoTracks()) track.addEventListener('ended', () => {
        if (s.stream === stream) {
          stop(lane);
          s.error = 'Camera đã ngắt kết nối. Kiểm tra thiết bị rồi bật lại.';
          paint(lane);
        }
      });
      await refreshDevices();
    } catch (error) {
      stream?.getTracks().forEach(track => track.stop());
      if (version !== s.version || currentGeneration !== generation) return;
      s.stream = null;
      s.error = {
        NotAllowedError: 'Chưa được cấp quyền camera. Cho phép camera trong trình duyệt rồi bật lại.',
        NotFoundError: 'Không tìm thấy camera. Hãy kết nối webcam rồi làm mới danh sách.',
        NotReadableError: 'Không mở được camera. Kiểm tra kết nối và đóng ứng dụng khác đang dùng webcam.',
        OverconstrainedError: 'Camera đã chọn không còn sẵn sàng. Hãy chọn lại thiết bị.'
      }[error.name] || 'Không bật được camera. Kiểm tra thiết bị và quyền truy cập.';
    } finally {
      if (version === s.version && currentGeneration === generation) {
        s.opening = false;
        LANES.forEach(paint);
      }
    }
  }

  async function recognize(lane) {
    const s = state[lane];
    if (!mounted || !s.stream || s.opening || job || !canRead()) return;
    const currentGeneration = generation, version = s.version;
    const valid = () => mounted && currentGeneration === generation && version === s.version;
    const request = { lane, controller: new AbortController() };
    job = request;
    s.error = '';
    LANES.forEach(paint);
    let timedOut = false;
    const timeout = setTimeout(() => { timedOut = true; request.controller.abort(); }, 65000);
    try {
      const video = find(lane, 'video');
      if (!video.videoWidth || !video.videoHeight) throw new Error('Camera chưa có khung hình. Vui lòng chờ rồi thử lại.');
      const canvas = document.createElement('canvas');
      const scale = Math.min(1, 1280 / video.videoWidth, 720 / video.videoHeight);
      canvas.width = Math.round(video.videoWidth * scale);
      canvas.height = Math.round(video.videoHeight * scale);
      canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
      const capturedAt = Date.now();
      const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', 0.9));
      if (!valid()) return;
      if (!blob) throw new Error('Không chụp được khung hình camera.');
      const form = new FormData();
      form.append('lane', lane);
      form.append('image', blob, 'frame.jpg');
      const token = config.session.access_token;
      const response = await fetch(config.apiBase.replace(/\/$/, '') + '/alpr/recognize', {
        method: 'POST', headers: { Authorization: 'Bearer ' + token },
        body: form, signal: request.controller.signal
      });
      const result = await response.json().catch(() => ({}));
      if (!valid()) return;
      if (response.status === 401) { config.onUnauthorized?.(); return; }
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Không đọc được biển số từ dịch vụ AI.');
      if (result.lane !== lane || !['success', 'no_text_detected'].includes(result.status)) {
        throw new Error('Kết quả nhận diện không khớp làn camera.');
      }
      resetResult(lane);
      s.result = result;
      s.capturedAt = capturedAt;
      paint(lane);
      if (/^\/api\/v1\/alpr\/images\/[A-Za-z0-9_-]+_[0-9a-f]{8}\.jpg$/.test(result.image_url || '')) {
        try {
          const image = await fetch(new URL(result.image_url, config.apiBase + '/'), {
            headers: { Authorization: 'Bearer ' + token }, signal: request.controller.signal, cache: 'no-store'
          });
          if (!image.ok) throw new Error('Image unavailable');
          const imageBlob = await image.blob();
          if (!valid()) return;
          s.imageUrl = URL.createObjectURL(imageBlob);
        } catch (error) {
          if (error.name === 'AbortError') throw error;
          if (valid()) s.error = 'Đã đọc biển số nhưng chưa tải được ảnh đối chiếu.';
        }
      }
    } catch (error) {
      if (valid()) {
        // Stop automatic mode after a failed request; do not silently retry captures.
        s.auto = false;
        if (timedOut) s.error = 'Nhận diện quá thời gian chờ. Kiểm tra dịch vụ AI rồi thử lại.';
        else if (error.name !== 'AbortError') s.error = error instanceof TypeError
          ? 'Không kết nối được backend. Kiểm tra địa chỉ API và dịch vụ đang chạy.' : error.message;
      }
    } finally {
      clearTimeout(timeout);
      if (job === request) job = null;
      LANES.forEach(paint);
    }
  }

  function tick() {
    if (!mounted || job) return;
    for (let offset = 0; offset < LANES.length; offset++) {
      const index = (nextLane + offset) % LANES.length, lane = LANES[index];
      if (state[lane].auto && state[lane].stream && !state[lane].opening) {
        nextLane = (index + 1) % LANES.length;
        recognize(lane);
        break;
      }
    }
  }

  function mount(options) {
    config = options;
    if (!mounted) {
      mounted = true;
      ++generation;
      timer = setInterval(tick, 4000);
      navigator.mediaDevices?.addEventListener?.('devicechange', refreshDevices);
    }
    for (const lane of LANES) {
      const panel = root(lane);
      if (!panel) continue;
      panel.querySelector('[data-action="start"]').onclick = () => start(lane);
      panel.querySelector('[data-action="stop"]').onclick = () => stop(lane);
      panel.querySelector('[data-action="recognize"]').onclick = () => recognize(lane);
      find(lane, 'source').onchange = event => {
        const selected = event.target.value;
        stop(lane);
        resetResult(lane);
        state[lane].deviceId = selected;
        state[lane].error = '';
        remember(lane, selected);
        paint(lane);
      };
      find(lane, 'auto').onchange = event => { state[lane].auto = event.target.checked; tick(); };
      const use = panel.querySelector('[data-action="use"]');
      if (use) use.onclick = () => { if (state[lane].result?.plate_number) config.onUsePlate?.(state[lane].result.plate_number); };
      paint(lane);
    }
    const refresh = document.getElementById('gate-camera-refresh');
    if (refresh) refresh.onclick = refreshDevices;
    refreshDevices();
  }

  function unmount() {
    mounted = false;
    ++generation;
    clearInterval(timer);
    navigator.mediaDevices?.removeEventListener?.('devicechange', refreshDevices);
    job?.controller.abort();
    job = null;
    for (const lane of LANES) {
      stop(lane);
      resetResult(lane);
      state[lane].error = '';
    }
  }

  window.addEventListener('pagehide', unmount);
  return { view, mount, unmount };
})();
