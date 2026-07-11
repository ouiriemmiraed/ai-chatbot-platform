import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import PageVersionHistory from './PageVersionHistory';
import InputModal from './InputModal';
import { useToast } from '../contexts/ToastContext';
import { getErrorMessage } from '../utils/errorMessage';
import api from '../services/api';
import './PageManagement.css';

import { ArrowRight, Building2, CheckCircle2, Eye, FileText, Pin, Plus, RefreshCw, Save, ScrollText, Trash2, X, XCircle, Zap } from 'lucide-react';
const API_BASE_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000';

const PageManagement = ({ chatbotId, config, onUpdate }) => {
    const { t } = useTranslation();
    const { show: showToast } = useToast();
    const [pages, setPages] = useState([]);
    const [statistics, setStatistics] = useState(null);
    const [selectedPages, setSelectedPages] = useState(new Set());
    const [filters, setFilters] = useState({
        strategy: null,
        is_pinned: null,
        priority: null,
        search: '',
        is_broken: null,
        quality_grade: null
    });
    const [sortBy, setSortBy] = useState('scraped_at');
    const [sortOrder, setSortOrder] = useState('desc');
    const [showBulkActions, setShowBulkActions] = useState(false);
    const [loading, setLoading] = useState(false);
    const [exclusionPatterns, setExclusionPatterns] = useState([]);
    const [newPattern, setNewPattern] = useState('');
    const [showPatternPreview, setShowPatternPreview] = useState(false);
    const [patternPreview, setPatternPreview] = useState(null);
    const [showVersionHistory, setShowVersionHistory] = useState(false);
    const [selectedPageForHistory, setSelectedPageForHistory] = useState(null);
    const [pinModalConfig, setPinModalConfig] = useState({ isOpen: false, isBulk: false, pageId: null });

    // Load pages with filters
    const loadPages = async () => {
        setLoading(true);
        try {
            const params = new URLSearchParams();
            if (filters.strategy) params.append('strategy', filters.strategy);
            if (filters.is_pinned !== null) params.append('is_pinned', filters.is_pinned);
            if (filters.priority !== null) params.append('priority', filters.priority);
            if (filters.search) params.append('search', filters.search);
            if (filters.is_broken !== null) params.append('is_broken', filters.is_broken);
            if (filters.quality_grade) params.append('quality_grade', filters.quality_grade);
            params.append('sort_by', sortBy);
            params.append('sort_order', sortOrder);
            params.append('limit', '100');

            const response = await fetch(`${API_BASE_URL}/api/chatbots/${chatbotId}/pages?${params}`, {
                credentials: 'include'
            });

            if (response.ok) {
                const data = await response.json();
                setPages(data.pages);
            }
        } catch (error) {
            console.error('Error loading pages:', error);
        } finally {
            setLoading(false);
        }
    };

    // Load statistics
    const loadStatistics = async () => {
        try {
            const response = await fetch(`${API_BASE_URL}/api/chatbots/${chatbotId}/pages/statistics`, {
                credentials: 'include'
            });

            if (response.ok) {
                const data = await response.json();
                setStatistics(data);
            }
        } catch (error) {
            console.error('Error loading statistics:', error);
        }
    };

    // Load exclusion patterns
    const loadExclusionPatterns = async () => {
        setExclusionPatterns(config?.excluded_url_patterns || []);
    };

    useEffect(() => {
        loadPages();
        loadStatistics();
        loadExclusionPatterns();
    }, [chatbotId, filters, sortBy, sortOrder]);

    // Toggle selection
    const toggleSelection = (pageId) => {
        const newSelected = new Set(selectedPages);
        if (newSelected.has(pageId)) {
            newSelected.delete(pageId);
        } else {
            newSelected.add(pageId);
        }
        setSelectedPages(newSelected);
        setShowBulkActions(newSelected.size > 0);
    };

    // Select all
    const toggleSelectAll = () => {
        if (selectedPages.size === pages.length) {
            setSelectedPages(new Set());
            setShowBulkActions(false);
        } else {
            setSelectedPages(new Set(pages.map(p => p._id)));
            setShowBulkActions(true);
        }
    };

    // Bulk actions
    const handleBulkAction = async (action, params = {}) => {
        try {
            await api.post(`/api/chatbots/${chatbotId}/pages/bulk-update`, {
                page_ids: Array.from(selectedPages),
                action,
                params
            });
            setSelectedPages(new Set());
            setShowBulkActions(false);
            loadPages();
            loadStatistics();
            if (onUpdate) onUpdate();
        } catch (error) {
            console.error('Bulk action error:', error);
            showToast(getErrorMessage(error, t('pageManagement.bulkActionError', 'Bulk action failed')), { type: 'error' });
        }
    };

    // Toggle pin
    const togglePin = async (pageId, currentlyPinned) => {
        if (!currentlyPinned) {
            // Open modal to get reason instead of prompt
            setPinModalConfig({ isOpen: true, isBulk: false, pageId });
            return;
        }

        // If unpinning, do it immediately without reason
        executeTogglePin(pageId, true, null);
    };

    const executeTogglePin = async (pageId, currentlyPinned, reason) => {
        try {
            await api.patch(`/api/chatbots/${chatbotId}/pages/${pageId}/policy`, {
                is_pinned: !currentlyPinned,
                pin_reason: reason
            });
            loadPages();
            loadStatistics();
            if (onUpdate) onUpdate();
        } catch (error) {
            console.error('Error toggling pin:', error);
            showToast(getErrorMessage(error, t('pageManagement.pinError', 'Failed to update pin')), { type: 'error' });
        }
    };

    // Change priority
    const changePriority = async (pageId, priority) => {
        try {
            await api.patch(`/api/chatbots/${chatbotId}/pages/${pageId}/policy`, { priority: parseInt(priority) });
            loadPages();
            if (onUpdate) onUpdate();
        } catch (error) {
            console.error('Error changing priority:', error);
            showToast(getErrorMessage(error, t('pageManagement.priorityError', 'Failed to change priority')), { type: 'error' });
        }
    };

    // Check page health
    const checkPageHealth = async (pageId) => {
        try {
            const { data } = await api.post(`/api/chatbots/${chatbotId}/pages/${pageId}/check-health`);
            loadPages();
            showToast(
                t('pageManagement.healthCheckComplete', `Health check complete: ${data.health.is_broken ? 'Broken' : 'OK'}`),
                { type: data.health.is_broken ? 'warning' : 'success' }
            );
        } catch (error) {
            console.error('Error checking health:', error);
            showToast(t('pageManagement.healthCheckError', 'Failed to check page health'), { type: 'error' });
        }
    };

    // Open version history
    const openVersionHistory = (page) => {
        setSelectedPageForHistory(page);
        setShowVersionHistory(true);
    };

    // Add exclusion pattern
    const addExclusionPattern = async () => {
        if (!newPattern.trim()) return;

        try {
            await api.post(`/api/chatbots/${chatbotId}/exclusion-patterns`, { pattern: newPattern });
            setNewPattern('');
            loadExclusionPatterns();
            if (onUpdate) onUpdate();
        } catch (error) {
            console.error('Error adding pattern:', error);
            showToast(getErrorMessage(error, t('pageManagement.patternAddError', 'Failed to add pattern')), { type: 'error' });
        }
    };

    // Remove exclusion pattern
    const removeExclusionPattern = async (pattern) => {
        try {
            await api.delete(`/api/chatbots/${chatbotId}/exclusion-patterns?pattern=${encodeURIComponent(pattern)}`);
            loadExclusionPatterns();
            if (onUpdate) onUpdate();
        } catch (error) {
            console.error('Error removing pattern:', error);
            showToast(getErrorMessage(error, t('pageManagement.patternRemoveError', 'Failed to remove pattern')), { type: 'error' });
        }
    };

    // Preview pattern
    const previewPattern = async (pattern) => {
        try {
            const response = await fetch(
                `${API_BASE_URL}/api/chatbots/${chatbotId}/exclusion-patterns/preview?pattern=${encodeURIComponent(pattern)}`,
                {
                    credentials: 'include'
                }
            );

            if (response.ok) {
                const data = await response.json();
                setPatternPreview(data);
                setShowPatternPreview(true);
            }
        } catch (error) {
            console.error('Error previewing pattern:', error);
        }
    };

    const formatSize = (bytes) => {
        if (!bytes) return '0 KB';
        return (bytes / 1024).toFixed(1) + ' KB';
    };

    const formatDate = (dateString) => {
        return new Date(dateString).toLocaleDateString();
    };

    const getPriorityLabel = (priority) => {
        switch (priority) {
            case 1: return { icon: <Zap size={14} />, label: t('pageManagement.priorityHigh'), class: 'high' };
            case -1: return { icon: '⬇️', label: t('pageManagement.priorityLow'), class: 'low' };
            default: return { icon: <ArrowRight size={14} />, label: t('pageManagement.priorityNormal'), class: 'normal' };
        }
    };

    const getQualityGradeColor = (grade) => {
        const colors = {
            'A+': '#4caf50',
            'A': '#66bb6a',
            'B+': '#9ccc65',
            'B': '#d4e157',
            'C': '#ffeb3b',
            'D': '#ff9800',
            'F': '#f44336'
        };
        return colors[grade] || '#9e9e9e';
    };

    return (
        <div className="page-management">
            {/* Statistics Header */}
            {statistics && (
                <div className="page-stats-header">
                    <div className="stat-card">
                        <div className="stat-icon"><FileText size={16} /></div>
                        <div className="stat-info">
                            <div className="stat-value">{statistics.total_pages}</div>
                            <div className="stat-label">{t('pageManagement.totalPages')}</div>
                        </div>
                    </div>
                    <div className="stat-card active">
                        <div className="stat-icon"><RefreshCw size={16} /></div>
                        <div className="stat-info">
                            <div className="stat-value">{statistics.active_count || 0}</div>
                            <div className="stat-label">{t('pageManagement.activePages')}</div>
                        </div>
                    </div>
                    <div className="stat-card pinned">
                        <div className="stat-icon"><Pin size={16} /></div>
                        <div className="stat-info">
                            <div className="stat-value">{statistics.pinned_count || 0}</div>
                            <div className="stat-label">{t('pageManagement.pinnedPages')}</div>
                        </div>
                    </div>
                    <div className="stat-card">
                        <div className="stat-icon"><Zap size={16} /></div>
                        <div className="stat-info">
                            <div className="stat-value">{statistics.high_priority || 0}</div>
                            <div className="stat-label">{t('pageManagement.highPriority')}</div>
                        </div>
                    </div>
                    <div className="stat-card">
                        <div className="stat-icon"><Save size={16} /></div>
                        <div className="stat-info">
                            <div className="stat-value">
                                {(statistics.total_size / 1024 / 1024).toFixed(1)} MB
                            </div>
                            <div className="stat-label">{t('pageManagement.storage')}</div>
                        </div>
                    </div>
                </div>
            )}

            {/* Filters */}
            <div className="page-filters">
                <input
                    type="text"
                    placeholder={t('pageManagement.searchPlaceholder')}
                    value={filters.search}
                    onChange={(e) => setFilters({ ...filters, search: e.target.value })}
                    className="search-input"
                />

                <select
                    value={filters.is_pinned ?? ''}
                    onChange={(e) => setFilters({
                        ...filters,
                        is_pinned: e.target.value === '' ? null : e.target.value === 'true'
                    })}
                    className="filter-select"
                >
                    <option value="">{t('pageManagement.allPages')}</option>
                    <option value="false">{t('pageManagement.activeOnly')}</option>
                    <option value="true">{t('pageManagement.pinnedOnly')}</option>
                </select>

                <select
                    value={filters.priority ?? ''}
                    onChange={(e) => setFilters({
                        ...filters,
                        priority: e.target.value === '' ? null : parseInt(e.target.value)
                    })}
                    className="filter-select"
                >
                    <option value="">{t('pageManagement.allPriorities')}</option>
                    <option value="1">{t('pageManagement.priorityHigh')}</option>
                    <option value="0">{t('pageManagement.priorityNormal')}</option>
                    <option value="-1">{t('pageManagement.priorityLow')}</option>
                </select>

                <select
                    value={sortBy}
                    onChange={(e) => setSortBy(e.target.value)}
                    className="filter-select"
                >
                    <option value="scraped_at">{t('pageManagement.sortByDate')}</option>
                    <option value="title">{t('pageManagement.sortByTitle')}</option>
                    <option value="char_count">{t('pageManagement.sortBySize')}</option>
                </select>

                <button
                    onClick={() => setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc')}
                    className="sort-toggle"
                    title={sortOrder === 'asc' ? t('pageManagement.ascending') : t('pageManagement.descending')}
                >
                    {sortOrder === 'asc' ? '↑' : '↓'}
                </button>
            </div>

            {/* Bulk Actions */}
            {showBulkActions && (
                <div className="bulk-actions-bar">
                    <span>{selectedPages.size} {t('pageManagement.pagesSelected')}</span>
                    <div className="bulk-actions-buttons">
                        <button
                            onClick={() => {
                                setPinModalConfig({ isOpen: true, isBulk: true, pageId: null });
                            }}
                        >
                            <Pin size={16} /> {t('pageManagement.pinSelected')}
                        </button>
                        <button onClick={() => handleBulkAction('unpin')}>
                            <RefreshCw size={16} /> {t('pageManagement.unpinSelected')}
                        </button>
                        <button onClick={() => handleBulkAction('set_priority', { priority: 1 })}>
                            <Zap size={16} /> {t('pageManagement.setHighPriority')}
                        </button>
                        <button onClick={() => handleBulkAction('set_priority', { priority: -1 })}>
                            ⬇️ {t('pageManagement.setLowPriority')}
                        </button>
                        <button
                            onClick={() => {
                                if (window.confirm(t('pageManagement.confirmDelete'))) {
                                    handleBulkAction('delete');
                                }
                            }}
                            className="danger"
                        >
                            <Trash2 size={16} /> {t('pageManagement.deleteSelected')}
                        </button>
                    </div>
                </div>
            )}

            {/* Pages Table */}
            <div className="pages-table-container">
                {loading ? (
                    <div className="loading-state">{t('pageManagement.loading')}</div>
                ) : pages.length === 0 ? (
                    <div className="empty-state">{t('pageManagement.noPages')}</div>
                ) : (
                    <table className="pages-table-advanced">
                        <thead>
                            <tr>
                                <th width="40">
                                    <input
                                        type="checkbox"
                                        checked={selectedPages.size === pages.length && pages.length > 0}
                                        onChange={toggleSelectAll}
                                    />
                                </th>
                                <th width="80">{t('pageManagement.status')}</th>
                                <th>{t('pageManagement.title')}</th>
                                <th>{t('pageManagement.url')}</th>
                                <th width="100">{t('pageManagement.size')}</th>
                                <th width="90">{t('pageManagement.health')}</th>
                                <th width="120">{t('pageManagement.lastScrape')}</th>
                                <th width="120">{t('pageManagement.priority')}</th>
                                <th width="150">{t('pageManagement.actions')}</th>
                            </tr>
                        </thead>
                        <tbody>
                            {pages.map((page) => {
                                const policy = page.rescrape_policy || {};
                                const isPinned = policy.is_pinned;
                                const priority = policy.priority || 0;
                                const priorityInfo = getPriorityLabel(priority);

                                return (
                                    <tr key={page._id} className={isPinned ? 'pinned-row' : ''}>
                                        <td>
                                            <input
                                                type="checkbox"
                                                checked={selectedPages.has(page._id)}
                                                onChange={() => toggleSelection(page._id)}
                                            />
                                        </td>
                                        <td className="status-cell" data-label={t('pageManagement.status')}>
                                            {isPinned ? (
                                                <span
                                                    className="status-badge pinned"
                                                    title={policy.pin_reason || t('pageManagement.pinnedByUser')}
                                                >
                                                    <Pin size={16} /> {t('pageManagement.pinned')}
                                                </span>
                                            ) : (
                                                <span className="status-badge active">
                                                    <RefreshCw size={16} /> {t('pageManagement.active')}
                                                </span>
                                            )}
                                        </td>
                                        <td className="page-title" data-label={t('pageManagement.title')}>
                                            {page.title || t('pageManagement.untitled')}
                                        </td>
                                        <td className="page-url" data-label={t('pageManagement.url')}>
                                            <a href={page.url} target="_blank" rel="noopener noreferrer">
                                                {page.url.length > 50 ? page.url.substring(0, 50) + '...' : page.url}
                                            </a>
                                        </td>
                                        <td className="text-right" data-label={t('pageManagement.size')}>
                                            {formatSize(page.char_count)}
                                        </td>
                                        <td className="text-center health-cell" data-label={t('pageManagement.health')}>
                                            {page.is_broken ? (
                                                <span className="health-badge broken" title={`HTTP ${page.http_status || 'Error'}`}>
                                                    <XCircle size={16} /> {t('pageManagement.broken')}
                                                </span>
                                            ) : (
                                                <span className="health-badge ok" title={`HTTP ${page.http_status || 200}`}>
                                                    <CheckCircle2 size={16} /> {t('pageManagement.ok')}
                                                </span>
                                            )}
                                        </td>
                                        <td className="text-center" data-label={t('pageManagement.lastScrape')}>
                                            {formatDate(page.scraped_at)}
                                        </td>
                                        <td className="priority-cell" data-label={t('pageManagement.priority')}>
                                            <select
                                                value={priority}
                                                onChange={(e) => changePriority(page._id, e.target.value)}
                                                className={`priority-select priority-${priorityInfo.class}`}
                                            >
                                                <option value="1">{t('pageManagement.priorityHigh')}</option>
                                                <option value="0">{t('pageManagement.priorityNormal')}</option>
                                                <option value="-1">{t('pageManagement.priorityLow')}</option>
                                            </select>
                                        </td>
                                        <td className="actions-cell" data-label={t('pageManagement.actions')}>
                                            <button
                                                onClick={() => togglePin(page._id, isPinned)}
                                                className={`action-btn ${isPinned ? 'pinned' : ''}`}
                                                title={isPinned ? t('pageManagement.unpin') : t('pageManagement.pin')}
                                            >
                                                {isPinned ? <RefreshCw size={16} /> : <Pin size={16} />}
                                            </button>
                                            <button
                                                onClick={() => checkPageHealth(page._id)}
                                                className="action-btn"
                                                title={t('pageManagement.checkHealth')}
                                            >
                                                <Building2 size={16} />
                                            </button>
                                            <button
                                                onClick={() => openVersionHistory(page)}
                                                className="action-btn"
                                                title={t('pageManagement.viewHistory')}
                                            >
                                                <ScrollText size={16} />
                                            </button>
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                )}
            </div>

            {/* Exclusion Patterns Section */}
            <div className="exclusion-patterns-section">
                <h3>{t('pageManagement.exclusionPatterns')}</h3>
                <p className="section-description">{t('pageManagement.exclusionPatternsDesc')}</p>

                <div className="pattern-input-group">
                    <input
                        type="text"
                        value={newPattern}
                        onChange={(e) => setNewPattern(e.target.value)}
                        placeholder="/blog/archive/* or *.pdf"
                        className="pattern-input"
                    />
                    <button onClick={() => previewPattern(newPattern)} className="preview-btn">
                        <Eye size={16} /> {t('pageManagement.preview')}
                    </button>
                    <button onClick={addExclusionPattern} className="add-pattern-btn">
                        <Plus size={16} /> {t('pageManagement.addPattern')}
                    </button>
                </div>

                {exclusionPatterns.length > 0 && (
                    <div className="patterns-list">
                        {exclusionPatterns.map((pattern, index) => (
                            <div key={index} className="pattern-item">
                                <code>{pattern}</code>
                                <button
                                    onClick={() => removeExclusionPattern(pattern)}
                                    className="remove-pattern-btn"
                                    title={t('pageManagement.removePattern')}
                                >
                                    <X size={14} />
                                </button>
                            </div>
                        ))}
                    </div>
                )}
            </div>

            {/* Pattern Preview Modal */}
            {showPatternPreview && patternPreview && (
                <div className="modal-overlay" onClick={() => setShowPatternPreview(false)}>
                    <div className="modal-content" onClick={(e) => e.stopPropagation()}>
                        <h3>{t('pageManagement.patternPreview')}</h3>
                        <p>
                            {t('pageManagement.patternWillAffect', {
                                count: patternPreview.affected_count,
                                total: patternPreview.total_pages
                            })}
                        </p>
                        <div className="affected-pages-list">
                            {patternPreview.affected_pages.map((page, idx) => (
                                <div key={idx} className="affected-page">
                                    <strong>{page.title || 'Untitled'}</strong>
                                    <small>{page.url}</small>
                                </div>
                            ))}
                        </div>
                        <button onClick={() => setShowPatternPreview(false)} className="close-modal-btn">
                            {t('pageManagement.close')}
                        </button>
                    </div>
                </div>
            )}

            {/* Version History Modal */}
            {showVersionHistory && selectedPageForHistory && (
                <PageVersionHistory
                    chatbotId={chatbotId}
                    pageId={selectedPageForHistory._id}
                    pageUrl={selectedPageForHistory.url}
                    onClose={() => setShowVersionHistory(false)}
                />
            )}

            {/* Pin Page Reason Modal */}
            <InputModal
                isOpen={pinModalConfig.isOpen}
                onClose={() => setPinModalConfig({ isOpen: false, isBulk: false, pageId: null })}
                onSubmit={(reason) => {
                    if (pinModalConfig.isBulk) {
                        handleBulkAction('pin', { reason });
                    } else {
                        executeTogglePin(pinModalConfig.pageId, false, reason);
                    }
                }}
                title={t('pageManagement.pin')}
                message={t('pageManagement.pinReasonPrompt')}
                placeholder={t('pageManagement.pinReasonPrompt')}
                confirmText={t('common.save') || 'Save'}
                cancelText={t('common.cancel') || 'Cancel'}
                allowEmpty={true}
            />
        </div>
    );
};

export default PageManagement;
