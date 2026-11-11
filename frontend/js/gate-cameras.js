'use strict';
// AI service owns the webcam; live MJPEG stream + lane ALPR capture.
window.GateCameras = (() => {
  const state = {IN:{}, OUT:{}};
  let config = null;
  const panel = lane => document.getElementById('gate-camera-' + lane);

  function view(lane) {
    return `<article id="gate-camera-${lane}" class="card camera-card"><h3>Camera làn ${lane==='IN'?'vào (Nửa trái)':'ra (Nửa phải)'}</h3>
      <label>Mã phiên đã tạo<input data-role="session" placeholder="SES_..." maxlength="64"></label>
      <div class="camera-screen">
        <img data-role="preview" class="camera-preview" alt="Webcam làn ${lane==='IN'?'vào':'ra'}" hidden>
        <div class="camera-placeholder">Đang kết nối webcam…</div>
      </div>
      <div class="row">
        <button type="button" class="go sm" data-action="capture">Chụp làn ${lane==='IN'?'vào':'ra'}</button>
        <button type="button" class="sec sm" data-action="resume" style="display:none;">Tiếp tục quay cam</button>
        <button type="button" class="sec sm" data-action="use" disabled>Dùng ảnh cho phiên</button>
      </div>
      <p class="camera-status" data-role="status" role="status">Nhập mã phiên rồi chụp ảnh.</p>
      <div class="camera-result"><img data-role="image" alt="Ảnh đã chụp để đối chiếu biển số" hidden><div>
      <strong data-role="plate">Chưa chụp ảnh</strong><span data-role="details"></span></div></div></article>`;
  }

  function paint(lane) {
    const p=panel(lane), s=state[lane]; if(!p)return;
    const sessionInput=p.querySelector('[data-role="session"]');
    const canCapture = !s.status || (lane==='IN' ? s.status==='ENTRY_PENDING' : s.status==='ACTIVE');
    const canUse = !s.status || (lane==='IN' ? s.status==='ENTRY_PENDING' : ['ACTIVE','PENDING_REVIEW'].includes(s.status));
    p.querySelector('[data-action="capture"]').disabled=!!s.busy||!canCapture||!sessionInput.value.trim();
    const useBtn=p.querySelector('[data-action="use"]');
    useBtn.disabled=!canUse||!s.result||!s.session||!s.image;
    useBtn.hidden=!!s.autoSession||!s.result||!s.image;
    sessionInput.readOnly=!!s.autoSession;
    useBtn.textContent=lane==='IN'?'Xác nhận biển vào từ ảnh':
      s.status==='PENDING_REVIEW'?'Xác nhận biển ra từ ảnh':'Xác nhận biển ra và chuẩn bị lượt ra';
    const resumeBtn = p.querySelector('[data-action="resume"]');
    if (resumeBtn) resumeBtn.style.display = s.frozen ? 'inline-block' : 'none';
    p.querySelector('[data-role="status"]').textContent=s.message||'Nhập mã phiên rồi chụp ảnh.';
    p.querySelector('[data-role="plate"]').textContent=s.result
      ? s.result.plate_number||s.result.suggested_plate||'Không đọc được biển'
      : 'Chưa chụp ảnh';
    p.querySelector('[data-role="details"]').textContent=s.result
      ? (s.result.confidence==null?'':` · Độ tin cậy OCR: ${Math.round(s.result.confidence*100)}%`)
      : '';
    const img=p.querySelector('[data-role="image"]');img.hidden=!s.image;
    if(s.image)img.src=s.image;else img.removeAttribute('src');
    const preview=p.querySelector('[data-role="preview"]');
    if(s.frozen&&s.image){preview.src=s.image;preview.hidden=false;p.querySelector('.camera-placeholder').hidden=true}
    else if(s.previewUrl){preview.src=s.previewUrl;preview.hidden=false;p.querySelector('.camera-placeholder').hidden=true}
    else {preview.hidden=true;p.querySelector('.camera-placeholder').hidden=false}
  }

  function clearCapture(lane){
    const s=state[lane];
    if(s.image)URL.revokeObjectURL(s.image);
    s.image=null;s.result=null;s.session=null;s.status=null;s.sourceImageUrl=null;s.previousImageUrl=null;s.suppressedImageUrl=null;s.autoSession=false;s.busy=false;s.frozen=false;
    s.captureSeq=(s.captureSeq||0)+1;s.imageSeq=(s.imageSeq||0)+1;
    paint(lane);
  }

  function resume(lane){
    const s=state[lane], p=panel(lane);
    if(!s)return;
    s.frozen=false;
    s.busy=false;
    s.previewBusy=false;
    s.message='Đang quay webcam trực tiếp.';
    const preview = p ? p.querySelector('[data-role="preview"]') : null;
    if (preview && s.previewUrl) {
      preview.src = s.previewUrl;
      preview.hidden = false;
    }
    paint(lane);
    refreshPreview(lane);
  }

  async function capture(lane) {
    const s=state[lane];
    if(s.busy)return;
    const p=panel(lane);if(!p)return;
    const sessionInput=p.querySelector('[data-role="session"]');
    const session=sessionInput?sessionInput.value.trim():'';
    if(!session){s.message='Cần tạo phiên và nhập mã phiên trước.';paint(lane);return}
    if(s.status&&(lane==='IN'?s.status!=='ENTRY_PENDING':s.status!=='ACTIVE')){
      s.message='Phiên không còn chờ chụp ảnh ở làn này.';paint(lane);return;
    }
    const status=s.status, autoSession=s.autoSession, previousImageUrl=s.sourceImageUrl;
    clearCapture(lane);
    s.session=session;s.status=status;s.autoSession=autoSession;s.previousImageUrl=previousImageUrl;s.suppressedImageUrl=previousImageUrl;
    const seq=s.captureSeq;
    s.busy=true;s.message='Đang chụp từ AI service…';paint(lane);
    try{
      const base=config.apiBase.replace(/\/$/,'');
      const auth='Bearer '+config.session.access_token;
      const response=await fetch(base+'/alpr/capture',{
        method:'POST',
        headers:{Authorization:auth,'Content-Type':'application/json'},
        body:JSON.stringify({lane, session_id: session})
      });
      if(response.status===401){config.onUnauthorized?.();return}
      const result=await response.json();
      if(state[lane]!==s||s.captureSeq!==seq)return;
      if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:'Không chụp được ảnh.');
      if(result.lane!==lane||result.session_id!==session)throw new Error('Kết quả không khớp phiên hoặc làn.');
      const alreadyLoaded=!!s.image&&s.sourceImageUrl===result.image_url;
      if(!alreadyLoaded&&s.image){URL.revokeObjectURL(s.image);s.image=null}
      s.result=result;s.session=session;s.sourceImageUrl=result.image_url||null;s.previousImageUrl=null;s.suppressedImageUrl=null;
      if(result.image_url&&!alreadyLoaded){
        const photo=await fetch(new URL(result.image_url,new URL(base).origin),{headers:{Authorization:auth},cache:'no-store'});
        if(photo.ok){
          const blob=await photo.blob();
          if(state[lane]!==s||s.captureSeq!==seq)return;
          s.image=URL.createObjectURL(blob);
          s.frozen=true;
          const preview=panel(lane).querySelector('[data-role="preview"]');
          if(preview){preview.src=s.image;preview.hidden=false}
          const ph=panel(lane).querySelector('.camera-placeholder');
          if(ph)ph.hidden=true;
        }
      }
      s.message=s.image?'Đã giữ ảnh lúc chụp. Đối chiếu biển số trước khi duyệt.':'Đã nhận OCR nhưng chưa tải được ảnh đối chiếu.';
    }catch(error){
      if(state[lane]===s&&s.captureSeq===seq)s.message=error.message||'Không kết nối được dịch vụ AI.';
    }finally{
      s.busy=false;
      paint(lane);
    }
  }

  async function setSession(lane, sessionCode, imageUrl, ocrPlate, confidence, status) {
    const s = state[lane], p = panel(lane);
    if (!p || !sessionCode) return;
    if (s.session !== sessionCode) clearCapture(lane);
    const input = p.querySelector('[data-role="session"]');
    if (input) input.value = sessionCode;
    if(imageUrl && imageUrl===s.suppressedImageUrl){
      s.session=sessionCode;s.autoSession=true;s.status=status;
      paint(lane);return;
    }
    if(imageUrl && imageUrl!==s.suppressedImageUrl)s.suppressedImageUrl=null;
    const imageChanged = !!imageUrl && s.sourceImageUrl !== imageUrl;
    s.session = sessionCode;
    s.autoSession = true;
    s.status = status;
    if (imageChanged || !s.result || s.result.plate_number !== ocrPlate) s.result = {plate_number: ocrPlate, suggested_plate: ocrPlate, confidence};
    if (imageChanged && config) {
      s.sourceImageUrl = imageUrl;
      const seq = s.imageSeq = (s.imageSeq||0)+1;
      try {
        const base = config.apiBase.replace(/\/$/, '');
        const auth = 'Bearer ' + config.session.access_token;
        const photo = await fetch(new URL(imageUrl, new URL(base).origin), {headers: {Authorization: auth}, cache: 'no-store'});
        if (photo.ok) {
          const blob = await photo.blob();
          if (state[lane]!==s || s.imageSeq !== seq || s.session !== sessionCode) return;
          if (s.image) URL.revokeObjectURL(s.image);
          s.image = URL.createObjectURL(blob);
          s.frozen = true;
          const preview = p.querySelector('[data-role="preview"]');
          if (preview) { preview.src = s.image; preview.hidden = false; }
          const ph = p.querySelector('.camera-placeholder');
          if (ph) ph.hidden = true;
        }else throw new Error('Không tải được ảnh đã chụp.');
      } catch (_) {
        if(state[lane]===s&&s.imageSeq===seq)s.sourceImageUrl=null;
      }
    }
    if(!s.busy)s.message = s.image ? 'Đã chụp ảnh lúc quẹt thẻ. Đang giữ khung hình.' : 'Đang chờ ảnh/OCR tự động; có thể chụp lại nếu cần.';
    paint(lane);
  }

  function clearSession(lane) {
    if (!state[lane].autoSession) return;
    clearCapture(lane);
    const input=panel(lane)?.querySelector('[data-role="session"]');
    if(input)input.value='';
    state[lane].message='Chờ lượt quẹt thẻ mới.';
    paint(lane);
  }

  async function refreshPreview(lane){
    const s=state[lane];
    if(!config||s.previewBusy||s.frozen||document.hidden)return;
    const p=panel(lane);if(!p)return;
    s.previewBusy=true;
    const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
    const timeoutId = controller ? setTimeout(() => controller.abort(), 2000) : null;
    try{
      const base=config.apiBase.replace(/\/$/,'');
      const fetchOpts = {
        headers:{Authorization:'Bearer '+config.session.access_token},
        cache:'no-store'
      };
      if (controller) fetchOpts.signal = controller.signal;
      const response=await fetch(base+'/alpr/preview?lane='+lane, fetchOpts);
      if (timeoutId) clearTimeout(timeoutId);
      if(response.status===401){config.onUnauthorized?.();return}
      if(!response.ok)throw new Error('preview');
      const blob=await response.blob();
      if(state[lane]!==s||!config||s.frozen)return;
      const url=URL.createObjectURL(blob);
      const img=p.querySelector('[data-role="preview"]');
      const oldUrl = s.previewUrl;
      s.previewUrl=url;
      img.onload=img.onerror=()=>{ if(oldUrl) URL.revokeObjectURL(oldUrl); };
      img.src=url;img.hidden=false;
      p.querySelector('.camera-placeholder').hidden=true;
    }catch(_){
      if (timeoutId) clearTimeout(timeoutId);
      const holder=p.querySelector('.camera-placeholder');
      if(holder&&!s.previewUrl)holder.textContent='Không xem được webcam.';
    }finally{s.previewBusy=false}
  }

  function mount(options){
    config=options;
    for(const lane of ['IN','OUT']){
      const p=panel(lane);if(!p)continue;
      p.querySelector('[data-action="capture"]').onclick=()=>capture(lane);
      p.querySelector('[data-action="resume"]').onclick=()=>resume(lane);
      p.querySelector('[data-action="use"]').onclick=()=>config.onUsePlate?.(lane,state[lane].session,state[lane].result);
      p.querySelector('[data-role="session"]').oninput=()=>{clearCapture(lane);state[lane].message='Nhập mã phiên rồi chụp ảnh.';paint(lane)};
      paint(lane);
      startStream(lane);
    }
    previewTimer=setInterval(()=>{
      refreshPreview('IN');
      refreshPreview('OUT');
    }, 350);
  }

  function unmount(){
    for(const lane of ['IN','OUT']){
      state[lane].captureSeq=(state[lane].captureSeq||0)+1;
      state[lane].imageSeq=(state[lane].imageSeq||0)+1;
      if(state[lane].image)URL.revokeObjectURL(state[lane].image);
      state[lane]={};
    }
    config=null;
  }

  return {view,mount,unmount,resume,setSession,clearSession,hasSession:(l,c)=>state[l]?.session===c};
})();
