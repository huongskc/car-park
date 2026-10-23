'use strict';
const $=s=>document.querySelector(s);
const LV={VIEWER:1,OPERATOR:2,ADMIN:3};
const RN={ADMIN:'Quản trị viên',OPERATOR:'Nhân viên vận hành',VIEWER:'Người xem'};
let API=localStorage.getItem('api')||'http://localhost:8000/api/v1';
let S=JSON.parse(localStorage.getItem('sess')||'null');
let timer=null,Q={q:'',st:''};const hist={};
const esc=s=>String(s??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const dt=s=>s?new Date(s).toLocaleString('vi-VN'):'—';
const vnd=v=>Number(v||0).toLocaleString('vi-VN')+'đ';
const dur=(a,b)=>{const m=Math.max(0,Math.round(((b?new Date(b):new Date())-new Date(a))/6e4));return Math.floor(m/60)+'g '+String(m%60).padStart(2,'0')+'p'};
const ST={EMPTY:['Trống','ok'],OCCUPIED:['Có xe','bad'],RESERVED:['Đặt trước','warn'],MAINTENANCE:['Bảo trì','mute'],ACTIVE:['Đang gửi','ok'],COMPLETED:['Hoàn tất','mute'],PENDING_REVIEW:['Chờ duyệt','warn'],CANCELLED:['Đã hủy','bad'],REGISTERED:['Đã đăng ký','mute'],ACTIVATED:['Hoạt động','ok'],SUSPENDED:['Tạm ngưng','warn'],DECOMMISSIONED:['Ngừng dùng','bad'],NEW:['Mới','bad'],ACKNOWLEDGED:['Đã tiếp nhận','warn'],RESOLVED:['Đã xử lý','ok'],INFO:['Thông tin','mute'],WARNING:['Cảnh báo','warn'],CRITICAL:['Nghiêm trọng','bad'],SENT:['Đã gửi','warn'],ACKED:['Đã phản hồi','ok'],FAILED:['Thất bại','bad'],PENDING:['Chờ','mute'],EXPIRED:['Hết hạn','mute']};
const AT={PLATE_MISMATCH:'Lệch biển số',UNAUTHORIZED_CARD:'Thẻ không hợp lệ',DEVICE_OFFLINE:'Mất kết nối',LONG_OCCUPANCY:'Chiếm chỗ lâu',SYSTEM_ERROR:'Lỗi hệ thống / cảm biến'};
const bd=v=>{const[t,c]=ST[v]||[v,'mute'];return `<span class="b ${c}">${esc(t)}</span>`};
const onl=o=>`<span class="b ${o?'ok':'bad'}">${o?'Online':'Offline'}</span>`;
const tr=c=>`<tr>${c.map(x=>`<td>${x}</td>`).join('')}</tr>`;
const tbl=(h,r)=>`<div class="tw"><table><thead><tr>${h.map(x=>`<th>${x}</th>`).join('')}</tr></thead><tbody>${r.length?r.join(''):`<tr><td colspan="${h.length}" class="mt">Chưa có dữ liệu.</td></tr>`}</tbody></table></div>`;
const kpi=(l,v,c)=>`<div class="kpi ${c}"><b>${v}</b>${l}</div>`;
const OP=()=>LV[S.role]>=2, AD=()=>S.role==='ADMIN';
const origin=()=>API.replace(/\/api\/v1$/,'');
function toast(m,err){const t=$('#toast');t.textContent=m;t.className='on'+(err?' e':'');clearTimeout(toast.t);toast.t=setTimeout(()=>t.className='',4000)}
async function api(p,o={}){
  if(S?.demo)return mock(p,o);
  const r=await fetch(API+p,{method:o.method||'GET',headers:{'Content-Type':'application/json',...(S?{Authorization:'Bearer '+S.access_token}:{})},body:o.body?JSON.stringify(o.body):undefined});
  if(r.status===401&&S){logout();throw new Error('Phiên đăng nhập đã hết hạn.')}
  const d=await r.json().catch(()=>({}));
  if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:'Dữ liệu gửi lên không hợp lệ.');
  return d;
}
function logout(){S=null;localStorage.removeItem('sess');render()}
async function doLogin(ev){ev.preventDefault();const d=Object.fromEntries(new FormData(ev.target));
  API=d.api.replace(/\/$/,'');localStorage.setItem('api',API);S=null;
  try{S=await api('/auth/login',{method:'POST',body:{username:d.username,password:d.password}});localStorage.setItem('sess',JSON.stringify(S));location.hash='#/dashboard';render()}
  catch(x){toast(x.message==='Failed to fetch'?'Không kết nối được backend tại '+API:x.message,1)}}
async function F(ev,n){ev.preventDefault();const d=Object.fromEntries(new FormData(ev.target));try{toast(await ACT[n](d));render()}catch(x){toast(x.message,1)}}
async function C(n,...a){try{toast(await ACT[n](...a));render()}catch(x){toast(x.message,1)}}
const nul=v=>v||null;
const ACT={
  mkUser:d=>api('/users',{method:'POST',body:{...d,email:nul(d.email)}}).then(()=>'Đã tạo tài khoản.'),
  lockUser:(id,v)=>api(`/users/${id}/status`,{method:'PATCH',body:{is_active:v}}).then(()=>'Đã cập nhật tài khoản.'),
  mkCard:d=>api('/cards',{method:'POST',body:{...d,owner_name:nul(d.owner_name),registered_plate:nul(d.registered_plate)}}).then(()=>'Đã đăng ký thẻ.'),
  lockCard:(u,v)=>api(`/cards/${u}/status`,{method:'PATCH',body:{is_active:v}}).then(()=>'Đã cập nhật thẻ.'),
  enter:d=>api('/sessions',{method:'POST',body:{card_uid:d.card_uid,entry_plate:nul(d.entry_plate),slot_id:nul(d.slot_id)}}).then(s=>'Đã tạo phiên '+s.session_code+'.'),
  pay:(id,m)=>api(`/sessions/${id}/checkout`,{method:'POST',body:{payment_method:m}}).then(()=>'Đã thu phí và kết thúc phiên.'),
  review:(id,ok)=>{const note=prompt(ok?'Lý do cho xe ra:':'Lý do từ chối:');if(!note)throw new Error('Cần nhập lý do xử lý.');return api(`/sessions/${id}/resolve-plate`,{method:'PATCH',body:{approved:ok,note}}).then(()=>'Đã xử lý ngoại lệ.')},
  cmd:(g,a)=>{const id=$('#dev')?.value;if(!id)throw new Error('Chưa chọn thiết bị.');return api(`/devices/${id}/commands`,{method:'POST',body:{gate_id:g,action:a,reason:'Thao tác từ web'}}).then(c=>'Đã gửi lệnh '+a+' ('+c.id+').')},
  devStatus:(id,s)=>api(`/devices/${id}/status`,{method:'PATCH',body:{status:s}}).then(()=>'Đã đổi trạng thái vòng đời.'),
  mkDev:d=>api('/devices',{method:'POST',body:d}).then(x=>'Đã đăng ký thiết bị. Token: '+x.auth_token),
  alert:(id,s)=>api(`/alerts/${id}/resolve`,{method:'PATCH',body:{status:s}}).then(()=>'Đã cập nhật cảnh báo.')
};
const lock=(fn,id,on)=>`<button class="sm sec" onclick="C('${fn}','${esc(id)}',${!on})">${on?'Khóa':'Mở khóa'}</button>`;

/* ---------- Trang ---------- */
async function pProfile(){const u=await api('/auth/me');
  return `<div class="card prof"><div class="av">${esc(u.full_name[0])}</div><h2>${esc(u.full_name)}</h2><dl><dt>Tên đăng nhập</dt><dd>${esc(u.username)}</dd><dt>Email</dt><dd>${esc(u.email)}</dd><dt>Vai trò</dt><dd>${RN[u.role]}</dd><dt>Trạng thái</dt><dd>${u.is_active?bd('ACTIVATED'):'Bị khóa'}</dd><dt>Ngày tạo</dt><dd>${dt(u.created_at)}</dd></dl></div>`}

async function pUsers(){const us=await api('/users?limit=100');
  return `<form class="f" onsubmit="F(event,'mkUser')"><label>Tên đăng nhập<input name="username" required minlength="3"></label><label>Mật khẩu<input name="password" type="password" required minlength="6"></label><label>Họ và tên<input name="full_name" required></label><label>Email<input name="email" type="email"></label><label>Vai trò<select name="role"><option value="OPERATOR">Nhân viên vận hành</option><option value="VIEWER">Người xem</option><option value="ADMIN">Quản trị viên</option></select></label><button class="go">Tạo tài khoản</button></form>`+
  tbl(['ID','Tên đăng nhập','Họ tên','Email','Vai trò','Trạng thái',''],us.map(u=>tr([u.id,esc(u.username),esc(u.full_name),esc(u.email),RN[u.role],u.is_active?bd('ACTIVATED'):'<span class="b bad">Bị khóa</span>',u.id===S.user_id?'':lock('lockUser',u.id,u.is_active)])))}

async function dashLive(){const op=OP();
  const[sl,se,al,dv]=await Promise.all([api('/slots'),op?api('/sessions?status=ACTIVE&limit=100'):[],op?api('/alerts?status=NEW&limit=5'):[],op?api('/devices'):[]]);
  const n=s=>sl.filter(x=>x.status===s).length;
  return `<div class="kpis">${kpi('Ô trống',n('EMPTY'),'ok')}${kpi('Có xe',n('OCCUPIED'),'bad')}${kpi('Chưa xác định',sl.length-n('EMPTY')-n('OCCUPIED'),'warn')}${op?kpi('Xe đang gửi',se.length,''):''}</div>
  <h3>Sơ đồ ô đỗ</h3><div class="slots">${sl.map(s=>`<div class="slot ${ST[s.status]?.[1]||''}"><b>${esc(s.id)}</b><small>${esc(s.name)}</small>${bd(s.status)}<small>${s.last_distance_cm??'—'} cm</small></div>`).join('')}</div>`+
  (op?`<h3>Thiết bị và cổng</h3>${tbl(['Thiết bị','Kết nối','Cổng vào/ra','Vòng đời'],dv.map(d=>tr([esc(d.id),onl(d.is_online),d.is_online&&d.status==='ACTIVATED'?'<span class="b ok">Sẵn sàng</span>':'<span class="b warn">Không khả dụng</span>',bd(d.status)])))}
  <h3>Cảnh báo mới</h3>${tbl(['Thời gian','Loại','Mức độ','Nội dung'],al.map(a=>tr([dt(a.created_at),AT[a.alert_type],bd(a.severity),`<span style="white-space:normal">${esc(a.message)}</span>`])))}`:'<p class="note">Tài khoản Người xem chỉ xem được sơ đồ ô đỗ.</p>')}

async function pGate(){const dv=await api('/devices');
  return `<form class="f" onsubmit="F(event,'enter')"><label>Thiết bị điều khiển<select id="dev" onchange="gateRefresh()">${dv.map(d=>`<option>${esc(d.id)}</option>`).join('')}</select></label><label>Mã thẻ (quẹt vào)<input name="card_uid" required minlength="4"></label><label>Biển số lúc vào<input name="entry_plate"></label><label>Ô đỗ<input name="slot_id" placeholder="SLOT_A1"></label><button class="go">Tạo lượt vào</button></form><div id="live"></div>`}
const gateRefresh=async()=>{const l=$('#live');if(l)try{l.innerHTML=await gateLive()}catch(x){l.innerHTML=`<div class="err">${esc(x.message)}</div>`}};
async function gateLive(){const id=$('#dev')?.value;
  const[cm,se]=await Promise.all([id?api(`/devices/${id}/commands?limit=20`):[],api('/sessions?limit=30')]);
  const lane=(g,t)=>{const c=cm.find(x=>x.gate_id===g);let w='<span class="mt">Chưa có lệnh</span>';
    if(c){w=`${c.action} ${bd(c.status)}<br><small class="mt">${esc(c.ack_reason||'Chờ phản hồi từ ESP32')}</small>`;
      if(c.action==='OPEN'){const r=30-(Date.now()-new Date(c.sent_at))/1e3;w+=`<div class="cd">${r>0?'Tự đóng sau '+Math.ceil(r)+'s':'Đã hết 30s chờ đóng'}</div>`}}
    return `<div class="card lane"><h3>${t}</h3>${w}<div class="row"><button onclick="C('cmd','${g}','OPEN')">Mở chắn</button><button class="sec" onclick="C('cmd','${g}','CLOSE')">Đóng chắn</button></div></div>`};
  const img=u=>u?`<img class="th" src="${esc(origin()+u)}" alt="" onerror="this.remove()">`:'—';
  const rows=se.map(s=>{const out=s.status==='COMPLETED'||s.exit_time;
    const ck=s.exit_plate?(s.exit_plate===s.entry_plate?'<span class="b ok">Khớp</span>':'<span class="b bad">Lệch</span>'):'—';
    const act=s.status==='ACTIVE'?`<button class="sm go" onclick="C('pay',${s.id},'CASH')">Thu tiền mặt</button> <button class="sm sec" onclick="C('pay',${s.id},'SIMULATED_MOMO')">MoMo</button>`:s.status==='PENDING_REVIEW'?`<button class="sm" onclick="C('review',${s.id},true)">Duyệt</button> <button class="sm dn" onclick="C('review',${s.id},false)">Từ chối</button>`:'';
    return tr([esc(s.session_code),esc(s.card_uid),out?'Ra':'Vào',img(s.entry_image_url),esc(s.entry_plate),esc(s.exit_plate),ck,dur(s.entry_time,s.exit_time),vnd(s.fee_amount),bd(s.status),act])});
  return `<div class="two">${lane('GATE_IN','Làn vào')}${lane('GATE_OUT','Làn ra')}</div><h3>Lượt gần đây</h3>`+tbl(['Phiên','Mã thẻ','Chiều','Ảnh vào','Biển vào','Biển ra','Đối chiếu','Thời gian gửi','Phí','Trạng thái',''],rows)}

async function pParking(tab){
  if(tab==='cards'){const cs=await api('/cards?limit=100');
    return `<form class="f" onsubmit="F(event,'mkCard')"><label>Mã thẻ (UID)<input name="uid" required minlength="4"></label><label>Loại thẻ<select name="card_type"><option value="VISITOR">Vãng lai</option><option value="MONTHLY">Thẻ tháng</option></select></label><label>Chủ thẻ<input name="owner_name"></label><label>Biển số đăng ký<input name="registered_plate"></label><button class="go">Đăng ký thẻ</button></form>`+
    tbl(['Mã thẻ','Loại','Chủ thẻ','Biển số đăng ký','Thời hạn','Trạng thái',''],cs.map(c=>tr([esc(c.uid),c.card_type==='MONTHLY'?'Thẻ tháng':'Vãng lai',esc(c.owner_name),esc(c.registered_plate),c.card_type==='MONTHLY'?'Từ '+dt(c.created_at):'Theo lượt',c.is_active?bd('ACTIVATED'):'<span class="b bad">Đã khóa</span>',lock('lockCard',c.uid,c.is_active)])))}
  if(tab==='tariffs'){let t,mock=0;try{t=await api('/tariffs')}catch{mock=1;t=[{name:'Vé lượt Ô tô Tiêu chuẩn',fee_type:'FLAT',flat_rate:20000,hourly_rate:0,grace_period_mins:5,effective_from:null}]}
    return (mock?'<p class="note">Backend chưa có endpoint /tariffs nên đang hiển thị biểu phí mẫu từ seed.sql.</p>':'')+tbl(['Biểu phí','Cách tính','Mức phí','Ân hạn','Áp dụng từ'],t.map(x=>tr([esc(x.name),x.fee_type==='FLAT'?'Đồng giá mỗi lượt':'Theo giờ',x.fee_type==='FLAT'?vnd(x.flat_rate)+'/lượt':vnd(x.hourly_rate)+'/giờ',x.grace_period_mins+' phút',dt(x.effective_from)])))+'<p class="note">Thẻ tháng được miễn phí mỗi lượt.</p>'}
  const q=new URLSearchParams({limit:100});if(Q.q)q.set('search',Q.q);if(Q.st)q.set('status',Q.st);
  const ss=await api('/sessions?'+q);
  return `<form class="f" onsubmit="event.preventDefault();Q.q=this.q.value;Q.st=this.st.value;render()"><label>Tìm kiếm<input name="q" value="${esc(Q.q==='—'?'':Q.q)}" placeholder="Mã phiên, thẻ, biển số"></label><label>Trạng thái<select name="st">${[['','Tất cả'],['ACTIVE','Đang gửi'],['COMPLETED','Hoàn tất'],['PENDING_REVIEW','Chờ duyệt'],['CANCELLED','Đã hủy']].map(o=>`<option value="${o[0]}"${Q.st===o[0]?' selected':''}>${o[1]}</option>`).join('')}</select></label><button>Lọc</button></form>`+
  tbl(['Phiên','Mã thẻ','Giờ vào','Giờ ra','Biển vào','Biển ra','Thời lượng','Phí','Thanh toán','Trạng thái'],ss.map(s=>tr([esc(s.session_code),esc(s.card_uid),dt(s.entry_time),dt(s.exit_time),esc(s.entry_plate),esc(s.exit_plate),dur(s.entry_time,s.exit_time),vnd(s.fee_amount),s.is_paid?'<span class="b ok">Đã thanh toán</span>':'<span class="b warn">Chưa thanh toán</span>',bd(s.status)])))}

async function pDevices(){const dv=await api('/devices?limit=100');
  const opt=d=>['REGISTERED','ACTIVATED','MAINTENANCE','SUSPENDED','DECOMMISSIONED'].map(s=>`<option value="${s}"${s===d.status?' selected':''}>${ST[s][0]}</option>`).join('');
  return (AD()?`<form class="f" onsubmit="F(event,'mkDev')"><label>Mã node ESP32<input name="id" required minlength="3"></label><label>Tên thiết bị<input name="name" required></label><label>Vị trí (site)<input name="site_id" value="PTIT_ZONE_1"></label><button class="go">Đăng ký thiết bị</button></form>`:'')+
  tbl(['Mã node','Loại / tên','Vị trí','Kết nối','Liên lạc gần nhất','Vòng đời','Phần cứng','Firmware','Cấu hình hiện tại'],dv.map(d=>{const c=d.config;
    return tr([esc(d.id),esc(d.name),esc(d.site_id),onl(d.is_online)+`<br><small class="mt">${esc(d.ip_address)}</small>`,dt(d.last_heartbeat_at),AD()&&d.status!=='DECOMMISSIONED'?`<select onchange="C('devStatus','${esc(d.id)}',this.value)">${opt(d)}</select>`:bd(d.status),esc(d.hw_version),esc(d.fw_version),c?`v${c.version}: ngưỡng ${c.distance_threshold_cm}cm, servo ${c.servo_open_time_sec}s, nhịp tim ${c.heartbeat_interval_sec}s, còi ${c.buzzer_enabled?'bật':'tắt'}`:'—'])}))}

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
  return tbl(['Ô đỗ','Trạng thái','Khoảng cách (cm)','Cập nhật','Biểu đồ'],sl.map(s=>tr([esc(s.id),bd(s.status),s.last_distance_cm??'—',dt(s.last_updated_at),spark(hist[s.id]||[])])))+'<p class="note">Biểu đồ được đồng bộ từ lịch sử đo telemetry của backend và cập nhật thời gian thực mỗi 5 giây.</p>'}
async function pMonitor(tab){
  if(tab==='alerts'){const al=await api('/alerts?limit=100');
    return tbl(['Thời gian','Loại sự cố','Mức độ','Nội dung','Thiết bị','Tình trạng','Xử lý'],al.map(a=>tr([dt(a.created_at),AT[a.alert_type],bd(a.severity),`<span style="white-space:normal">${esc(a.message)}</span>`,esc(a.device_id),bd(a.status),a.status==='RESOLVED'?'':(a.status==='NEW'?`<button class="sm sec" onclick="C('alert',${a.id},'ACKNOWLEDGED')">Tiếp nhận</button> `:'')+`<button class="sm" onclick="C('alert',${a.id},'RESOLVED')">Đã xử lý</button>`])))}
  if(tab==='logs'){const dv=await api('/devices');const cm=(await Promise.all(dv.map(d=>api(`/devices/${d.id}/commands?limit=30`)))).flat().sort((a,b)=>b.sent_at.localeCompare(a.sent_at));
    const au=AD()?await api('/audit-logs?limit=50'):null;
    return '<h3 style="margin-top:0">Lệnh điều khiển và phản hồi</h3>'+tbl(['Gửi lúc','Thiết bị','Cổng','Lệnh','Trạng thái','Phản hồi','Người gửi (ID)'],cm.map(c=>tr([dt(c.sent_at),esc(c.device_id),c.gate_id,c.action,bd(c.status),esc(c.ack_reason||c.ack_status),c.sent_by_user_id??'—'])))+
    (au?'<h3>Thay đổi cấu hình và thao tác người dùng</h3>'+tbl(['Thời gian','Người dùng (ID)','Thao tác','Đối tượng','Chi tiết'],au.map(a=>tr([dt(a.created_at),a.user_id??'—',esc(a.action),esc(a.resource_type+':'+(a.resource_id||'')),`<span style="white-space:normal">${esc(a.details)}</span>`]))):'<p class="note">Nhật ký thao tác người dùng chỉ dành cho Quản trị viên.</p>')}
  return '<div id="live"></div>'}

const PAGES={
  profile:{t:'Hồ sơ cá nhân',lv:'VIEWER',r:pProfile},
  users:{t:'Quản lý tài khoản',lv:'ADMIN',r:pUsers},
  dashboard:{t:'Dashboard bãi xe',lv:'VIEWER',r:()=>'<div id="live"></div>',live:dashLive},
  gate:{t:'Vận hành cổng',lv:'OPERATOR',r:pGate,live:()=>gateLive(),every:3000},
  parking:{t:'Lượt gửi xe, thẻ và biểu phí',lv:'OPERATOR',r:pParking,tabs:[['sessions','Lượt gửi xe'],['cards','Thẻ'],['tariffs','Biểu phí']]},
  devices:{t:'Quản lý thiết bị IoT',lv:'OPERATOR',r:pDevices},
  monitor:{t:'Dữ liệu, cảnh báo và nhật ký',lv:'OPERATOR',r:pMonitor,tabs:[['data','Dữ liệu'],['alerts','Cảnh báo'],['logs','Nhật ký']]}
};
const NAV=[['Quản lý tài khoản'],['profile','Hồ sơ'],['users','Quản lý tài khoản'],['Quản lý và vận hành bãi xe'],['dashboard','Dashboard'],['gate','Vận hành cổng'],['parking','Lượt gửi xe / Thẻ / Biểu phí'],['Quản lý và giám sát thiết bị IoT'],['devices','Quản lý thiết bị'],['monitor','Dữ liệu / Cảnh báo / Nhật ký']];

function loginView(){return `<div class="login"><form onsubmit="doLogin(event)"><h1><span class="lg">P</span>Hệ thống quản lý bãi đỗ xe</h1><label>Tên đăng nhập<input name="username" required autofocus></label><label>Mật khẩu<input name="password" type="password" required></label><label>Địa chỉ API backend<input name="api" value="${esc(API)}" required></label><button class="go">Đăng nhập</button><p class="note">Chưa có backend? Xem thử bằng dữ liệu mẫu:</p><div class="row" style="margin:0"><button type="button" class="sec sm" onclick="demo('ADMIN')">Admin</button><button type="button" class="sec sm" onclick="demo('OPERATOR')">Vận hành</button><button type="button" class="sec sm" onclick="demo('VIEWER')">Người xem</button></div></form></div>`}
function demo(role){S={access_token:'demo',demo:true,role,username:role.toLowerCase(),full_name:{ADMIN:'Quản trị viên (demo)',OPERATOR:'Nhân viên vận hành (demo)',VIEWER:'Người xem (demo)'}[role],user_id:{ADMIN:1,OPERATOR:2,VIEWER:3}[role]};localStorage.setItem('sess',JSON.stringify(S));location.hash='#/dashboard';render()}

async function render(){
  clearInterval(timer);
  if(!S){$('#app').innerHTML=loginView();return}
  const[pg='dashboard',t0]=location.hash.slice(2).split('/');const P=PAGES[pg];
  if(!P||LV[S.role]<LV[P.lv]){location.hash='#/dashboard';if(pg!=='dashboard')return}
  const cur=PAGES[P&&LV[S.role]>=LV[P.lv]?pg:'dashboard'],key=P&&LV[S.role]>=LV[P.lv]?pg:'dashboard';
  const tab=cur.tabs?(cur.tabs.some(x=>x[0]===t0)?t0:cur.tabs[0][0]):'';
  $('#app').innerHTML=`<aside><div class="logo"><span><span class="lg">P</span>Smart Parking</span><small>PTIT, IoT nhóm 08</small></div>${NAV.map(n=>n.length===1?`<div class="grp">${n[0]}</div>`:LV[S.role]>=LV[PAGES[n[0]].lv]?`<a href="#/${n[0]}" class="${n[0]===key?'on':''}">${n[1]}</a>`:'').join('')}</aside><section><header><h1>${cur.t}</h1><div><span>${esc(S.full_name)}</span><span class="b mute">${RN[S.role]}</span><button class="sec sm" onclick="logout()">Đăng xuất</button></div></header><main id="main"><p class="mt">Đang tải…</p></main></section>`;
  const m=$('#main');
  try{
    const tabs=cur.tabs?`<nav class="tabs">${cur.tabs.map(x=>`<a href="#/${key}/${x[0]}" class="${x[0]===tab?'on':''}">${x[1]}</a>`).join('')}</nav>`:'';
    m.innerHTML=tabs+await cur.r(tab);
    const live=cur.live||(key==='monitor'&&tab==='data'?dataLive:null);
    if(live){const f=async()=>{const l=$('#live');if(!l)return;try{l.innerHTML=await live(tab)}catch(x){l.innerHTML=`<div class="err">${esc(x.message)}</div>`}};f();timer=setInterval(f,cur.every||5000)}
  }catch(x){m.innerHTML=`<div class="err">${esc(x.message==='Failed to fetch'?'Không kết nối được backend tại '+API+'. Kiểm tra backend đang chạy (uvicorn) và địa chỉ API.':x.message)}</div>`}
}
/* ---------- Chế độ xem thử: dữ liệu mẫu trong trình duyệt ---------- */
const ago=m=>new Date(Date.now()-m*6e4).toISOString();let seq=100;
const mkSlot=(id,n,st,d)=>({id,name:n,site_id:'PTIT_ZONE_1',device_id:'ESP32_GATE_01',status:st,last_distance_cm:d,last_updated_at:ago(1)});
const mkSes=(id,c,st,en,ex,pe,px,fee,paid,sl)=>({id,session_code:'SES_'+id+'DEMO',card_uid:c,slot_id:sl,tariff_id:1,status:st,entry_time:ago(en),entry_plate:pe,entry_image_url:null,exit_time:ex?ago(ex):null,exit_plate:px,exit_image_url:null,fee_amount:fee,is_paid:paid,review_reason:null,reviewed_by:null});
const mkAl=(id,t,sv,m,st)=>({id,alert_type:t,severity:sv,message:m,device_id:'ESP32_GATE_01',session_id:null,status:st,resolved_by:null,created_at:ago(id*7),resolved_at:null});
const D={
  users:[[1,'admin','Quản trị viên Hệ thống','ADMIN'],[2,'operator','Nhân viên Trực bãi','OPERATOR'],[3,'viewer','Người xem Giám sát','VIEWER']].map(u=>({id:u[0],username:u[1],full_name:u[2],email:u[1]+'@ptit.edu.vn',role:u[3],is_active:true,created_at:ago(9e4)})),
  slots:[mkSlot('SLOT_A1','Ô đỗ Ô tô Số 1 (Làn chính)','OCCUPIED',11.2),mkSlot('SLOT_A2','Ô đỗ Ô tô Số 2','EMPTY',48),mkSlot('SLOT_A3','Ô đỗ Ô tô Số 3','MAINTENANCE',30)],
  cards:[['A1B2C3D4','VISITOR','Khách vãng lai 01',null,true],['E5F6G7H8','MONTHLY','Nguyễn Văn Hưởng','30A-999.88',true],['F9E8D7C6','VISITOR','Khách vãng lai 02',null,false]].map(c=>({uid:c[0],card_type:c[1],owner_name:c[2],registered_plate:c[3],is_active:c[4],created_at:ago(9e4),updated_at:ago(9e4)})),
  sessions:[mkSes(1,'A1B2C3D4','ACTIVE',95,0,'30A-123.45',null,20000,false,'SLOT_A1'),mkSes(2,'E5F6G7H8','PENDING_REVIEW',40,0,'30A-999.88','30A-999.80',0,false,null),mkSes(3,'E5F6G7H8','COMPLETED',300,240,'30A-999.88','30A-999.88',0,true,null)],
  alerts:[mkAl(1,'UNAUTHORIZED_CARD','WARNING','Phát hiện quẹt thẻ đã bị khóa: UID F9E8D7C6 tại cổng ENTRY.','NEW'),mkAl(2,'PLATE_MISMATCH','WARNING','Xe ra biển số 30A-999.80 không khớp lúc vào 30A-999.88.','NEW'),mkAl(3,'DEVICE_OFFLINE','CRITICAL','Thiết bị ESP32_GATE_01 mất kết nối quá 25 giây.','RESOLVED')],
  devices:[{id:'ESP32_GATE_01',name:'Bộ điều khiển Cổng Vào/Ra & Ô đỗ A1',site_id:'PTIT_ZONE_1',auth_token:'dtk_demo',status:'ACTIVATED',is_online:true,ip_address:'192.168.137.50',fw_version:'1.0.0',hw_version:'ESP32-WROOM-32',last_heartbeat_at:ago(0.1),config:{version:1,distance_threshold_cm:15,servo_open_time_sec:10,heartbeat_interval_sec:10,buzzer_enabled:true}}],
  cmds:[{id:'cmd_demo1',device_id:'ESP32_GATE_01',gate_id:'GATE_IN',action:'OPEN',status:'ACKED',sent_by_user_id:2,ack_status:'SUCCESS',ack_reason:'Barrier opened (demo)',sent_at:ago(30),acked_at:ago(30)}],
  audit:[{id:1,user_id:1,action:'SYSTEM_INITIALIZE',resource_type:'DATABASE',resource_id:'smart_parking_db',details:'Khởi tạo CSDL và nạp dữ liệu mẫu',created_at:ago(9e4)},{id:2,user_id:2,action:'BARRIER_OPEN',resource_type:'devices',resource_id:'ESP32_GATE_01',details:'Phát lệnh OPEN cho cổng GATE_IN',created_at:ago(30)}]
};
async function mock(p,o){
  await new Promise(r=>setTimeout(r,100));
  const m=o.method||'GET',[path,qs]=p.split('?'),q=new URLSearchParams(qs||''),b=o.body||{},[r,id,sub]=path.split('/').filter(Boolean);
  const by=(a,k,v)=>a.find(x=>String(x[k])===String(v));
  const need=x=>{if(!x)throw new Error('Không tìm thấy bản ghi.');return x};
  const lim=a=>a.slice(0,Number(q.get('limit')||50));
  if(r==='auth')return need(by(D.users,'username',S.username));
  if(r==='users'){
    if(m==='POST'){if(by(D.users,'username',b.username))throw new Error('Tên đăng nhập đã tồn tại.');const u={...b,password:undefined,id:++seq,is_active:true,created_at:ago(0)};D.users.push(u);return u}
    if(sub)return Object.assign(need(by(D.users,'id',id)),{is_active:b.is_active});
    return lim(D.users)}
  if(r==='slots'){D.slots.forEach(x=>{if(x.status!=='MAINTENANCE'){x.last_distance_cm=+(x.status==='OCCUPIED'?8+Math.random()*6:35+Math.random()*20).toFixed(1);x.last_updated_at=ago(0)}});return D.slots}
  if(r==='cards'){
    if(m==='POST'){if(by(D.cards,'uid',b.uid))throw new Error('Mã UID thẻ đã tồn tại.');const c={...b,is_active:true,created_at:ago(0),updated_at:ago(0)};D.cards.unshift(c);return c}
    if(sub)return Object.assign(need(by(D.cards,'uid',id)),{is_active:b.is_active});
    return lim(D.cards)}
  if(r==='sessions'){
    if(m==='POST'&&!id){const c=need(by(D.cards,'uid',b.card_uid));if(!c.is_active)throw new Error('Thẻ đang bị khóa hoặc báo mất.');if(D.sessions.some(x=>x.card_uid===c.uid&&['ACTIVE','PENDING_REVIEW'].includes(x.status)))throw new Error('Thẻ đang nằm trong phiên gửi xe chưa kết thúc.');const sl=by(D.slots,'id',b.slot_id);if(sl)sl.status='OCCUPIED';const x=mkSes(++seq,c.uid,'ACTIVE',0,0,b.entry_plate,null,c.card_type==='MONTHLY'?0:20000,false,b.slot_id);D.sessions.unshift(x);return x}
    if(sub==='checkout'){const x=need(by(D.sessions,'id',id));if(x.status==='PENDING_REVIEW')throw new Error('Phiên đang chờ duyệt ngoại lệ.');if(x.is_paid)throw new Error('Phiên đã được thanh toán.');Object.assign(x,{is_paid:true,status:'COMPLETED',exit_time:ago(0)});const sl=by(D.slots,'id',x.slot_id);if(sl)sl.status='EMPTY';return x}
    if(sub==='resolve-plate')return Object.assign(need(by(D.sessions,'id',id)),{status:b.approved?'ACTIVE':'CANCELLED',review_reason:b.note,reviewed_by:S.user_id});
    let a=D.sessions;const st=q.get('status'),se=(q.get('search')||'').toLowerCase();
    if(st)a=a.filter(x=>x.status===st);
    if(se)a=a.filter(x=>[x.session_code,x.card_uid,x.entry_plate,x.exit_plate].some(v=>(v||'').toLowerCase().includes(se)));
    return lim(a)}
  if(r==='alerts'){
    if(sub)return Object.assign(need(by(D.alerts,'id',id)),{status:b.status,resolved_by:S.user_id,resolved_at:ago(0)});
    const st=q.get('status');return lim(st?D.alerts.filter(x=>x.status===st):D.alerts)}
  if(r==='audit-logs')return D.audit;
  if(r==='devices'){
    if(!id){if(m==='POST'){const d={...b,auth_token:'dtk_demo_'+(++seq),status:'REGISTERED',is_online:false,ip_address:null,hw_version:'ESP32-WROOM-32',fw_version:'1.0.0',last_heartbeat_at:null,config:{version:1,distance_threshold_cm:15,servo_open_time_sec:10,heartbeat_interval_sec:10,buzzer_enabled:true}};D.devices.push(d);return d}return lim(D.devices)}
    const d=need(by(D.devices,'id',id));
    if(sub==='status'){d.status=b.status;if(b.status!=='ACTIVATED')d.is_online=false;return d}
    if(sub==='commands'){
      if(m==='POST'){if(!d.is_online)throw new Error('Thiết bị đang ngoại tuyến, không thể nhận lệnh.');const c={id:'cmd_demo'+(++seq),device_id:id,gate_id:b.gate_id,action:b.action,status:'SENT',sent_by_user_id:S.user_id,ack_status:null,ack_reason:null,sent_at:ago(0),acked_at:null};D.cmds.unshift(c);setTimeout(()=>Object.assign(c,{status:'ACKED',ack_status:'SUCCESS',ack_reason:'Barrier '+(b.action==='OPEN'?'opened':'closed')+' (demo)'}),1500);return c}
      return lim(D.cmds.filter(c=>c.device_id===id))}
    return d}
  throw new Error('Chế độ demo chưa có dữ liệu cho '+p);
}
window.addEventListener('hashchange',render);
render();
