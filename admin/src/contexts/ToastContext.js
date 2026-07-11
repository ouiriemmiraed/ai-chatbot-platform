import React, { createContext, useContext, useState, useCallback } from 'react';
import Toast from '../components/Toast';

const ToastContext = createContext(null);

let idCounter = 0;

export const ToastProvider = ({ children }) => {
  const [toasts, setToasts] = useState([]);

  const show = useCallback((message, options = {}) => {
    const id = ++idCounter;
    const toast = {
      id,
      message,
      type: options.type || 'info',
      duration: options.duration || 4000,
      title: options.title || null,
    };
    setToasts((t) => [...t, toast]);

    if (toast.duration > 0) {
      setTimeout(() => {
        setToasts((t) => t.filter(x => x.id !== id));
      }, toast.duration);
    }

    return id;
  }, []);

  const remove = useCallback((id) => {
    setToasts((t) => t.filter(x => x.id !== id));
  }, []);

  const value = {
    show,
    remove,
  };

  return (
    <ToastContext.Provider value={value}>
      {children}
      <Toast toasts={toasts} onRemove={remove} />
    </ToastContext.Provider>
  );
};

export const useToast = () => {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast must be used within ToastProvider');
  return ctx;
};

export default ToastContext;
