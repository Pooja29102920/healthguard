/* ================================================================
   storage.js  –  HealthGuard v9 cloud API client
   All data stored in SQLite via backend REST API
================================================================ */
const API = (() => {
  let _token = null;
  let _user   = null;

  function _t(){ return localStorage.getItem('hg_token') || null; }
  function _h(extra={}){ return { 'Content-Type':'application/json', ...(_t()?{'Authorization':'Bearer '+_t()}:{}), ...extra }; }

  async function req(method, path, body){
    const opts = { method, headers: _h() };
    if(body) opts.body = JSON.stringify(body);
    const r = await fetch(path, opts);
    const data = await r.json().catch(()=>({}));
    return { ok: r.ok, status: r.status, data };
  }

  return {
    /* ── AUTH ── */
    async register(name, email, password, role){
      return req('POST','/api/register',{name,email,password,role});
    },
    async login(email, password){
      const r = await req('POST','/api/login',{email,password});
      if(r.ok && r.data.token){
        localStorage.setItem('hg_token', r.data.token);
        localStorage.setItem('hg_user',  JSON.stringify(r.data.user));
        _token = r.data.token;
        _user  = r.data.user;
      }
      return r;
    },
    logout(){
      localStorage.removeItem('hg_token');
      localStorage.removeItem('hg_user');
      _token=null; _user=null;
    },
    currentUser(){
      if(_user) return _user;
      try{ _user = JSON.parse(localStorage.getItem('hg_user')); return _user; }catch(e){ return null; }
    },
    token(){ return _t(); },

    /* ── PATIENTS ── */
    async getPatients(){ return req('GET','/api/patients'); },
    async addPatientByEmail(email){ return req('POST','/api/patients/link',{email}); },
    async addPatientByCode(code){ return req('POST','/api/patients/link-code',{code}); },
    async addManualPatient(p){ return req('POST','/api/patients',p); },
    async deletePatient(pid){ return req('DELETE','/api/patients/'+pid); },
    async getMyPatient(){ return req('GET','/api/me'); },

    /* ── PAIRING ── */
    async getMyCode(){ return req('GET','/api/pairing/my-code'); },

    /* ── MEDICINES ── */
    async getMedicines(pid){ return req('GET',`/api/patients/${pid}/medicines`); },
    async addMedicine(pid, med){ return req('POST',`/api/patients/${pid}/medicines`, med); },
    async deleteMedicine(pid, mid){ return req('DELETE',`/api/patients/${pid}/medicines/${mid}`); },

    /* ── APPOINTMENTS ── */
    async getAppointments(pid){ return req('GET',`/api/patients/${pid}/appointments`); },
    async addAppointment(pid, a){ return req('POST',`/api/patients/${pid}/appointments`, a); },
    async deleteAppointment(pid, aid){ return req('DELETE',`/api/patients/${pid}/appointments/${aid}`); },

    /* ── REPORTS ── */
    async getReports(pid){ return req('GET',`/api/patients/${pid}/reports`); },
    async addReport(pid, file, label){
      // Read file as base64 data URL
      const fileData = await new Promise((res,rej)=>{
        const r = new FileReader();
        r.onload = ()=>res(r.result);
        r.onerror = ()=>rej(new Error('Read failed'));
        r.readAsDataURL(file);
      });
      return req('POST',`/api/patients/${pid}/reports`,{
        label, fileName:file.name, fileData,
        mimeType:file.type||'application/octet-stream', size:file.size
      });
    },
    getReportUrl(pid, rid){ return `/api/patients/${pid}/reports/${rid}/download?token=${encodeURIComponent(this.token()||'')}`; },
    async deleteReport(pid, rid){ return req('DELETE',`/api/patients/${pid}/reports/${rid}`); },

    /* ── SOS ── */
    async sendSOS(pid, lat, lng, accuracy){ return req('POST',`/api/patients/${pid}/sos`,{lat,lng,accuracy}); },
    async getLastSOS(pid){ return req('GET',`/api/patients/${pid}/sos`); },

    /* ── HISTORY ── */
    async getHistory(pid){ return req('GET',`/api/patients/${pid}/history`); },
    async clearHistory(pid){ return req('POST',`/api/patients/${pid}/history/clear`); },

    /* ── WATER ── */
    async getWater(){ return req('GET','/api/water'); },
    async setWater(minutes){ return req('POST','/api/water',{minutes}); },
    async clearWater(){ return req('POST','/api/water',{}); },

    /* ── NOTIFICATIONS ── */
    async getNotifications(since){ return req('GET','/api/notifications'+(since?'?since='+encodeURIComponent(since):'')); },
    async pushNotification(to, type, title, body){ return req('POST','/api/notifications',{to,type,title,body}); },
    async markAllNotificationsRead(){ return req('POST','/api/notifications/read-all'); },

    /* ── USER LOOKUP ── */
    async getUserByEmail(email){ return req('GET','/api/users/by-email?email='+encodeURIComponent(email)); },
    async getUser(id){ return req('GET','/api/users/'+id); },

    /* ── SSE STREAM ── */
    openEventStream(uid, onEvent){
      const token = _t();
      if(!token) return null;
      const url = `/api/events?token=${encodeURIComponent(token)}`;
      let es;
      function connect(){
        es = new EventSource(url);
        es.addEventListener('notification', e=>{
          try{ onEvent(JSON.parse(e.data)); }catch(err){}
        });
        es.onerror = ()=>{ es.close(); setTimeout(connect, 5000); };
      }
      connect();
      return { close(){ if(es)es.close(); } };
    }
  };
})();
