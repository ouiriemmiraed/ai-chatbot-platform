import React, { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { getLeads, updateLead, deleteLead, getChatbotConfigs } from '../services/api';
import { useToast } from '../contexts/ToastContext';
import '../pages/Leads.css';

import { Inbox, Pencil, Trash2 } from 'lucide-react';
const STATUS_COLORS = {
    new: 'status-new',
    contacted: 'status-contacted',
    qualified: 'status-qualified',
    unqualified: 'status-unqualified',
};

const PAGE_SIZE = 20;

const LeadsPanel = ({ embedded = false, showHeader = true }) => {
    const { t } = useTranslation();
    const { show: showToast } = useToast();

    const [leads, setLeads] = useState([]);
    const [total, setTotal] = useState(0);
    const [loading, setLoading] = useState(true);
    const [chatbots, setChatbots] = useState([]);
    const [filterChatbot, setFilterChatbot] = useState('');
    const [filterStatus, setFilterStatus] = useState('');
    const [page, setPage] = useState(0);
    const [editLead, setEditLead] = useState(null);
    const [editStatus, setEditStatus] = useState('');
    const [editNotes, setEditNotes] = useState('');
    const [editLoading, setEditLoading] = useState(false);
    const [deleteTarget, setDeleteTarget] = useState(null);
    const [deleting, setDeleting] = useState(false);

    const fetchLeads = useCallback(async () => {
        setLoading(true);
        try {
            const params = { skip: page * PAGE_SIZE, limit: PAGE_SIZE };
            if (filterChatbot) params.chatbot_id = filterChatbot;
            if (filterStatus) params.status = filterStatus;
            const res = await getLeads(params);
            setLeads(res.data.leads);
            setTotal(res.data.total);
        } catch {
            showToast(t('leads.fetchError'), { type: 'error' });
        } finally {
            setLoading(false);
        }
    }, [page, filterChatbot, filterStatus, t, showToast]);

    useEffect(() => {
        fetchLeads();
    }, [fetchLeads]);

    useEffect(() => {
        getChatbotConfigs()
            .then((res) => setChatbots(res.data || []))
            .catch(() => {});
    }, []);

    const openEdit = (lead) => {
        setEditLead(lead);
        setEditStatus(lead.status);
        setEditNotes(lead.notes || '');
    };

    const handleSaveEdit = async () => {
        if (!editLead) return;
        setEditLoading(true);
        try {
            await updateLead(editLead._id, { status: editStatus, notes: editNotes });
            showToast(t('leads.updateSuccess'), { type: 'success' });
            setEditLead(null);
            fetchLeads();
        } catch {
            showToast(t('leads.updateError'), { type: 'error' });
        } finally {
            setEditLoading(false);
        }
    };

    const handleDelete = async () => {
        if (!deleteTarget || deleting) return;
        setDeleting(true);
        try {
            await deleteLead(deleteTarget._id);
            showToast(t('leads.deleteSuccess'), { type: 'success' });
            setDeleteTarget(null);
            await fetchLeads();
        } catch {
            showToast(t('leads.deleteError'), { type: 'error' });
        } finally {
            setDeleting(false);
        }
    };

    const exportCSV = () => {
        if (!leads.length) return;
        // RFC-4180 escaping: wrap every field in quotes and double any embedded
        // quotes, so commas/quotes/newlines in the data don't break columns.
        const esc = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`;
        const headers = ['Email', 'Name', 'Chatbot', 'Status', 'Notes', 'Date'];
        const rows = leads.map((l) => [
            l.email,
            l.name || '',
            l.chatbot_name || l.chatbot_id,
            l.status,
            l.notes || '',
            new Date(l.created_at).toLocaleDateString(),
        ].map(esc));
        const csv = [headers.map(esc), ...rows].map((r) => r.join(',')).join('\n');
        const blob = new Blob([csv], { type: 'text/csv' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'leads.csv';
        a.click();
        URL.revokeObjectURL(url);
    };

    const totalPages = Math.ceil(total / PAGE_SIZE);

    return (
        <>
            <div className={`leads-container ${embedded ? 'embedded-leads' : ''}`}>
                {showHeader && (
                    <header className="leads-header">
                        <div>
                            <h1>{t('leads.title')}</h1>
                            <p>{t('leads.subtitle')}</p>
                        </div>
                        <button className="export-btn" onClick={exportCSV} disabled={!leads.length}>
                            ⬇ {t('leads.exportCSV')}
                        </button>
                    </header>
                )}

                <div className="leads-filters">
                    <select
                        value={filterChatbot}
                        onChange={(e) => {
                            setFilterChatbot(e.target.value);
                            setPage(0);
                        }}
                        className="filter-select"
                    >
                        <option value="">{t('leads.allChatbots')}</option>
                        {chatbots.map((c) => (
                            <option key={c._id} value={c._id}>{c.name}</option>
                        ))}
                    </select>
                    <select
                        value={filterStatus}
                        onChange={(e) => {
                            setFilterStatus(e.target.value);
                            setPage(0);
                        }}
                        className="filter-select"
                    >
                        <option value="">{t('leads.allStatuses')}</option>
                        <option value="new">{t('leads.statusNew')}</option>
                        <option value="contacted">{t('leads.statusContacted')}</option>
                        <option value="qualified">{t('leads.statusQualified')}</option>
                        <option value="unqualified">{t('leads.statusUnqualified')}</option>
                    </select>
                    <span className="leads-count">{t('leads.total', { count: total })}</span>
                    {!showHeader && (
                        <button className="export-btn" onClick={exportCSV} disabled={!leads.length}>
                            ⬇ {t('leads.exportCSV')}
                        </button>
                    )}
                </div>

                <div className="leads-table-wrapper">
                    {loading ? (
                        <div className="leads-loading">{t('common.loading')}</div>
                    ) : leads.length === 0 ? (
                        <div className="leads-empty">
                            <div className="leads-empty-icon"><Inbox size={32} /></div>
                            <h3>{t('leads.noLeads')}</h3>
                            <p>{t('leads.noLeadsHint')}</p>
                        </div>
                    ) : (
                        <table className="leads-table">
                            <thead>
                                <tr>
                                    <th>{t('leads.colEmail')}</th>
                                    <th>{t('leads.colName')}</th>
                                    <th>{t('leads.colChatbot')}</th>
                                    <th>{t('leads.colStatus')}</th>
                                    <th>{t('leads.colDate')}</th>
                                    <th>{t('leads.colActions')}</th>
                                </tr>
                            </thead>
                            <tbody>
                                {leads.map((lead) => (
                                    <tr key={lead._id}>
                                        <td className="lead-email" data-label={t('leads.colEmail')}>{lead.email}</td>
                                        <td data-label={t('leads.colName')}>{lead.name || <span className="text-muted">—</span>}</td>
                                        <td data-label={t('leads.colChatbot')}>
                                            <span className="chatbot-badge">{lead.chatbot_name || lead.chatbot_id}</span>
                                        </td>
                                        <td data-label={t('leads.colStatus')}>
                                            <span className={`status-badge ${STATUS_COLORS[lead.status] || ''}`}>
                                                {lead.status
                                                    ? t(`leads.status${lead.status.charAt(0).toUpperCase() + lead.status.slice(1)}`, lead.status)
                                                    : '—'}
                                            </span>
                                        </td>
                                        <td className="lead-date" data-label={t('leads.colDate')}>{new Date(lead.created_at).toLocaleDateString()}</td>
                                        <td data-label={t('leads.colActions')}>
                                            <div className="lead-actions">
                                                <button className="action-btn edit-btn" onClick={() => openEdit(lead)} title={t('common.edit')}><Pencil size={16} /></button>
                                                <button className="action-btn delete-btn" onClick={() => setDeleteTarget(lead)} title={t('common.delete')}><Trash2 size={16} /></button>
                                            </div>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    )}
                </div>

                {totalPages > 1 && (
                    <div className="leads-pagination">
                        <button className="page-btn" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>{t('conversations.prev')}</button>
                        <span>{page + 1} / {totalPages}</span>
                        <button className="page-btn" disabled={page >= totalPages - 1} onClick={() => setPage((p) => p + 1)}>{t('conversations.next')}</button>
                    </div>
                )}
            </div>

            {editLead && (
                <div className="modal-overlay" onClick={() => setEditLead(null)}>
                    <div className="modal-box" onClick={(e) => e.stopPropagation()}>
                        <h2>{t('leads.editTitle')}</h2>
                        <p className="modal-email">{editLead.email}</p>
                        <div className="form-group">
                            <label>{t('leads.colStatus')}</label>
                            <select value={editStatus} onChange={(e) => setEditStatus(e.target.value)} className="form-select">
                                <option value="new">{t('leads.statusNew')}</option>
                                <option value="contacted">{t('leads.statusContacted')}</option>
                                <option value="qualified">{t('leads.statusQualified')}</option>
                                <option value="unqualified">{t('leads.statusUnqualified')}</option>
                            </select>
                        </div>
                        <div className="form-group">
                            <label>{t('leads.notes')}</label>
                            <textarea
                                value={editNotes}
                                onChange={(e) => setEditNotes(e.target.value)}
                                className="form-textarea"
                                rows={3}
                                placeholder={t('leads.notesPlaceholder')}
                            />
                        </div>
                        <div className="modal-actions">
                            <button className="btn-secondary" onClick={() => setEditLead(null)}>{t('common.cancel')}</button>
                            <button className="btn-primary" onClick={handleSaveEdit} disabled={editLoading}>
                                {editLoading ? t('common.loading') : t('common.save')}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {deleteTarget && (
                <div className="modal-overlay" onClick={() => setDeleteTarget(null)}>
                    <div className="modal-box" onClick={(e) => e.stopPropagation()}>
                        <h2>{t('leads.deleteTitle')}</h2>
                        <p>{t('leads.deleteConfirm', { email: deleteTarget.email })}</p>
                        <div className="modal-actions">
                            <button className="btn-secondary" onClick={() => setDeleteTarget(null)} disabled={deleting}>{t('common.cancel')}</button>
                            <button className="btn-danger" onClick={handleDelete} disabled={deleting}>{deleting ? t('common.loading') : t('common.delete')}</button>
                        </div>
                    </div>
                </div>
            )}
        </>
    );
};

export default LeadsPanel;