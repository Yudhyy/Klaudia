const BASE_URL = 'http://10.210.2.127:8000/v1';

export const api = {
  // Chat
  sendMessage: async (payload: {
    messages: any[];
    session_id?: number;
    user_id?: number;
    user_name?: string;
  }) => {
    const res = await fetch(`${BASE_URL}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: 1, user_name: 'Ryuuky', ...payload }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  },

  // Sessions
  getSessions: async () => {
    const res = await fetch(`${BASE_URL}/sessions?user_id=1`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  },

  getSession: async (sessionId: number) => {
    const res = await fetch(`${BASE_URL}/sessions/${sessionId}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  },

  // Health
  health: async () => {
    const res = await fetch(`${BASE_URL}/health`);
    return res.json();
  },
};