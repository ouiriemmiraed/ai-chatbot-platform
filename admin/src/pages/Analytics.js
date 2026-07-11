import React, { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import {
    LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
    BarChart, Bar, PieChart, Pie, Cell, Legend,
} from 'recharts';
import Layout from '../components/Layout';
import {
    getChatbotConfigs, getFeedbackStats, getTopKeywords, getLeads,
} from '../services/api';
import api from '../services/api';
import AnalyticsInsights from '../components/analytics/AnalyticsInsights';
import './Analytics.css';

import { Hourglass, Mail, MessageCircle, RefreshCw, Smile, TrendingUp, Users } from 'lucide-react';
const getAnalyticsOverview = (params = {}) => {
    const query = new URLSearchParams();
    if (params.days) query.append('days', params.days);
    if (params.chatbot_id) query.append('chatbot_id', params.chatbot_id);
    return api.get(`/conversations/analytics-overview?${query.toString()}`);
};

const Analytics = () => {
    const { t } = useTranslation();

    const [chatbots, setChatbots] = useState([]);
    const [selectedChatbot, setSelectedChatbot] = useState('');
    const [days, setDays] = useState(30);
    const [overview, setOverview] = useState(null);
    const [feedbackStats, setFeedbackStats] = useState(null);
    const [keywords, setKeywords] = useState([]);
    const [totalLeads, setTotalLeads] = useState(0);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    // Build full 0-23 hour array filling missing hours with 0
    const buildHourArray = (peakHours) => {
        const map = {};
        (peakHours || []).forEach(h => { map[h.hour] = h.count; });
        return Array.from({ length: 24 }, (_, i) => ({
            hour: `${String(i).padStart(2, '0')}h`,
            count: map[i] || 0,
        }));
    };

    const fetchData = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            // Scope the lead count to the selected chatbot so the capture rate
            // (leads ÷ conversations) matches the other per-chatbot metrics.
            const leadsParams = { limit: 1 };
            if (selectedChatbot) leadsParams.chatbot_id = selectedChatbot;
            const [botsRes, leadsRes] = await Promise.all([
                getChatbotConfigs(),
                getLeads(leadsParams),
            ]);

            const bots = botsRes.data || [];
            setChatbots(bots);
            setTotalLeads(leadsRes.data?.total || 0);

            const ovParams = { days };
            if (selectedChatbot) ovParams.chatbot_id = selectedChatbot;
            const ovRes = await getAnalyticsOverview(ovParams);
            setOverview(ovRes.data);

            // Feedback stats: aggregate across all (or selected) chatbots
            const targetBots = selectedChatbot
                ? bots.filter(b => b._id === selectedChatbot)
                : bots;
            if (targetBots.length > 0) {
                const fbAll = await Promise.all(
                    targetBots.map(b =>
                        getFeedbackStats(b._id).catch(() => ({ data: { total: 0, positive: 0, negative: 0, satisfaction_rate: 0 } }))
                    )
                );
                const agg = fbAll.reduce((acc, r) => {
                    const d = r.data;
                    acc.total += d.total || 0;
                    acc.positive += d.positive || 0;
                    acc.negative += d.negative || 0;
                    return acc;
                }, { total: 0, positive: 0, negative: 0 });
                agg.satisfaction_rate = agg.total > 0 ? Math.round((agg.positive / agg.total) * 100) : 0;
                setFeedbackStats(agg);
            } else {
                setFeedbackStats({ total: 0, positive: 0, negative: 0, satisfaction_rate: 0 });
            }

            // Top keywords for selected chatbot (or first chatbot)
            const kwBot = selectedChatbot || (bots[0]?._id);
            if (kwBot) {
                const kwRes = await getTopKeywords(kwBot, 10, days).catch(() => ({ data: { keywords: [] } }));
                setKeywords(kwRes.data?.keywords || []);
            }
        } catch (err) {
            console.error(err);
            setError(t('analytics.loadError', 'Failed to load analytics data.'));
        } finally {
            setLoading(false);
        }
    }, [selectedChatbot, days, t]);

    useEffect(() => {
        fetchData();
    }, [fetchData]);

    const hourData = overview ? buildHourArray(overview.peak_hours) : [];

    // Chatbot comparison chart data (merge name from chatbots list)
    const botMap = {};
    chatbots.forEach(b => { botMap[b._id] = b.name || b._id; });
    const botComparison = (overview?.per_chatbot || []).map(b => ({
        name: botMap[b.chatbot_id] || (b.chatbot_id ? b.chatbot_id.slice(-6) : 'unknown'),
        conversations: b.conversations,
        messages: b.total_messages,
    }));

    // Satisfaction pie
    const satisfactionData = feedbackStats && feedbackStats.total > 0
        ? [
            { name: t('analytics.positive', 'Positive'), value: feedbackStats.positive },
            { name: t('analytics.negative', 'Negative'), value: feedbackStats.negative },
            { name: t('analytics.neutral', 'Neutral'), value: feedbackStats.total - feedbackStats.positive - feedbackStats.negative },
        ].filter(d => d.value > 0)
        : [];

    const SAT_COLORS = ['#43BFA7', '#FF6584', '#94A3B8'];

    const getChatbotName = (id) => {
        const bot = chatbots.find(b => b._id === id);
        return bot ? bot.name : id;
    };

    return (
        <Layout>
            <div className="analytics-page">
                {/* Header */}
                <div className="analytics-header">
                    <div>
                        <h1 className="analytics-title">
                            <span className="analytics-icon"><TrendingUp size={16} /></span>
                            {t('analytics.title', 'Analytics & Statistics')}
                        </h1>
                        <p className="analytics-subtitle">
                            {t('analytics.subtitle', 'Insights from your chatbot conversations')}
                        </p>
                    </div>
                    <div className="analytics-controls">
                        <select
                            className="analytics-select"
                            value={selectedChatbot}
                            onChange={e => setSelectedChatbot(e.target.value)}
                        >
                            <option value="">{t('analytics.allChatbots', 'All Chatbots')}</option>
                            {chatbots.map(b => (
                                <option key={b._id} value={b._id}>{b.name}</option>
                            ))}
                        </select>
                        <select
                            className="analytics-select"
                            value={days}
                            onChange={e => setDays(Number(e.target.value))}
                        >
                            <option value={7}>{t('analytics.last7days', 'Last 7 days')}</option>
                            <option value={30}>{t('analytics.last30days', 'Last 30 days')}</option>
                            <option value={90}>{t('analytics.last90days', 'Last 90 days')}</option>
                        </select>
                        <button className="analytics-refresh-btn" onClick={fetchData}>
                            {loading ? <Hourglass size={16} /> : <RefreshCw size={16} />} {t('analytics.refresh', 'Refresh')}
                        </button>
                    </div>
                </div>

                {error && <div className="analytics-error">{error}</div>}

                {loading ? (
                    <div className="analytics-loading">
                        <div className="analytics-spinner" />
                        <p>{t('analytics.loading', 'Loading analytics...')}</p>
                    </div>
                ) : (
                    <>
                        {/* KPI Cards */}
                        <div className="analytics-kpi-grid">
                            <div className="kpi-card">
                                <div className="kpi-icon"><MessageCircle size={16} /></div>
                                <div className="kpi-value">{overview?.total_conversations ?? 0}</div>
                                <div className="kpi-label">{t('analytics.kpi.conversations', 'Conversations')}</div>
                                <div className="kpi-period">{t('analytics.kpi.period', 'Last {{days}} days', { days })}</div>
                            </div>
                            <div className="kpi-card">
                                <div className="kpi-icon"><Mail size={16} /></div>
                                <div className="kpi-value">{overview?.total_messages ?? 0}</div>
                                <div className="kpi-label">{t('analytics.kpi.messages', 'Messages')}</div>
                                <div className="kpi-period">{t('analytics.kpi.period', 'Last {{days}} days', { days })}</div>
                            </div>
                            <div className="kpi-card">
                                <div className="kpi-icon"><Smile size={16} /></div>
                                <div className="kpi-value">{feedbackStats?.satisfaction_rate ?? 0}%</div>
                                <div className="kpi-label">{t('analytics.kpi.satisfaction', 'Satisfaction Rate')}</div>
                                <div className="kpi-period">
                                    {feedbackStats?.total ?? 0} {t('analytics.kpi.ratings', 'ratings')}
                                </div>
                            </div>
                            <div className="kpi-card">
                                <div className="kpi-icon"><Users size={16} /></div>
                                <div className="kpi-value">{totalLeads}</div>
                                <div className="kpi-label">{t('analytics.kpi.leads', 'Total Leads')}</div>
                                <div className="kpi-period">
                                    {overview?.total_conversations ? (() => {
                                        const pct = (totalLeads / overview.total_conversations) * 100;
                                        // Avoid the misleading "0%" when leads exist but round down,
                                        // and never exceed 100% (leads are all-time, convs windowed).
                                        const shown = pct > 0 && pct < 1 ? '<1' : Math.min(100, Math.round(pct));
                                        return `${shown}% ${t('analytics.kpi.captureRate', 'capture rate')}`;
                                    })() : '—'}
                                </div>
                            </div>
                        </div>

                        {/* Volume chart + Keywords */}
                        <div className="analytics-row-2col">
                            <div className="analytics-card">
                                <h3 className="chart-title">{t('analytics.volumeChart', 'Conversations Over Time')}</h3>
                                {overview?.conversations_by_day?.length > 0 ? (
                                    <ResponsiveContainer width="100%" height={240}>
                                        <LineChart data={overview.conversations_by_day} margin={{ top: 5, right: 16, left: 0, bottom: 5 }}>
                                            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                                            <XAxis
                                                dataKey="date"
                                                tick={{ fontSize: 11, fill: 'var(--text-secondary)' }}
                                                tickFormatter={d => d.slice(5)}
                                            />
                                            <YAxis
                                                allowDecimals={false}
                                                tick={{ fontSize: 11, fill: 'var(--text-secondary)' }}
                                            />
                                            <Tooltip
                                                contentStyle={{
                                                    background: 'var(--surface)',
                                                    border: '1px solid var(--border)',
                                                    borderRadius: '8px',
                                                    color: 'var(--text-primary)',
                                                }}
                                            />
                                            <Line
                                                type="monotone"
                                                dataKey="count"
                                                stroke="#6C63FF"
                                                strokeWidth={2}
                                                dot={{ r: 3 }}
                                                name={t('analytics.conversations', 'Conversations')}
                                            />
                                        </LineChart>
                                    </ResponsiveContainer>
                                ) : (
                                    <div className="chart-empty">{t('analytics.noData', 'No data for this period')}</div>
                                )}
                            </div>

                            <div className="analytics-card">
                                <h3 className="chart-title">
                                    {t('analytics.topKeywords', 'Top Keywords')}
                                    {selectedChatbot && (
                                        <span className="chart-subtitle"> — {getChatbotName(selectedChatbot)}</span>
                                    )}
                                </h3>
                                {keywords.length > 0 ? (
                                    <ResponsiveContainer width="100%" height={240}>
                                        <BarChart
                                            layout="vertical"
                                            data={keywords.slice(0, 10)}
                                            margin={{ top: 5, right: 16, left: 8, bottom: 5 }}
                                        >
                                            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" horizontal={false} />
                                            <XAxis type="number" tick={{ fontSize: 11, fill: 'var(--text-secondary)' }} />
                                            <YAxis
                                                type="category"
                                                dataKey="word"
                                                width={70}
                                                tick={{ fontSize: 11, fill: 'var(--text-secondary)' }}
                                            />
                                            <Tooltip
                                                contentStyle={{
                                                    background: 'var(--surface)',
                                                    border: '1px solid var(--border)',
                                                    borderRadius: '8px',
                                                    color: 'var(--text-primary)',
                                                }}
                                            />
                                            <Bar dataKey="count" fill="#43BFA7" radius={[0, 4, 4, 0]} name={t('analytics.occurrences', 'Occurrences')} />
                                        </BarChart>
                                    </ResponsiveContainer>
                                ) : (
                                    <div className="chart-empty">{t('analytics.noKeywords', 'No keyword data yet')}</div>
                                )}
                            </div>
                        </div>

                        {/* Satisfaction + Chatbot comparison */}
                        <div className="analytics-row-2col">
                            <div className="analytics-card">
                                <h3 className="chart-title">{t('analytics.satisfactionTitle', 'Feedback Distribution')}</h3>
                                {satisfactionData.length > 0 ? (
                                    <div className="satisfaction-container">
                                        <ResponsiveContainer width="60%" height={200}>
                                            <PieChart>
                                                <Pie
                                                    data={satisfactionData}
                                                    cx="50%"
                                                    cy="50%"
                                                    innerRadius={50}
                                                    outerRadius={80}
                                                    paddingAngle={3}
                                                    dataKey="value"
                                                >
                                                    {satisfactionData.map((entry, index) => (
                                                        <Cell key={entry.name} fill={SAT_COLORS[index]} />
                                                    ))}
                                                </Pie>
                                                <Tooltip
                                                    contentStyle={{
                                                        background: 'var(--surface)',
                                                        border: '1px solid var(--border)',
                                                        borderRadius: '8px',
                                                        color: 'var(--text-primary)',
                                                    }}
                                                />
                                            </PieChart>
                                        </ResponsiveContainer>
                                        <div className="satisfaction-legend">
                                            {satisfactionData.map((entry, i) => (
                                                <div key={entry.name} className="sat-legend-item">
                                                    <span className="sat-legend-dot" style={{ background: SAT_COLORS[i] }} />
                                                    <span>{entry.name}</span>
                                                    <span className="sat-legend-val">{entry.value}</span>
                                                </div>
                                            ))}
                                            <div className="sat-rate-badge">
                                                {feedbackStats?.satisfaction_rate ?? 0}%
                                                <span> {t('analytics.satisfied', 'satisfied')}</span>
                                            </div>
                                        </div>
                                    </div>
                                ) : (
                                    <div className="chart-empty">{t('analytics.noFeedback', 'No feedback data yet')}</div>
                                )}
                            </div>

                            <div className="analytics-card">
                                <h3 className="chart-title">{t('analytics.botComparison', 'Chatbot Comparison')}</h3>
                                {botComparison.length > 1 ? (
                                    <ResponsiveContainer width="100%" height={200}>
                                        <BarChart data={botComparison} margin={{ top: 5, right: 16, left: 0, bottom: 5 }}>
                                            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                                            <XAxis dataKey="name" tick={{ fontSize: 11, fill: 'var(--text-secondary)' }} />
                                            <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: 'var(--text-secondary)' }} />
                                            <Tooltip
                                                contentStyle={{
                                                    background: 'var(--surface)',
                                                    border: '1px solid var(--border)',
                                                    borderRadius: '8px',
                                                    color: 'var(--text-primary)',
                                                }}
                                            />
                                            <Legend wrapperStyle={{ fontSize: 12 }} />
                                            <Bar dataKey="conversations" fill="#6C63FF" name={t('analytics.conversations', 'Conversations')} radius={[4, 4, 0, 0]} />
                                            <Bar dataKey="messages" fill="#43BFA7" name={t('analytics.messages', 'Messages')} radius={[4, 4, 0, 0]} />
                                        </BarChart>
                                    </ResponsiveContainer>
                                ) : botComparison.length === 1 ? (
                                    <div className="single-bot-stats">
                                        <div className="sbs-row">
                                            <span>{t('analytics.conversations', 'Conversations')}</span>
                                            <strong>{botComparison[0].conversations}</strong>
                                        </div>
                                        <div className="sbs-row">
                                            <span>{t('analytics.messages', 'Messages')}</span>
                                            <strong>{botComparison[0].messages}</strong>
                                        </div>
                                        {botComparison[0].conversations > 0 && (
                                            <div className="sbs-row">
                                                <span>{t('analytics.avgPerConv', 'Avg per conv.')}</span>
                                                <strong>
                                                    {(overview?.per_chatbot?.[0]?.avg_messages ?? 0)} msg
                                                </strong>
                                            </div>
                                        )}
                                    </div>
                                ) : (
                                    <div className="chart-empty">{t('analytics.noData', 'No data for this period')}</div>
                                )}
                            </div>
                        </div>

                        {/* Peak Hours */}
                        <div className="analytics-card analytics-card-full">
                            <h3 className="chart-title">{t('analytics.peakHours', 'Peak Activity Hours (UTC)')}</h3>
                            {hourData.some(h => h.count > 0) ? (
                                <div className="peak-hours-heatmap-scroll">
                                <div className="peak-hours-heatmap">
                                    {hourData.map((h, i) => {
                                        const max = Math.max(...hourData.map(x => x.count), 1);
                                        const intensity = h.count / max;
                                        return (
                                            <div
                                                key={i}
                                                className="ph-cell"
                                                style={{ '--intensity': intensity }}
                                                title={`${h.hour}: ${h.count} ${t('analytics.conversations', 'conversations')}`}
                                            >
                                                <span className="ph-hour">{h.hour}</span>
                                                {h.count > 0 && <span className="ph-count">{h.count}</span>}
                                            </div>
                                        );
                                    })}
                                </div>
                                </div>
                            ) : (
                                <div className="chart-empty">{t('analytics.noData', 'No data for this period')}</div>
                            )}
                        </div>

                        {/* Sprint D advanced insights — only meaningful for ONE chatbot. */}
                        <AnalyticsInsights chatbotId={selectedChatbot} days={days} />
                    </>
                )}
            </div>
        </Layout>
    );
};

export default Analytics;
