import React, { useEffect, useRef } from 'react';
import './ConfirmationModal.css';

const ConfirmationModal = ({ isOpen, onClose, onConfirm, title, message, confirmText = 'Delete', cancelText = 'Cancel', isDanger = false, singleButton = false }) => {
    const dialogRef = useRef(null);

    // a11y: close on Escape and move focus into the dialog when it opens.
    useEffect(() => {
        if (!isOpen) return;
        const onKey = (e) => { if (e.key === 'Escape' && onClose) onClose(); };
        document.addEventListener('keydown', onKey);
        const btn = dialogRef.current?.querySelector('.modal-btn');
        if (btn) btn.focus();
        return () => document.removeEventListener('keydown', onKey);
    }, [isOpen, onClose]);

    if (!isOpen) return null;

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div
                className="modal-content"
                role="dialog"
                aria-modal="true"
                aria-label={title}
                ref={dialogRef}
                onClick={(e) => e.stopPropagation()}
            >
                <div className={`modal-header ${isDanger ? 'danger' : ''}`}>
                    <h3>{title}</h3>
                </div>
                <div className="modal-body">
                    <p>{message}</p>
                </div>
                <div className="modal-footer">
                    {singleButton ? (
                        <button
                            className={`modal-btn confirm ${isDanger ? 'danger' : 'primary'}`}
                            onClick={() => {
                                if (onConfirm) onConfirm();
                                onClose();
                            }}
                        >
                            {cancelText || confirmText}
                        </button>
                    ) : (
                        <>
                            <button className="modal-btn cancel" onClick={onClose}>
                                {cancelText}
                            </button>
                            <button
                                className={`modal-btn confirm ${isDanger ? 'danger' : 'primary'}`}
                                onClick={() => {
                                    if (onConfirm) onConfirm();
                                    onClose();
                                }}
                            >
                                {confirmText}
                            </button>
                        </>
                    )}
                </div>
            </div>
        </div>
    );
};

export default ConfirmationModal;
