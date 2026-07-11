import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { getChatbotConfigs, getChatbotDocuments, deleteChatbotDocument, uploadDocument } from '../services/api';
import { useToast } from '../contexts/ToastContext';
import '../pages/Documents.css';

import { BookOpen, FileText, FolderOpen, Trash2 } from 'lucide-react';
const MAX_UPLOAD_BYTES = 50 * 1024 * 1024; // keep in sync with backend MAX_DOC_SIZE

const formatSize = (bytes) => {
    if (!bytes) return '—';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

const DocumentsPanel = ({ embedded = false, forcedChatbotId = '', showHeader = true }) => {
    const { t } = useTranslation();
    const { show: showToast } = useToast();

    const [chatbots, setChatbots] = useState([]);
    const [selectedChatbot, setSelectedChatbot] = useState(forcedChatbotId || '');
    const [documents, setDocuments] = useState([]);
    const [loading, setLoading] = useState(false);
    const [uploading, setUploading] = useState(false);
    const [dragOver, setDragOver] = useState(false);
    const [deleteTarget, setDeleteTarget] = useState(null);
    const [deleting, setDeleting] = useState(false);
    const fileInputRef = useRef(null);

    useEffect(() => {
        setSelectedChatbot(forcedChatbotId || '');
    }, [forcedChatbotId]);

    useEffect(() => {
        getChatbotConfigs()
            .then((res) => {
                const bots = res.data || [];
                setChatbots(bots);
                if (!forcedChatbotId && !selectedChatbot && bots.length > 0) {
                    setSelectedChatbot(bots[0]._id);
                }
            })
            .catch(() => showToast(t('documents.fetchError'), { type: 'error' }));
        // selectedChatbot is intentionally NOT a dependency: it is set inside
        // this effect, and including it re-fetched the whole chatbot list on
        // every tab switch.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [forcedChatbotId]);

    const fetchDocuments = useCallback(async () => {
        if (!selectedChatbot) return;
        setLoading(true);
        try {
            const res = await getChatbotDocuments(selectedChatbot);
            setDocuments(res.data);
        } catch {
            showToast(t('documents.fetchError'), { type: 'error' });
        } finally {
            setLoading(false);
        }
    }, [selectedChatbot, t, showToast]);

    useEffect(() => {
        fetchDocuments();
    }, [fetchDocuments]);

    const handleUpload = async (files) => {
        if (!selectedChatbot) {
            showToast(t('documents.selectChatbotFirst'), { type: 'warning' });
            return;
        }

        const allowedExts = ['.pdf', '.txt', '.docx'];

        // Single uploading window for the whole batch so the dropzone doesn't
        // flicker idle between files (which could let a stray click/drop slip in).
        setUploading(true);
        try {
            for (const file of files) {
                const name = (file.name || '').toLowerCase();
                if (!allowedExts.some((ext) => name.endsWith(ext))) {
                    showToast(t('documents.unsupportedType', { name: file.name }), { type: 'error' });
                    continue;
                }
                if (file.size > MAX_UPLOAD_BYTES) {
                    showToast(t('documents.fileTooLarge', { name: file.name }), { type: 'error' });
                    continue;
                }
                try {
                    await uploadDocument(selectedChatbot, file);
                    showToast(t('documents.uploadSuccess', { name: file.name }), { type: 'success' });
                } catch {
                    showToast(t('documents.uploadError', { name: file.name }), { type: 'error' });
                }
            }
        } finally {
            setUploading(false);
        }

        fetchDocuments();
    };

    const handleDrop = (e) => {
        e.preventDefault();
        setDragOver(false);
        if (uploading) return;
        const files = Array.from(e.dataTransfer.files);
        if (files.length) handleUpload(files);
    };

    const handleFileInput = (e) => {
        const files = Array.from(e.target.files);
        if (files.length) handleUpload(files);
        e.target.value = '';
    };

    const handleDelete = async () => {
        if (!deleteTarget || deleting) return;
        setDeleting(true);
        try {
            await deleteChatbotDocument(selectedChatbot, deleteTarget._id);
            showToast(t('documents.deleteSuccess'), { type: 'success' });
            setDeleteTarget(null);
            await fetchDocuments();
        } catch {
            showToast(t('documents.deleteError'), { type: 'error' });
        } finally {
            setDeleting(false);
        }
    };

    const activeBot = chatbots.find((c) => c._id === selectedChatbot);

    return (
        <>
            <div className={`documents-container ${embedded ? 'embedded-documents' : ''}`}>
                {showHeader && (
                    <header className="documents-header">
                        <div>
                            <h1>{t('documents.title')}</h1>
                            <p>{t('documents.subtitle')}</p>
                        </div>
                    </header>
                )}

                {!forcedChatbotId && (
                    <div className="chatbot-selector">
                        <label>{t('documents.selectChatbot')}</label>
                        <div className="chatbot-tabs">
                            {chatbots.map((c) => (
                                <button
                                    key={c._id}
                                    type="button"
                                    className={`chatbot-tab ${selectedChatbot === c._id ? 'active' : ''}`}
                                    onClick={() => setSelectedChatbot(c._id)}
                                >
                                    {c.name}
                                </button>
                            ))}
                        </div>
                    </div>
                )}

                <div
                    className={`dropzone ${dragOver ? 'drag-active' : ''} ${uploading ? 'uploading' : ''}`}
                    onDragOver={(e) => {
                        e.preventDefault();
                        setDragOver(true);
                    }}
                    onDragLeave={() => setDragOver(false)}
                    onDrop={handleDrop}
                    onClick={() => !uploading && fileInputRef.current?.click()}
                >
                    <input
                        ref={fileInputRef}
                        type="file"
                        hidden
                        multiple
                        accept=".pdf,.txt,.docx"
                        onChange={handleFileInput}
                    />
                    {uploading ? (
                        <div className="dropzone-content">
                            <div className="upload-spinner" />
                            <span>{t('documents.uploading')}</span>
                        </div>
                    ) : (
                        <div className="dropzone-content">
                            <span className="dropzone-icon"><FolderOpen size={16} /></span>
                            <span className="dropzone-main">{t('documents.dropzoneMain')}</span>
                            <span className="dropzone-sub">{t('documents.dropzoneSub')}</span>
                        </div>
                    )}
                </div>

                <div className="docs-table-wrapper">
                    {loading ? (
                        <div className="docs-loading">{t('common.loading')}</div>
                    ) : documents.length === 0 ? (
                        <div className="docs-empty">
                            <div className="docs-empty-icon"><FileText size={16} /></div>
                            <h3>{t('documents.noDocs')}</h3>
                            <p>{t('documents.noDocsHint', { chatbot: activeBot?.name || '' })}</p>
                        </div>
                    ) : (
                        <table className="docs-table">
                            <thead>
                                <tr>
                                    <th>{t('documents.colName')}</th>
                                    <th>{t('documents.colType')}</th>
                                    <th>{t('documents.colSize')}</th>
                                    <th>{t('documents.colChunks')}</th>
                                    <th>{t('documents.colDate')}</th>
                                    <th>{t('documents.colActions')}</th>
                                </tr>
                            </thead>
                            <tbody>
                                {documents.map((doc) => (
                                    <tr key={doc._id}>
                                        <td className="doc-name" data-label={t('documents.colName')}>
                                            <span className="doc-icon">
                                                {doc.file_ext === '.pdf' ? <BookOpen size={16} /> : doc.file_ext === '.txt' ? <FileText size={16} /> : <FileText size={16} />}
                                            </span>
                                            {doc.original_name}
                                        </td>
                                        <td data-label={t('documents.colType')}>
                                            <span className="ext-badge">{doc.file_ext?.replace('.', '').toUpperCase() || '—'}</span>
                                        </td>
                                        <td data-label={t('documents.colSize')}>{formatSize(doc.file_size)}</td>
                                        <td data-label={t('documents.colChunks')}>
                                            <span className="chunks-badge">{doc.chunks_count || 0}</span>
                                        </td>
                                        <td className="doc-date" data-label={t('documents.colDate')}>{new Date(doc.uploaded_at).toLocaleDateString()}</td>
                                        <td data-label={t('documents.colActions')}>
                                            <button
                                                type="button"
                                                className="delete-doc-btn"
                                                onClick={() => setDeleteTarget(doc)}
                                                title={t('common.delete')}
                                            >
                                                <Trash2 size={16} />
                                            </button>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    )}
                </div>
            </div>

            {deleteTarget && (
                <div className="modal-overlay" onClick={() => setDeleteTarget(null)}>
                    <div className="modal-box" onClick={(e) => e.stopPropagation()}>
                        <h2>{t('documents.deleteTitle')}</h2>
                        <p>{t('documents.deleteConfirm', { name: deleteTarget.original_name })}</p>
                        <p className="delete-warning">{t('documents.deleteWarning')}</p>
                        <div className="modal-actions">
                            <button type="button" className="btn-secondary" onClick={() => setDeleteTarget(null)} disabled={deleting}>
                                {t('common.cancel')}
                            </button>
                            <button type="button" className="btn-danger" onClick={handleDelete} disabled={deleting}>
                                {deleting ? t('common.loading') : t('common.delete')}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </>
    );
};

export default DocumentsPanel;