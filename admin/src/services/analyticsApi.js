// Sprint D analytics endpoints. All calls require a chatbot_id; the
// existing Analytics page exposes a "All Chatbots" option but the
// per-owner dashboard is meaningful only for one bot at a time.
import api from './api';

export const getOwnerDashboard = (chatbotId, days = 30) =>
    api.get(`/analytics/owner/${chatbotId}/dashboard?days=${days}`);

export const getOwnerFunnel = (chatbotId, days = 30) =>
    api.get(`/analytics/owner/${chatbotId}/funnel?days=${days}`);

export const getOwnerTopQuestions = (chatbotId, { days = 30, limit = 20, onlyUnanswered = false } = {}) => {
    const params = new URLSearchParams();
    params.append('days', days);
    params.append('limit', limit);
    if (onlyUnanswered) params.append('only_unanswered', 'true');
    return api.get(`/analytics/owner/${chatbotId}/top-questions?${params.toString()}`);
};

export const getOwnerHeatmap = (chatbotId, days = 30) =>
    api.get(`/analytics/owner/${chatbotId}/heatmap?days=${days}`);

export const getOwnerQualityTrend = (chatbotId, days = 7) =>
    api.get(`/analytics/owner/${chatbotId}/quality-trend?days=${days}`);

export const getOwnerContentGaps = (chatbotId) =>
    api.get(`/analytics/owner/${chatbotId}/content-gaps`);

export const actionContentGap = (chatbotId, clusterId, action /* "addressed" | "ignored" */) =>
    api.post(`/analytics/owner/${chatbotId}/content-gaps/${clusterId}/${action}`);

export const getOwnerCost = (chatbotId, days = 30) =>
    api.get(`/analytics/owner/${chatbotId}/cost?days=${days}`);

export const getOwnerAnomalies = (chatbotId) =>
    api.get(`/analytics/owner/${chatbotId}/anomalies`);
