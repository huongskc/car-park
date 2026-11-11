'use strict';
const $=s=>document.querySelector(s);
const LV={VIEWER:1,OPERATOR:2,ADMIN:3};
const RN={ADMIN:'Quản trị viên',OPERATOR:'Nhân viên vận hành',VIEWER:'Người xem'};
let API=localStorage.getItem('api')||(location.origin.includes('8000')?location.origin+'/api/v1':'http://127.0.0.1:8000/api/v1');
let S=JSON.parse(localStorage.getItem('sess')||'null');
if(S?.demo){S=null;localStorage.removeItem('sess')}
let timer=null,renderEpoch=0,Q={q:'',st:''};const hist={};
const esc=s=>String(s??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const dt=s=>s?new Date(s).toLocaleString('vi-VN'):'—';
const vnd=v=>Number(v||0).toLocaleString('vi-VN')+'đ';
const normPlate=p=>(p||'').toUpperCase().replace(/[^A-Z0-9]/g,'');
const dur=(a,b)=>{if(!a)return'—';const m=Math.max(0,Math.round(((b?new Date(b):new Date())-new Date(a))/6e4));return Math.floor(m/60)+'g '+String(m%60).padStart(2,'0')+'p'};
const ST={EMPTY:['Trống','ok'],OCCUPIED:['Có xe','bad'],RESERVED:['Đã cấp','warn'],UNKNOWN:['Mất mẫu cảm biến','warn'],MAINTENANCE:['Bảo trì','mute'],ENTRY_PENDING:['Chờ biển / ACK vào','warn'],ENTRY_ACKED_WAITING_PASS:['ACK vào · chờ xe qua','warn'],ACTIVE:['Đang gửi','ok'],EXIT_READY:['Chờ thu tiền','warn'],PAID_WAITING_EXIT:['Đã trả, chờ ACK ra','warn'],EXIT_ACKED_WAITING_PASS:['ACK ra · chờ xe qua','warn'],COMPLETED:['Hoàn tất','mute'],PENDING_REVIEW:['Chờ duyệt biển','warn'],CANCELLED:['Đã hủy','bad'],REGISTERED:['Đã đăng ký','mute'],ACTIVATED:['Hoạt động','ok'],SUSPENDED:['Tạm ngưng','warn'],DECOMMISSIONED:['Ngừng dùng','bad'],NEW:['Mới','bad'],ACKNOWLEDGED:['Đã tiếp nhận','warn'],RESOLVED:['Đã xử lý','ok'],INFO:['Thông tin','mute'],WARNING:['Cảnh báo','warn'],CRITICAL:['Nghiêm trọng','bad'],SENT:['Đã gửi','warn'],ACKED:['Đã phản hồi','ok'],FAILED:['Thất bại','bad'],PENDING:['Chờ','mute'],EXPIRED:['Hết hạn','mute']};
const AT={PLATE_MISMATCH:'Lệch biển số',UNAUTHORIZED_CARD:'Thẻ không hợp lệ',DEVICE_OFFLINE:'Mất kết nối',LONG_OCCUPANCY:'Chiếm chỗ lâu',SYSTEM_ERROR:'Lỗi hệ thống / cảm biến'};
const bd=v=>{const[t,c]=ST[v]||[v,'mute'];return `<span class="b ${c}">${esc(t)}</span>`};
const onl=o=>`<span class="b ${o?'ok':'bad'}">${o?'Online':'Offline'}</span>`;
const tr=c=>`<tr>${c.map(x=>`<td>${x}</td>`).join('')}</tr>`;
const tbl=(h,r)=>`<div class="tw"><table><thead><tr>${h.map(x=>`<th>${x}</th>`).join('')}</tr></thead><tbody>${r.length?r.join(''):`<tr><td colspan="${h.length}" class="mt">Chưa có dữ liệu.</td></tr>`}</tbody></table></div>`;
const kpi=(l,v,c)=>`<div class="kpi ${c}"><b>${v}</b>${l}</div>`;
const OP=()=>LV[S.role]>=2, AD=()=>S.role==='ADMIN';
const origin=()=>API.replace(/\/api\/v1$/,'');
let wsClient=null;
const imageCache=new Map();
async function protectedImage(path){
  if(!path||imageCache.has(path))return;
  try{const r=await fetch(origin()+path,{headers:{Authorization:'Bearer '+S.access_token}});
    if(r.ok&&r.headers.get('content-type')?.startsWith('image/'))imageCache.set(path,URL.createObjectURL(await r.blob()));
  }catch(_){/* Image can arrive after the session update. */}
}
function initWs(){
  if(!S||wsClient)return;
  try{
    const u=origin().replace(/^http/,'ws')+'/ws?token='+encodeURIComponent(S.access_token);
    wsClient=new WebSocket(u);
    wsClient.onmessage=e=>{
      try{
        const m=JSON.parse(e.data);
        if(['SLOT_STATUS_CHANGED','SESSION_STATUS_CHANGED','CAR_ENTERED','CAR_EXITED','BARRIER_STATUS'].includes(m.event)){
          const pg=location.hash.slice(2).split('/')[0]||'dashboard';
          if(['dashboard','gate','parking'].includes(pg)){
            const l=$('#live');if(l&&PAGES[pg]?.live)PAGES[pg].live().then(h=>{const el=$('#live');if(el)el.innerHTML=h}).catch(()=>{});
          }
        }
        if(m.event==='NEW_ALERT')toast(m.data?.message||'Cảnh báo an ninh mới!',m.data?.severity==='CRITICAL');
      }catch(_){}
    };
    wsClient.onclose=()=>{wsClient=null;setTimeout(initWs,4000)};
    wsClient.onerror=()=>{try{wsClient.close()}catch(_){}wsClient=null};
  }catch(_){}
}
function toast(m,err){const t=$('#toast');t.textContent=m;t.className='on'+(err?' e':'');clearTimeout(toast.t);toast.t=setTimeout(()=>t.className='',4000)}
async function api(p,o={}){
  const r=await fetch(API+p,{method:o.method||'GET',headers:{'Content-Type':'application/json',...(S?{Authorization:'Bearer '+S.access_token}:{})},body:o.body?JSON.stringify(o.body):undefined});
  if(r.status===401&&S){logout();throw new Error('Phiên đăng nhập đã hết hạn.')}
  const d=await r.json().catch(()=>({}));
  if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:'Dữ liệu gửi lên không hợp lệ.');
  return d;
}
function logout(){if(window.GateCameras)GateCameras.unmount();if(wsClient){try{wsClient.close()}catch(_){}wsClient=null}for(const u of imageCache.values())URL.revokeObjectURL(u);imageCache.clear();S=null;localStorage.removeItem('sess');render()}
async function doLogin(ev){ev.preventDefault();const d=Object.fromEntries(new FormData(ev.target));
  S=null;
  try{S=await api('/auth/login',{method:'POST',body:{username:d.username,password:d.password}});localStorage.setItem('sess',JSON.stringify(S));location.hash='#/dashboard';render()}
  catch(x){toast(x.message==='Failed to fetch'?'Không kết nối được backend tại '+API:x.message,1)}}
async function F(ev,n){ev.preventDefault();const d=Object.fromEntries(new FormData(ev.target));try{toast(await ACT[n](d));render()}catch(x){toast(x.message,1)}}
async function C(n,...a){try{toast(await ACT[n](...a));render()}catch(x){toast(x.message,1)}}
const nul=v=>v||null;
const ACT={
  mkUser:d=>api('/users',{method:'POST',body:{...d,email:nul(d.email)}}).then(()=>'Đã tạo tài khoản.'),
  lockUser:(id,v)=>api(`/users/${id}/status`,{method:'PATCH',body:{is_active:v}}).then(()=>'Đã cập nhật tài khoản.'),
  mkCard:d=>api('/cards',{method:'POST',body:{...d,owner_name:nul(d.owner_name),registered_plate:nul(d.registered_plate),valid_from:nul(d.valid_from),valid_until:nul(d.valid_until)}}).then(()=>'Đã đăng ký thẻ.'),
  lockCard:(u,v)=>api(`/cards/${u}/status`,{method:'PATCH',body:{is_active:v}}).then(()=>'Đã cập nhật thẻ.'),
  entryPlate:async id=>{const s=await api(`/sessions/${id}`);const plate=prompt('Biển số nhìn thấy trên ảnh:',s.entry_ocr_plate||'');if(!plate)throw new Error('Cần nhập biển số.');const changed=!normPlate(s.entry_ocr_plate)||normPlate(plate)!==normPlate(s.entry_ocr_plate);const note=changed?prompt('Lý do sửa/nhập biển OCR:'):'Nhân viên đã đối chiếu ảnh và xác nhận biển vào';if(!note)throw new Error('Cần nhập lý do.');await api(`/sessions/${id}/entry-plate`,{method:'PATCH',body:{plate,note}});return 'Đã xác nhận biển vào.'},
  prepareExit:id=>{const exit_plate=prompt('Biển số xe tại cổng ra (để trống nếu camera lỗi):');return api(`/sessions/${id}/prepare-exit`,{method:'POST',body:{exit_plate:nul(exit_plate)}}).then(()=>'Đã kiểm tra biển ra.')},
  retryGate:id=>api(`/sessions/${id}/retry-gate`,{method:'POST'}).then(()=>'Đã thử gửi lại lệnh mở cổng.'),
  cancelEntry:(id,occupied)=>{const reason=prompt('Lý do hủy lượt vào:');if(!reason)throw new Error('Cần nhập lý do.');const confirmed_departure=occupied?confirm('Cảm biến đang báo có xe. Bạn đã kiểm tra thực tế và xác nhận xe đã rời?'):false;if(occupied&&!confirmed_departure)throw new Error('Chưa xác nhận xe đã rời.');return api(`/sessions/${id}/cancel-entry`,{method:'POST',body:{reason,confirmed_departure}}).then(()=>'Đã hủy lượt vào. Ô chỉ được cấp lại khi xác nhận trống.')},
  slotStatus:id=>{const status=prompt('Trạng thái sau khi kiểm tra ô (EMPTY, OCCUPIED, RESERVED, MAINTENANCE):');if(!status)throw new Error('Chưa chọn trạng thái.');const reason=prompt('Lý do và kết quả kiểm tra thực tế:');if(!reason)throw new Error('Cần ghi lý do.');return api(`/slots/${encodeURIComponent(id)}/status`,{method:'PATCH',body:{status:status.trim().toUpperCase(),reason}}).then(()=>'Đã cập nhật ô và ghi audit.')},
  updateProfile:d=>api('/auth/me',{method:'PATCH',body:{full_name:d.full_name,email:nul(d.email)}}).then(u=>{S.full_name=u.full_name;localStorage.setItem('sess',JSON.stringify(S));return'Đã cập nhật hồ sơ.'}),
  changePassword:d=>api('/auth/me/change-password',{method:'POST',body:d}).then(()=>'Đã đổi mật khẩu.'),
  confirmEntry:id=>api(`/sessions/${id}/confirm-entry`,{method:'POST',body:{confirmed_passage:true}}).then(()=>'Đã xác nhận xe vào.'),
  confirmExit:id=>api(`/sessions/${id}/confirm-exit`,{method:'POST',body:{confirmed_passage:true}}).then(()=>'Đã xác nhận xe ra.'),
  cancelExit:id=>{const reason=prompt('Lý do xe quay đầu/chưa ra:');if(!reason)throw new Error('Cần lý do.');if(!confirm('Xác nhận xe chưa qua cổng ra?'))throw new Error('Chưa xác nhận.');return api(`/sessions/${id}/cancel-exit`,{method:'POST',body:{reason,confirmed_not_exited:true}}).then(()=>'Xe quay lại bãi; khoản đã thu vẫn được giữ.')},
  cancelSession:id=>{const reason=prompt('Lý do hủy ngoại lệ sau khi xe đã ra thủ công:');if(!reason)throw new Error('Cần lý do.');if(!confirm('Đã xác nhận xe thực tế đã rời bãi?'))throw new Error('Chưa xác nhận.');return api(`/sessions/${id}/cancel`,{method:'POST',body:{reason,confirmed_exit:true}}).then(()=>'Đã hủy ngoại lệ.')},
  mkTariff:d=>api('/tariffs',{method:'POST',body:{name:d.name,flat_rate:Number(d.flat_rate),effective_from:d.effective_from}}).then(()=>'Đã lưu biểu phí đồng giá mới.'),
  pay:(id,m)=>api(`/sessions/${id}/checkout`,{method:'POST',body:{payment_method:m}}).then(()=>'Đã ghi nhận thu phí; chờ ACK mở cổng ra.'),
  review:(id,ok)=>{const note=prompt(ok?'Lý do cho xe ra:':'Lý do từ chối:');if(!note)throw new Error('Cần nhập lý do xử lý.');const actual_exit_plate=ok?prompt('Biển ra đã xác nhận:'):null;if(ok&&!actual_exit_plate)throw new Error('Cần xác nhận biển ra.');return api(`/sessions/${id}/resolve-plate`,{method:'PATCH',body:{approved:ok,note,actual_exit_plate}}).then(()=>'Đã xử lý ngoại lệ.')},
  confirmExitPlate:async id=>{const s=await api(`/sessions/${id}`);const norm=p=>(p||'').toUpperCase().replace(/[^A-Z0-9]/g,'');if(s.status!=='PENDING_REVIEW'||!norm(s.exit_ocr_plate)||norm(s.entry_plate)!==norm(s.exit_ocr_plate))throw new Error('Biển ra không còn khớp; cần duyệt thủ công.');await api(`/sessions/${id}/resolve-plate`,{method:'PATCH',body:{approved:true,note:'Nhân viên đã đối chiếu ảnh và xác nhận biển ra khớp',actual_exit_plate:s.exit_ocr_plate}});return 'Đã xác nhận biển ra.'},
  cmd:(g,a)=>{const id=$('#dev')?.value;if(!id)throw new Error('Chưa chọn thiết bị.');const reason=prompt('Lý do thao tác trực tiếp:');if(!reason)throw new Error('Cần nhập lý do.');return api(`/devices/${id}/commands`,{method:'POST',body:{gate_id:g,action:a,reason}}).then(c=>'Đã gửi lệnh '+a+' ('+c.id+').')},
  devStatus:(id,s)=>api(`/devices/${id}/status`,{method:'PATCH',body:{status:s}}).then(()=>'Đã đổi trạng thái vòng đời.'),
  mkDev:d=>api('/devices',{method:'POST',body:d}).then(x=>'Đã đăng ký thiết bị. Token: '+x.auth_token),
  alert:(id,s)=>api(`/alerts/${id}/resolve`,{method:'PATCH',body:{status:s}}).then(()=>'Đã cập nhật cảnh báo.')
};
const lock=(fn,id,on)=>`<button class="sm sec" onclick="C('${fn}','${esc(id)}',${!on})">${on?'Khóa':'Mở khóa'}</button>`;

/* ---------- Trang ---------- */
async function pProfile(){const u=await api('/auth/me');
  return `<div class="card prof"><div class="av">${esc(u.full_name[0])}</div><h2>${esc(u.full_name)}</h2><dl><dt>Tên đăng nhập</dt><dd>${esc(u.username)}</dd><dt>Email</dt><dd>${esc(u.email)}</dd><dt>Vai trò</dt><dd>${RN[u.role]}</dd><dt>Trạng thái</dt><dd>${u.is_active?bd('ACTIVATED'):'Bị khóa'}</dd><dt>Ngày tạo</dt><dd>${dt(u.created_at)}</dd></dl></div><form class="f" onsubmit="F(event,'updateProfile')"><label>Họ tên<input name="full_name" value="${esc(u.full_name)}" required maxlength="100"></label><label>Email<input name="email" type="email" value="${esc(u.email||'')}" maxlength="100"></label><button class="go">Lưu hồ sơ</button></form><form class="f" onsubmit="F(event,'changePassword')"><h3>Đổi mật khẩu</h3><label>Mật khẩu hiện tại<input name="current_password" type="password" required></label><label>Mật khẩu mới<input name="new_password" type="password" required minlength="6"></label><button class="sec">Đổi mật khẩu</button></form>`}

async function pUsers(){const us=await api('/users?limit=100');
  return `<form class="f" onsubmit="F(event,'mkUser')"><label>Tên đăng nhập<input name="username" required minlength="3"></label><label>Mật khẩu<input name="password" type="password" required minlength="6"></label><label>Họ và tên<input name="full_name" required></label><label>Email<input name="email" type="email"></label><label>Vai trò<select name="role"><option value="OPERATOR">Nhân viên vận hành</option><option value="VIEWER">Người xem</option><option value="ADMIN">Quản trị viên</option></select></label><button class="go">Tạo tài khoản</button></form>`+
  tbl(['ID','Tên đăng nhập','Họ tên','Email','Vai trò','Trạng thái',''],us.map(u=>tr([u.id,esc(u.username),esc(u.full_name),esc(u.email),RN[u.role],u.is_active?bd('ACTIVATED'):'<span class="b bad">Bị khóa</span>',u.id===S.user_id?'':lock('lockUser',u.id,u.is_active)])))}

async function dashLive(){const op=OP();
  const[sl,se,al,dv]=await Promise.all([api('/slots'),op?api('/sessions?status=ACTIVE&limit=100'):[],op?api('/alerts?status=NEW&limit=5'):[],op?api('/devices'):[]]);
  const n=s=>sl.filter(x=>x.status===s).length;
  return `<div class="kpis">${kpi('Ô trống',n('EMPTY'),'ok')}${kpi('Có xe',n('OCCUPIED'),'bad')}${kpi('Chưa xác định',sl.length-n('EMPTY')-n('OCCUPIED'),'warn')}${op?kpi('Xe đang gửi',se.length,''):''}</div>
  <h3>Sơ đồ ô đỗ</h3><div class="slots">${sl.map(s=>`<div class="slot ${ST[s.status]?.[1]||''}"><b>${esc(s.id)}</b><small>${esc(s.name)}</small>${bd(s.status)}<small>${s.last_distance_cm??'—'} cm</small></div>`).join('')}</div>`+
  (op?`<h3>Thiết bị và cổng</h3>${tbl(['Thiết bị','Kết nối','Cổng vào/ra','Vòng đời'],dv.map(d=>tr([esc(d.id),onl(d.is_online),d.is_online&&d.status==='ACTIVATED'?'<span class="b ok">Sẵn sàng</span>':'<span class="b warn">Không khả dụng</span>',bd(d.status)])))}
  <h3>Cảnh báo mới</h3>${tbl(['Thời gian','Loại','Mức độ','Nội dung'],al.map(a=>tr([dt(a.created_at),AT[a.alert_type],bd(a.severity),`<span style="white-space:normal">${esc(a.message)}</span>`])))}`:'')}

async function pGate(){const dv=await api('/devices');
  const device=dv.find(d=>d.status==='ACTIVATED')||dv[0];
  const cams=window.GateCameras?`<div class="two gate-stations"><div>${GateCameras.view('IN')}<div id="gate-station-IN" class="card station-card"><div class="station-empty"><b style="font-size:16px">Chờ xe quẹt thẻ vào…</b><p class="mt" style="font-size:13px;margin:6px 0 0 0">Cổng sẵn sàng tiếp nhận</p></div></div></div><div>${GateCameras.view('OUT')}<div id="gate-station-OUT" class="card station-card"><div class="station-empty"><b style="font-size:16px">Chờ xe quẹt thẻ ra…</b><p class="mt" style="font-size:13px;margin:6px 0 0 0">Cổng sẵn sàng soát vé</p></div></div></div></div>`:'';
  return `<input id="dev" type="hidden" value="${esc(device?.id||'')}">${cams}<div id="live"></div>`}
const gateRefresh=async()=>{const l=$('#live');if(l)try{l.innerHTML=await gateLive()}catch(x){l.innerHTML=`<div class="err">${esc(x.message)}</div>`}};
function stationPlateChanged(input){
  const form=input.form, ocr=form.dataset.ocr, entry=form.dataset.entry;
  const needsReason=!normPlate(ocr)||normPlate(input.value)!==normPlate(ocr)||
    (form.dataset.lane==='OUT'&&normPlate(ocr)!==normPlate(entry))||
    (form.dataset.registered&&normPlate(ocr)!==normPlate(form.dataset.registered));
  const reason=form.querySelector('.station-reason');
  reason.hidden=!needsReason;
  reason.querySelector('input').required=needsReason;
  return needsReason;
}
async function stationConfirm(event){
  event.preventDefault();
  const form=event.currentTarget, plate=form.elements.plate.value.trim(), lane=form.dataset.lane;
  if(!normPlate(plate)){toast('Cần nhập biển số nhìn thấy trên ảnh.',1);return}
  const note=stationPlateChanged(form.elements.plate)
    ?form.elements.note.value.trim()
    :`Nhân viên đã đối chiếu ảnh và xác nhận biển ${lane==='IN'?'vào':'ra'} khớp`;
  if(!note){toast('Cần ghi lý do sửa hoặc duyệt biển.',1);return}
  const button=form.querySelector('button[type="submit"]');button.disabled=true;
  try{
    const id=form.dataset.id;
    if(lane==='IN')await api(`/sessions/${id}/entry-plate`,{method:'PATCH',body:{plate,note}});
    else if(form.dataset.status==='ACTIVE'){
      const prepared=await api(`/sessions/${id}/prepare-exit`,{method:'POST',body:{exit_plate:plate,exit_image_url:form.dataset.image||null}});
      if(prepared.status==='PENDING_REVIEW')await api(`/sessions/${id}/resolve-plate`,{method:'PATCH',body:{approved:true,actual_exit_plate:plate,note}});
    }else await api(`/sessions/${id}/resolve-plate`,{method:'PATCH',body:{approved:true,actual_exit_plate:plate,note}});
    toast(`Đã xác nhận biển ${lane==='IN'?'vào':'ra'}.`);gateRefresh();
  }catch(error){toast(error.message,1);button.disabled=false}
}
function gateStation(lane,s,command,slot,card){
  if(!s)return `<div class="station-empty"><b style="font-size:16px">Chờ xe quẹt thẻ ${lane==='IN'?'vào':'ra'}…</b><p class="mt" style="font-size:13px;margin:6px 0 0 0">Cổng sẵn sàng tiếp nhận</p></div>`;
  const imagePath=lane==='IN'?s.entry_image_url:s.exit_image_url;
  const image=imageCache.get(imagePath);
  const ocr=lane==='IN'?s.entry_ocr_plate:s.exit_ocr_plate;
  const photo=image?`<img class="station-photo" src="${esc(image)}" alt="Ảnh biển số làn ${lane==='IN'?'vào':'ra'}">`:
    `<div class="station-no-photo">Chưa có ảnh</div>`;
  const commandText=command?`${esc(command.action)} ${bd(command.status)}${['FAILED','EXPIRED'].includes(command.status)&&command.ack_reason?` · ${esc(command.ack_reason)}`:''}`:'Chưa gửi lệnh';
  const registered=lane==='IN'&&card?.card_type==='MONTHLY'?card.registered_plate||'':'';
  const form=(label)=>{const needsReason=!normPlate(ocr)||(lane==='OUT'&&normPlate(ocr)!==normPlate(s.entry_plate))||
      (registered&&normPlate(ocr)!==normPlate(registered));
    return `<form class="station-form" data-id="${s.id}" data-lane="${lane}" data-status="${s.status}" data-image="${esc(imagePath||'')}" data-ocr="${esc(ocr||'')}" data-entry="${esc(s.entry_plate||'')}" data-registered="${esc(registered)}" onsubmit="stationConfirm(event)">
      <label>Biển OCR · kiểm tra trên ảnh<input name="plate" value="${esc(ocr||'')}" maxlength="20" autocomplete="off" oninput="stationPlateChanged(this)" required></label>
      <label class="station-reason" ${needsReason?'':'hidden'}>Lý do sửa/duyệt biển<input name="note" maxlength="255" placeholder="Ví dụ: OCR đọc sai ký tự" ${needsReason?'required':''}></label>
      <button class="go" type="submit">${label}</button></form>`};
  let action='';
  if(lane==='IN'&&s.status==='ENTRY_PENDING'){
    action=s.entry_plate
      ?command?.status==='SENT'?'<p class="note">Đã xác nhận biển; đang chờ ACK mở cổng vào.</p>':`<button class="go" onclick="C('retryGate',${s.id})">Thử mở cổng vào</button>`
      :form('Xác nhận biển vào');
    if(command?.status!=='SENT')action+=` <button class="sec sm" onclick="C('cancelEntry',${s.id},${!!slot&&['OCCUPIED','UNKNOWN'].includes(slot.status)})">Hủy lượt vào</button>`;
  }else if(lane==='IN'&&s.status==='ENTRY_ACKED_WAITING_PASS'){
    action=`<button class="go" onclick="C('confirmEntry',${s.id})">Đã thấy xe vào</button> <button class="sec sm" onclick="C('cancelEntry',${s.id},${!!slot&&['OCCUPIED','UNKNOWN'].includes(slot.status)})">Xe không vào</button>`;
  }else if(lane==='OUT'&&s.status==='ACTIVE'&&s.exit_image_url){
    action=form('Xác nhận biển ra từ ảnh');
  }else if(lane==='OUT'&&s.status==='PENDING_REVIEW'){
    action=form(normPlate(ocr)&&normPlate(ocr)===normPlate(s.entry_plate)?'Xác nhận biển ra':'Duyệt biển ra sau kiểm tra');
    action+=`<div class="row"><button class="sec sm" onclick="C('review',${s.id},false)">Từ chối</button><button class="sec sm" onclick="C('cancelSession',${s.id})">Xe đã ra thủ công</button></div>`;
  }else if(lane==='OUT'&&s.status==='EXIT_READY'){
    action=Number(s.fee_amount)>0&&!s.is_paid
      ?`<p><strong>Cần thu ${vnd(s.fee_amount)}</strong></p><div class="row"><button class="go" onclick="C('pay',${s.id},'CASH')">Đã nhận tiền mặt</button><button class="sec" onclick="C('pay',${s.id},'SIMULATED_MOMO')">MoMo giả lập</button></div>`
      :`<button class="go" onclick="C('pay',${s.id},'SYSTEM')">Mở cổng ra (0đ/đã thu)</button>`;
  }else if(lane==='OUT'&&s.status==='PAID_WAITING_EXIT'){
    action=command?.status==='SENT'?'<p class="note">Đã ghi nhận thanh toán; đang chờ ACK mở cổng ra.</p>':
      `<button class="go" onclick="C('retryGate',${s.id})">Thử mở cổng ra</button>`;
    action+=` <button class="sec sm" onclick="C('cancelExit',${s.id})">Xe quay đầu</button>`;
  }else if(lane==='OUT'&&s.status==='EXIT_ACKED_WAITING_PASS'){
    action=`<button class="go" onclick="C('confirmExit',${s.id})">Đã thấy xe ra</button> <button class="sec sm" onclick="C('cancelExit',${s.id})">Xe chưa ra</button>`;
  }
  return `<div class="station-head"><strong>${esc(s.session_code)}</strong>${bd(s.status)}</div>
    <div class="station-meta">Thẻ ${esc(s.card_uid)} · Ô ${esc(s.slot_id)} ${slot?bd(slot.status):''}${lane==='OUT'?` · Biển vào ${esc(s.entry_plate)} · Phí ${vnd(s.fee_amount)}`:registered?` · Thẻ tháng đăng ký ${esc(registered)}`:''}</div>
    ${registered&&normPlate(ocr)&&normPlate(ocr)!==normPlate(registered)?'<p class="err">OCR khác biển đăng ký thẻ tháng. Kiểm tra ảnh và sửa biển trước khi xác nhận.</p>':''}
    <div class="station-body">${photo}<div class="station-work"><p class="station-command">Lệnh cổng: ${commandText}</p>${action}</div></div>`;
}
function updateGateStation(lane,session,command,slot,card){
  const el=document.getElementById('gate-station-'+lane);if(!el)return;
  const path=lane==='IN'?session?.entry_image_url:session?.exit_image_url;
  const key=JSON.stringify([session?.id,session?.status,session?.entry_plate,session?.entry_ocr_plate,
    session?.exit_ocr_plate,session?.fee_amount,session?.is_paid,path,imageCache.has(path),command?.id,command?.status,slot?.status,card?.registered_plate]);
  if(el.dataset.key===key)return;
  const oldForm=el.querySelector('.station-form');
  const ocr=lane==='IN'?session?.entry_ocr_plate:session?.exit_ocr_plate;
  const keepDraft=oldForm&&oldForm.dataset.id===String(session?.id)&&el.dataset.status===session?.status&&
    oldForm.dataset.ocr===(ocr||'')&&oldForm.dataset.registered===(lane==='IN'&&card?.card_type==='MONTHLY'?card.registered_plate||'':'');
  const draft=keepDraft?{plate:oldForm.elements.plate.value,note:oldForm.elements.note.value,
    focus:document.activeElement?.name&&oldForm.contains(document.activeElement)?document.activeElement.name:null}:null;
  el.innerHTML=gateStation(lane,session,command,slot,card);el.dataset.key=key;
  el.dataset.status=session?.status||'';
  if(draft){const form=el.querySelector('.station-form');if(form){
    form.elements.plate.value=draft.plate;form.elements.note.value=draft.note;
    stationPlateChanged(form.elements.plate);
    if(draft.focus)form.elements[draft.focus]?.focus();
  }}
}
async function gateLive(){const id=$('#dev')?.value;
  const[cm,se,slots]=await Promise.all([id?api(`/devices/${id}/commands?limit=20`):[],api('/sessions?limit=30'),api('/slots')]);
  const inSes=se.find(s=>['ENTRY_PENDING','ENTRY_ACKED_WAITING_PASS'].includes(s.status));
  const outSes=se.find(s=>['PENDING_REVIEW','EXIT_READY','PAID_WAITING_EXIT','EXIT_ACKED_WAITING_PASS'].includes(s.status)||
    s.status==='ACTIVE'&&!!s.exit_image_url);
  if(window.GateCameras?.setSession){
    if(inSes){
      GateCameras.setSession('IN',inSes.session_code,inSes.entry_image_url,inSes.entry_ocr_plate||inSes.entry_plate,null,inSes.status);
    }else GateCameras.clearSession?.('IN');
    if(outSes){
      GateCameras.setSession('OUT',outSes.session_code,outSes.exit_image_url,outSes.exit_ocr_plate||outSes.exit_plate,null,outSes.status);
    }else GateCameras.clearSession?.('OUT');
  }
  await Promise.all(se.flatMap(s=>[protectedImage(s.entry_image_url),protectedImage(s.exit_image_url)]));
  const entryCards=inSes?await api('/cards?search='+encodeURIComponent(inSes.card_uid)):[];
  const entryCard=entryCards.find(c=>c.uid===inSes?.card_uid);
  const latestCommand=s=>cm.find(c=>c.session_id===s?.id&&(c.action==='OPEN'));
  updateGateStation('IN',inSes,latestCommand(inSes),slots.find(x=>x.id===inSes?.slot_id),entryCard);
  updateGateStation('OUT',outSes,latestCommand(outSes),slots.find(x=>x.id===outSes?.slot_id));
  const lane=(g,t)=>{const c=cm.find(x=>x.gate_id===g);let w='<span class="mt">Chưa có lệnh</span>';
    if(c)w=`${c.action} ${bd(c.status)}${['FAILED','EXPIRED'].includes(c.status)&&c.ack_reason?`<br><small class="err">${esc(c.ack_reason)}</small>`:''}`;
    return `<div class="card lane"><h3>${t}</h3>${w}<div class="row"><button class="sm" onclick="C('cmd','${g}','OPEN')">Mở chắn</button><button class="sec sm" onclick="C('cmd','${g}','CLOSE')">Đóng chắn</button><button class="sec sm" onclick="C('cmd','${g}','HOLD_OPEN')">Giữ mở</button><button class="sec sm" onclick="C('cmd','${g}','RESUME_AUTO')">Tự động lại</button></div></div>`};
  const img=u=>imageCache.has(u)?`<img class="th" src="${esc(imageCache.get(u))}" alt="Ảnh biển số">`:'—';
  const rows=se.map(s=>{const out=s.status==='COMPLETED'||s.exit_time;
    const norm=p=>(p||'').toUpperCase().replace(/[^A-Z0-9]/g,'');const ck=s.exit_plate?(norm(s.exit_plate)===norm(s.entry_plate)?'<span class="b ok">Khớp</span>':'<span class="b bad">Lệch</span>'):'—';
    const commands=cm.filter(c=>c.session_id===s.id),last=commands[0],waiting=commands.some(c=>c.status==='SENT');
    const occupied=slots.some(x=>x.id===s.slot_id&&['OCCUPIED','UNKNOWN'].includes(x.status));
    const plateMatches=!!norm(s.entry_plate)&&norm(s.entry_plate)===norm(s.exit_ocr_plate);
    const act=s.status==='ENTRY_PENDING'?`<button class="sm" onclick="C('entryPlate',${s.id})">Xác nhận/sửa biển</button> ${s.entry_plate&&!waiting?`<button class="sm sec" onclick="C('retryGate',${s.id})">Thử mở lại</button>`:''} ${!waiting?`<button class="sm dn" onclick="C('cancelEntry',${s.id},${occupied})">Hủy lượt</button>`:''}`:s.status==='ENTRY_ACKED_WAITING_PASS'?`<button class="sm go" onclick="C('confirmEntry',${s.id})">Đã thấy xe vào</button> <button class="sm dn" onclick="C('cancelEntry',${s.id},${occupied})">Xe không vào</button>`:s.status==='ACTIVE'?`<button class="sm" onclick="C('prepareExit',${s.id})">Chuẩn bị ra thủ công</button>`:s.status==='EXIT_READY'?`${s.is_paid?'<button class="sm go" onclick="C(\'pay\','+s.id+',\'SYSTEM\')">Mở cổng (đã thu)</button>':`<button class="sm go" onclick="C('pay',${s.id},'CASH')">Đã thu tiền mặt</button> <button class="sm sec" onclick="C('pay',${s.id},'SIMULATED_MOMO')">MoMo giả lập</button>`}`:s.status==='PAID_WAITING_EXIT'?`${!waiting?'<button class="sm" onclick="C(\'retryGate\','+s.id+')">Thử mở lại</button>':''} <button class="sm dn" onclick="C('cancelExit',${s.id})">Xe quay đầu</button>`:s.status==='EXIT_ACKED_WAITING_PASS'?`<button class="sm go" onclick="C('confirmExit',${s.id})">Đã thấy xe ra</button> <button class="sm dn" onclick="C('cancelExit',${s.id})">Xe chưa ra</button>`:s.status==='PENDING_REVIEW'?`${plateMatches?`<button class="sm go" onclick="C('confirmExitPlate',${s.id})">Xác nhận biển ra</button>`:`<button class="sm" onclick="C('review',${s.id},true)">Duyệt biển lệch</button>`} <button class="sm dn" onclick="C('review',${s.id},false)">Từ chối</button> <button class="sm dn" onclick="C('cancelSession',${s.id})">Hủy ngoại lệ</button>`:'';
    const entryPlate=s.entry_plate?esc(s.entry_plate):s.entry_ocr_plate?`<span class="mt">OCR: ${esc(s.entry_ocr_plate)}</span>`:'—';
    const exitPlate=s.exit_plate?esc(s.exit_plate):s.exit_ocr_plate?`<span class="mt">OCR: ${esc(s.exit_ocr_plate)}</span>`:'—';
    return tr([esc(s.session_code),esc(s.card_uid),esc(s.slot_id),out?'Ra':'Vào',img(s.entry_image_url),entryPlate,img(s.exit_image_url),exitPlate,ck,dur(s.entry_time,s.exit_time),vnd(s.fee_amount),bd(s.status),last?bd(last.status):s.id===inSes?.id||s.id===outSes?.id?'Xử lý ở làn phía trên':act])});
  return `<h3>Can thiệp cổng</h3><div class="two">${lane('GATE_IN','Làn vào')}${lane('GATE_OUT','Làn ra')}</div><h3>Lượt gần đây</h3>`+tbl(['Phiên','Mã thẻ','Ô được cấp','Chiều','Ảnh vào','Biển vào','Ảnh ra','Biển ra','Đối chiếu','Thời gian gửi','Phí','Trạng thái','Lệnh cổng',''],rows)}

async function pParking(tab){
  if(tab==='payments'){const ps=await api('/payments?limit=100');
    return '<h3>Giao dịch đã ghi nhận</h3>'+tbl(['Mã giao dịch','Phiên ID','Số tiền','Phương thức','Trạng thái','Thu lúc','Nhân viên ID'],
      ps.map(p=>tr([esc(p.transaction_code),p.session_id,vnd(p.amount),esc(p.payment_method),bd(p.payment_status),dt(p.paid_at),p.operator_id??'Hệ thống'])));}
  if(tab==='cards'){const cs=await api('/cards?limit=100');
  return `<form class="f" onsubmit="F(event,'mkCard')"><label>Mã thẻ (UID)<input name="uid" required minlength="4"></label><label>Loại thẻ<select name="card_type"><option value="VISITOR">Vãng lai</option><option value="MONTHLY">Thẻ tháng</option></select></label><label>Chủ thẻ<input name="owner_name"></label><label>Biển số đăng ký cho thẻ tháng<input name="registered_plate"></label><label>Hiệu lực từ<input name="valid_from" type="date"></label><label>Đến hết ngày<input name="valid_until" type="date"></label><button class="go">Đăng ký thẻ</button></form>`+
    tbl(['Mã thẻ','Loại','Chủ thẻ','Biển số đăng ký','Thời hạn','Trạng thái',''],cs.map(c=>tr([esc(c.uid),c.card_type==='MONTHLY'?'Thẻ tháng':'Vãng lai',esc(c.owner_name),esc(c.registered_plate),c.card_type==='MONTHLY'?`${esc(c.valid_from)} – ${esc(c.valid_until)}`:'Theo lượt',c.is_active?bd('ACTIVATED'):'<span class="b bad">Đã khóa</span>',lock('lockCard',c.uid,c.is_active)])))}
  if(tab==='tariffs'){const t=await api('/tariffs');
    return (AD()?`<form class="f" onsubmit="F(event,'mkTariff')"><label>Tên biểu phí<input name="name" required></label><label>Đồng giá mỗi lượt (VNĐ)<input name="flat_rate" type="number" min="0" required></label><label>Áp dụng từ<input name="effective_from" type="datetime-local" required></label><button class="go">Thêm biểu phí</button></form>`:'')+tbl(['Biểu phí','Cách tính','Mức phí','Áp dụng từ'],t.map(x=>tr([esc(x.name),esc(x.fee_type),vnd(x.flat_rate)+'/lượt',dt(x.effective_from)])))}
  const q=new URLSearchParams({limit:100});if(Q.q)q.set('search',Q.q);if(Q.st)q.set('status',Q.st);
  const ss=await api('/sessions?'+q);
  return `<form class="f" onsubmit="event.preventDefault();Q.q=this.q.value;Q.st=this.st.value;render()"><label>Tìm kiếm<input name="q" value="${esc(Q.q==='—'?'':Q.q)}" placeholder="Mã phiên, thẻ, biển số"></label><label>Trạng thái<select name="st">${[['','Tất cả'],['ENTRY_PENDING','Chờ vào'],['ENTRY_ACKED_WAITING_PASS','Chờ xe qua cổng vào'],['ACTIVE','Đang gửi'],['PENDING_REVIEW','Chờ duyệt'],['EXIT_READY','Chờ thu phí'],['PAID_WAITING_EXIT','Chờ ACK ra'],['EXIT_ACKED_WAITING_PASS','Chờ xe qua cổng ra'],['COMPLETED','Hoàn tất'],['CANCELLED','Đã hủy']].map(o=>`<option value="${o[0]}"${Q.st===o[0]?' selected':''}>${o[1]}</option>`).join('')}</select></label><button>Lọc</button></form>`+
  tbl(['Phiên','Mã thẻ','Giờ vào','Giờ ra','Biển vào','Biển ra','Thời lượng','Phí','Thanh toán','Trạng thái'],ss.map(s=>tr([esc(s.session_code),esc(s.card_uid),dt(s.entry_time),dt(s.exit_time),esc(s.entry_plate),esc(s.exit_plate),dur(s.entry_time,s.exit_time),vnd(s.fee_amount),s.is_paid?'<span class="b ok">Đã thanh toán</span>':'<span class="b warn">Chưa thanh toán</span>',bd(s.status)])))}

async function pDevices(){const dv=await api('/devices?limit=100');
  const opt=d=>['REGISTERED','ACTIVATED','MAINTENANCE','SUSPENDED','DECOMMISSIONED'].map(s=>`<option value="${s}"${s===d.status?' selected':''}>${ST[s][0]}</option>`).join('');
  return (AD()?`<form class="f" onsubmit="F(event,'mkDev')"><label>Mã node ESP32<input name="id" required minlength="3"></label><label>Tên thiết bị<input name="name" required></label><label>Vị trí (site)<input name="site_id" value="PTIT_ZONE_1"></label><button class="go">Đăng ký thiết bị</button></form>`:'')+
  tbl(['Mã node','Loại / tên','Vị trí','Kết nối','Liên lạc gần nhất','Vòng đời','Phần cứng','Firmware','Cấu hình hiện tại'],dv.map(d=>{const c=d.config;
    return tr([esc(d.id),esc(d.name),esc(d.site_id),onl(d.is_online)+`<br><small class="mt">${esc(d.ip_address)}</small>`,dt(d.last_heartbeat_at),AD()&&d.status!=='DECOMMISSIONED'?`<select onchange="C('devStatus','${esc(d.id)}',this.value)">${opt(d)}</select>`:bd(d.status),esc(d.hw_version),esc(d.fw_version),c?`v${c.version}: ngưỡng ${c.distance_threshold_cm}cm, nhịp tim ${c.heartbeat_interval_sec}s, còi ${c.buzzer_enabled?'bật':'tắt'}`:'—'])}))}

const spark=a=>{if(a.length<2)return'<span class="mt">Đang thu thập…</span>';const mx=Math.max(...a,20),w=300,h=60;return `<svg class="sp" viewBox="0 0 ${w} ${h}"><polyline fill="none" stroke="currentColor" stroke-width="2" points="${a.map((v,i)=>`${(i/(a.length-1)*w).toFixed(1)},${(h-v/mx*h).toFixed(1)}`).join(' ')}"/></svg>`};
async function dataLive(){const sl=await api('/slots');
  await Promise.all(sl.map(async s=>{
    if(!hist[s.id]){try{const h=await api(`/slots/${s.id}/telemetry?limit=40`);hist[s.id]=h.map(x=>Number(x.distance_cm))}catch{hist[s.id]=[]}}
    const a=hist[s.id]=hist[s.id]||[];
    if(s.last_distance_cm!=null){
      if(!a.length||a[a.length-1]!==Number(s.last_distance_cm))a.push(Number(s.last_distance_cm));
      if(a.length>40)a.shift();
    }
  }));
  return tbl(['Ô đỗ','Trạng thái','Khoảng cách (cm)','Cập nhật','Biểu đồ','Kiểm tra thực tế'],sl.map(s=>tr([esc(s.id),bd(s.status),s.last_distance_cm??'—',dt(s.last_updated_at),spark(hist[s.id]||[]),`<button class="sm sec" onclick="C('slotStatus','${esc(s.id)}')">Cập nhật ô</button>`])))}
async function pMonitor(tab){
  if(tab==='alerts'){const al=await api('/alerts?limit=100');
    return tbl(['Thời gian','Loại sự cố','Mức độ','Nội dung','Thiết bị','Tình trạng','Xử lý'],al.map(a=>tr([dt(a.created_at),AT[a.alert_type],bd(a.severity),`<span style="white-space:normal">${esc(a.message)}</span>`,esc(a.device_id),bd(a.status),a.status==='RESOLVED'?'':(a.status==='NEW'?`<button class="sm sec" onclick="C('alert',${a.id},'ACKNOWLEDGED')">Tiếp nhận</button> `:'')+`<button class="sm" onclick="C('alert',${a.id},'RESOLVED')">Đã xử lý</button>`])))}
  if(tab==='logs'){const dv=await api('/devices');const cm=(await Promise.all(dv.map(d=>api(`/devices/${d.id}/commands?limit=30`)))).flat().sort((a,b)=>b.sent_at.localeCompare(a.sent_at));
    const au=AD()?await api('/audit-logs?limit=50'):null;
    return '<h3 style="margin-top:0">Lệnh điều khiển và phản hồi</h3>'+tbl(['Gửi lúc','Thiết bị','Cổng','Lệnh','Trạng thái','Phản hồi','Người gửi (ID)'],cm.map(c=>tr([dt(c.sent_at),esc(c.device_id),c.gate_id,c.action,bd(c.status),['FAILED','EXPIRED'].includes(c.status)?esc(c.ack_reason||c.ack_status):'—',c.sent_by_user_id??'—'])))+
    (au?'<h3>Thay đổi cấu hình và thao tác người dùng</h3>'+tbl(['Thời gian','Người dùng (ID)','Thao tác','Đối tượng','Chi tiết'],au.map(a=>tr([dt(a.created_at),a.user_id??'—',esc(a.action),esc(a.resource_type+':'+(a.resource_id||'')),`<span style="white-space:normal">${esc(a.details)}</span>`]))):'')}
  return '<div id="live"></div>'}

const PAGES={
  profile:{t:'Hồ sơ cá nhân',lv:'VIEWER',r:pProfile},
  users:{t:'Quản lý tài khoản',lv:'ADMIN',r:pUsers},
  dashboard:{t:'Dashboard bãi xe',lv:'VIEWER',r:()=>'<div id="live"></div>',live:dashLive},
  gate:{t:'Vận hành cổng',lv:'OPERATOR',r:pGate,live:()=>gateLive(),every:3000},
  parking:{t:'Lượt gửi xe, thẻ và biểu phí',lv:'OPERATOR',r:pParking,tabs:[['sessions','Lượt gửi xe'],['cards','Thẻ'],['tariffs','Biểu phí'],['payments','Thanh toán']]},
  devices:{t:'Quản lý thiết bị IoT',lv:'OPERATOR',r:pDevices},
  monitor:{t:'Dữ liệu, cảnh báo và nhật ký',lv:'OPERATOR',r:pMonitor,tabs:[['data','Dữ liệu'],['alerts','Cảnh báo'],['logs','Nhật ký']]}
};
const NAV=[['Quản lý tài khoản'],['profile','Hồ sơ'],['users','Quản lý tài khoản'],['Quản lý và vận hành bãi xe'],['dashboard','Dashboard'],['gate','Vận hành cổng'],['parking','Lượt gửi xe / Thẻ / Biểu phí'],['Quản lý và giám sát thiết bị IoT'],['devices','Quản lý thiết bị'],['monitor','Dữ liệu / Cảnh báo / Nhật ký']];

function loginView(){return `<div class="login"><form onsubmit="doLogin(event)"><h1><span class="lg">P</span>Hệ thống quản lý bãi đỗ xe</h1><label>Tên đăng nhập<input name="username" required autofocus></label><label>Mật khẩu<input name="password" type="password" required></label><button class="go">Đăng nhập</button></form></div>`}

async function render(){
  clearInterval(timer);
  const myEpoch=++renderEpoch;
  if(!S){if(window.GateCameras)GateCameras.unmount();$('#app').innerHTML=loginView();return}
  initWs();
  const[pg='dashboard',t0]=location.hash.slice(2).split('/');const P=PAGES[pg];
  if(!P||LV[S.role]<LV[P.lv]){location.hash='#/dashboard';if(pg!=='dashboard')return}
  const cur=PAGES[P&&LV[S.role]>=LV[P.lv]?pg:'dashboard'],key=P&&LV[S.role]>=LV[P.lv]?pg:'dashboard';
  const tab=cur.tabs?(cur.tabs.some(x=>x[0]===t0)?t0:cur.tabs[0][0]):'';
  $('#app').innerHTML=`<aside><div class="logo"><span><span class="lg">P</span>Smart Parking</span></div>${NAV.map(n=>n.length===1?`<div class="grp">${n[0]}</div>`:LV[S.role]>=LV[PAGES[n[0]].lv]?`<a href="#/${n[0]}" class="${n[0]===key?'on':''}">${n[1]}</a>`:'').join('')}</aside><section><header><h1>${cur.t}</h1><div><span>${esc(S.full_name)}</span><span class="b mute">${RN[S.role]}</span><button class="sec sm" onclick="logout()">Đăng xuất</button></div></header><main id="main"><p class="mt">Đang tải…</p></main></section>`;
  const m=$('#main');
  try{
    const tabs=cur.tabs?`<nav class="tabs">${cur.tabs.map(x=>`<a href="#/${key}/${x[0]}" class="${x[0]===tab?'on':''}">${x[1]}</a>`).join('')}</nav>`:'';
    const content=await cur.r(tab);
    if(myEpoch!==renderEpoch)return;
    m.innerHTML=tabs+content;
    if(window.GateCameras){
      if(key==='gate'){
        GateCameras.mount({
          apiBase: API,
          session: S,
          onUnauthorized: ()=>logout(),
          onCapture: gateRefresh
        });
      }else{
        GateCameras.unmount();
      }
    }
    const live=cur.live||(key==='monitor'&&tab==='data'?dataLive:null);
    if(live){const f=async()=>{if(myEpoch!==renderEpoch)return;const l=$('#live');if(!l)return;try{const html=await live(tab);if(myEpoch!==renderEpoch)return;l.innerHTML=html}catch(x){if(myEpoch!==renderEpoch)return;l.innerHTML=`<div class="err">${esc(x.message)}</div>`}};f();timer=setInterval(f,cur.every||5000)}
  }catch(x){if(window.GateCameras&&key!=='gate')GateCameras.unmount();if(myEpoch===renderEpoch)m.innerHTML=`<div class="err">${esc(x.message==='Failed to fetch'?'Không kết nối được backend tại '+API+'. Kiểm tra backend đang chạy (uvicorn) và địa chỉ API.':x.message)}</div>`}
}
window.addEventListener('hashchange',render);
render();
