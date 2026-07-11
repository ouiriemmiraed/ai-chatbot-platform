import React, { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import Layout from '../components/Layout';
import { getConversations, getConversation, deleteConversation, getChatbotConfigs } from '../services/api';
import LeadsPanel from '../components/LeadsPanel';
import './Conversations.css';
import ConfirmationModal from '../components/ConfirmationModal';
import { useToast } from '../contexts/ToastContext';
import { getErrorMessage } from '../utils/errorMessage';

import { Bot, MessageCircle, User, Users } from 'lucide-react';
const PAGE_SIZE = 20;

const Conversations = () => {
    const [conversations, setConversations] = useState([]);
    const [total, setTotal] = useState(0);
    const [page, setPage] = useState(0);
    const [selectedConv, setSelectedConv] = useState(null);
    const [loading, setLoading] = useState(true);
    const [isDeleteModalOpen, setIsDeleteModalOpen] = useState(false);
    const [conversationToDelete, setConversationToDelete] = useState(null);
    const [chatbots, setChatbots] = useState([]);
    const [selectedChatbotId, setSelectedChatbotId] = useState('');
    const [activeTab, setActiveTab] = useState('messages');

    const { t } = useTranslation();
    const { show: showToast } = useToast();

    const loadChatbots = useCallback(async () => {
        try {
            const response = await getChatbotConfigs();
            setChatbots(response.data);
        } catch (error) {
            console.error('Error loading chatbots:', error);
        }
    }, []);

    const loadConversations = useCallback(async () => {
        setLoading(true);
        try {
            const params = { skip: page * PAGE_SIZE, limit: PAGE_SIZE };
            if (selectedChatbotId) params.chatbot_id = selectedChatbotId;
            const response = await getConversations(params);
            setConversations(response.data.conversations);
            setTotal(response.data.total);
        } catch (error) {
            console.error('Error loading conversations:', error);
            showToast(getErrorMessage(error, t('conversations.loadError', 'Failed to load conversations')), { type: 'error' });
        } finally {
            setLoading(false);
        }
    }, [page, selectedChatbotId, showToast, t]);

    useEffect(() => {
        loadChatbots();
    }, [loadChatbots]);

    useEffect(() => {
        loadConversations();
    }, [loadConversations]);

    const handleSelectConversation = async (convId) => {
        try {
            const response = await getConversation(convId);
            setSelectedConv(response.data);
        } catch (error) {
            console.error('Error loading conversation:', error);
        }
    };

    const handleDeleteClick = (convId) => {
        setConversationToDelete(convId);
        setIsDeleteModalOpen(true);
    };

    const handleConfirmDelete = async () => {
        if (!conversationToDelete) return;

        try {
            await deleteConversation(conversationToDelete);
            if (selectedConv && selectedConv._id === conversationToDelete) {
                setSelectedConv(null);
            }
            setConversationToDelete(null);
            await loadConversations();
            showToast(t('conversations.deleteSuccess', 'Conversation deleted'), { type: 'success' });
        } catch (error) {
            console.error('Error deleting conversation:', error);
            showToast(getErrorMessage(error, t('conversations.deleteError', 'Failed to delete conversation')), { type: 'error' });
        }
    };

    // Only take over the whole page on the very first load. Subsequent reloads
    // (pagination, filter change, after delete) keep the UI and open detail
    // panel in place rather than blanking everything to a loading line.
    if (loading && conversations.length === 0) {
        return (
            <Layout>
                <div className="loading">{t('common.loading')}</div>
            </Layout>
        );
    }

    return (
        <Layout>
            <div className="conversations-page">
                <header className="page-header">
                    <h1>{t('conversations.title')}</h1>
                    <p className="subtitle">{t('conversations.subtitle')}</p>
                    <div className="conversation-view-tabs">
                        <button
                            type="button"
                            className={`conversation-view-tab ${activeTab === 'messages' ? 'active' : ''}`}
                            onClick={() => setActiveTab('messages')}
                            aria-pressed={activeTab === 'messages'}
                        >
                            <MessageCircle size={16} /> {t('conversations.inbox')}
                        </button>
                        <button
                            type="button"
                            className={`conversation-view-tab ${activeTab === 'leads' ? 'active' : ''}`}
                            onClick={() => setActiveTab('leads')}
                            aria-pressed={activeTab === 'leads'}
                        >
                            <Users size={16} /> {t('nav.leads')}
                        </button>
                    </div>
                </header>

                {activeTab === 'messages' ? (
                <div className="conversations-container">
                    <div className="conversations-sidebar">
                        <div className="sidebar-title">
                            <h2>{t('conversations.inbox')}</h2>
                            <span className="badge">{total}</span>
                        </div>
                        <div className="sidebar-filter">
                            <select
                                value={selectedChatbotId}
                                onChange={(e) => { setSelectedChatbotId(e.target.value); setPage(0); }}
                                className="chatbot-filter-select"
                            >
                                <option value="">{t('conversations.allChatbots')}</option>
                                {chatbots.map((bot) => (
                                    <option key={bot._id} value={bot._id}>{bot.name}</option>
                                ))}
                            </select>
                        </div>
                        {conversations.length === 0 ? (
                            <div className="empty-state-sidebar">
                                <p>{t('conversations.noConversations')}</p>
                            </div>
                        ) : (
                            <div className="conversations-list">
                                {conversations.map((conv) => (
                                    <div
                                        key={conv._id}
                                        className={`conversation-item ${selectedConv?._id === conv._id ? 'active' : ''}`}
                                        onClick={() => handleSelectConversation(conv._id)}
                                    >
                                        <div className="conversation-avatar">
                                            {conv.page_title ? conv.page_title.charAt(0).toUpperCase() : '?'}
                                        </div>
                                        <div className="conversation-info-wrapper">
                                            <div className="conversation-header-row">
                                                <div className="conversation-title">
                                                    {conv.page_title || t('conversations.untitledPage')}
                                                </div>
                                                <span className="conversation-time">
                                                    {new Date(conv.updated_at).toLocaleDateString()}
                                                </span>
                                            </div>
                                            <div className="conversation-meta-row">
                                                <span className="msg-preview">
                                                    {conv.message_count > 0
                                                        ? `${conv.message_count} ${t('chatbotDetails.conversations.messages')}`
                                                        : t('conversations.noMessagesYet')}
                                                </span>
                                                
                                            </div>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        )}
                        {total > PAGE_SIZE && (
                            <div className="pagination-controls">
                                <button
                                    disabled={page === 0}
                                    onClick={() => setPage(p => Math.max(0, p - 1))}
                                    className="pagination-btn"
                                >
                                    {t('conversations.prev')}
                                </button>
                                <span className="pagination-info">
                                    {page + 1} / {Math.ceil(total / PAGE_SIZE)}
                                </span>
                                <button
                                    disabled={(page + 1) * PAGE_SIZE >= total}
                                    onClick={() => setPage(p => p + 1)}
                                    className="pagination-btn"
                                >
                                    {t('conversations.next')}
                                </button>
                            </div>
                        )}
                    </div>

                    <div className="conversation-detail">
                        {selectedConv ? (
                            <>
                                <div className="detail-header">
                                    <div className="detail-info">
                                        <h2>{selectedConv.page_title || t('conversations.untitledConversation')}</h2>
                                        {selectedConv.page_url && (
                                            <a
                                                href={selectedConv.page_url}
                                                target="_blank"
                                                rel="noopener noreferrer"
                                                className="url-link"
                                            >
                                                <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" width="14" height="14">
                                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                                                </svg>
                                                {selectedConv.page_url}
                                            </a>
                                        )}
                                    </div>
                                    <button
                                        onClick={() => handleDeleteClick(selectedConv._id)}
                                        className="action-btn delete1 small"
                                        title={t('conversations.deleteTooltip')}
                                    >
                                        <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" width="16" height="16">
                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                        </svg>
                                    </button>
                                </div>

                                <div className="messages-scroll-area">
                                    {selectedConv.messages && selectedConv.messages.length > 0 ? (
                                        <div className="messages-list">
                                            {selectedConv.messages.map((msg, index) => (
                                                <div key={index} className={`message-bubble-wrapper ${msg.role}`}>
                                                    <div className="message-avatar">
                                                        {msg.role === 'user' ? <User size={16} /> : <Bot size={16} />}
                                                    </div>
                                                    <div className="message-bubble">
                                                        <div className="message-text">{msg.content}</div>
                                                        <div className="message-timestamp">
                                                            {new Date(msg.timestamp).toLocaleString()}
                                                        </div>
                                                    </div>
                                                </div>
                                            ))}
                                        </div>
                                        ) : (
                                        <div className="empty-chat-state">
                                            <span>{t('conversations.noMessagesYet')}</span>
                                        </div>
                                    )}
                                </div>
                            </>
                        ) : (
                            <div className="empty-detail-state">
                                <div className="empty-icon"><MessageCircle size={16} /></div>
                                <h3>{t('conversations.selectConversation')}</h3>
                                <p>{t('conversations.chooseFromSidebar')}</p>
                            </div>
                        )}
                    </div>
                </div>
                ) : (
                    <LeadsPanel embedded={true} showHeader={false} />
                )}
            </div>
            <ConfirmationModal
                isOpen={isDeleteModalOpen}
                onClose={() => setIsDeleteModalOpen(false)}
                onConfirm={handleConfirmDelete}
                title={t('conversations.deleteTooltip')}
                message={t('conversations.deleteConfirm')}
                confirmText={t('common.delete')}
                isDanger={true}
            />
        </Layout >
    );
};

export default Conversations;
