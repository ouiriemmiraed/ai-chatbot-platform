import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import './PermissionModal.css';
import { getErrorMessage } from '../utils/errorMessage';

const PermissionModal = ({ isOpen, onClose, onSubmit, title, initialEmail = '', initialLevel = 'viewer' }) => {
    const { t } = useTranslation();
    const [email, setEmail] = useState(initialEmail);
    const [permissionLevel, setPermissionLevel] = useState(initialLevel);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    // Keep local state in sync when the modal opens or the inputs change.
    // The modal instance is reused (mounted once, toggled via isOpen), so the
    // permission level MUST be re-synced too — otherwise editing user B shows
    // the level last selected for user A.
    useEffect(() => {
        setEmail(initialEmail || '');
        setPermissionLevel(initialLevel || 'viewer');
        setError('');
    }, [initialEmail, initialLevel, isOpen]);

    const handleSubmit = async (e) => {
        e.preventDefault();
        setError('');

        // basic email regex validation
        const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
        if (!email || !emailRegex.test(email)) {
            setError(t('chatbotDetails.access.invalidEmail'));
            return;
        }

        setLoading(true);
        try {
            await onSubmit(email, permissionLevel);
            onClose();
            setEmail('');
            setPermissionLevel('viewer');
        } catch (err) {
            setError(getErrorMessage(err, t('chatbotDetails.access.saveFailed')));
        } finally {
            setLoading(false);
        }
    };

    if (!isOpen) return null;

    return (
        <div className="permission-modal-overlay" onClick={onClose}>
            <div className="permission-modal-content" onClick={(e) => e.stopPropagation()}>
                <div className="permission-modal-header">
                    <h2>{title}</h2>
                    <button className="close-btn" onClick={onClose}>&times;</button>
                </div>

                <form onSubmit={handleSubmit}>
                    <div className="form-group">
                        <label htmlFor="email">{t('chatbotDetails.access.userEmail')}</label>
                        <input
                            type="email"
                            id="email"
                            value={email}
                            onChange={(e) => setEmail(e.target.value)}
                            placeholder=""
                            required
                            readOnly={initialEmail !== ''}
                        />
                        <small>{t('chatbotDetails.access.inviteHint')}</small>
                    </div>

                    <div className="form-group">
                        <label htmlFor="permission">{t('chatbotDetails.access.permissionLevel')}</label>
                        <select
                            id="permission"
                            value={permissionLevel}
                            onChange={(e) => setPermissionLevel(e.target.value)}
                        >
                            <option value="viewer">{t('chatbotDetails.access.viewer')} — {t('chatbotDetails.access.viewerDesc')}</option>
                            <option value="editor">{t('chatbotDetails.access.editor')} — {t('chatbotDetails.access.editorDesc')}</option>
                            <option value="admin">{t('chatbotDetails.access.admin')} — {t('chatbotDetails.access.adminDesc')}</option>
                        </select>
                    </div>

                    {error && <div className="error-message">{error}</div>}

                    <div className="modal-actions">
                        <button type="button" onClick={onClose} className="btn-cancel" disabled={loading}>
                            {t('common.cancel')}
                        </button>
                        <button type="submit" className="btn-submit" disabled={loading}>
                            {loading ? t('common.saving') : t('common.save')}
                        </button>
                    </div>
                </form>
            </div>
        </div>
    );
};

export default PermissionModal;
