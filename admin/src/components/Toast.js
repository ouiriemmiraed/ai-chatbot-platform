import React from 'react';
import { useTranslation } from 'react-i18next';
import { Info, CheckCircle2, XCircle, AlertTriangle } from 'lucide-react';
import './Toast.css';

const ICONS = {
  info: <Info size={18} />,
  success: <CheckCircle2 size={18} />,
  error: <XCircle size={18} />,
  warn: <AlertTriangle size={18} />,
};

const ToastItem = ({ toast, onRemove }) => {
  const { t } = useTranslation();
  return (
    <div className={`toast-item ${toast.type || 'info'}`} role="status">
      <div className="toast-left">
        <span className="toast-icon">{ICONS[toast.type] || ICONS.info}</span>
      </div>
      <div className="toast-body">
        {toast.title && <div className="toast-title">{toast.title}</div>}
        <div className="toast-message">{toast.message}</div>
      </div>
      <button className="toast-close" onClick={() => onRemove(toast.id)} aria-label={t('aria.close')}>×</button>
    </div>
  );
};

const Toast = ({ toasts = [], onRemove = () => {} }) => {
  return (
    <div className="toast-viewport" aria-live="polite">
      {toasts.map(t => (
        <ToastItem key={t.id} toast={t} onRemove={onRemove} />
      ))}
    </div>
  );
};

export default Toast;
