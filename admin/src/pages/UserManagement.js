import React, { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../context/AuthContext';
import { getUsers, createManagedUser, deleteManagedUser, updateManagedUser } from '../services/api';
import Layout from '../components/Layout';
import './UserManagement.css';

import { AlertTriangle, Check, Lock, Pencil, Trash2, User, Users, X } from 'lucide-react';
import { getErrorMessage } from '../utils/errorMessage';
const UserManagement = () => {
    const { t } = useTranslation();
    const { isSuperuser } = useAuth();

    const [users, setUsers] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [success, setSuccess] = useState('');

    // Create modal state
    const [showCreateModal, setShowCreateModal] = useState(false);
    const [createForm, setCreateForm] = useState({ email: '', username: '', password: '' });
    const [createLoading, setCreateLoading] = useState(false);
    const [createError, setCreateError] = useState('');

    // Edit modal state
    const [editUser, setEditUser] = useState(null);
    const [editForm, setEditForm] = useState({ username: '', password: '' });
    const [editLoading, setEditLoading] = useState(false);
    const [editError, setEditError] = useState('');

    // Delete confirmation
    const [deleteConfirm, setDeleteConfirm] = useState(null);
    const [deleteLoading, setDeleteLoading] = useState(false);

    const fetchUsers = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const res = await getUsers();
            setUsers(res.data);
        } catch (err) {
            setError(getErrorMessage(err, t('userManagement.loadError')));
        } finally {
            setLoading(false);
        }
    }, [t]);

    useEffect(() => {
        if (isSuperuser) {
            fetchUsers();
        }
    }, [isSuperuser, fetchUsers]);

    const handleCreate = async (e) => {
        e.preventDefault();
        setCreateLoading(true);
        setCreateError('');
        try {
            await createManagedUser(createForm);
            setSuccess(t('userManagement.createSuccess'));
            setShowCreateModal(false);
            setCreateForm({ email: '', username: '', password: '' });
            await fetchUsers();
            setTimeout(() => setSuccess(''), 4000);
        } catch (err) {
            setCreateError(getErrorMessage(err, t('userManagement.createError')));
        } finally {
            setCreateLoading(false);
        }
    };

    const handleEdit = async (e) => {
        e.preventDefault();
        setEditLoading(true);
        setEditError('');
        const payload = {};
        if (editForm.username.trim()) payload.username = editForm.username.trim();
        if (editForm.password) payload.password = editForm.password;
        try {
            await updateManagedUser(editUser._id, payload);
            setSuccess(t('userManagement.updateSuccess'));
            setEditUser(null);
            await fetchUsers();
            setTimeout(() => setSuccess(''), 4000);
        } catch (err) {
            setEditError(getErrorMessage(err, t('userManagement.updateError')));
        } finally {
            setEditLoading(false);
        }
    };

    const handleDelete = async () => {
        if (!deleteConfirm) return;
        setDeleteLoading(true);
        try {
            await deleteManagedUser(deleteConfirm._id);
            setSuccess(t('userManagement.deleteSuccess'));
            setDeleteConfirm(null);
            await fetchUsers();
            setTimeout(() => setSuccess(''), 4000);
        } catch (err) {
            setError(getErrorMessage(err, t('userManagement.deleteError')));
            setDeleteConfirm(null);
        } finally {
            setDeleteLoading(false);
        }
    };

    const openEdit = (user) => {
        setEditUser(user);
        setEditForm({ username: '', password: '' });
        setEditError('');
    };

    const formatDate = (dateStr) => {
        if (!dateStr) return '—';
        return new Date(dateStr).toLocaleDateString(undefined, {
            year: 'numeric', month: 'short', day: 'numeric'
        });
    };

    if (!isSuperuser) {
        return (
            <Layout>
                <div className="access-denied" style={{
                    maxWidth: 480, margin: '4rem auto', padding: '2rem',
                    textAlign: 'center', background: 'var(--surface)',
                    border: '1px solid var(--border)', borderRadius: 'var(--radius-md)',
                }}>
                    <Lock size={48} style={{ color: 'var(--text-tertiary)', marginBottom: '1rem' }} />
                    <h2 style={{ marginTop: 0 }}>{t('access.forbiddenTitle', 'Access denied')}</h2>
                    <p style={{ color: 'var(--text-secondary)' }}>
                        {t('access.forbiddenBody', 'Only the platform administrator can manage users.')}
                    </p>
                </div>
            </Layout>
        );
    }

    return (
        <Layout>
            <div className="um-container">
                {/* Header */}
                <div className="um-header">
                    <div className="um-header-left">
                        <h1 className="um-title">
                            <span className="um-title-icon"><Users size={16} /></span>
                            {t('userManagement.title')}
                        </h1>
                        <p className="um-subtitle">{t('userManagement.subtitle')}</p>
                    </div>
                    <button className="um-btn um-btn-primary" onClick={() => { setShowCreateModal(true); setCreateError(''); }}>
                        <span>+</span> {t('userManagement.newUser')}
                    </button>
                </div>

                {/* Feedback banners */}
                {success && (
                    <div className="um-alert um-alert-success">
                        <span><Check size={16} /></span> {success}
                    </div>
                )}
                {error && (
                    <div className="um-alert um-alert-error">
                        <span><AlertTriangle size={16} /></span> {error}
                    </div>
                )}

                {/* Users table */}
                <div className="um-card">
                    {loading ? (
                        <div className="um-loading">
                            <div className="um-spinner" />
                            <p>{t('userManagement.loadingUsers')}</p>
                        </div>
                    ) : users.length === 0 ? (
                        <div className="um-empty">
                            <span className="um-empty-icon"><User size={16} /></span>
                            <p>{t('userManagement.empty')}</p>
                        </div>
                    ) : (
                        <div className="um-table-wrapper">
                            <table className="um-table">
                                <thead>
                                    <tr>
                                        <th>{t('userManagement.colUser')}</th>
                                        <th>{t('userManagement.colEmail')}</th>
                                        <th>{t('userManagement.colRole')}</th>
                                        <th>{t('userManagement.colCreatedBy')}</th>
                                        <th>{t('userManagement.colJoined')}</th>
                                        <th>{t('userManagement.colActions')}</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {users.map(u => (
                                        <tr key={u._id}>
                                            <td data-label={t('userManagement.colUser')}>
                                                <div className="um-user-cell">
                                                    <div className="um-avatar">
                                                        {u.username ? u.username[0].toUpperCase() : '?'}
                                                    </div>
                                                    <span className="um-username">{u.username}</span>
                                                </div>
                                            </td>
                                            <td className="um-email" data-label={t('userManagement.colEmail')}>{u.email}</td>
                                            <td data-label={t('userManagement.colRole')}>
                                                {u.is_superuser ? (
                                                    <span className="um-badge um-badge-super">{t('userManagement.roleSuperuser')}</span>
                                                ) : (
                                                    <span className="um-badge um-badge-user">{t('userManagement.roleManaged')}</span>
                                                )}
                                            </td>
                                            <td className="um-created-by" data-label={t('userManagement.colCreatedBy')}>
                                                {u.created_by ? u.created_by : <span className="um-muted">—</span>}
                                            </td>
                                            <td className="um-date" data-label={t('userManagement.colJoined')}>{formatDate(u.created_at)}</td>
                                            <td data-label={t('userManagement.colActions')}>
                                                {!u.is_superuser && (
                                                    <div className="um-actions">
                                                        <button
                                                            className="um-action-btn um-action-edit"
                                                            onClick={() => openEdit(u)}
                                                            title={t('users.editTooltip')}
                                                        >
                                                            <Pencil size={16} />
                                                        </button>
                                                        <button
                                                            className="um-action-btn um-action-delete"
                                                            onClick={() => setDeleteConfirm(u)}
                                                            title={t('users.deleteTooltip')}
                                                        >
                                                            <Trash2 size={16} />
                                                        </button>
                                                    </div>
                                                )}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </div>

                {/* Create User Modal */}
                {showCreateModal && (
                    <div className="um-modal-overlay" onClick={() => setShowCreateModal(false)}>
                        <div className="um-modal" onClick={e => e.stopPropagation()}>
                            <div className="um-modal-header">
                                <h2>{t('userManagement.createTitle')}</h2>
                                <button className="um-modal-close" onClick={() => setShowCreateModal(false)}><X size={14} /></button>
                            </div>
                            <form className="um-form" onSubmit={handleCreate}>
                                {createError && <div className="um-form-error">{createError}</div>}
                                <div className="um-form-group">
                                    <label>{t('userManagement.fieldEmail')}</label>
                                    <input
                                        type="email"
                                        value={createForm.email}
                                        onChange={e => setCreateForm(f => ({ ...f, email: e.target.value }))}
                                        placeholder={t('userManagement.emailPlaceholder')}
                                        required
                                    />
                                </div>
                                <div className="um-form-group">
                                    <label>{t('userManagement.fieldUsername')}</label>
                                    <input
                                        type="text"
                                        value={createForm.username}
                                        onChange={e => setCreateForm(f => ({ ...f, username: e.target.value }))}
                                        placeholder={t('userManagement.usernamePlaceholder')}
                                        required
                                        minLength={3}
                                    />
                                </div>
                                <div className="um-form-group">
                                    <label>{t('userManagement.fieldPassword')}</label>
                                    <input
                                        type="password"
                                        value={createForm.password}
                                        onChange={e => setCreateForm(f => ({ ...f, password: e.target.value }))}
                                        placeholder={t('userManagement.passwordPlaceholder')}
                                        required
                                    />
                                    <p className="um-form-hint">{t('userManagement.passwordHint')}</p>
                                </div>
                                <div className="um-form-info">
                                    <span>ℹ️</span> {t('userManagement.createInfoPre')} <strong>{t('userManagement.createInfoStrong')}</strong>.
                                </div>
                                <div className="um-modal-footer">
                                    <button type="button" className="um-btn um-btn-secondary" onClick={() => setShowCreateModal(false)}>
                                        {t('common.cancel')}
                                    </button>
                                    <button type="submit" className="um-btn um-btn-primary" disabled={createLoading}>
                                        {createLoading ? t('userManagement.creating') : t('userManagement.createSubmit')}
                                    </button>
                                </div>
                            </form>
                        </div>
                    </div>
                )}

                {/* Edit User Modal */}
                {editUser && (
                    <div className="um-modal-overlay" onClick={() => setEditUser(null)}>
                        <div className="um-modal" onClick={e => e.stopPropagation()}>
                            <div className="um-modal-header">
                                <h2>{t('userManagement.editTitle', { username: editUser.username })}</h2>
                                <button className="um-modal-close" onClick={() => setEditUser(null)}><X size={14} /></button>
                            </div>
                            <form className="um-form" onSubmit={handleEdit}>
                                {editError && <div className="um-form-error">{editError}</div>}
                                <div className="um-form-group">
                                    <label>{t('userManagement.newUsernameLabel')} <span className="um-muted">{t('userManagement.keepBlank')}</span></label>
                                    <input
                                        type="text"
                                        value={editForm.username}
                                        onChange={e => setEditForm(f => ({ ...f, username: e.target.value }))}
                                        placeholder={editUser.username}
                                        minLength={3}
                                    />
                                </div>
                                <div className="um-form-group">
                                    <label>{t('userManagement.newPasswordLabel')} <span className="um-muted">{t('userManagement.keepBlank')}</span></label>
                                    <input
                                        type="password"
                                        value={editForm.password}
                                        onChange={e => setEditForm(f => ({ ...f, password: e.target.value }))}
                                        placeholder={t('userManagement.newPasswordPlaceholder')}
                                    />
                                </div>
                                <div className="um-modal-footer">
                                    <button type="button" className="um-btn um-btn-secondary" onClick={() => setEditUser(null)}>
                                        {t('common.cancel')}
                                    </button>
                                    <button type="submit" className="um-btn um-btn-primary" disabled={editLoading || (!editForm.username.trim() && !editForm.password)}>
                                        {editLoading ? t('userManagement.saving') : t('userManagement.saveChanges')}
                                    </button>
                                </div>
                            </form>
                        </div>
                    </div>
                )}

                {/* Delete Confirmation Modal */}
                {deleteConfirm && (
                    <div className="um-modal-overlay" onClick={() => setDeleteConfirm(null)}>
                        <div className="um-modal um-modal-sm" onClick={e => e.stopPropagation()}>
                            <div className="um-modal-header">
                                <h2>{t('userManagement.deleteTitle')}</h2>
                                <button className="um-modal-close" onClick={() => setDeleteConfirm(null)}><X size={14} /></button>
                            </div>
                            <div className="um-form">
                                <div className="um-delete-warning">
                                    <span className="um-delete-icon"><AlertTriangle size={16} /></span>
                                    <p>{t('userManagement.deleteConfirm', { username: deleteConfirm.username, email: deleteConfirm.email })}</p>
                                </div>
                                <div className="um-modal-footer">
                                    <button className="um-btn um-btn-secondary" onClick={() => setDeleteConfirm(null)}>
                                        {t('common.cancel')}
                                    </button>
                                    <button className="um-btn um-btn-danger" onClick={handleDelete} disabled={deleteLoading}>
                                        {deleteLoading ? t('userManagement.deleting') : t('userManagement.deleteSubmit')}
                                    </button>
                                </div>
                            </div>
                        </div>
                    </div>
                )}
            </div>
        </Layout>
    );
};

export default UserManagement;
