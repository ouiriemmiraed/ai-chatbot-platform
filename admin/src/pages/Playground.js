import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import Layout from '../components/Layout';
import { getChatbotConfigs } from '../services/api';
import api from '../services/api';
import './Playground.css';

import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { AlertTriangle, Bot, ChevronRight, FlaskConical, Hourglass, Library, Paperclip, RefreshCw, Search } from 'lucide-react';
import { getErrorMessage } from '../utils/errorMessage';
const Playground = () => {
    const { t } = useTranslation();
    const [searchParams] = useSearchParams();

    const [chatbots, setChatbots] = useState([]);
    const [selectedBot, setSelectedBot] = useState(null);
    const [messages, setMessages] = useState([]);
    const [conversationId, setConversationId] = useState(null);
    const [input, setInput] = useState('');
    const [simulatedUrl, setSimulatedUrl] = useState('https://example.com/');
    const [simulatedTitle, setSimulatedTitle] = useState('Example Page');
    const [loading, setLoading] = useState(false);
    const [botsLoading, setBotsLoading] = useState(true);
    const [activeSource, setActiveSource] = useState(null); // message index for sources panel
    const messagesEndRef = useRef(null);
    const inputRef = useRef(null);

    useEffect(() => {
        getChatbotConfigs()
            .then(res => {
                const bots = res.data || [];
                setChatbots(bots);
                const preselectedId = searchParams.get('chatbot');
                const preselected = preselectedId ? bots.find(b => b._id === preselectedId) : null;
                setSelectedBot(preselected || (bots.length > 0 ? bots[0] : null));
            })
            .catch(() => {})
            .finally(() => setBotsLoading(false));
            }, [searchParams]);

    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [messages]);

    const handleBotChange = (e) => {
        const bot = chatbots.find(b => b._id === e.target.value);
        setSelectedBot(bot || null);
        resetSession();
    };

    const resetSession = useCallback(() => {
        setMessages([]);
        setConversationId(null);
        setActiveSource(null);
        setInput('');
    }, []);

    const sendMessage = async () => {
        const text = input.trim();
        if (!text || !selectedBot || loading) return;

        const userMsg = { role: 'user', content: text, id: Date.now() };
        setMessages(prev => [...prev, userMsg]);
        setInput('');
        setLoading(true);

        try {
            const res = await api.post('/chat/', {
                chatbot_id: selectedBot._id,
                message: text,
                conversation_id: conversationId || null,
                page_url: simulatedUrl || window.location.href,
                page_title: simulatedTitle || 'Admin Playground',
            }, {
                headers: selectedBot.api_key ? { 'X-API-Key': selectedBot.api_key } : {},
            });

            const data = res.data;
            setConversationId(data.conversation_id);
            const assistantMsg = {
                role: 'assistant',
                content: data.message,
                sources: data.sources || [],
                id: Date.now() + 1,
            };
            setMessages(prev => {
                const next = [...prev, assistantMsg];
                // Auto-open sources panel for the last assistant message if it has sources
                if (assistantMsg.sources.length > 0) {
                    setActiveSource(next.length - 1);
                }
                return next;
            });
        } catch (err) {
            const detail = getErrorMessage(err, t('playground.errorFallback', 'Error sending message'));
            const errMsg = {
                role: 'error',
                content: detail,
                id: Date.now() + 1,
            };
            setMessages(prev => [...prev, errMsg]);
        } finally {
            setLoading(false);
            inputRef.current?.focus();
        }
    };

    const handleKeyDown = (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    };

    // Active sources from the selected message (if assistant)
    const activeSources = (activeSource !== null && messages[activeSource]?.sources) || [];

    return (
        <Layout>
            <div className="playground-page">
                {/* Header */}
                <div className="playground-header">
                    <div>
                        <h1 className="playground-title">
                            <span><FlaskConical size={16} /></span>
                            {t('playground.title', 'Playground')}
                            <span className="test-badge">{t('playground.testMode', 'TEST MODE')}</span>
                        </h1>
                        <p className="playground-subtitle">
                            {t('playground.subtitle', 'Test your chatbot live without leaving the dashboard')}
                        </p>
                    </div>
                </div>

                {botsLoading ? (
                    <div className="pg-loading">
                        <div className="analytics-spinner" />
                    </div>
                ) : chatbots.length === 0 ? (
                    <div className="pg-empty">
                        <span><Bot size={16} /></span>
                        <p>{t('playground.noBots', 'No chatbots found. Create one first.')}</p>
                    </div>
                ) : (
                    <div className="playground-layout">
                        {/* Left: configuration + chat */}
                        <div className="playground-left">
                            {/* Config bar */}
                            <div className="pg-config-bar">
                                <div className="pg-config-row">
                                    <label className="pg-config-label">
                                        {t('playground.chatbot', 'Chatbot')}
                                    </label>
                                    <select
                                        className="pg-select"
                                        value={selectedBot?._id || ''}
                                        onChange={handleBotChange}
                                    >
                                        {chatbots.map(b => (
                                            <option key={b._id} value={b._id}>{b.name}</option>
                                        ))}
                                    </select>
                                </div>
                                <div className="pg-config-row">
                                    <label className="pg-config-label">
                                        {t('playground.simulatedUrl', 'Simulated URL')}
                                    </label>
                                    <input
                                        className="pg-input"
                                        type="text"
                                        value={simulatedUrl}
                                        onChange={e => setSimulatedUrl(e.target.value)}
                                        placeholder="https://example.com/page"
                                    />
                                </div>
                                <div className="pg-config-row">
                                    <label className="pg-config-label">
                                        {t('playground.simulatedTitle', 'Page Title')}
                                    </label>
                                    <input
                                        className="pg-input"
                                        type="text"
                                        value={simulatedTitle}
                                        onChange={e => setSimulatedTitle(e.target.value)}
                                        placeholder={t('playground.pageTitle')}
                                    />
                                </div>
                                <button
                                    className="pg-reset-btn"
                                    onClick={resetSession}
                                    title={t('playground.resetTooltip', 'Clear conversation and start fresh')}
                                >
                                    <RefreshCw size={16} /> {t('playground.reset', 'Reset')}
                                </button>
                            </div>

                            {/* Messages */}
                            <div className="pg-messages">
                                {messages.length === 0 && (
                                    <div className="pg-welcome">
                                        <div className="pg-welcome-icon"><Bot size={16} /></div>
                                        <p className="pg-welcome-title">{selectedBot?.name}</p>
                                        <p className="pg-welcome-sub">
                                            {t('playground.welcomeHint', 'Type a message to start testing this chatbot.')}
                                        </p>
                                    </div>
                                )}
                                {messages.map((msg, idx) => (
                                    <div key={msg.id} className={`pg-message pg-message--${msg.role}`}>
                                        {msg.role === 'user' && (
                                            <div className="pg-bubble pg-bubble--user">
                                                {msg.content}
                                            </div>
                                        )}
                                        {msg.role === 'assistant' && (
                                            <div className="pg-bubble-wrap">
                                                <div className="pg-bot-avatar"><Bot size={16} /></div>
                                                <div className="pg-bubble pg-bubble--assistant">
                                                    <div className="pg-answer"><ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown></div>
                                                    {msg.sources?.length > 0 && (
                                                        <button
                                                            className={`pg-sources-btn ${activeSource === idx ? 'active' : ''}`}
                                                            onClick={() => setActiveSource(activeSource === idx ? null : idx)}
                                                        >
                                                            <Paperclip size={16} /> {msg.sources.length} {t('playground.sources', 'sources')}
                                                        </button>
                                                    )}
                                                </div>
                                            </div>
                                        )}
                                        {msg.role === 'error' && (
                                            <div className="pg-bubble pg-bubble--error">
                                                <AlertTriangle size={16} /> {msg.content}
                                            </div>
                                        )}
                                    </div>
                                ))}
                                {loading && (
                                    <div className="pg-message pg-message--assistant">
                                        <div className="pg-bubble-wrap">
                                            <div className="pg-bot-avatar"><Bot size={16} /></div>
                                            <div className="pg-bubble pg-bubble--assistant pg-bubble--typing">
                                                <span /><span /><span />
                                            </div>
                                        </div>
                                    </div>
                                )}
                                <div ref={messagesEndRef} />
                            </div>

                            {/* Input */}
                            <div className="pg-input-bar">
                                <textarea
                                    ref={inputRef}
                                    className="pg-textarea"
                                    value={input}
                                    onChange={e => setInput(e.target.value)}
                                    onKeyDown={handleKeyDown}
                                    placeholder={
                                        selectedBot
                                            ? t('playground.inputPlaceholder', 'Message {{name}}…', { name: selectedBot.name })
                                            : t('playground.selectFirst', 'Select a chatbot first')
                                    }
                                    disabled={!selectedBot || loading}
                                    rows={2}
                                />
                                <button
                                    className="pg-send-btn"
                                    onClick={sendMessage}
                                    disabled={!input.trim() || !selectedBot || loading}
                                >
                                    {loading ? <Hourglass size={16} /> : <ChevronRight size={16} />}
                                </button>
                            </div>
                        </div>

                        {/* Right: Sources Panel */}
                        <div className={`playground-right ${activeSources.length > 0 ? 'has-sources' : ''}`}>
                            <div className="sources-panel">
                                <div className="sources-header">
                                    <h3 className="sources-title">
                                        <Library size={16} /> {t('playground.ragSources', 'RAG Sources')}
                                    </h3>
                                    {activeSources.length > 0 && (
                                        <span className="sources-count">
                                            {activeSources.length} {t('playground.chunks', 'chunks')}
                                        </span>
                                    )}
                                </div>
                                {activeSources.length === 0 ? (
                                    <div className="sources-empty">
                                        <span><Search size={16} /></span>
                                        <p>{t('playground.sourcesHint', 'Click "sources" on an assistant message to see the knowledge chunks used.')}</p>
                                    </div>
                                ) : (
                                    <div className="sources-list">
                                        {activeSources.map((src, i) => {
                                            const meta = src.metadata || {};
                                            const sourceUrl = meta.source || '';
                                            const title = meta.title || sourceUrl || t('playground.unknownSource', 'Unknown source');
                                            const content = src.content || '';
                                            return (
                                                <div key={i} className="source-item">
                                                    <div className="source-item-header">
                                                        <span className="source-index">#{i + 1}</span>
                                                        <span className="source-title" title={sourceUrl}>{title}</span>
                                                    </div>
                                                    {sourceUrl && (
                                                        <div className="source-url">
                                                            <a
                                                                href={sourceUrl}
                                                                target="_blank"
                                                                rel="noopener noreferrer"
                                                            >
                                                                {sourceUrl.length > 60 ? sourceUrl.slice(0, 60) + '…' : sourceUrl}
                                                            </a>
                                                        </div>
                                                    )}
                                                    <p className="source-content">
                                                        {content.length > 300 ? content.slice(0, 300) + '…' : content}
                                                    </p>
                                                </div>
                                            );
                                        })}
                                    </div>
                                )}

                                {/* Session info */}
                                {conversationId && (
                                    <div className="session-info">
                                        <span>🆔 {t('playground.sessionId', 'Session')}: </span>
                                        <code>{conversationId.slice(-8)}</code>
                                        <span className="session-msg-count">
                                            {messages.filter(m => m.role !== 'error').length} msg
                                        </span>
                                    </div>
                                )}
                            </div>
                        </div>
                    </div>
                )}
            </div>
        </Layout>
    );
};

export default Playground;
