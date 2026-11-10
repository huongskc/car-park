'use strict';
// AI service owns the webcam; each button requests one frame from its lane ROI.
window.GateCameras = (() => {
  const state = {IN:{}, OUT:{}};
  let config = null, previewTimer = null;
  const panel = lane => document.getElementById('gate-camera-' + lane);
  function view(lane) {
    return `<article id="gate-camera-${lane}" class="card camera-card"><h3>Camera làn ${lane==='IN'?'vào':'ra'}</h3>
      <label>Mã phiên đã tạo<input data-role="session" placeholder="SES_..." maxlength="64"></label>
      <div class="camera-screen"><img data-role="preview" class="camera-preview" alt="Webcam làn ${lane==='IN'?'vào':'ra'}" hidden><div class="camera-placeholder" data-role="placeholder">Đang kết nối webcam…</div></div>
      <div class="row"><button type="button" class="go sm" data-action="capture">Chụp làn ${lane==='IN'?'vào':'ra'}</button>
      <button type="button" class="sec sm" data-action="use" disabled>Dùng ảnh cho phiên</button></div>
      <p class="camera-status" data-role="status" role="status">Nhập mã phiên rồi chụp ảnh.</p>
      <div class="camera-result"><img data-role="image" alt="Ảnh chụp làn ${lane==='IN'?'vào':'ra'}" style="max-width:120px;max-height:80px;object-fit:contain" hidden><div>
      <strong data-role="plate">Chưa chụp ảnh</strong><span data-role="details"></span></div></div></article>`;
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
    const ph=p.querySelector('[data-role="placeholder"]');
    if(ph&&!s.previewUrl)ph.hidden=!!s.image;
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
      s.message=result.needs_confirmation?'Đối chiếu ảnh và xác nhận biển trước khi dùng.':'Đã tự ghi biển '+(result.plate_number||result.suggested_plate||'')+'.';
    }catch(error){s.message=error.message||'Không kết nối được dịch vụ AI.'}
    finally{s.busy=false;paint(lane)}
  }
  async function refreshPreview(lane){
    const s=state[lane];
    if(!config||s.previewBusy)return;
    const p=panel(lane);if(!p)return;
    s.previewBusy=true;
    try{
      const base=config.apiBase.replace(/\/$/,'');
      const response=await fetch(base+'/alpr/preview?lane='+lane,{headers:{Authorization:'Bearer '+config.session.access_token},cache:'no-store'});
      if(response.status===401){config.onUnauthorized?.();return}
      if(!response.ok)throw new Error('preview');
      const url=URL.createObjectURL(await response.blob());
      const img=p.querySelector('[data-role="preview"]');
      if(s.previewUrl)URL.revokeObjectURL(s.previewUrl);
      s.previewUrl=url;
      if(img){img.src=url;img.hidden=false;}
      const ph=p.querySelector('.camera-placeholder');
      if(ph)ph.hidden=true;
    }catch(_){
      const holder=p.querySelector('.camera-placeholder');
      if(holder&&!s.previewUrl)holder.textContent='Không xem được webcam.';
    }finally{s.previewBusy=false}
  }
  function mount(options){
    if(previewTimer)clearInterval(previewTimer);
    config=options;
    for(const lane of ['IN','OUT']){
      const p=panel(lane);if(!p)continue;
      p.querySelector('[data-action="capture"]').onclick=()=>capture(lane);
      p.querySelector('[data-action="use"]').onclick=()=>config.onUsePlate?.(lane,state[lane].session,state[lane].result);
      paint(lane);
      refreshPreview(lane);
    }
    previewTimer=setInterval(()=>{refreshPreview('IN');refreshPreview('OUT')},800);
  }
  function unmount(){
    if(previewTimer)clearInterval(previewTimer);
    previewTimer=null;
    for(const lane of ['IN','OUT']){
      if(state[lane].image)URL.revokeObjectURL(state[lane].image);
      if(state[lane].previewUrl)URL.revokeObjectURL(state[lane].previewUrl);
      state[lane]={};
    }
    config=null;
  }
  return {view,mount,unmount};
})();
