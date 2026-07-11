import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useLocation, useSearchParams } from 'react-router-dom';
import Layout from '../components/Layout';
import PositionPicker from '../components/PositionPicker';
import DocumentsPanel from '../components/DocumentsPanel';
import { useAuth } from '../context/AuthContext';
import { copyText } from '../utils/clipboard';
import {
    getChatbotConfigs,
    createChatbotConfig,
    updateChatbotConfig,
    deleteChatbotConfig,
    uploadImage,
    uploadDocument,
    crawlWebsite,
    getCrawlStatus,
    sendChatMessage,
    buildWidgetBundle,
    downloadWidgetBundle,
} from '../services/api';
import './ChatbotConfig.css';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import ConfirmationModal from '../components/ConfirmationModal';
import InputModal from '../components/InputModal';
import { getErrorMessage } from '../utils/errorMessage';

import { Bot, Camera, Eye, FileEdit, FileText, FlaskConical, Folder, Globe, Magnet, Map, Package, Palette, Pencil, Rocket, Ruler, Search, Settings, Sparkles } from 'lucide-react';
// Pick a readable text colour (dark/light) for a given background hex so the
// test-panel bot bubble stays legible regardless of the admin theme or the
// chatbot's chosen bubble colour.
const readableTextColor = (hex) => {
    if (!hex || typeof hex !== 'string') return '#1f2937';
    let h = hex.trim().replace('#', '');
    if (h.length === 3) h = h.split('').map(c => c + c).join('');
    if (h.length !== 6) return '#1f2937';
    const r = parseInt(h.slice(0, 2), 16), g = parseInt(h.slice(2, 4), 16), b = parseInt(h.slice(4, 6), 16);
    if ([r, g, b].some(Number.isNaN)) return '#1f2937';
    const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
    return luminance > 0.6 ? '#1f2937' : '#ffffff';
};

const ChatbotConfig = () => {
    const navigate = useNavigate();
    const location = useLocation();
    const { t } = useTranslation();
    const { canCreateChatbot } = useAuth();
    const [configs, setConfigs] = useState([]);
    const [selectedConfig, setSelectedConfig] = useState(null);
    const [formData, setFormData] = useState({
        name: t('chatbotConfig.defaults.name'),
        system_prompt: t('chatbotConfig.defaults.systemPrompt'),
        primary_color: '#3B82F6',
        secondary_color: '#1E40AF',
        icon_url: '',
        position: 'bottom-right',
        position_style: { bottom: '20px', right: '20px' },
        button_size: 60,
        transparent_background: false,
        welcome_message: t('chatbotConfig.defaults.welcome'),
        suggested_questions: '',
        window_title: t('chatbotConfig.defaults.windowTitle'),
        temperature: 0.7,
        // New design options
        widget_width: 400,
        widget_height: 600,
        background_color: '#FFFFFF',
        border_radius: 16,
        message_bubble_radius: 12,
        user_message_color: '#3B82F6',
        bot_message_color: '#F3F4F6',
        font_size: 14,
        rescrape_interval_hours: null,
        // Security & Model
        website_url: '',
        allowed_domains: '', // Stored as comma-separated string in UI, converted to array on save if needed
        ignored_urls: '',
        sitemap_url: '',
        ai_model: 'gemini-2.5-flash-lite',
        documents: [],
        theme: 'light',
        collect_feedback: true,
        custom_css: '',
        entry_animation: 'slide',
        bot_avatar_url: '',
        require_email: false,
        in_chat_email_capture: false,
        initial_questions: [],
        supplemental_info: '',
    });
    const [configTab, setConfigTab] = useState('general');
    const [loading, setLoading] = useState(false);
    const [message, setMessage] = useState('');
    // Track message kind explicitly so the banner color is correct in every
    // locale (the previous message.includes('Error') check only worked in English).
    const [messageType, setMessageType] = useState('success');
    const notify = (text, type = 'success') => { setMessage(text); setMessageType(type); };
    const [isDeleteModalOpen, setIsDeleteModalOpen] = useState(false);
    const [configToDelete, setConfigToDelete] = useState(null);
    const [isInputModalOpen, setIsInputModalOpen] = useState(false);
    const [configToCrawl, setConfigToCrawl] = useState(null);
    const [crawlStatuses, setCrawlStatuses] = useState({});
    const [crawlInitialUrl, setCrawlInitialUrl] = useState('');

    const [showForm, setShowForm] = useState(false);
    const createBtnRef = useRef(null);
    const [buildLoading, setBuildLoading] = useState(false); // widget build state
    
    // Search and Filter state — searchQuery is backed by URL param ?q= so the
    // global header search bar can drive this list.
    const [searchParams, setSearchParams] = useSearchParams();
    const searchQuery = searchParams.get('q') || '';
    const setSearchQuery = (v) => {
        const np = new URLSearchParams(searchParams);
        if (v) np.set('q', v); else np.delete('q');
        setSearchParams(np, { replace: true });
    };
    const [statusFilter, setStatusFilter] = useState('all');

    // Test Panel state
    const [testPanel, setTestPanel] = useState({
        open: false,
        config: null,
        messages: [],
        input: '',
        loading: false,
        conversationId: null,
    });
    const testChatEndRef = useRef(null);

    const formatShortDate = (value) => {
        if (!value) return '—';
        try {
            const date = new Date(value);
            if (Number.isNaN(date.getTime())) return '—';
            return date.toLocaleDateString('en-GB', {
                day: '2-digit',
                month: 'short',
                year: 'numeric',
            });
        } catch (err) {
            return '—';
        }
    };

    // Given a CSS color (hex, rgb, rgba), return '#000' or '#fff' for best contrast
    const getContrastColor = (col) => {
        try {
            if (!col) return '#000';
            col = col.trim();
            let r, g, b;
            if (col.startsWith('#')) {
                let hex = col.slice(1);
                if (hex.length === 3) hex = hex.split('').map(c => c+c).join('');
                r = parseInt(hex.slice(0,2), 16);
                g = parseInt(hex.slice(2,4), 16);
                b = parseInt(hex.slice(4,6), 16);
            } else if (col.startsWith('rgb')) {
                const nums = col.match(/\d+/g);
                [r, g, b] = nums.map(n => parseInt(n,10));
            } else {
                return '#000';
            }
            // Relative luminance
            const [R, G, B] = [r, g, b].map(v => {
                v /= 255;
                return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
            });
            const L = 0.2126 * R + 0.7152 * G + 0.0722 * B;
            return L > 0.45 ? '#000' : '#fff';
        } catch (e) {
            return '#000';
        }
    };

    useEffect(() => {
        loadConfigs();
    }, []);

    const resetForm = useCallback(() => {
        setFormData({
            name: t('chatbotConfig.defaults.name'),
            system_prompt: t('chatbotConfig.defaults.systemPrompt'),
            primary_color: '#3B82F6',
            secondary_color: '#1E40AF',
            icon_url: '',
            position: 'bottom-right',
            position_style: { bottom: '20px', right: '20px' },
            button_size: 60,
            transparent_background: false,
            welcome_message: t('chatbotConfig.defaults.welcome'),
            suggested_questions: '',
            window_title: t('chatbotConfig.defaults.windowTitle'),
            temperature: 0.7,
            // New design options
            widget_width: 400,
            widget_height: 600,
            background_color: '#FFFFFF',
            border_radius: 16,
            message_bubble_radius: 12,
            user_message_color: '#3B82F6',
            bot_message_color: '#F3F4F6',
            font_size: 14,
            rescrape_interval_hours: null,
            // Security & Model
            website_url: '',
            allowed_domains: '', // Stored as comma-separated string in UI, converted to array on save if needed
            ignored_urls: '',
            sitemap_url: '',
            ai_model: 'gemini-2.5-flash-lite',
            documents: [],
            theme: 'light',
            collect_feedback: true,
            custom_css: '',
            entry_animation: 'slide',
            bot_avatar_url: '',
            require_email: false,
            in_chat_email_capture: false,
            initial_questions: [],
            supplemental_info: '',
        });
        setConfigTab('general');
        setSelectedConfig(null);
        setShowForm(false);
    }, [t]);

    // Close or reset form on Escape for better keyboard UX
    useEffect(() => {
        const onKey = (e) => {
            if (e.key === 'Escape' && showForm) {
                resetForm();
            }
        };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [showForm, resetForm]);

    // Poll crawl status for chatbots that are currently crawling
    useEffect(() => {
        const crawlingIds = configs
            .filter(c => crawlStatuses[c._id]?.crawl_status === 'crawling')
            .map(c => c._id);

        if (crawlingIds.length === 0) return;

        const interval = setInterval(async () => {
            for (const id of crawlingIds) {
                try {
                    const res = await getCrawlStatus(id);
                    setCrawlStatuses(prev => ({ ...prev, [id]: res.data }));
                    if (res.data.crawl_status !== 'crawling') {
                        loadConfigs(); // Refresh configs when done
                    }
                } catch (err) {
                    console.error('Polling error:', err);
                }
            }
        }, 10000); // Reduced from 3s to 10s to minimize backend load

        return () => clearInterval(interval);
    }, [configs, crawlStatuses]);

    // Handle editId from navigation state (when coming from ChatbotDetails)
    useEffect(() => {
        const editId = location.state?.editId;
        if (editId && configs.length > 0) {
            const configToEdit = configs.find(c => (c._id || c.id) === editId);
            if (configToEdit) {
                // Inline a minimal version of handleEdit here to avoid depending on its identity
                if (configToEdit.permission_level === 'viewer') {
                    notify(t('chatbotConfig.messages.noEditPermission'), 'error');
                } else {
                    setSelectedConfig(configToEdit);
                    setFormData({
                        name: configToEdit.name,
                        system_prompt: configToEdit.system_prompt,
                        primary_color: configToEdit.primary_color,
                        secondary_color: configToEdit.secondary_color,
                        icon_url: configToEdit.icon_url || '',
                        position: configToEdit.position,
                        position_style: configToEdit.position_style,
                        button_size: configToEdit.button_size,
                        transparent_background: configToEdit.transparent_background,
                        welcome_message: configToEdit.welcome_message || t('chatbotConfig.defaults.welcome'),
                        suggested_questions: Array.isArray(configToEdit.suggested_questions)
                            ? configToEdit.suggested_questions.join(', ')
                            : configToEdit.suggested_questions || '',
                        window_title: configToEdit.window_title || t('chatbotConfig.defaults.windowTitle'),
                        temperature: configToEdit.temperature || 0.7,
                        widget_width: configToEdit.widget_width || 400,
                        widget_height: configToEdit.widget_height || 600,
                        background_color: configToEdit.background_color || '#FFFFFF',
                        border_radius: configToEdit.border_radius || 16,
                        message_bubble_radius: configToEdit.message_bubble_radius || 12,
                        user_message_color: configToEdit.user_message_color || '#3B82F6',
                        bot_message_color: configToEdit.bot_message_color || '#F3F4F6',
                        font_size: configToEdit.font_size || 14,
                        rescrape_interval_hours: configToEdit.rescrape_interval_hours || null,
                        website_url: configToEdit.website_url || '',
                        allowed_domains: Array.isArray(configToEdit.allowed_domains)
                            ? configToEdit.allowed_domains.join(', ')
                            : configToEdit.allowed_domains || '',
                        ignored_urls: Array.isArray(configToEdit.ignored_urls)
                            ? configToEdit.ignored_urls.join(', ')
                            : configToEdit.ignored_urls || '',
                        sitemap_url: configToEdit.sitemap_url || '',
                        ai_model: configToEdit.ai_model || 'gemini-2.5-flash-lite',
                        documents: configToEdit.documents || [],
                        theme: configToEdit.theme || 'light',
                        collect_feedback: configToEdit.collect_feedback !== undefined ? configToEdit.collect_feedback : true,
                        custom_css: configToEdit.custom_css || '',
                        entry_animation: configToEdit.entry_animation || 'slide',
                        bot_avatar_url: configToEdit.bot_avatar_url || '',
                        require_email: configToEdit.require_email || false,
                        in_chat_email_capture: configToEdit.in_chat_email_capture || false,
                        initial_questions: configToEdit.initial_questions || [],
                        supplemental_info: configToEdit.supplemental_info || '',
                    });
                    setShowForm(true);
                }

                // Clear the navigation state to prevent re-triggering on subsequent renders
                navigate(location.pathname, { replace: true, state: {} });
            }
        }
    }, [location.state?.editId, configs, navigate, location.pathname, t]);

    const loadConfigs = async () => {
        try {
            const response = await getChatbotConfigs();
            const normalizedData = response.data.map(c => ({
                ...c,
                _id: c._id || c.id
            }));
            setConfigs(normalizedData);

            // Load initial crawl statuses
            const statuses = {};
            for (const config of normalizedData) {
                statuses[config._id] = {
                    crawl_status: config.crawl_status || 'idle',
                    crawl_pages_done: config.crawl_pages_done || 0,
                    crawl_pages_total: config.crawl_pages_total || 0,
                    crawl_error: config.crawl_error,
                    last_scraped_at: config.last_scraped_at,
                };
            }
            setCrawlStatuses(statuses);
        } catch (error) {
            console.error('Error loading configs:', error);
        }
    };

    const handleSubmit = async (e) => {
        e.preventDefault();
        
        // Check if user has edit permission when updating
        if (selectedConfig && selectedConfig.permission_level === 'viewer') {
            notify(t('chatbotConfig.messages.noEditPermission'), 'error');
            return;
        }
        
        setLoading(true);
        setMessage('');

        // Prepare payload: convert string inputs to arrays
        const payload = {
            ...formData,
            suggested_questions: typeof formData.suggested_questions === 'string'
                ? formData.suggested_questions.split(',').map(q => q.trim()).filter(q => q)
                : formData.suggested_questions,
            allowed_domains: typeof formData.allowed_domains === 'string'
                ? formData.allowed_domains.split(',').map(d => d.trim()).filter(d => d)
                : formData.allowed_domains,
            ignored_urls: typeof formData.ignored_urls === 'string'
                ? formData.ignored_urls.split(',').map(d => d.trim()).filter(d => d)
                : formData.ignored_urls
        };
        // `documents` is managed via the upload endpoints, not the chatbot config —
        // the backend ignores it, so don't send a stale copy on create/update.
        delete payload.documents;

        try {
            if (selectedConfig) {
                const configId = selectedConfig._id || selectedConfig.id;
                await updateChatbotConfig(configId, payload);
                notify(t('chatbotConfig.messages.updated'));
            } else {
                const response = await createChatbotConfig(payload);
                const newBot = {
                    ...response.data,
                    _id: response.data._id || response.data.id
                };

                // Automatically start scraping if website_url is provided
                    if (payload.website_url) {
                    try {
                        const newBotId = newBot._id || newBot.id;
                        await crawlWebsite(newBotId, payload.website_url);
                        notify(t('chatbotConfig.messages.createdAndCrawling', { url: payload.website_url }));
                    } catch (crawlErr) {
                        console.error('Initial crawl failed:', crawlErr);
                        notify(t('chatbotConfig.messages.createdCrawlFailed'), 'error');
                    }
                } else {
                    notify(t('chatbotConfig.messages.created'));
                }
            }
            loadConfigs();
            resetForm();
            setShowForm(false); // Hide form after success
        } catch (error) {
            notify(t('chatbotConfig.messages.saveError'), 'error');
        } finally {
            setLoading(false);
        }
    };

    const handleEdit = (config) => {
        // Check if user has edit permission
        if (config.permission_level === 'viewer') {
            notify(t('chatbotConfig.messages.noEditPermission'), 'error');
            return;
        }
        setSelectedConfig(config);
        setFormData({
            name: config.name,
            system_prompt: config.system_prompt,
            primary_color: config.primary_color,
            secondary_color: config.secondary_color,
            icon_url: config.icon_url || '',
            position: config.position,
            position_style: config.position_style,
            button_size: config.button_size,
            transparent_background: config.transparent_background,
            welcome_message: config.welcome_message || t('chatbotConfig.defaults.welcome'),
            suggested_questions: Array.isArray(config.suggested_questions)
                ? config.suggested_questions.join(', ')
                : config.suggested_questions || '',
            window_title: config.window_title || t('chatbotConfig.defaults.windowTitle'),
            temperature: config.temperature || 0.7,
            // New design options with defaults
            widget_width: config.widget_width || 400,
            widget_height: config.widget_height || 600,
            background_color: config.background_color || '#FFFFFF',
            border_radius: config.border_radius || 16,
            message_bubble_radius: config.message_bubble_radius || 12,
            user_message_color: config.user_message_color || '#3B82F6',
            bot_message_color: config.bot_message_color || '#F3F4F6',
            font_size: config.font_size || 14,
            rescrape_interval_hours: config.rescrape_interval_hours || null,
            // Security & Model
            website_url: config.website_url || '',
            allowed_domains: Array.isArray(config.allowed_domains)
                ? config.allowed_domains.join(', ')
                : config.allowed_domains || '',
            ignored_urls: Array.isArray(config.ignored_urls)
                ? config.ignored_urls.join(', ')
                : config.ignored_urls || '',
            sitemap_url: config.sitemap_url || '',
            ai_model: config.ai_model || 'gemini-2.5-flash-lite',
            documents: config.documents || [],
            theme: config.theme || 'light',
            collect_feedback: config.collect_feedback !== undefined ? config.collect_feedback : true,
            custom_css: config.custom_css || '',
            entry_animation: config.entry_animation || 'slide',
            bot_avatar_url: config.bot_avatar_url || '',
            require_email: config.require_email || false,
            in_chat_email_capture: config.in_chat_email_capture || false,
            initial_questions: config.initial_questions || [],
            supplemental_info: config.supplemental_info || '',
        });
        setShowForm(true); // Show form when editing
    };

    const handleDeleteClick = (id) => {
        setConfigToDelete(id);
        setIsDeleteModalOpen(true);
    };

    const handleConfirmDelete = async () => {
        if (!configToDelete) return;

        try {
            await deleteChatbotConfig(configToDelete);
            loadConfigs();
            notify(t('chatbotConfig.messages.deleted'));
        } catch (error) {
            notify(t('chatbotConfig.messages.deleteError'), 'error');
        }
    };

    const handleCrawlClick = (config) => {
        setConfigToCrawl(config._id);
        setCrawlInitialUrl(config.website_url || '');
        setIsInputModalOpen(true);
    };

    const handleConfirmCrawl = async (url) => {
        if (!configToCrawl || !url) return;

        try {
            notify(t('chatbotConfig.messages.startingCrawler'));
            await crawlWebsite(configToCrawl, url);
            setCrawlStatuses(prev => ({
                ...prev,
                [configToCrawl]: { crawl_status: 'crawling', crawl_pages_done: 0, crawl_pages_total: 0 }
            }));
            notify(t('chatbotConfig.messages.crawlingStarted', { url }));
        } catch (error) {
            console.error('Crawl error:', error);
            notify(t('chatbotConfig.messages.crawlError'), 'error');
        }
    };

    

    const cancelEdit = () => {
        resetForm(); // Ensure clear state
        setShowForm(false); // Hide form when cancelling
    };

    // --- Onboarding Flow Helpers ---
    const addOnboardingStep = () => {
        const newStep = {
            id: 'step_' + Date.now(),
            message: '',
            options: []
        };
        setFormData({ ...formData, initial_questions: [...formData.initial_questions, newStep] });
    };

    const removeOnboardingStep = (qIndex) => {
        const updated = [...formData.initial_questions];
        updated.splice(qIndex, 1);
        setFormData({ ...formData, initial_questions: updated });
    };

    const updateOnboardingStep = (qIndex, field, value) => {
        const updated = [...formData.initial_questions];
        updated[qIndex][field] = value;
        setFormData({ ...formData, initial_questions: updated });
    };

    const addOnboardingOption = (qIndex) => {
        const updated = [...formData.initial_questions];
        updated[qIndex].options.push({ id: 'opt_' + Date.now(), label: '', action: 'start_ai' });
        setFormData({ ...formData, initial_questions: updated });
    };

    const updateOnboardingOption = (qIndex, optIndex, field, value) => {
        const updated = [...formData.initial_questions];
        updated[qIndex].options[optIndex][field] = value;
        setFormData({ ...formData, initial_questions: updated });
    };

    const removeOnboardingOption = (qIndex, optIndex) => {
        const updated = [...formData.initial_questions];
        updated[qIndex].options.splice(optIndex, 1);
        setFormData({ ...formData, initial_questions: updated });
    };
    // -------------------------------

    const handleCreateNew = () => {
        resetForm(); // Ensure clear state
        setShowForm(true);
    };

    // --- Widget Build Helpers ---
    const triggerDownload = (blob, filename) => {
        const url = window.URL.createObjectURL(new Blob([blob], { type: 'application/javascript' }));
        const link = document.createElement('a');
        link.href = url;
        link.setAttribute('download', filename);
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.URL.revokeObjectURL(url);
    };

    const handleBuildWidget = async () => {
        setBuildLoading('building');
            setMessage('');
            try {
                const res = await buildWidgetBundle();
                triggerDownload(res.data, 'widget.bundle.js');
                notify(t('chatbotConfig.messages.widgetBuilt'));
                setTimeout(() => setMessage(''), 5000);
            } catch (err) {
                const detail = err.response?.data ? await err.response.data.text?.() : null;
                const detailText = detail ? ` ${detail}` : '';
                notify(t('chatbotConfig.messages.widgetBuildFailed') + detailText, 'error');
            } finally {
                setBuildLoading(false);
            }
    };

    const handleDownloadWidget = async () => {
        setBuildLoading('downloading');
            setMessage('');
        try {
            const res = await downloadWidgetBundle();
            triggerDownload(res.data, 'widget.bundle.js');
            notify(t('chatbotConfig.messages.widgetDownloaded'));
            setTimeout(() => setMessage(''), 4000);
        } catch (err) {
            notify(t('chatbotConfig.messages.noBuildFound'), 'error');
        } finally {
            setBuildLoading(false);
        }
    };
    // ----------------------------
    const openTestPanel = (config) => {
        setTestPanel({
            open: true,
            config,
            messages: [{ role: 'bot', content: config.welcome_message || t('chatbotConfig.defaults.welcome') }],
            input: '',
            loading: false,
            conversationId: null,
        });
    };

    const closeTestPanel = () => {
        setTestPanel(prev => ({ ...prev, open: false, messages: [], conversationId: null }));
    };

    const handleTestSend = async () => {
        const msg = testPanel.input.trim();
        if (!msg || testPanel.loading) return;

        setTestPanel(prev => ({
            ...prev,
            input: '',
            loading: true,
            messages: [...prev.messages, { role: 'user', content: msg }],
        }));

        try {
            const res = await sendChatMessage(
                testPanel.config._id,
                msg,
                testPanel.conversationId,
                testPanel.config.api_key
            );
            setTestPanel(prev => ({
                ...prev,
                loading: false,
                conversationId: res.data.conversation_id,
                messages: [...prev.messages, { role: 'bot', content: res.data.message }],
            }));
        } catch (err) {
            const statusCode = err?.response?.status;
            let errMsg;
            if (statusCode === 503 || err?.response?.data?.detail?.includes('temporarily unavailable')) {
                errMsg = t('chatbotConfig.testPanel.aiUnavailable');
            } else if (statusCode === 429) {
                errMsg = t('chatbotConfig.testPanel.tooManyRequests');
            } else if (statusCode === 401) {
                errMsg = t('chatbotConfig.testPanel.invalidApiKey');
            } else if (!statusCode) {
                errMsg = t('chatbotConfig.testPanel.cannotReachBackend');
            } else {
                const detail = getErrorMessage(err, t('chatbotConfig.testPanel.genericDetailFallback'));
                errMsg = t('chatbotConfig.testPanel.genericError', { status: statusCode, detail });
            }
            setTestPanel(prev => ({
                ...prev,
                loading: false,
                messages: [...prev.messages, { role: 'bot', content: errMsg }],
            }));
        }
    };

    // Auto-scroll test chat to bottom when messages change
    React.useEffect(() => {
        testChatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [testPanel.messages, testPanel.loading]);
    // --------------------------

    // compute preview input styles based on background/theme contrast
    const isPreviewBgDark = getContrastColor(formData.background_color) === '#fff';
    const previewInputBg = isPreviewBgDark ? 'rgba(255,255,255,0.12)' : '#f8f9fa';
    const previewInputColor = isPreviewBgDark ? '#e6eef8' : '#111827';
    const previewInputBorder = isPreviewBgDark ? '1px solid rgba(255,255,255,0.14)' : '1px solid var(--border-light)';

    // Calculate stats
    const totalChatbots = configs.length;
    const activeChatbots = configs.filter(c => crawlStatuses[c._id]?.crawl_status !== 'error').length;

    // Filter configs based on search and status
    const filteredConfigs = React.useMemo(() => {
        return configs.filter(config => {
            // Search filter: match name, website or id
            const q = searchQuery.trim().toLowerCase();
            const matchesSearch = q === '' ||
                (config.name || '').toLowerCase().includes(q) ||
                (config.scraped_url || '').toLowerCase().includes(q) ||
                ((config._id || config.id || '').toLowerCase().includes(q));

            // Status filter
            // Status filter
            let matchesStatus = true;
            if (statusFilter === 'active') {
                matchesStatus = crawlStatuses[config._id]?.crawl_status !== 'error' && 
                               crawlStatuses[config._id]?.crawl_status !== 'failed';
            } else if (statusFilter === 'inactive') {
                matchesStatus = crawlStatuses[config._id]?.crawl_status === 'error' || 
                               crawlStatuses[config._id]?.crawl_status === 'failed';
            }

            return matchesSearch && matchesStatus;
        });
    }, [configs, searchQuery, statusFilter, crawlStatuses]);

    return (
        <Layout>
            <div className="chatbot-config">
                <header className="page-header">
                    <div className="header-main">
                        <div className="header-left">
                            <h1 className="page-title">{t('chatbotConfig.title')}</h1>
                            <p className="page-subtitle">{t('chatbotConfig.subtitle')}</p>
                        </div>
                        {!showForm && canCreateChatbot && (
                            <button
                                onClick={handleCreateNew}
                                ref={createBtnRef}
                                className="create-chatbot-btn"
                                aria-controls="config-form-section"
                                aria-expanded={showForm}
                            >
                                <span className="btn-plus-icon">+</span> Create Chatbot
                            </button>
                        )}
                    </div>


                    {!showForm && totalChatbots > 0 && (
                        <div className="search-filter-row">
                            <div className="stats-badge stats-badge--lg">
                                {totalChatbots} {totalChatbots === 1 ? 'Chatbot' : 'Chatbots'} • {activeChatbots} Active
                            </div>
                            <select
                                value={statusFilter}
                                onChange={(e) => setStatusFilter(e.target.value)}
                                className="filter-dropdown"
                            >
                                <option value="all">All Status</option>
                                <option value="active">Active</option>
                                <option value="inactive">Inactive</option>
                            </select>
                            <button
                                className={`filter-icon-btn ${(searchQuery || statusFilter !== 'all') ? 'active' : ''}`}
                                title={`${(searchQuery || statusFilter !== 'all') ? 'Clear filters' : 'Filters'}`}
                                onClick={() => {
                                    if (searchQuery || statusFilter !== 'all') {
                                        setSearchQuery('');
                                        setStatusFilter('all');
                                    }
                                }}
                            >
                                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                                    <path d="M3 5h18l-7 8v6l-4-2v-4L3 5z" />
                                </svg>
                                <span className="filter-label">{(searchQuery || statusFilter !== 'all') ? 'Clear' : 'Filtrer'}</span>
                            </button>
                        </div>
                    )}
                </header>



                {message && (
                    <div
                        className={`message ${messageType}`}
                        role="status"
                        aria-live="polite"
                    >
                        {message}
                    </div>
                )}

                <div className={`config-container ${showForm ? 'showing-form' : ''}`}>
                    {showForm ? (
                        <>
                            <div id="config-form-section" className="config-form-section">
                                <h2 id="config-form-heading">
                                    <span className="section-icon"><Pencil size={16} /></span>
                                    {selectedConfig ? t('chatbotConfig.editHeadingEdit') : t('chatbotConfig.editHeadingCreate')}
                                </h2>

                                <div className="form-tabs">
                                    <button
                                        type="button"
                                        className={`form-tab-btn ${configTab === 'general' ? 'active' : ''}`}
                                        onClick={() => setConfigTab('general')}
                                    >
                                        <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" width="20" height="20">
                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                                        </svg>
                                        {t('chatbotConfig.tabs.basic')}
                                    </button>
                                    <button
                                        type="button"
                                        className={`form-tab-btn ${configTab === 'connect' ? 'active' : ''}`}
                                        onClick={() => setConfigTab('connect')}
                                    >
                                        <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" width="20" height="20">
                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
                                        </svg>
                                        {t('chatbotConfig.tabs.knowledge')}
                                    </button>
                                    <button
                                        type="button"
                                        className={`form-tab-btn ${configTab === 'design' ? 'active' : ''}`}
                                        onClick={() => setConfigTab('design')}
                                    >
                                        <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" width="20" height="20">
                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M7 21a4 4 0 01-4-4V5a2 2 0 012-2h4a2 2 0 012 2v12a4 4 0 01-4 4zm0 0h12a2 2 0 002-2v-4a2 2 0 00-2-2h-2.343M11 7.343l1.657-1.657a2 2 0 012.828 0l2.829 2.829a2 2 0 010 2.828l-8.486 8.485M7 17h.01" />
                                        </svg>
                                        {t('chatbotConfig.tabs.design')}
                                    </button>
                                    <button
                                        type="button"
                                        className={`form-tab-btn ${configTab === 'flow' ? 'active' : ''}`}
                                        onClick={() => setConfigTab('flow')}
                                    >
                                        <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" width="20" height="20">
                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 002-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
                                        </svg>
                                        {t('chatbotConfig.tabs.flow')}
                                    </button>
                                    {selectedConfig && (
                                        <button
                                            type="button"
                                            className={`form-tab-btn ${configTab === 'documents' ? 'active' : ''}`}
                                            onClick={() => setConfigTab('documents')}
                                        >
                                            <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" width="20" height="20">
                                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M7 7V5a2 2 0 012-2h6l5 5v11a2 2 0 01-2 2H9a2 2 0 01-2-2V7z" />
                                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M14 3v5h5" />
                                            </svg>
                                            {t('nav.documents')}
                                        </button>
                                    )}
                                    {selectedConfig && (
                                        <button
                                            type="button"
                                            className={`form-tab-btn ${configTab === 'embed' ? 'active' : ''}`}
                                            onClick={() => setConfigTab('embed')}
                                        >
                                            <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" width="20" height="20">
                                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4" />
                                            </svg>
                                            {t('chatbotConfig.tabs.embed')}
                                        </button>
                                    )}
                                </div>

                                <form onSubmit={handleSubmit} aria-labelledby="config-form-heading">
                                    {configTab === 'general' && (
                                        <div className="form-section-card">
                                            <div className="form-section-header">
                                                <span><FileEdit size={16} /></span> {t('chatbotConfig.sections.generalSettings')}
                                            </div>
                                            <div className="form-group">
                                                <label>{t('chatbotConfig.labels.chatbotName')}</label>
                                                <input
                                                    type="text"
                                                    value={formData.name}
                                                    onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                                                    required
                                                    placeholder={t('chatbotConfig.placeholders.nameExample')}
                                                />
                                            </div>

                                            <div className="form-group">
                                                <label>{t('chatbotConfig.labels.systemPrompt')}</label>
                                                <p id="system-prompt-hint" className="field-hint">{t('chatbotConfig.hints.systemPrompt')}</p>
                                                <textarea
                                                    value={formData.system_prompt}
                                                    onChange={(e) => setFormData({ ...formData, system_prompt: e.target.value })}
                                                    rows="4"
                                                    required
                                                    placeholder={t('chatbotConfig.placeholders.systemPrompt')}
                                                    aria-describedby="system-prompt-hint"
                                                />
                                            </div>

                                            <div className="form-group">
                                                <label>{t('chatbotConfig.labels.chatbotIcon')}</label>
                                                <div className="icon-upload-wrapper">
                                                    <div className="icon-preview">
                                                        {formData.icon_url ? (
                                                            <img src={formData.icon_url} alt="Bot Icon" />
                                                        ) : (
                                                            <div className="icon-placeholder"><Bot size={16} /></div>
                                                        )}
                                                    </div>
                                                    <div className="upload-controls">
                                                        <input
                                                            type="file"
                                                            id="icon-upload"
                                                            accept="image/*"
                                                            onChange={async (e) => {
                                                                const file = e.target.files[0];
                                                                if (file) {
                                                                    try {
                                                                        const response = await uploadImage(file);
                                                                        setFormData({ ...formData, icon_url: response.data.url });
                                                                    } catch (error) {
                                                                        console.error('Upload failed:', error);
                                                                        notify(t('chatbotConfig.messages.avatarUploadFailed'), 'error');
                                                                    }
                                                                }
                                                            }}
                                                            style={{ display: 'none' }}
                                                        />
                                                        <label htmlFor="icon-upload" className="upload-btn">
                                                            {t('chatbotConfig.actions.changeIcon')}
                                                        </label>
                                                    </div>
                                                </div>
                                            </div>

                                            <div className="form-group">
                                                <label>{t('chatbotConfig.labels.aiModel')}</label>
                                                <select
                                                    value={formData.ai_model}
                                                    onChange={(e) => setFormData({ ...formData, ai_model: e.target.value })}
                                                    className="model-select"
                                                >
                                                    <option value="openai/gpt-oss-20b:free">{t('chatbotConfig.models.openrouter')}</option>
                                                    <option value="gemini-2.5-flash-lite">{t('chatbotConfig.models.gemini25FlashLite')}</option>
                                                </select>
                                            </div>

                                            <div className="form-group">
                                                <label>{t('chatbotConfig.labels.aiTemperature', { temp: formData.temperature })}</label>
                                                <p className="field-hint">{t('chatbotConfig.hints.temperature')}</p>
                                                <input
                                                    type="range"
                                                    min="0"
                                                    max="1"
                                                    step="0.1"
                                                    value={formData.temperature}
                                                    onChange={(e) => setFormData({ ...formData, temperature: parseFloat(e.target.value) })}
                                                    className="size-slider"
                                                />
                                            </div>
                                        </div>
                                    )}

                                    {configTab === 'connect' && (
                                        <div className="form-section-card">
                                            <div className="form-section-header">
                                                                    <span><Globe size={16} /></span> {t('chatbotConfig.sections.knowledge')}
                                                                </div>
                                            <div className="form-group">
                                                <label>{t('chatbotConfig.labels.websiteUrl')}</label>
                                                <input
                                                    type="url"
                                                    value={formData.website_url}
                                                    onChange={(e) => {
                                                        const url = e.target.value;
                                                        const updates = { website_url: url };
                                                        if (url) {
                                                            try {
                                                                const domain = new URL(url).hostname;
                                                                updates.allowed_domains = domain;
                                                            } catch (e) { }
                                                        }
                                                        setFormData(prev => ({ ...prev, ...updates }));
                                                    }}
                                                    placeholder={t('chatbotConfig.placeholders.websiteUrl')}
                                                />
                                            </div>

                                            <div className="form-group">
                                                <label>{t('chatbotConfig.labels.allowedDomains')}</label>
                                                <input
                                                    type="text"
                                                    value={formData.allowed_domains}
                                                    onChange={(e) => setFormData({ ...formData, allowed_domains: e.target.value })}
                                                    placeholder={t('chatbotConfig.placeholders.allowedDomains')}
                                                />
                                                <p className="field-hint">{t('chatbotConfig.hints.allowedDomains')}</p>
                                            </div>

                                            <div className="form-group">
                                                <label>{t('chatbotConfig.labels.ignoredUrls')}</label>
                                                <input
                                                    type="text"
                                                    value={formData.ignored_urls}
                                                    onChange={(e) => setFormData({ ...formData, ignored_urls: e.target.value })}
                                                    placeholder={t('chatbotConfig.placeholders.ignoredUrls')}
                                                />
                                                <p className="field-hint">{t('chatbotConfig.hints.ignoredUrls')}</p>
                                            </div>

                                            <div className="form-group">
                                                <label>{t('chatbotConfig.labels.sitemapUrl')}</label>
                                                <input
                                                    type="url"
                                                    value={formData.sitemap_url}
                                                    onChange={(e) => setFormData({ ...formData, sitemap_url: e.target.value })}
                                                    placeholder={t('chatbotConfig.placeholders.sitemapUrl')}
                                                />
                                                <p className="field-hint">{t('chatbotConfig.hints.sitemapUrl')}</p>
                                            </div>

                                            <div className="form-group">
                                                <label>{t('chatbotConfig.labels.rescrapeInterval')}</label>
                                                <input
                                                    type="number"
                                                    value={formData.rescrape_interval_hours || ''}
                                                    onChange={(e) => setFormData({ ...formData, rescrape_interval_hours: parseInt(e.target.value) || null })}
                                                    placeholder={t('chatbotConfig.placeholders.rescrapeInterval')}
                                                />
                                                <p className="field-hint">{t('chatbotConfig.hints.rescrapeInterval')}</p>
                                            </div>

                                            {/* Supplemental Info */}
                                            <div className="form-group supplemental-info-group">
                                                <label>
                                                    <FileEdit size={16} /> {t('chatbotConfig.labels.supplementalInfo', 'Supplemental Information')}
                                                </label>
                                                <p className="field-hint">
                                                    {t('chatbotConfig.hints.supplementalInfo', 'Add extra knowledge here. When the chatbot cannot find the answer in the scraped website or documents, it will use this text as a fallback source.')}
                                                </p>
                                                <div className="supplemental-textarea-wrapper">
                                                    <textarea
                                                        value={formData.supplemental_info}
                                                        onChange={(e) => setFormData({ ...formData, supplemental_info: e.target.value })}
                                                        placeholder={t('chatbotConfig.placeholders.supplementalInfo', 'Example:\n- Our office hours are Monday to Friday, 9am–6pm.\n- Return policy: 30 days no questions asked.\n- Contact: support@example.com or +1-800-000-0000')}
                                                        rows={8}
                                                        className="supplemental-textarea"
                                                        maxLength={10000}
                                                    />
                                                    <div className="supplemental-char-count">
                                                        <span className={formData.supplemental_info.length > 9000 ? 'char-warn' : ''}>
                                                            {formData.supplemental_info.length} / 10,000
                                                        </span>
                                                    </div>
                                                </div>
                                            </div>

                                            <div className="form-group" style={{ marginTop: '1.5rem' }}>
                                                <label>{t('chatbotConfig.labels.knowledgeDocuments')}</label>
                                                <div className="file-upload-zone">
                                                    <input
                                                        type="file"
                                                        id="doc-upload"
                                                        multiple
                                                        accept=".pdf,.txt,.docx"
                                                        onChange={async (e) => {
                                                            const files = Array.from(e.target.files);
                                                            if (files.length > 0 && selectedConfig) {
                                                                setLoading(true);
                                                                try {
                                                                    for (const file of files) {
                                                                        const res = await uploadDocument(selectedConfig._id, file);
                                                                        const newDoc = {
                                                                            name: file.name,
                                                                            id: res.data.document_id,
                                                                            chunks: res.data.chunks_count
                                                                        };
                                                                        setFormData(prev => ({
                                                                            ...prev,
                                                                            documents: [...prev.documents, newDoc]
                                                                        }));
                                                                    }
                                                                    notify(t('chatbotConfig.messages.documentsUploaded'));
                                                                    setTimeout(() => setMessage(''), 3000);
                                                                } catch (error) {
                                                                    console.error('Doc upload failed:', error);
                                                                } finally {
                                                                    setLoading(false);
                                                                }
                                                            }
                                                        }}
                                                        style={{ display: 'none' }}
                                                    />
                                                        <label htmlFor="doc-upload" className="dropzone-btn">
                                                        <span><Folder size={16} /></span> {loading ? t('common.loading') : t('chatbotConfig.actions.clickToUpload')}
                                                    </label>
                                                </div>
                                            </div>
                                        </div>
                                    )}

                                    {configTab === 'design' && (
                                        <>
                                            <div className="form-section-card">
                                                <div className="form-section-header">
                                                    <span><Palette size={16} /></span> {t('chatbotConfig.sections.appearance')}
                                                </div>

                                                <div className="form-group">
                                                    <label>{t('chatbotConfig.labels.theme')}</label>
                                                    <div className="theme-selector-grid">
                                                        <button
                                                            type="button"
                                                            className={`theme-opt ${formData.theme === 'light' ? 'active' : ''}`}
                                                            onClick={() => setFormData({ ...formData, theme: 'light', background_color: '#FFFFFF', bot_message_color: '#F3F4F6' })}
                                                        >
                                                            <div className="theme-preview light"></div>
                                                            <span>{t('chatbotConfig.labels.themeLight')}</span>
                                                        </button>
                                                        <button
                                                            type="button"
                                                            className={`theme-opt ${formData.theme === 'dark' ? 'active' : ''}`}
                                                            onClick={() => setFormData({ ...formData, theme: 'dark', background_color: '#1F2937', bot_message_color: '#374151' })}
                                                        >
                                                            <div className="theme-preview dark"></div>
                                                            <span>{t('chatbotConfig.labels.themeDark')}</span>
                                                        </button>
                                                        <button
                                                            type="button"
                                                            className={`theme-opt ${formData.theme === 'glass' ? 'active' : ''}`}
                                                            onClick={() => setFormData({ ...formData, theme: 'glass', background_color: 'rgba(255, 255, 255, 0.7)', bot_message_color: 'rgba(243, 244, 246, 0.5)' })}
                                                        >
                                                            <div className="theme-preview glass"></div>
                                                            <span>{t('chatbotConfig.labels.themeGlass')}</span>
                                                        </button>
                                                    </div>
                                                </div>

                                                <div className="form-grid">
                                                    <div className="form-group">
                                                        <label>{t('chatbotConfig.labels.primaryColor')}</label>
                                                        <div className="color-input-wrapper">
                                                            <input
                                                                type="color"
                                                                value={formData.primary_color}
                                                                onChange={(e) => setFormData({ ...formData, primary_color: e.target.value })}
                                                            />
                                                            <input
                                                                type="text"
                                                                className="color-text-input"
                                                                value={formData.primary_color}
                                                                onChange={(e) => setFormData({ ...formData, primary_color: e.target.value })}
                                                                onBlur={(e) => {
                                                                    let v = e.target.value.trim();
                                                                    if (v && !v.startsWith('#') && /^([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(v)) v = '#' + v;
                                                                    setFormData(prev => ({ ...prev, primary_color: v }));
                                                                }}
                                                            />
                                                        </div>
                                                    </div>
                                                    <div className="form-group">
                                                        <label>{t('chatbotConfig.labels.secondaryColor')}</label>
                                                        <div className="color-input-wrapper">
                                                            <input
                                                                type="color"
                                                                value={formData.secondary_color}
                                                                onChange={(e) => setFormData({ ...formData, secondary_color: e.target.value })}
                                                            />
                                                            <input
                                                                type="text"
                                                                className="color-text-input"
                                                                value={formData.secondary_color}
                                                                onChange={(e) => setFormData({ ...formData, secondary_color: e.target.value })}
                                                                onBlur={(e) => {
                                                                    let v = e.target.value.trim();
                                                                    if (v && !v.startsWith('#') && /^([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(v)) v = '#' + v;
                                                                    setFormData(prev => ({ ...prev, secondary_color: v }));
                                                                }}
                                                            />
                                                        </div>
                                                    </div>
                                                </div>

                                                <div className="form-grid">
                                                    <div className="form-group">
                                                        <label>{t('chatbotConfig.designSections.userMessageColor')}</label>
                                                        <div className="color-input-wrapper">
                                                            <input
                                                                type="color"
                                                                value={formData.user_message_color}
                                                                onChange={(e) => setFormData({ ...formData, user_message_color: e.target.value })}
                                                            />
                                                            <input
                                                                type="text"
                                                                className="color-text-input"
                                                                value={formData.user_message_color}
                                                                onChange={(e) => setFormData({ ...formData, user_message_color: e.target.value })}
                                                                onBlur={(e) => {
                                                                    let v = e.target.value.trim();
                                                                    if (v && !v.startsWith('#') && /^([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(v)) v = '#' + v;
                                                                    setFormData(prev => ({ ...prev, user_message_color: v }));
                                                                }}
                                                            />
                                                        </div>
                                                    </div>
                                                    <div className="form-group">
                                                        <label>{t('chatbotConfig.designSections.botMessageColor')}</label>
                                                        <div className="color-input-wrapper">
                                                            <input
                                                                type="color"
                                                                value={formData.bot_message_color}
                                                                onChange={(e) => setFormData({ ...formData, bot_message_color: e.target.value })}
                                                            />
                                                            <input
                                                                type="text"
                                                                className="color-text-input"
                                                                value={formData.bot_message_color}
                                                                onChange={(e) => setFormData({ ...formData, bot_message_color: e.target.value })}
                                                                onBlur={(e) => {
                                                                    let v = e.target.value.trim();
                                                                    if (v && !v.startsWith('#') && /^([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(v)) v = '#' + v;
                                                                    setFormData(prev => ({ ...prev, bot_message_color: v }));
                                                                }}
                                                            />
                                                        </div>
                                                    </div>
                                                </div>

                                                <div className="form-group checkbox-group">
                                                    <label className="checkbox-label">
                                                        <input
                                                            type="checkbox"
                                                            checked={formData.collect_feedback}
                                                            onChange={(e) => setFormData({ ...formData, collect_feedback: e.target.checked })}
                                                        />
                                                        <span>{t('chatbotConfig.labels.enableFeedback')}</span>
                                                    </label>
                                                </div>
                                            </div>
                                            <div className="form-section-card">
                                                <div className="form-section-header">
                                                    <span><Settings size={16} /></span> {t('chatbotConfig.designSections.behavior')}
                                                </div>
                                                <div className="form-group">
                                                    <label>{t('chatbotConfig.labels.windowTitle')}</label>
                                                    <input
                                                        type="text"
                                                        value={formData.window_title}
                                                        onChange={(e) => setFormData({ ...formData, window_title: e.target.value })}
                                                    />
                                                </div>
                                                <div className="form-group">
                                                    <label>{t('chatbotConfig.labels.welcomeMessage')}</label>
                                                    <input
                                                        type="text"
                                                        value={formData.welcome_message}
                                                        onChange={(e) => setFormData({ ...formData, welcome_message: e.target.value })}
                                                    />
                                                </div>
                                                <div className="form-group">
                                                    <label>{t('chatbotConfig.labels.suggestedQuestions')}</label>
                                                    <input
                                                        type="text"
                                                        value={formData.suggested_questions}
                                                        onChange={(e) => setFormData({ ...formData, suggested_questions: e.target.value })}
                                                        placeholder={t('chatbotConfig.placeholders.suggestedQuestionsExample')}
                                                    />
                                                    <p className="field-hint">{t('chatbotConfig.hints.suggestedQuestions')}</p>
                                                </div>

                                                <div className="form-group checkbox-group" style={{ marginTop: '2rem' }}>
                                                    <label style={{ display: 'block', marginBottom: '1rem', color: 'var(--text-primary)', fontWeight: '700', fontSize: '1rem' }}>
                                                        <span style={{ marginRight: '8px' }}><Magnet size={16} /></span> {t('chatbotConfig.designSections.leadGeneration')}
                                                    </label>
                                                    <label className="checkbox-label" style={{ marginBottom: '1rem' }}>
                                                        <input
                                                            type="checkbox"
                                                            checked={formData.require_email}
                                                            onChange={(e) => setFormData({ ...formData, require_email: e.target.checked })}
                                                        />
                                                        <div>
                                                            <span style={{ display: 'block' }}>Require Email Before Chat (Pre-chat Form)</span>
                                                            <span className="field-hint" style={{ fontWeight: 'normal' }}>Users must enter their name and email before sending the first message.</span>
                                                        </div>
                                                    </label>
                                                    <label className="checkbox-label">
                                                        <input
                                                            type="checkbox"
                                                            checked={formData.in_chat_email_capture}
                                                            onChange={(e) => setFormData({ ...formData, in_chat_email_capture: e.target.checked })}
                                                        />
                                                        <div>
                                                            <span style={{ display: 'block' }}>In-Chat Email Capture Prompt</span>
                                                            <span className="field-hint" style={{ fontWeight: 'normal' }}>The bot will proactively ask for the user's email if they ask complex questions.</span>
                                                        </div>
                                                    </label>
                                                </div>
                                            </div>

                                            <div className="form-section-card">
                                                <div className="form-section-header">
                                                    <span><Ruler size={16} /></span> {t('chatbotConfig.designSections.layoutDimensions')}
                                                </div>
                                                <div className="form-group">
                                                    <label>{t('chatbotConfig.labels.widgetPosition')}</label>
                                                    <PositionPicker
                                                        value={formData.position_style}
                                                        onChange={(pos) => setFormData({ ...formData, position_style: pos })}
                                                    />
                                                </div>
                                                <div className="form-grid">
                                                    <div className="form-group">
                                                        <label>{t('chatbotConfig.labels.widgetWidth')}</label>
                                                        <input
                                                            type="number"
                                                            value={formData.widget_width}
                                                            onChange={(e) => setFormData({ ...formData, widget_width: parseInt(e.target.value) })}
                                                        />
                                                    </div>
                                                    <div className="form-group">
                                                        <label>{t('chatbotConfig.labels.widgetHeight')}</label>
                                                        <input
                                                            type="number"
                                                            value={formData.widget_height}
                                                            onChange={(e) => setFormData({ ...formData, widget_height: parseInt(e.target.value) })}
                                                        />
                                                    </div>
                                                </div>
                                                <div className="form-grid">
                                                    <div className="form-group">
                                                        <label>{t('chatbotConfig.labels.borderRadius')}</label>
                                                        <input
                                                            type="number"
                                                            value={formData.border_radius}
                                                            onChange={(e) => setFormData({ ...formData, border_radius: parseInt(e.target.value) })}
                                                        />
                                                    </div>
                                                    <div className="form-group">
                                                        <label>{t('chatbotConfig.labels.bubbleRadius')}</label>
                                                        <input
                                                            type="number"
                                                            value={formData.message_bubble_radius}
                                                            onChange={(e) => setFormData({ ...formData, message_bubble_radius: parseInt(e.target.value) })}
                                                        />
                                                    </div>
                                                </div>
                                                <div className="form-grid">
                                                    <div className="form-group">
                                                        <label>{t('chatbotConfig.labels.fontSize')}</label>
                                                        <input
                                                            type="number"
                                                            value={formData.font_size}
                                                            onChange={(e) => setFormData({ ...formData, font_size: parseInt(e.target.value) })}
                                                        />
                                                    </div>
                                                    <div className="form-group">
                                                        <label>{t('chatbotConfig.labels.buttonSize')}</label>
                                                        <input
                                                            type="number"
                                                            value={formData.button_size}
                                                            onChange={(e) => setFormData({ ...formData, button_size: parseInt(e.target.value) })}
                                                        />
                                                    </div>
                                                </div>
                                                <div className="form-group checkbox-group">
                                                    <label className="checkbox-label">
                                                        <input
                                                            type="checkbox"
                                                            checked={formData.transparent_background}
                                                            onChange={(e) => setFormData({ ...formData, transparent_background: e.target.checked })}
                                                        />
                                                        <span>{t('chatbotConfig.labels.transparentBackground')}</span>
                                                    </label>
                                                </div>
                                            </div>

                                            <div className="form-section-card">
                                                <div className="form-section-header">
                                                    <span><Sparkles size={16} /></span> {t('chatbotConfig.designSections.advancedCustomization')}
                                                </div>

                                                <div className="form-group">
                                                    <label>{t('chatbotConfig.labels.entryAnimation')}</label>
                                                    <p className="field-hint">{t('chatbotConfig.hints.entryAnimation')}</p>
                                                    <div className="animation-selector-grid">
                                                        {['slide', 'fade', 'bounce', 'none'].map((anim) => (
                                                            <button
                                                                key={anim}
                                                                type="button"
                                                                className={`animation-opt ${formData.entry_animation === anim ? 'active' : ''}`}
                                                                onClick={() => setFormData({ ...formData, entry_animation: anim })}
                                                            >
                                                                <div className={`animation-preview anim-${anim}`}>
                                                                    <div className="anim-demo-box" />
                                                                </div>
                                                                <span>{anim.charAt(0).toUpperCase() + anim.slice(1)}</span>
                                                            </button>
                                                        ))}
                                                    </div>
                                                </div>

                                                <div className="form-group">
                                                    <label>{t('chatbotConfig.labels.botAvatarUrl')}</label>
                                                    <p className="field-hint">{t('chatbotConfig.hints.botAvatarUrl')}</p>
                                                    <div className="avatar-input-row">
                                                        <input
                                                            type="text"
                                                            value={formData.bot_avatar_url}
                                                            onChange={(e) => setFormData({ ...formData, bot_avatar_url: e.target.value })}
                                                            placeholder={t('chatbotConfig.placeholders.avatarUrl')}
                                                        />
                                                        {formData.bot_avatar_url && (
                                                            <img
                                                                src={formData.bot_avatar_url}
                                                                alt="Bot avatar preview"
                                                                className="avatar-preview"
                                                                onError={(e) => { e.target.style.display = 'none'; }}
                                                                onLoad={(e) => { e.target.style.display = 'block'; }}
                                                            />
                                                        )}
                                                    </div>
                                                    <label htmlFor="avatar-upload" className="dropzone-btn" style={{ marginTop: '8px', display: 'inline-flex' }}>
                                                        <span><Camera size={16} /></span> {t('chatbotConfig.actions.orUploadAvatar')}
                                                    </label>
                                                    <input
                                                        id="avatar-upload"
                                                        type="file"
                                                        accept="image/*"
                                                        style={{ display: 'none' }}
                                                        onChange={async (e) => {
                                                            const file = e.target.files?.[0];
                                                            if (!file) return;
                                                            try {
                                                                const res = await uploadImage(file);
                                                                setFormData(prev => ({ ...prev, bot_avatar_url: res.data.url }));
                                                                notify(t('chatbotConfig.messages.avatarUploaded'));
                                                            } catch (err) {
                                                                notify(t('chatbotConfig.messages.avatarUploadFailed'), 'error');
                                                            }
                                                        }}
                                                    />
                                                </div>

                                                <div className="form-group">
                                                    <label>{t('chatbotConfig.labels.customCss')}</label>
                                                    <p className="field-hint">{t('chatbotConfig.hints.customCss')}</p>
                                                    <textarea
                                                        value={formData.custom_css}
                                                        onChange={(e) => setFormData({ ...formData, custom_css: e.target.value })}
                                                        placeholder={`.chat-window {\n  /* your custom styles */\n}\n.message.assistant {\n  /* bot message styles */\n}`}
                                                        rows="8"
                                                        className="custom-css-editor"
                                                    />
                                                </div>
                                            </div>
                                        </>
                                    )}

                                    {configTab === 'documents' && selectedConfig && (
                                        <div className="form-section-card documents-tab-card">
                                            <div className="form-section-header">
                                                <span><FileText size={16} /></span> {t('documents.title')}
                                            </div>
                                            <DocumentsPanel embedded={true} forcedChatbotId={selectedConfig._id} showHeader={false} />
                                        </div>
                                    )}

                                    {configTab === 'flow' && (
                                        <div className="form-section-card">
                                            <div className="form-section-header">
                                                <span><Map size={16} /></span> {t('chatbotConfig.designSections.preChatFlow')}
                                            </div>
                                            <p className="field-hint" style={{ marginBottom: '1.5rem', display: 'block' }}>
                                                {t('chatbotConfig.designSections.preChatFlowHint')}
                                            </p>

                                            {formData.initial_questions.map((q, qIndex) => (
                                                <div key={q.id || qIndex} className="onboarding-step-card" style={{ border: '1px solid var(--border-color)', padding: '20px', borderRadius: '12px', marginBottom: '20px', background: 'var(--bg-secondary)' }}>
                                                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '15px' }}>
                                                        <label style={{ fontWeight: '700', fontSize: '1.1rem', margin: 0, color: 'var(--text-primary)' }}>Step {qIndex + 1}</label>
                                                        <button
                                                            type="button"
                                                            onClick={() => removeOnboardingStep(qIndex)}
                                                            style={{ color: '#ef4444', background: 'none', border: 'none', cursor: 'pointer', fontSize: '0.9rem', fontWeight: 'bold' }}
                                                        >
                                                            Delete Step
                                                        </button>
                                                    </div>

                                                    <div className="form-group" style={{ marginBottom: '15px' }}>
                                                        <label style={{ fontSize: '0.9rem' }}>Bot Message</label>
                                                        <textarea
                                                            value={q.message}
                                                            onChange={(e) => updateOnboardingStep(qIndex, 'message', e.target.value)}
                                                            placeholder={t('chatbotConfig.placeholders.welcomeMessageExample')}
                                                            rows="2"
                                                            style={{ width: '100%', padding: '10px', borderRadius: '8px', border: '1px solid var(--border-color)', outline: 'none' }}
                                                        />
                                                    </div>

                                                    <div className="options-list">
                                                        <label style={{ fontSize: '0.9rem', color: 'var(--text-secondary)', display: 'block', marginBottom: '8px', fontWeight: '600' }}>Response Buttons (Options)</label>
                                                        {q.options.map((opt, optIndex) => (
                                                            <div key={opt.id || optIndex} style={{ display: 'flex', gap: '10px', marginBottom: '10px', alignItems: 'center' }}>
                                                                <input
                                                                    type="text"
                                                                    value={opt.label}
                                                                    onChange={(e) => updateOnboardingOption(qIndex, optIndex, 'label', e.target.value)}
                                                                    placeholder={t('chatbotConfig.placeholders.buttonTextExample')}
                                                                    style={{ flex: 1, padding: '8px 12px', borderRadius: '6px', border: '1px solid var(--border-color)', outline: 'none' }}
                                                                />
                                                                <span style={{ color: 'var(--text-secondary)' }}>→</span>
                                                                <select
                                                                    value={opt.action}
                                                                    onChange={(e) => updateOnboardingOption(qIndex, optIndex, 'action', e.target.value)}
                                                                    style={{ width: '180px', padding: '8px 12px', borderRadius: '6px', border: '1px solid var(--border-color)', outline: 'none', background: 'var(--bg-primary)' }}
                                                                >
                                                                    <option value="start_ai"><Bot size={16} /> Start AI Chat</option>
                                                                    {formData.initial_questions.map((targetQ, tIndex) => (
                                                                        targetQ.id !== q.id && <option key={targetQ.id} value={targetQ.id}>↪ Go to Step {tIndex + 1}</option>
                                                                    ))}
                                                                </select>
                                                                <button
                                                                    type="button"
                                                                    onClick={() => removeOnboardingOption(qIndex, optIndex)}
                                                                    style={{ padding: '8px 12px', background: '#fee2e2', color: '#dc2626', borderRadius: '6px', border: 'none', cursor: 'pointer', fontWeight: 'bold' }}
                                                                >
                                                                    X
                                                                </button>
                                                            </div>
                                                        ))}
                                                        <button
                                                            type="button"
                                                            onClick={() => addOnboardingOption(qIndex)}
                                                            style={{ marginTop: '5px', fontSize: '0.85rem', color: 'var(--primary-color)', background: '#eff6ff', border: 'none', cursor: 'pointer', padding: '6px 12px', borderRadius: '6px', fontWeight: '600' }}
                                                        >
                                                            + Add Option Button
                                                        </button>
                                                    </div>
                                                </div>
                                            ))}

                                            <button
                                                type="button"
                                                onClick={addOnboardingStep}
                                                style={{ width: '100%', padding: '14px', background: 'transparent', color: 'var(--text-primary)', border: '2px dashed var(--border-color)', borderRadius: '12px', cursor: 'pointer', fontWeight: 'bold', fontSize: '1rem', transition: 'all 0.2s' }}
                                                onMouseOver={(e) => Object.assign(e.target.style, { background: 'var(--bg-secondary)', borderColor: 'var(--primary-color)', color: 'var(--primary-color)' })}
                                                onMouseOut={(e) => Object.assign(e.target.style, { background: 'transparent', borderColor: 'var(--border-color)', color: 'var(--text-primary)' })}
                                            >
                                                + Add New Step
                                            </button>
                                        </div>
                                    )}

                                    {configTab === 'embed' && selectedConfig && (
                                        <div className="form-section-card installation-section-active">
                                            <div className="form-section-header">
                                                <span><Rocket size={16} /></span> {t('chatbotConfig.embed.implementation')}
                                            </div>
                                            <div className="installation-guide">
                                                <p>{t('chatbotConfig.embed.pasteScript')}</p>
                                                <div className="code-block-container">
                                                    <pre className="code-block">
                                                        {`<script
  src="${(process.env.REACT_APP_API_URL || '').replace(/\/$/, '')}/widget.js"
  data-chatbot-id="${selectedConfig._id}"
  defer
></script>`}
                                                    </pre>
                                                    <button
                                                        type="button"
                                                        className="copy-btn"
                                                        onClick={async () => {
                                                            const code = `<script src="${(process.env.REACT_APP_API_URL || '').replace(/\/$/, '')}/widget.js" data-chatbot-id="${selectedConfig._id}" defer></script>`;
                                                            const ok = await copyText(code);
                                                            notify(ok
                                                                ? t('chatbotConfig.actions.copied')
                                                                : t('chatbotConfig.actions.copyFailed', 'Copie impossible — sélectionnez et copiez manuellement (Ctrl+C).'));
                                                            setTimeout(() => setMessage(''), 3000);
                                                        }}
                                                    >
                                                        {t('chatbotConfig.actions.copyCode')}
                                                    </button>
                                                </div>
                                            </div>

                                            {/* ---- Widget Bundle Section ---- */}
                                            <div className="widget-build-section">
                                                <div className="widget-build-header">
                                                    <span><Package size={16} /></span>
                                                    <div>
                                                        <strong>{t('chatbotConfig.embed.standaloneBundle')}</strong>
                                                        <p className="field-hint" style={{ margin: 0 }}>
                                                            {t('chatbotConfig.embed.standaloneBundleHint')}
                                                        </p>
                                                    </div>
                                                </div>

                                                <div className="widget-build-actions">
                                                    {/* Build & Download */}
                                                    <button
                                                        type="button"
                                                        className={`widget-build-btn primary ${buildLoading === 'building' ? 'is-loading' : ''}`}
                                                        onClick={handleBuildWidget}
                                                        disabled={!!buildLoading}
                                                        title="Run npm run build:widget on the server and download the result"
                                                    >
                                                        {buildLoading === 'building' ? (
                                                            <>
                                                                    <span className="widget-btn-spinner" />
                                                                    {t('chatbotConfig.actions.building')}
                                                                </>
                                                        ) : (
                                                            <>
                                                                <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" width="18" height="18">
                                                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                                                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                                                                </svg>
                                                                {t('chatbotConfig.actions.buildAndDownload')}
                                                            </>
                                                        )}
                                                    </button>

                                                    {/* Download existing */}
                                                    <button
                                                        type="button"
                                                        className={`widget-build-btn secondary ${buildLoading === 'downloading' ? 'is-loading' : ''}`}
                                                        onClick={handleDownloadWidget}
                                                        disabled={!!buildLoading}
                                                        title={t('chatbotConfig.downloadLastBuildTooltip')}
                                                    >
                                                            {buildLoading === 'downloading' ? (
                                                            <>
                                                                <span className="widget-btn-spinner" />
                                                                {t('chatbotConfig.actions.downloading')}
                                                            </>
                                                        ) : (
                                                            <>
                                                                <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" width="18" height="18">
                                                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                                                                </svg>
                                                                {t('chatbotConfig.actions.downloadLastBuild')}
                                                            </>
                                                        )}
                                                    </button>
                                                </div>

                                                <p className="field-hint" style={{ marginTop: '0.75rem' }}>
                                                    <strong>{t('chatbotConfig.actions.buildAndDownload')}</strong> {t('chatbotConfig.hints.buildAndDownload')}<br />
                                                    <strong>{t('chatbotConfig.actions.downloadLastBuild')}</strong> {t('chatbotConfig.hints.downloadLastBuild')}
                                                </p>
                                            </div>
                                        </div>
                                    )}

                                    <div className="form-actions" style={{ display: 'flex', gap: '1rem', marginTop: '2rem' }}>
                                        <button type="submit" disabled={loading} className="submit-btn" style={{ flex: 1 }}>
                                            {loading ? t('common.loading') : selectedConfig ? t('chatbotConfig.actions.updateBot') : t('chatbotConfig.actions.createBot')}
                                        </button>
                                        {selectedConfig && (
                                            <button
                                                type="button"
                                                onClick={() => openTestPanel(selectedConfig)}
                                                className="test-bot-btn-form"
                                                title={t('chatbotConfig.testLiveTooltip')}
                                            >
                                                <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" width="18" height="18">
                                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
                                                </svg>
                                                {t('chatbotConfig.actions.testBot')}
                                            </button>
                                        )}
                                        <button type="button" onClick={cancelEdit} className="cancel-btn" style={{ flex: 1 }}>
                                            {t('common.cancel')}
                                        </button>
                                    </div>
                                </form>
                            </div>

                            {/* RIGHT SIDE: LIVE PREVIEW */}
                            <div className="preview-section" style={{ position: 'sticky', top: '2rem' }}>
                                <div className="preview-card">
                                    <div className="preview-header">
                                        <span><Eye size={16} /></span> {t('chatbotConfig.preview.title')}
                                    </div>

                                        <div
                                            className="widget-preview-window"
                                            style={{
                                                borderRadius: `${formData.border_radius || 12}px`,
                                                fontSize: `${formData.font_size}px`,
                                                background: formData.theme === 'dark' ? '#0b1220' : 'white',
                                                color: formData.theme === 'dark' ? '#e6eef8' : 'inherit'
                                            }}
                                        >
                                        {/* Widget Top Bar */}
                                        <div className="preview-top-bar" style={{ background: `linear-gradient(90deg, ${formData.primary_color}, ${formData.secondary_color})` }}>
                                            <div className="preview-bot-avatar">
                                                {formData.icon_url ? <img src={formData.icon_url} alt="Bot" /> : <span><Bot size={16} /></span>}
                                            </div>
                                            <div className="preview-title-area">
                                                <span className="name">{formData.window_title}</span>
                                                <span className="status">Online • Typically replies instantly</span>
                                            </div>
                                        </div>

                                        {/* Chat Body */}
                                        <div className="preview-chat-body" style={{ background: formData.background_color }}>
                                            {/* Bot Message */}
                                            <div className="preview-msg bot" style={{
                                                backgroundColor: formData.bot_message_color,
                                                color: getContrastColor(formData.bot_message_color),
                                                borderRadius: `${formData.message_bubble_radius}px`,
                                                borderBottomLeftRadius: '4px'
                                            }}>
                                                {formData.welcome_message}
                                            </div>

                                            {/* Suggested Questions */}
                                            {formData.suggested_questions && (
                                                <div className="preview-suggestions">
                                                    {(typeof formData.suggested_questions === 'string'
                                                        ? formData.suggested_questions.split(',')
                                                        : Array.isArray(formData.suggested_questions) ? formData.suggested_questions : []
                                                    ).map((q, i) => i < 3 && q.trim() && (
                                                        <div key={i} className="suggestion-pill">
                                                            {q.trim()}
                                                        </div>
                                                    ))}
                                                </div>
                                            )}

                                            {/* User Message Mockup */}
                                            <div className="preview-msg user" style={{
                                                backgroundColor: formData.user_message_color,
                                                color: getContrastColor(formData.user_message_color),
                                                borderRadius: `${formData.message_bubble_radius}px`,
                                                borderBottomRightRadius: '4px',
                                                marginTop: 'auto'
                                            }}>
                                                How do I reset my password?
                                            </div>
                                        </div>

                                        {/* Input Area */}
                                        <div className="preview-input-area" style={{ background: formData.theme === 'dark' ? 'rgba(255,255,255,0.02)' : 'transparent', borderTop: previewInputBorder }}>
                                            <div className="preview-input-field" style={{ backgroundColor: previewInputBg, color: previewInputColor, border: previewInputBorder }}></div>
                                            <div className="preview-send-btn" style={{ backgroundColor: formData.primary_color, color: getContrastColor(formData.primary_color) }}></div>
                                        </div>
                                    </div>
                                    <p className="field-hint" style={{ marginTop: '1rem', textAlign: 'center' }}>
                                        {t('chatbotConfig.preview.hint')}
                                    </p>
                                </div>
                            </div>
                        </>
                    ) : (
                        <div className="config-list-section full-width">
                            {configs.length === 0 ? (
                                <div className="empty-state">
                                    <div className="empty-icon"><Bot size={16} /></div>
                                    <h3>{t('chatbotConfig.empty.title')}</h3>
                                    <p>{t('chatbotConfig.empty.subtitle')}</p>
                                    {canCreateChatbot && (
                                        <button onClick={handleCreateNew} className="action-btn-primary">
                                            <span className="btn-icon">+</span> {t('chatbotConfig.createBtn')}
                                        </button>
                                    )}
                                </div>
                            ) : filteredConfigs.length === 0 ? (
                                <div className="empty-state">
                                    <div className="empty-icon"><Search size={16} /></div>
                                    <h3>No chatbots found</h3>
                                    <p>Try adjusting your search or filter criteria</p>
                                    <button onClick={() => { setSearchQuery(''); setStatusFilter('all'); }} className="action-btn-secondary">
                                        Clear Filters
                                    </button>
                                </div>
                            ) : (
                                <div className="config-list">
                                    {filteredConfigs.map((config) => (
                                        <div key={config._id} className="config-item">
                                            <div className="config-card-body">
                                                <div className="config-card-header">
                                                    <div className="config-card-title-group">
                                                        <div className="config-card-icon" aria-hidden="true">
                                                            {config.icon_url ? (
                                                                <img
                                                                    src={config.icon_url}
                                                                    alt=""
                                                                    className="config-card-icon-img"
                                                                />
                                                            ) : (
                                                                <span className="config-card-icon-text">
                                                                    {(config.name || 'C').charAt(0).toUpperCase()}
                                                                </span>
                                                            )}
                                                        </div>
                                                        <div>
                                                            <h3 className="config-card-title">{config.name}</h3>
                                                            <div className="config-card-meta">
                                                                ID: {config._id ? config._id.substring(0, 8) : '—'}
                                                                <span className="meta-separator" />
                                                                Created: {formatShortDate(config.created_at || config.createdAt || config.created)}
                                                            </div>
                                                        </div>
                                                    </div>
                                                    <span className="config-card-status">Active</span>
                                                </div>

                                                <div className="config-card-grid">
                                                    <div className="config-card-section">
                                                        <p className="config-card-label">Theme</p>
                                                        <div className="config-colors-mini">
                                                            <span className="color-dot" style={{ backgroundColor: config.primary_color }} title={t('chatbotConfig.colorPrimary')} />
                                                            <span className="color-dot" style={{ backgroundColor: config.secondary_color }} title={t('chatbotConfig.colorSecondary')} />
                                                            <span className="color-dot neutral" title={t('chatbotConfig.colorNeutral')} />
                                                        </div>
                                                    </div>

                                                    <div className="config-card-section">
                                                        <p className="config-card-label">Crawl Status</p>
                                                        <div className="config-card-progress">
                                                            <div className="config-card-progress-track">
                                                                <div
                                                                    className="config-card-progress-fill"
                                                                    style={{
                                                                        width: `${(() => {
                                                                            const status = crawlStatuses[config._id];
                                                                            const total = status?.crawl_pages_total || 0;
                                                                            const done = status?.crawl_pages_done || 0;
                                                                            if (!status || total === 0) return done > 0 ? '100%' : '0%';
                                                                            return `${Math.min(100, Math.round((done / total) * 100))}%`;
                                                                        })()}`,
                                                                    }}
                                                                />
                                                            </div>
                                                            <span className="config-card-progress-value">
                                                                {(() => {
                                                                    const status = crawlStatuses[config._id];
                                                                    const total = status?.crawl_pages_total || 0;
                                                                    const done = status?.crawl_pages_done || 0;
                                                                    if (!status || total === 0) return done > 0 ? '100%' : '0%';
                                                                    return `${Math.min(100, Math.round((done / total) * 100))}%`;
                                                                })()}
                                                            </span>
                                                        </div>
                                                        <p className="config-card-subtext">
                                                            {(() => {
                                                                const status = crawlStatuses[config._id];
                                                                if (!status || status.crawl_status === 'idle') return 'No crawl yet';
                                                                if (status.crawl_status === 'failed') return 'Crawl failed';
                                                                return `${status.crawl_pages_done || 0} pages done`;
                                                            })()}
                                                        </p>
                                                    </div>

                                                    <div className="config-card-section">
                                                        <p className="config-card-label">Website</p>
                                                        {config.scraped_url ? (
                                                            <a
                                                                href={config.scraped_url}
                                                                target="_blank"
                                                                rel="noopener noreferrer"
                                                                className="config-card-website"
                                                                title={config.scraped_url}
                                                            >
                                                                <span className="config-card-website-text">
                                                                    {config.scraped_url.length > 32
                                                                        ? `${config.scraped_url.substring(0, 32)}...`
                                                                        : config.scraped_url}
                                                                </span>
                                                            </a>
                                                        ) : (
                                                            <div className="config-card-website" title={t('chatbotConfig.notSet')}>
                                                                <span className="config-card-website-text">Not set</span>
                                                            </div>
                                                        )}
                                                    </div>

                                                    <div className="config-card-section">
                                                        <p className="config-card-label">API Key</p>
                                                        <div className="config-card-apikey">
                                                            <span className="config-card-apikey-text">
                                                                {config.api_key ? `••••••••${config.api_key.slice(-4)}` : 'No key'}
                                                            </span>
                                                        </div>
                                                    </div>
                                                </div>
                                            </div>

                                            <div className="config-card-footer">
                                                <div className="config-card-footer-left">
                                                    {(config.permission_level === 'owner' || config.permission_level === 'admin' || config.permission_level === 'editor') && (
                                                        <button onClick={() => handleEdit(config)} className="config-card-icon-btn" title="Edit">
                                                            <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" width="18" height="18">
                                                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                                                            </svg>
                                                        </button>
                                                    )}
                                                    <button onClick={() => navigate(`/chatbot/${config._id}`)} className="config-card-icon-btn" title={t('chatbotConfig.viewDetailsTooltip')}>
                                                        <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" width="18" height="18">
                                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
                                                        </svg>
                                                    </button>
                                                    {config.is_owner && (
                                                        <button onClick={() => handleDeleteClick(config._id)} className="config-card-icon-btn delete" title={t('chatbotConfig.deleteTooltip')}>
                                                            <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" width="18" height="18">
                                                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                                            </svg>
                                                        </button>
                                                    )}
                                                </div>
                                                <div className="config-card-footer-right">
                                                    {(config.permission_level === 'owner' || config.permission_level === 'admin' || config.permission_level === 'editor') && (
                                                        <button onClick={() => handleCrawlClick(config)} className="config-card-cta" title={t('chatbotConfig.scrapeWebsiteTooltip')}>
                                                            <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" width="18" height="18">
                                                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                                                            </svg>
                                                            Scrape
                                                        </button>
                                                    )}
                                                    <button onClick={() => navigate(`/playground?chatbot=${config._id}`)} className="config-card-cta primary" title={t('chatbotConfig.actions.testBot')}>
                                                        <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" width="18" height="18">
                                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
                                                        </svg>
                                                        {t('chatbotConfig.actions.testBot')}
                                                    </button>
                                                </div>
                                            </div>
                                        </div>
                                    ))}
                                    {canCreateChatbot && (
                                        <button type="button" className="config-add-card" onClick={handleCreateNew}>
                                            <div className="config-add-icon">
                                                <span className="config-add-plus" aria-hidden="true">+</span>
                                            </div>
                                            <h4>Add New Instance</h4>
                                            <p>Deploy a new AI chatbot to another website or channel.</p>
                                        </button>
                                    )}
                                </div>
                            )}
                        </div>
                    )}
                </div>
            </div >
            <ConfirmationModal
                isOpen={isDeleteModalOpen}
                onClose={() => setIsDeleteModalOpen(false)}
                onConfirm={handleConfirmDelete}
                title={t('chatbotConfig.confirm.deleteTitle')}
                message={t('chatbotConfig.confirm.deleteMessage')}
                confirmText={t('common.delete')}
                isDanger={true}
            />
            <InputModal
                isOpen={isInputModalOpen}
                onClose={() => setIsInputModalOpen(false)}
                onSubmit={handleConfirmCrawl}
                title={t('chatbotConfig.input.scrapeTitle')}
                message={t('chatbotConfig.input.scrapeMessage')}
                placeholder={t('chatbotConfig.input.scrapePlaceholder')}
                confirmText={t('chatbotConfig.input.startScraping')}
                cancelText={t('common.cancel')}
                defaultValue={crawlInitialUrl}
            />

            {/* ---- Test Chat Panel ---- */}
            {testPanel.open && testPanel.config && (
                <>
                    <div className="test-panel-backdrop" onClick={closeTestPanel} />
                    <div className="test-chat-overlay">
                        {/* Header */}
                        <div
                            className="test-chat-header"
                            style={{ background: `linear-gradient(90deg, ${testPanel.config.primary_color}, ${testPanel.config.secondary_color})` }}
                        >
                            <div className="test-chat-bot-avatar">
                                {testPanel.config.icon_url
                                    ? <img src={testPanel.config.icon_url} alt="bot" />
                                    : <span><Bot size={16} /></span>}
                            </div>
                            <div className="test-chat-title-area">
                                <span className="bot-name">{testPanel.config.window_title || testPanel.config.name}</span>
                                <span className="test-badge"><FlaskConical size={16} /> Test Mode</span>
                            </div>
                            <button className="close-test-btn" onClick={closeTestPanel} title={t('chatbotConfig.closeTestPanelTooltip')}>
                                <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" width="18" height="18">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.5" d="M6 18L18 6M6 6l12 12" />
                                </svg>
                            </button>
                        </div>

                        {/* Messages */}
                        <div className="test-chat-messages">
                            {testPanel.messages.map((msg, i) => (
                                <div
                                    key={i}
                                    className={`test-msg ${msg.role}`}
                                    style={msg.role === 'bot'
                                        ? { backgroundColor: testPanel.config.bot_message_color || '#F3F4F6', color: readableTextColor(testPanel.config.bot_message_color || '#F3F4F6'), borderRadius: `${testPanel.config.message_bubble_radius || 12}px`, borderBottomLeftRadius: '4px' }
                                        : { backgroundColor: testPanel.config.user_message_color || '#3B82F6', color: '#fff', borderRadius: `${testPanel.config.message_bubble_radius || 12}px`, borderBottomRightRadius: '4px' }}
                                >
                                    {msg.role === 'bot' ? (
                                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
                                    ) : (
                                        msg.content
                                    )}
                                </div>
                            ))}
                            {testPanel.loading && (
                                <div
                                    className="test-msg bot typing"
                                    style={{ backgroundColor: testPanel.config.bot_message_color || '#F3F4F6', borderRadius: `${testPanel.config.message_bubble_radius || 12}px`, borderBottomLeftRadius: '4px' }}
                                >
                                    <div className="test-typing-dots">
                                        <span /><span /><span />
                                    </div>
                                </div>
                            )}
                            <div ref={testChatEndRef} />
                        </div>

                        {/* Suggested questions (shown only at the start) */}
                        {testPanel.messages.length === 1 && testPanel.config.suggested_questions?.length > 0 && (
                            <div className="test-suggestions">
                                {(Array.isArray(testPanel.config.suggested_questions)
                                    ? testPanel.config.suggested_questions
                                    : testPanel.config.suggested_questions.split(',').map(s => s.trim())
                                ).filter(Boolean).slice(0, 4).map((q, i) => (
                                    <button
                                        key={i}
                                        className="test-suggestion-pill"
                                        onClick={() => setTestPanel(prev => ({ ...prev, input: q }))}
                                    >
                                        {q}
                                    </button>
                                ))}
                            </div>
                        )}

                        {/* Input */}
                        <div className="test-chat-input-area">
                            <textarea
                                value={testPanel.input}
                                onChange={(e) => setTestPanel(prev => ({ ...prev, input: e.target.value }))}
                                onKeyDown={(e) => {
                                    if (e.key === 'Enter' && !e.shiftKey) {
                                        e.preventDefault();
                                        handleTestSend();
                                    }
                                }}
                                placeholder={t('chatbotConfig.placeholders.testPanelInput')}
                                rows={2}
                                disabled={testPanel.loading}
                            />
                            <button
                                className="test-send-btn"
                                style={{ backgroundColor: testPanel.config.primary_color }}
                                onClick={handleTestSend}
                                disabled={testPanel.loading || !testPanel.input.trim()}
                                title="Send"
                            >
                                <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" width="18" height="18">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
                                </svg>
                            </button>
                        </div>
                    </div>
                </>
            )}
        </Layout >
    );
};

export default ChatbotConfig;
