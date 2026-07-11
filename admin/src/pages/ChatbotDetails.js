import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import Layout from '../components/Layout';
import PermissionModal from '../components/PermissionModal';
import ConfirmationModal from '../components/ConfirmationModal';
import PageManagement from '../components/PageManagement';
import { useTranslation } from 'react-i18next';
import { getChatbotConfig, getScrapedPages, getConversations, getChatbotPermissions, grantPermission, updatePermission, revokePermission, getAuditLogs, getFeedbackStats, getTopKeywords, crawlWebsite, getCrawlStatus } from '../services/api';
import './ChatbotDetails.css';
import './AccessManagement.css';

import { ThumbsDown, ThumbsUp } from 'lucide-react';
const ChatbotDetails = () => {
    const { id } = useParams();
    const navigate = useNavigate();
    const { t, i18n } = useTranslation();
    const [config, setConfig] = useState(null);
    const [pages, setPages] = useState([]);
    const [conversations, setConversations] = useState([]);
    const [permissions, setPermissions] = useState([]);
    const [loading, setLoading] = useState(true);
    const [activeTab, setActiveTab] = useState('overview');
    const [isPermissionModalOpen, setIsPermissionModalOpen] = useState(false);
    const [editingPermission, setEditingPermission] = useState(null);
    const [permissionMessage, setPermissionMessage] = useState('');
    const [confirmOpen, setConfirmOpen] = useState(false);
    const [confirmOptions, setConfirmOptions] = useState({
        title: '',
        message: '',
        confirmText: '',
        cancelText: '',
        isDanger: false,
        onConfirm: null,
    });
    const [auditLogs, setAuditLogs] = useState([]);
    const [auditLoading, setAuditLoading] = useState(false);
    const [feedbackStats, setFeedbackStats] = useState(null);
    const [topKeywords, setTopKeywords] = useState(null);
    const [dateRange, setDateRange] = useState(30); // 7, 30, or 90 days
    const [isRescraping, setIsRescraping] = useState(false);

    const loadData = useCallback(async () => {
        try {
            setLoading(true);
            const [configRes, pagesRes, convRes] = await Promise.all([
                getChatbotConfig(id),
                getScrapedPages(id),
                getConversations({ chatbot_id: id })
            ]);

            setConfig(configRes.data);
            setPages(pagesRes.data);

            const convs = convRes.data.conversations || convRes.data || [];
            setConversations(convs);

            // Debug permission info
            console.log('Chatbot config:', {
                is_owner: configRes.data.is_owner,
                permission_level: configRes.data.permission_level,
                showAccessTab: configRes.data.is_owner || configRes.data.permission_level === 'admin'
            });

            // Load permissions if user is owner or admin
            if (configRes.data.is_owner || configRes.data.permission_level === 'admin') {
                try {
                    const permsRes = await getChatbotPermissions(id);
                    setPermissions(permsRes.data);
                } catch (err) {
                    console.error('Error loading permissions:', err);
                }
            }

            // Load feedback stats
            try {
                const statsRes = await getFeedbackStats(id);
                setFeedbackStats(statsRes.data);
            } catch (err) {
                console.error('Error loading feedback stats:', err);
            }

            // Load top keywords
            try {
                const keywordsRes = await getTopKeywords(id, 20);
                setTopKeywords(keywordsRes.data);
            } catch (err) {
                console.error('Error loading top keywords:', err);
            }
        } catch (error) {
            console.error('Error loading chatbot details:', error);
        } finally {
            setLoading(false);
        }
    }, [id]);

    useEffect(() => {
        loadData();
    }, [loadData]);

    // Live crawl progress: while a crawl is running, poll the lightweight
    // /crawl-status endpoint so the re-scrape card reflects real progress and
    // auto-clears when the crawl finishes (no manual refresh needed).
    useEffect(() => {
        if (config?.crawl_status !== 'crawling') return undefined;
        let cancelled = false;
        const interval = setInterval(async () => {
            try {
                const { data } = await getCrawlStatus(id);
                if (cancelled) return;
                setConfig(prev => (prev ? { ...prev, ...data } : prev));
                if (data.crawl_status !== 'crawling') {
                    clearInterval(interval);
                    setIsRescraping(false);
                    // Crawl finished — quietly refresh the scraped pages list.
                    try {
                        const pagesRes = await getScrapedPages(id);
                        if (!cancelled) setPages(pagesRes.data);
                    } catch (e) { /* non-fatal */ }
                }
            } catch (e) {
                /* transient error — keep polling */
            }
        }, 3000);
        return () => { cancelled = true; clearInterval(interval); };
    }, [config?.crawl_status, id]);

    const handleGrantPermission = async (email, level) => {
        try {
            await grantPermission(id, email, level);
            setPermissionMessage(t('chatbotDetails.access.grantSuccess'));
            setTimeout(() => setPermissionMessage(''), 3000);
            loadData();
        } catch (error) {
            throw error;
        }
    };

    const handleReScrape = async () => {
        if (!config.scraped_url) {
            setConfirmOptions({
                title: t('common.confirm'),
                message: t('chatbotDetails.overview.noUrlToScrape'),
                cancelText: t('common.close') || 'OK',
                singleButton: true,
                onConfirm: () => {},
            });
            setConfirmOpen(true);
            return;
        }

        // Open styled confirmation modal instead of native confirm()
        setConfirmOptions({
            title: t('chatbotDetails.overview.reScrapeNow') || t('common.confirm'),
            message: t('chatbotDetails.overview.confirmReScrape', { url: config.scraped_url }),
            confirmText: t('chatbotDetails.overview.reScrapeNow'),
            cancelText: t('common.cancel') || 'Cancel',
            isDanger: true,
            onConfirm: async () => {
                try {
                    setConfirmOpen(false);
                    setIsRescraping(true);
                    await crawlWebsite(id, config.scraped_url);
                    // Optimistically flip to "crawling" so the live-status
                    // poller starts immediately and drives the UI from here on.
                    setConfig(prev => (prev ? { ...prev, crawl_status: 'crawling', crawl_pages_done: 0, crawl_pages_total: null, crawl_error: null } : prev));
                    setConfirmOptions({
                        title: t('chatbotDetails.overview.reScrapeStartedTitle'),
                        message: t('chatbotDetails.overview.reScrapeStarted'),
                        cancelText: t('common.close') || 'OK',
                        singleButton: true,
                        onConfirm: () => {},
                    });
                    setConfirmOpen(true);
                } catch (error) {
                    console.error('Error starting re-scrape:', error);
                    setConfirmOptions({
                        title: t('chatbotDetails.overview.reScrapeErrorTitle'),
                        message: t('chatbotDetails.overview.reScrapeError'),
                        cancelText: t('common.close') || 'OK',
                        singleButton: true,
                        onConfirm: () => {},
                    });
                    setConfirmOpen(true);
                    setIsRescraping(false);
                }
            }
        });

        setConfirmOpen(true);
    };

    const handleUpdatePermission = async (email, level) => {
        try {
            if (editingPermission) {
                await updatePermission(editingPermission._id, level);
                setPermissionMessage(t('chatbotDetails.access.updateSuccess'));
                setTimeout(() => setPermissionMessage(''), 3000);
                setEditingPermission(null);
                loadData();
            }
        } catch (error) {
            throw error;
        }
    };

    const handleRevokePermission = (permissionId) => {
        setConfirmOptions({
            title: t('chatbotDetails.access.revokeTitle') || t('common.confirm'),
            message: t('chatbotDetails.access.revokeConfirm'),
            confirmText: t('chatbotDetails.access.revoke') || 'Revoke',
            cancelText: t('common.cancel') || 'Cancel',
            isDanger: true,
            onConfirm: async () => {
                try {
                    await revokePermission(permissionId);
                    setPermissionMessage(t('chatbotDetails.access.revokeSuccess'));
                    setTimeout(() => setPermissionMessage(''), 3000);
                    loadData();
                } catch (error) {
                    console.error('Error revoking permission:', error);
                    setConfirmOptions({
                        title: t('common.confirm'),
                        message: t('chatbotDetails.access.revokeFailed'),
                        cancelText: t('common.close') || 'OK',
                        singleButton: true,
                        onConfirm: () => {},
                    });
                    setConfirmOpen(true);
                }
            }
        });
        setConfirmOpen(true);
    };

    const openInviteModal = () => {
        setEditingPermission(null);
        setIsPermissionModalOpen(true);
    };

    const openEditModal = (permission) => {
        setEditingPermission(permission);
        setIsPermissionModalOpen(true);
    };

    const loadAuditLogs = async () => {
        try {
            setAuditLoading(true);
            const res = await getAuditLogs(id, { limit: 100 });
            setAuditLogs(res.data.logs || []);
        } catch (err) {
            console.error('Error loading audit logs:', err);
        } finally {
            setAuditLoading(false);
        }
    };

    const exportAnalyticsCSV = () => {
        if (!analytics) return;
        
        // Prepare CSV data
        const csvRows = [];
        csvRows.push(['Chatbot Analytics Export']);
        csvRows.push(['Chatbot', config.name]);
        csvRows.push(['Date Range', `Last ${dateRange} days`]);
        csvRows.push(['Export Date', new Date().toLocaleString()]);
        csvRows.push([]);
        
        // Message stats
        csvRows.push(['Message Statistics']);
        csvRows.push(['Average', analytics.avgMessages]);
        csvRows.push(['Minimum', analytics.minMessages]);
        csvRows.push(['Maximum', analytics.maxMessages]);
        csvRows.push(['Total', analytics.totalMessages]);
        csvRows.push([]);
        
        // Satisfaction score
        if (feedbackStats) {
            csvRows.push(['Satisfaction Score']);
            csvRows.push(['Total Feedback', feedbackStats.total]);
            csvRows.push(['Positive', feedbackStats.positive]);
            csvRows.push(['Negative', feedbackStats.negative]);
            csvRows.push(['Satisfaction Rate', `${feedbackStats.satisfaction_rate}%`]);
            csvRows.push([]);
        }
        
        // Daily data
        csvRows.push(['Date', 'Conversations']);
        analytics.dailyData.forEach(d => {
            csvRows.push([d.date, d.count]);
        });
        csvRows.push([]);
        
        // Top pages
        csvRows.push(['Top Source Pages']);
        csvRows.push(['Page', 'Count']);
        analytics.topPages.forEach(p => {
            csvRows.push([p.page, p.count]);
        });
        
        // Create CSV string
        const csvContent = csvRows.map(row => row.join(',')).join('\n');
        
        // Download
        const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
        const link = document.createElement('a');
        const url = URL.createObjectURL(blob);
        link.setAttribute('href', url);
        link.setAttribute('download', `analytics_${config.name}_${new Date().toISOString().slice(0, 10)}.csv`);
        link.style.visibility = 'hidden';
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    };

    // ── Analytics computations ──
    const analytics = useMemo(() => {
        if (!conversations || conversations.length === 0) return null;

        const now = new Date();
        const rangeStart = new Date(now);
        rangeStart.setDate(rangeStart.getDate() - dateRange);

        // Filter conversations by date range
        const filteredConvs = conversations.filter(conv => {
            const convDate = new Date(conv.created_at || conv.updated_at);
            return convDate >= rangeStart;
        });

        if (filteredConvs.length === 0) return null;

        // 1. Conversations over time (within selected date range)
        const dailyMap = {};
        for (let i = dateRange - 1; i >= 0; i--) {
            const d = new Date(now);
            d.setDate(d.getDate() - i);
            const key = d.toISOString().slice(0, 10);
            dailyMap[key] = 0;
        }
        filteredConvs.forEach(conv => {
            const date = (conv.created_at || conv.updated_at || '').slice(0, 10);
            if (dailyMap.hasOwnProperty(date)) {
                dailyMap[date]++;
            }
        });
        const dailyData = Object.entries(dailyMap).map(([date, count]) => ({ date, count }));
        const maxDaily = Math.max(...dailyData.map(d => d.count), 1);

        // 2. Messages per conversation
        const messageCounts = filteredConvs.map(c => c.message_count || 0);
        const totalMessages = messageCounts.reduce((a, b) => a + b, 0);
        const avgMessages = messageCounts.length > 0 ? (totalMessages / messageCounts.length).toFixed(1) : 0;
        const minMessages = messageCounts.length > 0 ? Math.min(...messageCounts) : 0;
        const maxMessages = messageCounts.length > 0 ? Math.max(...messageCounts) : 0;

        // 3. Peak hours (0-23)
        const hourMap = Array(24).fill(0);
        filteredConvs.forEach(conv => {
            const dateStr = conv.created_at || conv.updated_at;
            if (dateStr) {
                const hour = new Date(dateStr).getHours();
                hourMap[hour]++;
            }
        });
        const maxHour = Math.max(...hourMap, 1);

        // 4. Top pages
        const pageMap = {};
        filteredConvs.forEach(conv => {
            const page = conv.page_title || conv.page_url || 'Unknown';
            pageMap[page] = (pageMap[page] || 0) + 1;
        });
        const topPages = Object.entries(pageMap)
            .sort((a, b) => b[1] - a[1])
            .slice(0, 8)
            .map(([page, count]) => ({ page, count }));

        // 5. Weekly growth
        const startOfThisWeek = new Date(now);
        startOfThisWeek.setDate(now.getDate() - now.getDay());
        startOfThisWeek.setHours(0, 0, 0, 0);
        const startOfLastWeek = new Date(startOfThisWeek);
        startOfLastWeek.setDate(startOfLastWeek.getDate() - 7);

        let thisWeekCount = 0;
        let lastWeekCount = 0;
        filteredConvs.forEach(conv => {
            const d = new Date(conv.created_at || conv.updated_at);
            if (d >= startOfThisWeek) thisWeekCount++;
            else if (d >= startOfLastWeek && d < startOfThisWeek) lastWeekCount++;
        });
        const growthPercent = lastWeekCount > 0
            ? (((thisWeekCount - lastWeekCount) / lastWeekCount) * 100).toFixed(1)
            : thisWeekCount > 0 ? 100 : 0;

        return {
            dailyData, maxDaily,
            avgMessages, minMessages, maxMessages, totalMessages,
            hourMap, maxHour,
            topPages,
            thisWeekCount, lastWeekCount, growthPercent,
        };
    }, [conversations, dateRange]);

    if (loading) {
        return (
            <Layout>
                <div className="loading-container">
                    <div className="loader"></div>
                    <p>{t('chatbotDetails.loading')}</p>
                </div>
            </Layout>
        );
    }

    if (!config) {
        return (
            <Layout>
                <div className="error-container">
                    <h2>{t('chatbotDetails.notFound')}</h2>
                    <button onClick={() => navigate('/chatbot-config')} className="back-btn-simple">
                        {t('chatbotDetails.goBack')}
                    </button>
                </div>
            </Layout>
        );
    }

    return (
        <Layout>
            <div className="chatbot-details">
                <header className="details-header">
                    <div className="header-nav">
                        <button onClick={() => navigate('/chatbot-config')} className="back-btn-icon" title={t('chatbotDetails.backToListTooltip')}>
                            <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" width="24" height="24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.5" d="M10 19l-7-7m0 0l7-7m-7 7h18" />
                            </svg>
                        </button>
                        <div>
                            <h1>{config.name}</h1>
                            <div className="header-badges">
                                <span className={`status-badge ${config.crawl_status}`}>
                                    {config.crawl_status || 'Idle'}
                                </span>
                                <span className="model-badge">
                                    {config.ai_model?.split('/').pop() || 'GPT-3.5'}
                                </span>
                            </div>
                        </div>
                    </div>
                    <div className="header-actions">
                        {(config.permission_level === 'owner' || config.permission_level === 'admin' || config.permission_level === 'editor') && (
                            <button onClick={() => navigate('/chatbot-config', { state: { editId: id } })} className="edit-btn">
                                <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" width="18" height="18">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                                </svg>
                                {t('chatbotDetails.editConfig')}
                            </button>
                        )}
                        {config.permission_level === 'viewer' && (
                            <span className="permission-badge viewer" style={{padding: '8px 16px', fontSize: '14px'}}>
                                {t('chatbotDetails.viewOnly')}
                            </span>
                        )}
                    </div>
                </header>

                <div className="details-tabs">
                    <button
                        className={`tab-btn ${activeTab === 'overview' ? 'active' : ''}`}
                        onClick={() => setActiveTab('overview')}
                    >
                        {t('chatbotDetails.tabs.overview')}
                    </button>
                    <button
                        className={`tab-btn ${activeTab === 'pages' ? 'active' : ''}`}
                        onClick={() => setActiveTab('pages')}
                    >
                        {t('chatbotDetails.tabs.scrapedPages')} ({pages.length})
                    </button>
                    <button
                        className={`tab-btn ${activeTab === 'conversations' ? 'active' : ''}`}
                        onClick={() => setActiveTab('conversations')}
                    >
                        {t('chatbotDetails.tabs.conversations')} ({conversations.length})
                    </button>
                    {(config.is_owner || config.permission_level === 'admin') && (
                        <button
                            className={`tab-btn ${activeTab === 'access' ? 'active' : ''}`}
                            onClick={() => setActiveTab('access')}
                        >
                            <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" width="16" height="16" style={{marginRight: '6px'}}>
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z" />
                            </svg>
                            {t('chatbotDetails.tabs.access')} ({permissions.length})
                        </button>
                    )}
                    {(config.is_owner || config.permission_level === 'admin') && (
                        <button
                            className={`tab-btn ${activeTab === 'audit' ? 'active' : ''}`}
                            onClick={() => { setActiveTab('audit'); if (auditLogs.length === 0) loadAuditLogs(); }}
                        >
                            <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" width="16" height="16" style={{marginRight: '6px'}}>
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01" />
                            </svg>
                            {t('chatbotDetails.tabs.auditLog')}
                        </button>
                    )}
                    <button
                        className={`tab-btn ${activeTab === 'analytics' ? 'active' : ''}`}
                        onClick={() => setActiveTab('analytics')}
                    >
                        <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" width="16" height="16" style={{marginRight: '6px'}}>
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                        </svg>
                        {t('chatbotDetails.tabs.analytics')}
                    </button>
                </div>

                <div className="tab-content">
                    {activeTab === 'overview' && (
                        <div className="overview-grid">
                            <div className="summary-card">
                                <h3>{t('chatbotDetails.overview.configSummary')}</h3>
                                <div className="summary-details">
                                    <div className="detail-item">
                                        <span className="label">{t('chatbotDetails.overview.primaryWebsite')}</span>
                                        <a href={config.website_url} target="_blank" rel="noopener noreferrer" className="value-link">
                                            {config.website_url || t('chatbotDetails.overview.notSet')}
                                        </a>
                                    </div>
                                    <div className="detail-item">
                                        <span className="label">{t('chatbotDetails.overview.lastScraped')}</span>
                                        <span className="value">
                                            {config.last_scraped_at ? new Date(config.last_scraped_at).toLocaleString() : t('chatbotDetails.overview.never')}
                                        </span>
                                    </div>
                                    <div className="detail-item">
                                        <span className="label">{t('chatbotDetails.overview.allowedDomains')}</span>
                                        <span className="value">
                                            {config.allowed_domains?.join(', ') || t('chatbotDetails.overview.any')}
                                        </span>
                                    </div>
                                    <div className="detail-item">
                                        <span className="label">{t('chatbotDetails.overview.createdOn')}</span>
                                        <span className="value">
                                            {new Date(config.created_at).toLocaleDateString()}
                                        </span>
                                    </div>
                                </div>
                                <div className="theme-preview-card">
                                    <span>{t('chatbotDetails.overview.themeColors')}</span>
                                    <div className="colors">
                                        <div className="color-box" style={{ backgroundColor: config.primary_color }} title={t('chatbotConfig.colorPrimary')}></div>
                                        <div className="color-box" style={{ backgroundColor: config.secondary_color }} title={t('chatbotConfig.colorSecondary')}></div>
                                    </div>
                                </div>
                            </div>

                            <div className="stats-cards-mini">
                                <div className="mini-stat">
                                    <div className="stat-val">{conversations.length}</div>
                                    <div className="stat-lab">{t('chatbotDetails.overview.totalChats')}</div>
                                </div>
                                <div className="mini-stat">
                                    <div className="stat-val">{pages.length}</div>
                                    <div className="stat-lab">{t('chatbotDetails.overview.scrapedUrls')}</div>
                                </div>
                                <div className="mini-stat">
                                    <div className="stat-val">{config.documents?.length || 0}</div>
                                    <div className="stat-lab">{t('chatbotDetails.overview.knowledgeFiles')}</div>
                                </div>
                            </div>

                            {/* Re-scrape Section */}
                            {config.scraped_url && (config.permission_level === 'owner' || config.permission_level === 'admin' || config.permission_level === 'editor') && (
                                <div className="rescrape-card">
                                    <div className="rescrape-header">
                                        <div className="rescrape-icon">
                                            <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" width="20" height="20">
                                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                                            </svg>
                                        </div>
                                        <div className="rescrape-info">
                                            <h4>{t('chatbotDetails.overview.autoRescrape')}</h4>
                                            <div className="rescrape-details">
                                                <div className="detail-row">
                                                    <span className="detail-label">{t('chatbotDetails.overview.lastScraped')}</span>
                                                    <span className="detail-value">
                                                        {config.last_scraped_at
                                                            ? new Date(config.last_scraped_at).toLocaleString(i18n.language)
                                                            : t('chatbotDetails.overview.never')}
                                                    </span>
                                                </div>
                                                {config.rescrape_interval_hours && (
                                                    <div className="detail-row">
                                                        <span className="detail-label">{t('chatbotDetails.overview.rescrapeInterval')}</span>
                                                        <span className="detail-value">
                                                            {t('chatbotDetails.overview.everyNHours', { hours: config.rescrape_interval_hours })}
                                                        </span>
                                                    </div>
                                                )}
                                                {config.crawl_status === 'crawling' && (
                                                    <div className="detail-row">
                                                        <span className="detail-label">{t('chatbotDetails.overview.status')}</span>
                                                        <span className="detail-value crawling-status">
                                                            <span className="status-indicator"></span>
                                                            {t('chatbotDetails.overview.crawling')} ({config.crawl_pages_total
                                                                ? `${config.crawl_pages_done || 0}/${config.crawl_pages_total}`
                                                                : t('chatbotDetails.overview.pagesCrawled', { count: config.crawl_pages_done || 0 })})
                                                        </span>
                                                    </div>
                                                )}
                                            </div>
                                        </div>
                                    </div>
                                    <button
                                        onClick={handleReScrape}
                                        disabled={isRescraping || config.crawl_status === 'crawling'}
                                        className="rescrape-btn"
                                        title={t('chatbotDetails.overview.reScrapeTooltip')}
                                    >
                                        {isRescraping || config.crawl_status === 'crawling' ? (
                                            <>
                                                <svg className="spinner" fill="none" viewBox="0 0 24 24" width="18" height="18">
                                                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                                                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                                                </svg>
                                                {t('chatbotDetails.overview.scraping')}
                                            </>
                                        ) : (
                                            <>
                                                <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" width="18" height="18">
                                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                                                </svg>
                                                {t('chatbotDetails.overview.reScrapeNow')}
                                            </>
                                        )}
                                    </button>
                                </div>
                            )}
                        </div>
                    )}

                    {activeTab === 'pages' && (
                        <PageManagement 
                            chatbotId={id}
                            config={config}
                            onUpdate={loadData}
                        />
                    )}

                    {activeTab === 'conversations' && (
                        <div className="conversations-grid">
                            {conversations.length === 0 ? (
                                <div className="empty-state">
                                    <p>{t('chatbotDetails.conversations.noConversations')}</p>
                                </div>
                            ) : (
                                <div className="conversations-list-mini">
                                    {conversations.map((conv) => (
                                        <div key={conv._id} className="conv-item-mini" onClick={() => navigate(`/conversations?id=${conv._id}`)}>
                                            <div className="conv-main">
                                                <span className="conv-title">{conv.page_title || t('dashboard.untitledSession')}</span>
                                                <span className="conv-id">#{conv._id.slice(-6)}</span>
                                            </div>
                                            <div className="conv-meta">
                                                <span>{conv.message_count} {t('chatbotDetails.conversations.messages')}</span>
                                                <span>{new Date(conv.updated_at).toLocaleDateString()}</span>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                    )}

                    {activeTab === 'access' && (
                        <div className="access-management">
                            {permissionMessage && (
                                <div className="success-message">{permissionMessage}</div>
                            )}

                            <div className="access-header">
                                <h3>{t('chatbotDetails.access.title')}</h3>
                                <button onClick={openInviteModal} className="invite-btn">
                                    <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" width="18" height="18">
                                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M18 9v3m0 0v3m0-3h3m-3 0h-3m-2-5a4 4 0 11-8 0 4 4 0 018 0zM3 20a6 6 0 0112 0v1H3v-1z" />
                                    </svg>
                                    {t('chatbotDetails.access.inviteUser')}
                                </button>
                            </div>

                            <div className="permissions-info">
                                <p><strong>{t('chatbotDetails.access.permissionLevels')}</strong></p>
                                <ul>
                                    <li><strong>{t('chatbotDetails.access.viewer')}:</strong> {t('chatbotDetails.access.viewerDesc')}</li>
                                    <li><strong>{t('chatbotDetails.access.editor')}:</strong> {t('chatbotDetails.access.editorDesc')}</li>
                                    <li><strong>{t('chatbotDetails.access.admin')}:</strong> {t('chatbotDetails.access.adminDesc')}</li>
                                </ul>
                            </div>

                            {permissions.length === 0 ? (
                                <div className="empty-state">
                                    <p>{t('chatbotDetails.access.noUsers')}</p>
                                    <p>{t('chatbotDetails.access.inviteHint')}</p>
                                </div>
                            ) : (
                                <div className="permissions-table">
                                    <table>
                                        <thead>
                                            <tr>
                                                <th>{t('chatbotDetails.access.userEmail')}</th>
                                                <th>{t('chatbotDetails.access.permissionLevel')}</th>
                                                <th>{t('chatbotDetails.access.grantedBy')}</th>
                                                <th>{t('chatbotDetails.access.grantedAt')}</th>
                                                <th>{t('chatbotDetails.access.actions')}</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {permissions.map((perm) => (
                                                <tr key={perm._id}>
                                                    <td data-label={t('chatbotDetails.access.userEmail')}>{perm.user_email}</td>
                                                    <td data-label={t('chatbotDetails.access.permissionLevel')}>
                                                        <span className={`permission-badge ${perm.permission_level}`}>
                                                            {perm.permission_level}
                                                        </span>
                                                    </td>
                                                    <td data-label={t('chatbotDetails.access.grantedBy')}>{perm.granted_by}</td>
                                                    <td data-label={t('chatbotDetails.access.grantedAt')}>{new Date(perm.granted_at).toLocaleDateString()}</td>
                                                    <td data-label={t('chatbotDetails.access.actions')}>
                                                        <div className="action-buttons">
                                                            <button 
                                                                onClick={() => openEditModal(perm)} 
                                                                className="edit-perm-btn"
                                                                title={t('chatbotDetails.editPermissionTooltip')}
                                                            >
                                                                <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" width="16" height="16">
                                                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                                                                </svg>
                                                            </button>
                                                            <button 
                                                                onClick={() => handleRevokePermission(perm._id)} 
                                                                className="revoke-btn"
                                                                title={t('chatbotDetails.revokeAccessTooltip')}
                                                            >
                                                                <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" width="16" height="16">
                                                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                                                </svg>
                                                            </button>
                                                        </div>
                                                    </td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            )}
                        </div>
                    )}

                    {activeTab === 'audit' && (
                        <div className="audit-log-container">
                            <div className="access-header">
                                <h3>{t('chatbotDetails.audit.title')}</h3>
                                <button onClick={loadAuditLogs} className="invite-btn" disabled={auditLoading}>
                                    <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" width="18" height="18">
                                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                                    </svg>
                                    {auditLoading ? t('common.loading') : t('common.refresh')}
                                </button>
                            </div>
                            {auditLoading ? (
                                <div className="empty-state"><div className="loader"></div><p>{t('chatbotDetails.audit.loading')}</p></div>
                            ) : auditLogs.length === 0 ? (
                                <div className="empty-state">
                                    <p>{t('chatbotDetails.audit.noLogs')}</p>
                                    <p>{t('chatbotDetails.audit.noLogsHint')}</p>
                                </div>
                            ) : (
                                <div className="permissions-table">
                                    <table>
                                        <thead>
                                            <tr>
                                                <th>{t('chatbotDetails.audit.action')}</th>
                                                <th>{t('chatbotDetails.audit.performedBy')}</th>
                                                <th>{t('chatbotDetails.audit.targetUser')}</th>
                                                <th>{t('chatbotDetails.audit.details')}</th>
                                                <th>{t('chatbotDetails.audit.ipAddress')}</th>
                                                <th>{t('chatbotDetails.audit.timestamp')}</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {auditLogs.map((log) => {
                                                const actionCategory = log.action?.split(':')[0] || '';
                                                const badgeClass = {
                                                    permission: 'editor',
                                                    chatbot: 'admin',
                                                    widget: 'viewer',
                                                }[actionCategory] || 'viewer';
                                                return (
                                                    <tr key={log._id}>
                                                        <td data-label={t('chatbotDetails.audit.action')}>
                                                            <span className={`permission-badge ${badgeClass}`}>
                                                                {log.action}
                                                            </span>
                                                        </td>
                                                        <td data-label={t('chatbotDetails.audit.performedBy')}>{log.performed_by}</td>
                                                        <td data-label={t('chatbotDetails.audit.targetUser')}>{log.target_user || <span style={{color:'#9ca3af'}}>—</span>}</td>
                                                        <td data-label={t('chatbotDetails.audit.details')}>
                                                            <code style={{fontSize:'12px', color:'#6b7280'}}>
                                                                {log.details && Object.keys(log.details).length > 0
                                                                    ? Object.entries(log.details)
                                                                        .map(([k, v]) => `${k}: ${JSON.stringify(v)}`)
                                                                        .join(', ')
                                                                    : '—'}
                                                            </code>
                                                        </td>
                                                        <td data-label={t('chatbotDetails.audit.ipAddress')}>{log.ip_address || <span style={{color:'#9ca3af'}}>—</span>}</td>
                                                        <td data-label={t('chatbotDetails.audit.timestamp')}>{log.timestamp ? new Date(log.timestamp).toLocaleString() : '—'}</td>
                                                    </tr>
                                                );
                                            })}
                                        </tbody>
                                    </table>
                                </div>
                            )}
                        </div>
                    )}

                    {activeTab === 'analytics' && (
                        <div className="analytics-dashboard">
                            {/* Date Range Filter + Export */}
                            <div className="analytics-controls">
                                <div className="date-range-selector">
                                    <button
                                        className={`date-range-btn ${dateRange === 7 ? 'active' : ''}`}
                                        onClick={() => setDateRange(7)}
                                    >
                                        {t('chatbotDetails.analytics.last7Days')}
                                    </button>
                                    <button
                                        className={`date-range-btn ${dateRange === 30 ? 'active' : ''}`}
                                        onClick={() => setDateRange(30)}
                                    >
                                        {t('chatbotDetails.analytics.last30Days')}
                                    </button>
                                    <button
                                        className={`date-range-btn ${dateRange === 90 ? 'active' : ''}`}
                                        onClick={() => setDateRange(90)}
                                    >
                                        {t('chatbotDetails.analytics.last90Days')}
                                    </button>
                                </div>
                                {analytics && (
                                    <button className="csv-export-btn" onClick={exportAnalyticsCSV}>
                                        <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" width="18" height="18">
                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                                        </svg>
                                        {t('chatbotDetails.analytics.exportCSV')}
                                    </button>
                                )}
                            </div>

                            {!analytics ? (
                                <div className="empty-state">
                                    <p>{t('chatbotDetails.analytics.noData')}</p>
                                </div>
                            ) : (
                                <>
                                    {/* Row 1: Satisfaction Score + Message stats + Weekly growth */}
                                    <div className="analytics-stats-row">
                                        {feedbackStats && feedbackStats.total > 0 && (
                                            <div className="analytics-card analytics-satisfaction-card">
                                                <h3>{t('chatbotDetails.analytics.satisfactionScore')}</h3>
                                                <div className="satisfaction-display">
                                                    <div className="satisfaction-rate">
                                                        <span className="satisfaction-value">{feedbackStats.satisfaction_rate}%</span>
                                                        <span className="satisfaction-label">{t('chatbotDetails.analytics.satisfied')}</span>
                                                    </div>
                                                    <div className="satisfaction-details">
                                                        <div className="satisfaction-row positive">
                                                            <span><ThumbsUp size={16} /> {t('chatbotDetails.analytics.positive')}</span>
                                                            <strong>{feedbackStats.positive}</strong>
                                                        </div>
                                                        <div className="satisfaction-row negative">
                                                            <span><ThumbsDown size={16} /> {t('chatbotDetails.analytics.negative')}</span>
                                                            <strong>{feedbackStats.negative}</strong>
                                                        </div>
                                                        <div className="satisfaction-row total">
                                                            <span>{t('chatbotDetails.analytics.totalFeedback')}</span>
                                                            <strong>{feedbackStats.total}</strong>
                                                        </div>
                                                    </div>
                                                </div>
                                            </div>
                                        )}
                                        <div className="analytics-card">
                                            <h3>{t('chatbotDetails.analytics.messagesPerConversation')}</h3>
                                            <div className="analytics-stats-grid">
                                                <div className="analytics-stat">
                                                    <span className="analytics-stat-value">{analytics.avgMessages}</span>
                                                    <span className="analytics-stat-label">{t('chatbotDetails.analytics.average')}</span>
                                                </div>
                                                <div className="analytics-stat">
                                                    <span className="analytics-stat-value">{analytics.minMessages}</span>
                                                    <span className="analytics-stat-label">{t('chatbotDetails.analytics.minimum')}</span>
                                                </div>
                                                <div className="analytics-stat">
                                                    <span className="analytics-stat-value">{analytics.maxMessages}</span>
                                                    <span className="analytics-stat-label">{t('chatbotDetails.analytics.maximum')}</span>
                                                </div>
                                                <div className="analytics-stat">
                                                    <span className="analytics-stat-value">{analytics.totalMessages}</span>
                                                    <span className="analytics-stat-label">{t('chatbotDetails.analytics.total')}</span>
                                                </div>
                                            </div>
                                        </div>
                                        <div className="analytics-card analytics-growth-card">
                                            <h3>{t('chatbotDetails.analytics.weeklyGrowth')}</h3>
                                            <div className="growth-display">
                                                <span className={`growth-percent ${Number(analytics.growthPercent) >= 0 ? 'positive' : 'negative'}`}>
                                                    {Number(analytics.growthPercent) >= 0 ? '+' : ''}{analytics.growthPercent}%
                                                </span>
                                                <div className="growth-details">
                                                    <div className="growth-row">
                                                        <span>{t('chatbotDetails.analytics.thisWeek')}</span>
                                                        <strong>{analytics.thisWeekCount}</strong>
                                                    </div>
                                                    <div className="growth-row">
                                                        <span>{t('chatbotDetails.analytics.lastWeek')}</span>
                                                        <strong>{analytics.lastWeekCount}</strong>
                                                    </div>
                                                </div>
                                            </div>
                                        </div>
                                    </div>

                                    {/* Row 2: Conversations over time bar chart */}
                                    <div className="analytics-card analytics-chart-card">
                                        <h3>{t('chatbotDetails.analytics.conversationsOverTime')}</h3>
                                        <span className="analytics-subtitle">{t('chatbotDetails.analytics.dateRangeLabel', { days: dateRange })}</span>
                                        <div className="bar-chart">
                                            {analytics.dailyData.map((d, i) => (
                                                <div key={i} className="bar-col" title={`${d.date}: ${d.count} ${t('chatbotDetails.analytics.conversations')}`}>
                                                    <div
                                                        className="bar"
                                                        style={{ height: `${(d.count / analytics.maxDaily) * 100}%` }}
                                                    >
                                                        {d.count > 0 && <span className="bar-value">{d.count}</span>}
                                                    </div>
                                                    {(dateRange === 7 || i % 5 === 0) && (
                                                        <span className="bar-label">{d.date.slice(5)}</span>
                                                    )}
                                                </div>
                                            ))}
                                        </div>
                                    </div>

                                    {/* Row 3: Peak hours + Top pages */}
                                    <div className="analytics-stats-row">
                                        <div className="analytics-card analytics-chart-card">
                                            <h3>{t('chatbotDetails.analytics.peakHours')}</h3>
                                            <span className="analytics-subtitle">{t('chatbotDetails.analytics.hourDistribution')}</span>
                                            <div className="bar-chart bar-chart-hours">
                                                {analytics.hourMap.map((count, hour) => (
                                                    <div key={hour} className="bar-col" title={`${hour}h: ${count} ${t('chatbotDetails.analytics.conversations')}`}>
                                                        <div
                                                            className="bar"
                                                            style={{ height: `${(count / analytics.maxHour) * 100}%` }}
                                                        >
                                                            {count > 0 && <span className="bar-value">{count}</span>}
                                                        </div>
                                                        {hour % 3 === 0 && (
                                                            <span className="bar-label">{hour}h</span>
                                                        )}
                                                    </div>
                                                ))}
                                            </div>
                                        </div>
                                        <div className="analytics-card">
                                            <h3>{t('chatbotDetails.analytics.topPages')}</h3>
                                            {analytics.topPages.length === 0 ? (
                                                <p className="empty-hint">{t('chatbotDetails.analytics.noData')}</p>
                                            ) : (
                                                <div className="top-pages-list">
                                                    {analytics.topPages.map((p, i) => (
                                                        <div key={i} className="top-page-item">
                                                            <span className="top-page-rank">#{i + 1}</span>
                                                            <span className="top-page-name" title={p.page}>{p.page}</span>
                                                            <div className="top-page-bar-wrapper">
                                                                <div
                                                                    className="top-page-bar"
                                                                    style={{ width: `${(p.count / analytics.topPages[0].count) * 100}%` }}
                                                                ></div>
                                                            </div>
                                                            <span className="top-page-count">{p.count}</span>
                                                        </div>
                                                    ))}
                                                </div>
                                            )}
                                        </div>
                                    </div>

                                    {/* Row 4: Top Keywords/Most Frequently Asked Topics */}
                                    {topKeywords && topKeywords.keywords && topKeywords.keywords.length > 0 && (
                                        <div className="analytics-card analytics-keywords-card">
                                            <h3>{t('chatbotDetails.analytics.topKeywords')}</h3>
                                            <span className="analytics-subtitle">
                                                {t('chatbotDetails.analytics.keywordsSubtitle', {
                                                    total: topKeywords.total_messages,
                                                    conversations: topKeywords.total_conversations
                                                })}
                                            </span>
                                            <div className="keywords-grid">
                                                {topKeywords.keywords.map((keyword, i) => (
                                                    <div key={i} className="keyword-item">
                                                        <div className="keyword-header">
                                                            <span className="keyword-rank">#{i + 1}</span>
                                                            <span className="keyword-word">{keyword.word}</span>
                                                        </div>
                                                        <div className="keyword-stats">
                                                            <div className="keyword-bar-wrapper">
                                                                <div
                                                                    className="keyword-bar"
                                                                    style={{
                                                                        width: `${(keyword.count / topKeywords.keywords[0].count) * 100}%`
                                                                    }}
                                                                ></div>
                                                            </div>
                                                            <div className="keyword-numbers">
                                                                <span className="keyword-count">{keyword.count}</span>
                                                                <span className="keyword-percentage">{keyword.percentage}%</span>
                                                            </div>
                                                        </div>
                                                    </div>
                                                ))}
                                            </div>
                                            <div className="keywords-info">
                                                <p>
                                                    <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                                                    </svg>
                                                    {t('chatbotDetails.analytics.keywordsInfo')}
                                                </p>
                                            </div>
                                        </div>
                                    )}
                                </>
                            )}
                        </div>
                    )}
                </div>

                <PermissionModal
                    isOpen={isPermissionModalOpen}
                    onClose={() => setIsPermissionModalOpen(false)}
                    onSubmit={editingPermission ? handleUpdatePermission : handleGrantPermission}
                    title={editingPermission ? t('chatbotDetails.access.editTitle') : t('chatbotDetails.access.inviteUser')}
                    initialEmail={editingPermission?.user_email || ''}
                    initialLevel={editingPermission?.permission_level || 'viewer'}
                />
                <ConfirmationModal
                    isOpen={confirmOpen}
                    onClose={() => setConfirmOpen(false)}
                    onConfirm={async () => {
                        // invoke provided action then close
                        if (typeof confirmOptions.onConfirm === 'function') {
                            await confirmOptions.onConfirm();
                        }
                    }}
                    title={confirmOptions.title}
                    message={confirmOptions.message}
                    confirmText={confirmOptions.confirmText}
                    cancelText={confirmOptions.cancelText}
                    isDanger={confirmOptions.isDanger}
                    singleButton={confirmOptions.singleButton}
                />
            </div>
        </Layout>
    );
};

export default ChatbotDetails;
