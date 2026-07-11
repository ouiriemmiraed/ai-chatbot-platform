import React, { useState, useEffect } from 'react';
import './InputModal.css';

const InputModal = ({ isOpen, onClose, onSubmit, title, message, placeholder = '', confirmText = 'Submit', cancelText = 'Cancel', defaultValue = '', allowEmpty = false }) => {
    const [inputValue, setInputValue] = useState('');

    useEffect(() => {
        if (isOpen) {
            setInputValue(defaultValue);
        }
    }, [isOpen, defaultValue]);

    // a11y: close on Escape.
    useEffect(() => {
        if (!isOpen) return;
        const onKey = (e) => { if (e.key === 'Escape' && onClose) onClose(); };
        document.addEventListener('keydown', onKey);
        return () => document.removeEventListener('keydown', onKey);
    }, [isOpen, onClose]);

    if (!isOpen) return null;

    const handleSubmit = (e) => {
        e.preventDefault();
        onSubmit(inputValue);
        onClose();
    };

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div
                className="modal-content input-modal"
                role="dialog"
                aria-modal="true"
                aria-label={title}
                onClick={(e) => e.stopPropagation()}
            >
                <div className="modal-header">
                    <h3>{title}</h3>
                </div>
                <form onSubmit={handleSubmit}>
                    <div className="modal-body">
                        {message && <p className="modal-message">{message}</p>}
                        <input
                            type="text"
                            value={inputValue}
                            onChange={(e) => setInputValue(e.target.value)}
                            placeholder={placeholder}
                            className="modal-input"
                            autoFocus
                        />
                    </div>
                    <div className="modal-footer">
                        <button type="button" className="modal-btn cancel" onClick={onClose}>
                            {cancelText}
                        </button>
                        <button type="submit" className="modal-btn confirm primary" disabled={!allowEmpty && !inputValue.trim()}>
                            {confirmText}
                        </button>
                    </div>
                </form>
            </div>
        </div>
    );
};

export default InputModal;
