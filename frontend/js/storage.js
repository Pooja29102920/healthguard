/* ================================================================
   storage.js  –  HealthGuard REST API client
   Talks to Python SQLite backend at localhost:8000
   Compatible API surface with v7 where possible
================================================================ */
const API = (() => {
  const BASE = '';
  let _token = null;
  function _t() { return localStorage.getItem('hg_token') || null; }

  async function req(method, path, body) {
    const headers = { 'Content-Type': 'application/json' };
    const tok = _t();
    if (tok) headers['Authorization'] = 'Bearer ' + tok;
    try {
      const r = await fetch(BASE + path, {
        method,
        headers,
        body: body != null ? JSON.stringify(body) : undefined
      });
      const data = await r.json().catch(() => ({}));
      return { ok: r.ok, status: r.status, data };
    } catch (e) {
      return { ok: false, status: 0, data: { error: 'Network error' } };
    }
  }

  return {
    token() { return _t(); },
    currentUser() {
      try { return JSON.parse(localStorage.getItem('hg_user')) || null; } catch { return null; }
    },
    logout() {
      localStorage.removeItem('hg_token');
      localStorage.removeItem('hg_user');
    },

    // Auth
    async register(name, email, password, role) {
      const r = await req('POST', '/api/register', { name, email, password, role });
      if (r.ok && r.data.token) {
        localStorage.setItem('hg_token', r.data.token);
        localStorage.setItem('hg_user', JSON.stringify(r.data.user));
      }
      return r;
    },
    async login(email, password) {
      const r = await req('POST', '/api/login', { email, password });
      if (r.ok && r.data.token) {
        localStorage.setItem('hg_token', r.data.token);
        localStorage.setItem('hg_user', JSON.stringify(r.data.user));
      }
      return r;
    },

    // Me / patient record
    async getMe()         { return req('GET', '/api/me'); },
    async getUser(uid)    { return req('GET', '/api/users/' + uid); },

    // Patients (caregiver)
    async getPatients()                 { return req('GET', '/api/patients'); },
    async addPatientByEmail(email)      { return req('POST', '/api/patients/link', { email }); },
    async addPatientByCode(code)        { return req('POST', '/api/patients/link-code', { code }); },
    async addManualPatient(data)        { return req('POST', '/api/patients', data); },
    async deletePatient(pid)            { return req('DELETE', '/api/patients/' + pid); },

    // Pairing codes (patient)
    async genPairingCode()   { return req('POST', '/api/pairing-code'); },

    // Medicines
    async getMedicines(pid)           { return req('GET', `/api/patients/${pid}/medicines`); },
    async addMedicine(pid, med)       { return req('POST', `/api/patients/${pid}/medicines`, med); },
    async deleteMedicine(pid, mid)    { return req('DELETE', `/api/patients/${pid}/medicines/${mid}`); },

    // Appointments
    async getAppointments(pid)        { return req('GET', `/api/patients/${pid}/appointments`); },
    async addAppointment(pid, a)      { return req('POST', `/api/patients/${pid}/appointments`, a); },
    async deleteAppointment(pid, aid) { return req('DELETE', `/api/patients/${pid}/appointments/${aid}`); },

    // Reports
    async getReports(pid) { return req('GET', `/api/patients/${pid}/reports`); },
    async addReport(pid, file, label) {
      const fileData = await new Promise((res, rej) => {
        const r = new FileReader();
        r.onload  = () => res(r.result);
        r.onerror = () => rej(new Error('Read failed'));
        r.readAsDataURL(file);
      });
      return req('POST', `/api/patients/${pid}/reports`, {
        label, fileName: file.name, fileData,
        mimeType: file.type || 'application/octet-stream', size: file.size
      });
    },
    getReportUrl(pid, rid) {
      return `/api/patients/${pid}/reports/${rid}/download?token=${encodeURIComponent(_t() || '')}`;
    },
    async deleteReport(pid, rid) { return req('DELETE', `/api/patients/${pid}/reports/${rid}`); },

    // SOS
    async sendSOS(pid, lat, lng, accuracy) { return req('POST', `/api/patients/${pid}/sos`, { lat, lng, accuracy }); },
    async getLastSOS(pid)                  { return req('GET',  `/api/patients/${pid}/sos`); },

    // Water
    async getWater()        { return req('GET',  '/api/water'); },
    async setWater(minutes) { return req('POST', '/api/water', { minutes }); },
    async stopWater()       { return req('POST', '/api/water', {}); },

    // History
    async getHistory(pid)   { return req('GET',  `/api/patients/${pid}/history`); },
    async clearHistory(pid) { return req('POST', `/api/patients/${pid}/history/clear`); },

    // Notifications
    async getNotifications(since)        { return req('GET', '/api/notifications' + (since ? '?since=' + encodeURIComponent(since) : '')); },
    async markAllNotificationsRead()     { return req('POST', '/api/notifications/read-all'); },
    async markNotificationRead(id)       { return req('POST', `/api/notifications/${id}/read`); },
    async pushNotification(to, type, title, body) { return req('POST', '/api/notifications', { to, type, title, body }); },

    // SSE real-time events
    openEventStream(uid, onEvent) {
      const tok = _t();
      if (!tok) return null;
      const url = `/api/events?token=${encodeURIComponent(tok)}`;
      const es = new EventSource(url);
      // Named event: server sends "event: notification"
      es.addEventListener('notification', e => {
        try { const data = JSON.parse(e.data); if (data && data.type) onEvent(data); } catch {}
      });
      // Also handle unnamed events (fallback)
      es.onmessage = e => {
        try { const data = JSON.parse(e.data); if (data && data.type) onEvent(data); } catch {}
      };
      return es;
    }
  };
})();
