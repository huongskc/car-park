'use strict';
// AI service owns the webcam; each button requests one frame from its lane ROI.
window.GateCameras = (() => {
  const state = {IN:{}, OUT:{}};
  let config = null;
  const panel = lane => document.getElementById('gate-camera-' + lane);
  function view(lane) {
    return `<article id="gate-camera-${lane}" class="card camera-card"><h3>Camera làn ${lane==='IN'?'vào':'ra'}</h3>
      <label>Mã phiên đã tạo<input data-role="session" placeholder="SES_..." maxlength="64"></label>
      <div class="camera-screen">
        <div class="camera-placeholder" data-role="placeholder"><span>Không phát video trực tiếp</span><small>Ảnh chụp ROI_${lane} sẽ hiện ở đây</small></div>
        <img data-role="image" alt="Ảnh biển số làn ${lane==='IN'?'vào':'ra'}" hidden>
      </div>
      <div class="row"><button type="button" class="go sm" data-action="capture">Chụp làn ${lane==='IN'?'vào':'ra'}</button>
      <button type="button" class="sec sm" data-action="use" disabled>Dùng ảnh cho phiên</button></div>
      <p class="camera-status" data-role="status" role="status">Nhập mã phiên rồi chụp ảnh.</p>
      <div class="camera-result"><div>
      <strong data-role="plate">Chưa chụp ảnh</strong><span data-role="details"></span></div></div></article>`;
  }
  function paint(lane) {
    const p=panel(lane), s=state[lane]; if(!p)return;
    p.querySelector('[data-action="capture"]').disabled=!!s.busy;
    p.querySelector('[data-action="use"]').disabled=!s.result||!s.session;
    p.querySelector('[data-role="status"]').textContent=s.message||'Nhập mã phiên rồi chụp ảnh.';
    p.querySelector('[data-role="plate"]').textContent=s.result
      ? s.result.plate_number||s.result.suggested_plate||'Không đọc được biển'
      : 'Chưa chụp ảnh';
    p.querySelector('[data-role="details"]').textContent=s.result?(s.result.needs_confirmation?'Cần xác nhận ảnh · ':'OCR tự tin · ')+(s.result.confidence==null?'':Math.round(s.result.confidence*100)+'%'):'';
    const img=p.querySelector('[data-role="image"]');img.hidden=!s.image;
    p.querySelector('[data-role="placeholder"]').hidden=!!s.image;
    if(s.image)img.src=s.image;else img.removeAttribute('src');
  }
  async function capture(lane) {
    const s=state[lane], session=panel(lane).querySelector('[data-role="session"]').value.trim();
    if(!session){s.message='Cần tạo phiên và nhập mã phiên trước.';paint(lane);return}
    s.busy=true;s.message='Đang chụp từ AI service…';paint(lane);
    try{
      const base=config.apiBase.replace(/\/$/,'');
      const auth='Bearer '+config.session.access_token;
      const response=await fetch(base+'/alpr/capture',{method:'POST',headers:{Authorization:auth,'Content-Type':'application/json'},body:JSON.stringify({lane,session_id:session})});
      if(response.status===401){config.onUnauthorized?.();return}
      const result=await response.json();
      if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:'Không chụp được ảnh.');
      if(result.lane!==lane||result.session_id!==session)throw new Error('Kết quả không khớp phiên hoặc làn.');
      if(s.image)URL.revokeObjectURL(s.image);
      s.image=null;s.result=result;s.session=session;
      if(result.image_url){
        const photo=await fetch(new URL(result.image_url,new URL(base).origin),{headers:{Authorization:auth},cache:'no-store'});
        if(photo.ok)s.image=URL.createObjectURL(await photo.blob());
      }
      s.message=s.image?'Đối chiếu ảnh và xác nhận biển trước khi dùng.':'Đã nhận OCR nhưng chưa tải được ảnh đối chiếu.';
    }catch(error){s.message=error.message||'Không kết nối được dịch vụ AI.'}
    finally{s.busy=false;paint(lane)}
  }
  function mount(options){
    config=options;
    for(const lane of ['IN','OUT']){
      const p=panel(lane);if(!p)continue;
      p.querySelector('[data-action="capture"]').onclick=()=>capture(lane);
      p.querySelector('[data-action="use"]').onclick=()=>config.onUsePlate?.(lane,state[lane].session,state[lane].result);
      paint(lane);
    }
  }
  function unmount(){for(const lane of ['IN','OUT']){if(state[lane].image)URL.revokeObjectURL(state[lane].image);state[lane]={}}config=null}
  return {view,mount,unmount};
})();
