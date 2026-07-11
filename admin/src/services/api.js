import axios from 'axios';
import { getCsrfTokenFromCookie, fetchCsrfToken } from '../utils/csrf';

const API_BASE_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000';

const api = axios.create({
    baseURL: API_BASE_URL,
    withCredentials: true,  // Include cookies for CSRF
});

// Public widget endpoints (/chat/, /chat/stream) authenticate via X-API-Key
// only — they use neither cookies nor CSRF. They are served by the permissive
// "widget" CORS policy on the backend, which echoes the origin with
// Access-Control-Allow-Credentials: false and only allows the Content-Type /
// X-API-Key headers. Sending withCredentials or an X-CSRF-Token header to them
// makes the browser's CORS preflight fail, so we explicitly opt those requests
// out of credentials + CSRF.
const isPublicWidgetPath = (url = '') => url.startsWith('/chat/');

// Add CSRF token to requests
api.interceptors.request.use(
    async (config) => {
        if (isPublicWidgetPath(config.url)) {
            config.withCredentials = false;
            return config;
        }

        // Add CSRF token for state-changing methods
        const method = config.method?.toUpperCase();
        if (['POST', 'PUT', 'DELETE', 'PATCH'].includes(method)) {
            let csrfToken = getCsrfTokenFromCookie();

            // If no CSRF token, fetch it
            if (!csrfToken) {
                csrfToken = await fetchCsrfToken();
            }

            if (csrfToken) {
                config.headers['X-CSRF-Token'] = csrfToken;
            }
        }

        return config;
    },
    (error) => {
        return Promise.reject(error);
    }
);

// Global 401 handler: when the session cookie expires, every data call starts
// 401-ing. Without this, callers that only console.error the failure leave the
// user staring at stale/empty panels until the 60s AuthContext poll logs them
// out. Redirect to /login immediately instead. Auth endpoints are excluded so
// this doesn't fight AuthContext's own login/checkAuth flow or loop on /login.
const isAuthPath = (url = '') =>
    url.includes('/auth/login') ||
    url.includes('/auth/me') ||
    url.includes('/auth/logout') ||
    url.includes('/auth/2fa/login') ||
    url.includes('/auth/register');

api.interceptors.response.use(
    (response) => response,
    (error) => {
        const status = error?.response?.status;
        const url = error?.config?.url || '';
        if (status === 401 && !isPublicWidgetPath(url) && !isAuthPath(url)) {
            if (window.location.pathname !== '/login') {
                try { sessionStorage.setItem('session_expired', '1'); } catch (e) { /* ignore */ }
                window.location.assign('/login');
            }
        }
        return Promise.reject(error);
    }
);

// Auth
export const login = (email, password) => {
    const formData = new FormData();
    formData.append('username', email); // OAuth2 uses 'username' field
    formData.append('password', password);
    return api.post('/auth/login', formData);
};

export const loginJson = (email, password) =>
    api.post('/auth/login-json', { email, password });

export const login2FA = (email, password, two_factor_code) =>
    api.post('/auth/2fa/login', { email, password, two_factor_code });

export const logout = () => api.post('/auth/logout');

export const checkAuth = () => api.get('/auth/me');

export const changePassword = (currentPassword, newPassword) =>
    api.put('/auth/change-password', { current_password: currentPassword, new_password: newPassword });

export const updateProfile = (username) =>
    api.put('/auth/profile', { username });

export const getChatbotConfigs = () => api.get('/chatbot/');

export const getChatbotConfig = (id) => api.get(`/chatbot/${id}`);

export const createChatbotConfig = (config) => api.post('/chatbot/', config);

export const updateChatbotConfig = (id, config) =>
    api.put(`/chatbot/${id}`, config);


export const deleteChatbotConfig = (id) => api.delete(`/chatbot/${id}`);

export const crawlWebsite = (id, url, maxPages = null) => 
    api.post(`/chatbot/${id}/crawl`, { url, max_pages: maxPages });

export const getCrawlStatus = (id) => api.get(`/chatbot/${id}/crawl-status`);
export const getScrapedPages = (id) => api.get(`/chatbot/${id}/scraped-pages`);

export const deleteScrapedPage = (chatbotId, pageId) =>
    api.delete(`/chatbot/${chatbotId}/scraped-pages/${pageId}`);

export const sendChatMessage = (chatbotId, message, conversationId, apiKey) =>
    api.post('/chat/', {
        chatbot_id: chatbotId,
        message,
        conversation_id: conversationId || null,
        page_url: window.location.href,
        page_title: 'Admin Test Panel',
    }, {
        headers: apiKey ? { 'X-API-Key': apiKey } : {},
    });

// Widget build & download
export const buildWidgetBundle = () =>
    api.post('/widget/build', {}, { responseType: 'blob', timeout: 300000 });

export const downloadWidgetBundle = () =>
    api.get('/widget/download', { responseType: 'blob' });

// Conversations
export const getConversations = (params = {}) => {
    const query = new URLSearchParams();
    if (params.skip !== undefined) query.append('skip', params.skip);
    if (params.limit !== undefined) query.append('limit', params.limit);
    if (params.chatbot_id) query.append('chatbot_id', params.chatbot_id);
    const qs = query.toString();
    return api.get(`/conversations/${qs ? '?' + qs : ''}`);
};

export const getConversation = (id) => api.get(`/conversations/${id}`);

export const deleteConversation = (id) => api.delete(`/conversations/${id}`);

export const getFeedbackStats = (chatbotId) => 
    api.get(`/conversations/feedback-stats/${chatbotId}`);

export const getTopKeywords = (chatbotId, limit = 20, days = null) => {
    const params = new URLSearchParams({ limit });
    if (days) params.append('days', days);
    return api.get(`/conversations/analytics/${chatbotId}/keywords?${params.toString()}`);
};

// Upload
export const uploadImage = (file) => {
    const formData = new FormData();
    formData.append('file', file);
    return api.post('/upload/image', formData, {
        headers: {
            'Content-Type': 'multipart/form-data',
        },
    });
};

export const uploadDocument = (chatbotId, file) => {
    const formData = new FormData();
    formData.append('file', file);
    return api.post(`/upload/document?chatbot_id=${chatbotId}`, formData, {
        headers: {
            'Content-Type': 'multipart/form-data',
        },
    });
};

// Permissions
export const getChatbotPermissions = (chatbotId) => 
    api.get(`/permissions/chatbot/${chatbotId}`);

export const grantPermission = (chatbotId, userEmail, permissionLevel) =>
    api.post('/permissions/', { chatbot_id: chatbotId, user_email: userEmail, permission_level: permissionLevel });

export const updatePermission = (permissionId, permissionLevel) =>
    api.put(`/permissions/${permissionId}`, { permission_level: permissionLevel });

export const revokePermission = (permissionId) =>
    api.delete(`/permissions/${permissionId}`);

export const getMyAccessibleChatbots = () =>
    api.get('/permissions/my-chatbots');

// Audit Logs
export const getAuditLogs = (chatbotId, params = {}) => {
    const query = new URLSearchParams();
    if (params.skip !== undefined) query.append('skip', params.skip);
    if (params.limit !== undefined) query.append('limit', params.limit);
    if (params.action_filter) query.append('action_filter', params.action_filter);
    const qs = query.toString();
    return api.get(`/audit/chatbot/${chatbotId}${qs ? '?' + qs : ''}`);
};

// Leads
export const getLeads = (params = {}) => api.get('/leads/', { params });
export const submitLead = (data) => api.post('/leads/submit', data);
export const updateLead = (id, data) => api.put(`/leads/${id}`, data);
export const deleteLead = (id) => api.delete(`/leads/${id}`);

// Documents
export const getChatbotDocuments = (chatbotId) => api.get(`/chatbot/${chatbotId}/documents`);
export const deleteChatbotDocument = (chatbotId, docId) =>
    api.delete(`/chatbot/${chatbotId}/documents/${docId}`);

// 2FA Settings
export const setup2FA = () => api.post('/auth/2fa/setup');
export const verify2FA = (code) => api.post('/auth/2fa/verify', { code });
export const disable2FA = (password) => api.post('/auth/2fa/disable', { password });

// User Management (superuser only)
export const getUsers = () => api.get('/auth/users');
export const createManagedUser = (data) => api.post('/auth/users', data);
export const updateManagedUser = (userId, data) => api.put(`/auth/users/${userId}`, data);
export const deleteManagedUser = (userId) => api.delete(`/auth/users/${userId}`);

export default api;
