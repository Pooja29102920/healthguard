/* ================================================================
   notifications.js  –  HealthGuard v9 notification + sound engine
   Sounds: Web Audio API — zero external files needed
   Cloud: pushes via REST API, receives via SSE + polling fallback
================================================================ */
const Notif = (() => {

  /* SOUND ENGINE */
  let _actx=null;
  function _ctx(){
    if(!_actx){try{_actx=new(window.AudioContext||window.webkitAudioContext)();}catch(e){}}
    if(_actx&&_actx.state==='suspended')_actx.resume().catch(()=>{});
    return _actx;
  }
  function _unlock(){const c=_ctx();if(!c)return;const b=c.createBuffer(1,1,22050);const s=c.createBufferSource();s.buffer=b;s.connect(c.destination);s.start(0);}
  document.addEventListener('click',_unlock,{once:true});
  document.addEventListener('touchend',_unlock,{once:true});

  function _playChime(){
    const c=_ctx();if(!c)return;const now=c.currentTime;
    [523,659,784,1047].forEach((f,i)=>{
      const o=c.createOscillator(),g=c.createGain();o.connect(g);g.connect(c.destination);
      o.type='sine';o.frequency.value=f;
      g.gain.setValueAtTime(0,now+i*.13);g.gain.linearRampToValueAtTime(.3,now+i*.13+.05);g.gain.exponentialRampToValueAtTime(.001,now+i*.13+.55);
      o.start(now+i*.13);o.stop(now+i*.13+.6);
    });
  }
  function _playMedAlert(){
    const c=_ctx();if(!c)return;const now=c.currentTime;
    [[660,.32],[880,.28]].forEach(([f,v],i)=>{
      const o=c.createOscillator(),g=c.createGain();o.connect(g);g.connect(c.destination);
      o.type='triangle';o.frequency.value=f;
      g.gain.setValueAtTime(0,now+i*.22);g.gain.linearRampToValueAtTime(v,now+i*.22+.04);g.gain.exponentialRampToValueAtTime(.001,now+i*.22+.5);
      o.start(now+i*.22);o.stop(now+i*.22+.55);
    });
  }
  function _playReportAlert(){
    const c=_ctx();if(!c)return;const now=c.currentTime;
    [[440,.22],[554,.22],[659,.22]].forEach(([f,v],i)=>{
      const o=c.createOscillator(),g=c.createGain();o.connect(g);g.connect(c.destination);
      o.type='sine';o.frequency.value=f;
      g.gain.setValueAtTime(0,now+i*.18);g.gain.linearRampToValueAtTime(v,now+i*.18+.05);g.gain.exponentialRampToValueAtTime(.001,now+i*.18+.5);
      o.start(now+i*.18);o.stop(now+i*.18+.55);
    });
  }
  function _playMessageAlert(){
    const c=_ctx();if(!c)return;const now=c.currentTime;
    const o=c.createOscillator(),g=c.createGain();o.connect(g);g.connect(c.destination);
    o.type='sine';o.frequency.setValueAtTime(400,now);o.frequency.exponentialRampToValueAtTime(900,now+.18);
    g.gain.setValueAtTime(.28,now);g.gain.exponentialRampToValueAtTime(.001,now+.35);
    o.start(now);o.stop(now+.4);
  }
  let _sirenTimer=null,_sirenCount=0;
  function _playSiren(){
    stopSiren();_sirenCount=0;
    function burst(){
      if(_sirenCount>=14)return;_sirenCount++;
      const c=_ctx();if(!c)return;const now=c.currentTime;
      const o=c.createOscillator(),g=c.createGain();o.connect(g);g.connect(c.destination);
      o.type='sawtooth';o.frequency.setValueAtTime(380,now);o.frequency.linearRampToValueAtTime(980,now+.38);o.frequency.linearRampToValueAtTime(380,now+.76);
      g.gain.setValueAtTime(.65,now);g.gain.setValueAtTime(.65,now+.7);g.gain.linearRampToValueAtTime(0,now+.82);
      o.start(now);o.stop(now+.85);
      const o2=c.createOscillator(),g2=c.createGain();o2.connect(g2);g2.connect(c.destination);
      o2.type='square';o2.frequency.value=1600;
      g2.gain.setValueAtTime(0,now+.32);g2.gain.linearRampToValueAtTime(.25,now+.36);g2.gain.linearRampToValueAtTime(0,now+.58);
      o2.start(now+.32);o2.stop(now+.62);
      const o3=c.createOscillator(),g3=c.createGain();o3.connect(g3);g3.connect(c.destination);
      o3.type='sine';o3.frequency.setValueAtTime(90,now);o3.frequency.exponentialRampToValueAtTime(40,now+.15);
      g3.gain.setValueAtTime(.5,now);g3.gain.exponentialRampToValueAtTime(.001,now+.18);
      o3.start(now);o3.stop(now+.2);
      _sirenTimer=setTimeout(burst,950);
    }
    burst();
  }
  function stopSiren(){clearTimeout(_sirenTimer);_sirenTimer=null;_sirenCount=99;}
  function _playPing(){
    const c=_ctx();if(!c)return;const now=c.currentTime;
    const o=c.createOscillator(),g=c.createGain();o.connect(g);g.connect(c.destination);
    o.type='sine';o.frequency.setValueAtTime(880,now);o.frequency.exponentialRampToValueAtTime(440,now+.45);
    g.gain.setValueAtTime(.36,now);g.gain.exponentialRampToValueAtTime(.001,now+.55);
    o.start(now);o.stop(now+.6);
  }
  function playSound(type){
    if(type==='sos_alert')             _playSiren();
    else if(type==='water'||type==='medicine') _playPing();
    else if(type==='new_med_assigned') _playMedAlert();
    else if(type==='new_report')       _playReportAlert();
    else if(type==='cg_message'||type==='pt_message') _playMessageAlert();
    else _playChime();
  }

  /* POPUP */
  const CSS=`
    #hg-stack{position:fixed;bottom:24px;right:14px;z-index:9999;display:flex;flex-direction:column-reverse;gap:10px;width:min(320px,calc(100vw - 28px));pointer-events:none}
    .hg-pop{pointer-events:all;background:#fff;border-radius:16px;box-shadow:0 8px 32px rgba(0,0,0,.2);padding:13px 14px;display:flex;gap:11px;align-items:flex-start;animation:hgIn .35s cubic-bezier(.22,.68,0,1.2) forwards}
    .hg-pop.out{animation:hgOut .3s ease forwards}
    @keyframes hgIn{from{opacity:0;transform:translateX(70px)}to{opacity:1;transform:translateX(0)}}
    @keyframes hgOut{from{opacity:1;transform:translateX(0)}to{opacity:0;transform:translateX(70px)}}
    .hg-ico{width:38px;height:38px;border-radius:11px;display:flex;align-items:center;justify-content:center;font-size:1.15rem;flex-shrink:0}
    .hg-bd{flex:1;min-width:0}
    .hg-ttl{font-weight:700;font-size:.84rem;color:#1e293b}
    .hg-msg{font-size:.77rem;color:#64748b;line-height:1.4;margin-top:2px;word-break:break-word}
    .hg-time{font-size:.66rem;color:#94a3b8;margin-top:3px}
    .hg-x{background:none;border:none;cursor:pointer;color:#94a3b8;font-size:1rem;padding:0;margin-left:4px;flex-shrink:0;line-height:1}
    .hg-x:hover{color:#ef4444}
    .hg-t-sos_alert{border-left:4px solid #e11d48;background:#fff5f6}
    .hg-t-sos_alert .hg-ico{background:#fff1f2}
    .hg-t-water .hg-ico,.hg-t-water_started .hg-ico{background:#e0f2fe}
    .hg-t-medicine .hg-ico,.hg-t-new_medicine .hg-ico,.hg-t-new_med_assigned .hg-ico{background:#fef9c3}
    .hg-t-new_appointment .hg-ico,.hg-t-appointment_reminder .hg-ico{background:#ede9fe}
    .hg-t-cg_message .hg-ico,.hg-t-pt_message .hg-ico{background:#dcfce7}
    .hg-t-new_report .hg-ico{background:#f0fdf4}
    .hg-t-caregiver_linked .hg-ico{background:#dbeafe}
    #hg-badge{display:none;position:absolute;top:-5px;right:-5px;background:#ef4444;color:#fff;border-radius:99px;font-size:.6rem;font-weight:700;padding:1px 5px;min-width:16px;text-align:center;pointer-events:none}
    #hg-panel{position:fixed;top:0;right:0;bottom:0;width:min(310px,100vw);background:#fff;box-shadow:-4px 0 24px rgba(0,0,0,.15);z-index:10001;transform:translateX(100%);transition:transform .3s cubic-bezier(.22,.68,0,1.2);display:flex;flex-direction:column}
    #hg-panel.open{transform:translateX(0)}
    #hg-pov{display:none;position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:10000}
    .hg-stop{margin-top:6px;padding:4px 10px;background:#e11d48;color:#fff;border:none;border-radius:6px;font-size:.72rem;font-weight:700;cursor:pointer;font-family:inherit;display:inline-block}
  `;
  const EMOJI={sos_alert:'🚨',water:'💧',water_started:'💧',medicine:'💊',new_medicine:'💊',new_med_assigned:'💊',new_appointment:'📅',appointment_reminder:'📅',cg_message:'📩',pt_message:'💬',new_report:'📋',caregiver_linked:'🔗'};
  let _stack=null,_panel=null,_pov=null,_badge=null,_uid=null,_panelNotifs=[],_panelColor='#0f766e';

  function _boot(){
    if(_stack)return;
    const s=document.createElement('style');s.textContent=CSS;document.head.appendChild(s);
    _stack=document.createElement('div');_stack.id='hg-stack';document.body.appendChild(_stack);
  }
  function _e(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}

  function showPopup(type,title,body,ms=6000){
    _boot();
    const d=document.createElement('div');d.className='hg-pop hg-t-'+type;
    const t=new Date().toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});
    const isSOS=type==='sos_alert';
    d.innerHTML='<div class="hg-ico">'+(EMOJI[type]||'🔔')+'</div>'
      +'<div class="hg-bd"><div class="hg-ttl">'+_e(title)+'</div><div class="hg-msg">'+_e(body)+'</div>'
      +(isSOS?'<button class="hg-stop" onclick="Notif.stopSiren();this.closest(\'.hg-pop\').remove()">⏹ Stop Alarm</button>':'')
      +'<div class="hg-time">'+t+'</div></div>'
      +'<button class="hg-x" onclick="'+(isSOS?'Notif.stopSiren();':'')+'this.closest(\'.hg-pop\').remove()">✕</button>';
    _stack.appendChild(d);
    if(ms>0&&!isSOS)setTimeout(()=>{d.classList.add('out');setTimeout(()=>d.remove(),350);},ms);
  }

  function _os(title,body){if(window.Notification&&Notification.permission==='granted'){try{new Notification(title,{body,icon:'icons/icon-192.png'});}catch(e){}}}
  function requestPermission(cb){if(!window.Notification){cb&&cb(false);return;}if(Notification.permission==='granted'){cb&&cb(true);return;}Notification.requestPermission().then(p=>cb&&cb(p==='granted'));}

  function initPanel(uid,color){
    _uid=uid;_panelColor=color||'#0f766e';_boot();
    if(_panel)return;
    _pov=document.createElement('div');_pov.id='hg-pov';_pov.onclick=closePanel;document.body.appendChild(_pov);
    _panel=document.createElement('div');_panel.id='hg-panel';
    _panel.innerHTML='<div style="background:'+_panelColor+';color:#fff;padding:14px 18px;display:flex;align-items:center;justify-content:space-between;flex-shrink:0">'
      +'<span style="font-weight:700;font-size:.95rem">🔔 Notifications</span>'
      +'<div style="display:flex;gap:8px;align-items:center">'
        +'<button onclick="Notif.markAllRead()" style="font-size:.67rem;background:rgba(255,255,255,.2);border:none;color:#fff;padding:3px 8px;border-radius:6px;cursor:pointer">Mark all read</button>'
        +'<button onclick="Notif.closePanel()" style="background:none;border:none;color:#fff;font-size:1.4rem;cursor:pointer;line-height:1">×</button>'
      +'</div></div>'
      +'<div id="hg-plist" style="flex:1;overflow-y:auto;-webkit-overflow-scrolling:touch;padding:12px"></div>';
    document.body.appendChild(_panel);
  }

  function refreshPanel(notifs){
    if(notifs)_panelNotifs=notifs;
    const list=document.getElementById('hg-plist');if(!list)return;
    if(!_panelNotifs.length){list.innerHTML='<div style="text-align:center;padding:40px 0;color:#94a3b8;font-size:.85rem">No notifications yet.</div>';_updBadge(0);return;}
    list.innerHTML=[..._panelNotifs].reverse().map(n=>{
      const time=new Date(n.ts).toLocaleString([],{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});
      return '<div style="display:flex;gap:10px;align-items:flex-start;padding:10px 8px;border-radius:10px;margin-bottom:6px;background:'+(n.read?'#fff':'#f0f9ff')+';border:1px solid '+(n.read?'#f1f5f9':'#bae6fd')+'">'
        +'<span style="font-size:1.2rem;flex-shrink:0">'+(EMOJI[n.type]||'🔔')+'</span>'
        +'<div style="flex:1;min-width:0"><div style="font-weight:'+(n.read?'500':'700')+';font-size:.82rem;color:#1e293b">'+_e(n.title)+'</div>'
        +'<div style="font-size:.76rem;color:#64748b;margin-top:2px;word-break:break-word">'+_e(n.body)+'</div>'
        +'<div style="font-size:.67rem;color:#94a3b8;margin-top:3px">'+time+'</div></div>'
        +(n.read?'':'<div style="width:8px;height:8px;background:#0ea5e9;border-radius:50%;flex-shrink:0;margin-top:4px"></div>')
        +'</div>';
    }).join('');
    _updBadge(_panelNotifs.filter(n=>!n.read).length);
  }

  function _updBadge(count){
    if(!_badge)return;
    const c = count !== undefined ? count : _panelNotifs.filter(n=>!n.read).length;
    _badge.textContent=c>9?'9+':String(c);_badge.style.display=c?'block':'none';
  }
  function openPanel(){_panel&&_panel.classList.add('open');_pov&&(_pov.style.display='block');}
  function closePanel(){_panel&&_panel.classList.remove('open');_pov&&(_pov.style.display='none');}

  function injectBell(slot,uid,color){
    initPanel(uid,color);
    const wrap=document.createElement('div');wrap.style.cssText='position:relative;display:inline-flex;align-items:center';
    const btn=document.createElement('button');btn.style.cssText='background:none;border:none;cursor:pointer;font-size:1.4rem;line-height:1;padding:4px;position:relative';
    btn.innerHTML='🔔';_badge=document.createElement('span');_badge.id='hg-badge';btn.appendChild(_badge);btn.onclick=openPanel;
    wrap.appendChild(btn);slot.appendChild(wrap);
  }

  // Cloud mark-all-read
  let _markAllReadFn = null;
  function setMarkAllReadFn(fn){_markAllReadFn=fn;}
  function markAllRead(){if(_markAllReadFn)_markAllReadFn();}

  // Water reminder
  let _wId=null;
  function startWater(mins,onTick){
    stopWater();
    _wId=setInterval(()=>{
      const title='💧 Drink Water!',body='Time to hydrate — drink a glass of water now.';
      showPopup('water',title,body,8000);_playPing();_os(title,body);
      if(onTick)onTick(title,body);
    },mins*60*1000);
  }
  function stopWater(){if(_wId){clearInterval(_wId);_wId=null;}}
  function waterOn(){return _wId!==null;}

  // Medicine checker
  let _mId=null,_notifiedMeds=new Set();
  function startMedCheck(getMeds,onAlert){
    stopMedCheck();
    _mId=setInterval(()=>{
      const hhmm=new Date().toTimeString().slice(0,5);
      (getMeds()||[]).forEach(m=>{
        const k=m.id+'_'+hhmm;
        if(m.time===hhmm&&!_notifiedMeds.has(k)){
          _notifiedMeds.add(k);
          const title='💊 Medicine Time!',body='Take '+m.dosage+' of '+m.name+(m.notes?' — '+m.notes:'');
          showPopup('medicine',title,body,0);_playPing();_os(title,body);
          if(onAlert)onAlert(title,body,m);
        }
      });
    },30000);
  }
  function stopMedCheck(){if(_mId){clearInterval(_mId);_mId=null;}}

  // Appointment 15-min checker
  let _aId=null,_notifiedAppts=new Set();
  function startApptCheck(getAppts,onAlert){
    stopApptCheck();
    _aId=setInterval(()=>{
      const now=new Date();
      (getAppts()||[]).forEach(a=>{
        const k=a.id+'_15';
        if(_notifiedAppts.has(k))return;
        try{
          const diff=new Date(a.date+'T'+a.time)-now;
          if(diff>0&&diff<=15*60*1000){
            _notifiedAppts.add(k);
            const title='📅 Appointment in 15 min',body='With '+a.doctor+' at '+a.time;
            showPopup('appointment_reminder',title,body,0);_playChime();_os(title,body);
            if(onAlert)onAlert(title,body,a);
          }
        }catch(e){}
      });
    },60000);
  }
  function stopApptCheck(){if(_aId){clearInterval(_aId);_aId=null;}}

  return{showPopup,requestPermission,injectBell,openPanel,closePanel,refreshPanel,
    playSound,stopSiren,markAllRead,setMarkAllReadFn,
    startWater,stopWater,waterOn,startMedCheck,stopMedCheck,startApptCheck,stopApptCheck};
})();
