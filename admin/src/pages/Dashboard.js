import React, { useState, useEffect } from 'react';
import Layout from '../components/Layout';
import { getChatbotConfigs, getConversations } from '../services/api';
import { useTranslation } from 'react-i18next';
import './Dashboard.css';

const Dashboard = () => {
    const [stats, setStats] = useState({
        chatbots: 0,
        conversations: 0,
        messages: 0,
    });
    const [recentConversations, setRecentConversations] = useState([]);
    const [loading, setLoading] = useState(true);
    const { t } = useTranslation();

    useEffect(() => {
        loadDashboardData();
    }, []);

    const loadDashboardData = async () => {
        try {
            const [chatbotsRes, conversationsRes] = await Promise.all([
                getChatbotConfigs(),
                getConversations(),
            ]);

            const chatbots = chatbotsRes.data;
            const conversationsData = conversationsRes.data;
            const conversations = conversationsData.conversations || conversationsData;
            const totalConversations = conversationsData.total || conversations.length;

            const totalMessages = conversations.reduce(
                (sum, conv) => sum + (conv.message_count || 0),
                0
            );

            setStats({
                chatbots: chatbots.length,
                conversations: totalConversations,
                messages: totalMessages,
            });

            setRecentConversations(conversations.slice(0, 5));
        } catch (error) {
            console.error('Error loading dashboard:', error);
        } finally {
            setLoading(false);
        }
    };

    if (loading) {
        return (
            <Layout>
                <div className="loading">{t('dashboard.loading')}</div>
            </Layout>
        );
    }

    return (
        <Layout>
            <div className="dashboard">
                <header className="page-header">
                    <h1>{t('dashboard.title')}</h1>
                    <p className="subtitle">{t('dashboard.subtitle')}</p>
                </header>

                <div className="stats-grid">
                    <div className="stat-card">
                        <div className="stat-icon-wrapper primary">
                            <svg className="stat-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
                            </svg>
                        </div>
                        <div className="stat-content">
                            <div className="stat-value">{stats.chatbots}</div>
                            <div className="stat-label">{t('dashboard.activeChatbots')}</div>
                            <div className="stat-trend positive">
                                <span>{t('common.online')}</span>
                            </div>
                        </div>
                    </div>

                    <div className="stat-card">
                        <div className="stat-icon-wrapper success">
                            <svg className="stat-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" />
                            </svg>
                        </div>
                        <div className="stat-content">
                            <div className="stat-value">{stats.conversations}</div>
                            <div className="stat-label">{t('dashboard.totalConversations')}</div>
                            <div className="stat-trend positive">
                                <span>{t('common.active')}</span>
                            </div>
                        </div>
                    </div>

                    <div className="stat-card">
                        <div className="stat-icon-wrapper warning">
                            <svg className="stat-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
                            </svg>
                        </div>
                        <div className="stat-content">
                            <div className="stat-value">{stats.messages}</div>
                            <div className="stat-label">{t('dashboard.totalMessages')}</div>
                            <div className="stat-trend positive">
                                <span>{stats.messages > 0 ? `${(stats.messages / Math.max(stats.conversations, 1)).toFixed(1)} ${t('dashboard.msgsPerConv')}` : t('common.noDataYet')}</span>
                            </div>
                        </div>
                    </div>
                </div>

                <div className="recent-section">
                    <div className="recent-section-header">
                        <h2>{t('dashboard.recentConversations')}</h2>
                    </div>

                    {recentConversations.length === 0 ? (
                        <div className="empty-state">
                            <p>{t('dashboard.noConversations')}</p>
                        </div>
                    ) : (
                        <div className="conversations-list">
                            {recentConversations.map((conv) => (
                                <div key={conv._id} className="conversation-item">
                                    <div className="conversation-info">
                                        <div className="conversation-title">
                                            {conv.page_title || t('dashboard.untitledSession')}
                                            <span className="conversation-id">#{conv._id.slice(-6)}</span>
                                        </div>
                                        <div className="conversation-meta">
                                            {conv.page_url && (
                                                <div className="meta-item">
                                                    <svg className="meta-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
                                                    </svg>
                                                    <a href={conv.page_url} target="_blank" rel="noopener noreferrer" className="conversation-url">
                                                        {conv.page_url.replace(/^https?:\/\//, '')}
                                                    </a>
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                    <div className="conversation-right">
                                        <div className="conversation-date">
                                            {new Date(conv.updated_at).toLocaleDateString(undefined, {
                                                month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
                                            })}
                                        </div>
                                        <div className="meta-item">
                                            <span className="status-badge active">
                                                {conv.message_count} msgs
                                            </span>
                                        </div>
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            </div>
        </Layout>
    );
};

export default Dashboard;
