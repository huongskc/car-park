'use strict';
// AI service owns the webcam; live MJPEG stream + lane ALPR capture.
window.GateCameras = (() => {
  const state = {IN:{}, OUT:{}};
  let config = null;
  const panel = lane => document.getElementById('gate-camera-' + lane);

  function view(lane) {
    return `<article id="gate-camera-${lane}" class="card camera-card"><h3>Camera làn ${lane==='IN'?'vào':'ra'}</h3>
      <label>Mã phiên đã tạo<input data-role="session" placeholder="SES_..." maxlength="64"></label>
      <div class="camera-screen">
        <img data-role="preview" class="camera-preview" alt="Webcam làn ${lane==='IN'?'vào':'ra'}" hidden>
        <div class="camera-placeholder" data-role="placeholder">Đang kết nối webcam…</div>
      </div>
      <div class="row">
        <button type="button" class="go sm" data-action="capture">Chụp làn ${lane==='IN'?'vào':'ra'}</button>
        <button type="button" class="sec sm" data-action="use" disabled>Dùng ảnh cho phiên</button>
      </div>
      <p class="camera-status" data-role="status" role="status">Nhập mã phiên rồi chụp ảnh.</p>
      <div class="camera-result">
        <img data-role="image" alt="Ảnh chụp làn ${lane==='IN'?'vào':'ra'}" style="max-width:120px;max-height:80px;object-fit:contain" hidden>
        <div>
          <strong data-role="plate">Chưa chụp ảnh</strong>
          <span data-role="details"></span>
        </div>
      </div>
    </article>`;
  }

  function paint(lane) {
    const p=panel(lane), s=state[lane]; if(!p)return;
    const capBtn=p.querySelector('[data-action="capture"]');
    if(capBtn)capBtn.disabled=!!s.busy;
    const useBtn=p.querySelector('[data-action="use"]');
    if(useBtn)useBtn.disabled=!s.result||!s.session||s.result.needs_confirmation===false;
    const statusEl=p.querySelector('[data-role="status"]');
    if(statusEl)statusEl.textContent=s.message||'Nhập mã phiên rồi chụp ảnh.';
    const plateEl=p.querySelector('[data-role="plate"]');
    if(plateEl)plateEl.textContent=s.result
      ? s.result.plate_number||s.result.suggested_plate||'Không đọc được biển'
      : 'Chưa chụp ảnh';
    const detailsEl=p.querySelector('[data-role="details"]');
    if(detailsEl)detailsEl.textContent=s.result?(s.result.needs_confirmation?'Cần xác nhận ảnh · ':'OCR tự tin · ')+(s.result.confidence==null?'':Math.round(s.result.confidence*100)+'%'):'';
    const img=p.querySelector('[data-role="image"]');
    if(img){
      img.hidden=!s.image;
      if(s.image)img.src=s.image;else img.removeAttribute('src');
    }
  }

  async function capture(lane) {
    const s=state[lane];
    if(s.busy)return;
    const p=panel(lane);if(!p)return;
    const sessionInput=p.querySelector('[data-role="session"]');
    const session=sessionInput?sessionInput.value.trim():'';
    if(!session){s.message='Cần tạo phiên và nhập mã phiên trước.';paint(lane);return}
    s.busy=true;
    s.message='Đang chụp từ AI service…';
    paint(lane);
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
      if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:'Không chụp được ảnh.');
      if(result.lane!==lane||result.session_id!==session)throw new Error('Kết quả không khớp phiên hoặc làn.');
      if(s.image)URL.revokeObjectURL(s.image);
      s.image=null;s.result=result;s.session=session;
      if(result.image_url){
        const photo=await fetch(new URL(result.image_url,new URL(base).origin),{headers:{Authorization:auth},cache:'no-store'});
        if(photo.ok)s.image=URL.createObjectURL(await photo.blob());
      }
      s.message=result.needs_confirmation?'Đối chiếu ảnh và xác nhận biển trước khi dùng.':'Đã tự ghi biển '+(result.plate_number||result.suggested_plate||'')+'.';
    }catch(error){
      s.message=error.message||'Không kết nối được dịch vụ AI.';
    }finally{
      s.busy=false;
      paint(lane);
    }
  }

  function startStream(lane){
    const p=panel(lane);if(!p||!config?.session?.access_token)return;
    const img=p.querySelector('[data-role="preview"]');
    const ph=p.querySelector('.camera-placeholder');
    if(!img)return;
    const base=config.apiBase.replace(/\/$/,'');
    const token=encodeURIComponent(config.session.access_token);
    const streamUrl=`${base}/alpr/stream?lane=${lane}&token=${token}`;
    img.onload=()=>{
      img.hidden=false;
      if(ph)ph.hidden=true;
    };
    img.onerror=()=>{
      if(ph&&img.hidden){
        ph.textContent='Không xem được webcam.';
        ph.hidden=false;
      }
    };
    img.src=streamUrl;
  }

  function mount(options){
    config=options;
    for(const lane of ['IN','OUT']){
      const p=panel(lane);if(!p)continue;
      p.querySelector('[data-action="capture"]').onclick=()=>capture(lane);
      p.querySelector('[data-action="use"]').onclick=()=>config.onUsePlate?.(lane,state[lane].session,state[lane].result);
      paint(lane);
      startStream(lane);
    }
  }

  function unmount(){
    for(const lane of ['IN','OUT']){
      const p=panel(lane);
      if(p){
        const img=p.querySelector('[data-role="preview"]');
        if(img){
          img.removeAttribute('src');
          img.hidden=true;
        }
        const ph=p.querySelector('.camera-placeholder');
        if(ph)ph.hidden=false;
      }
      if(state[lane].image)URL.revokeObjectURL(state[lane].image);
      state[lane]={};
    }
    config=null;
  }

  return {view,mount,unmount};
})();
