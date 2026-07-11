import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import Layout from '../components/Layout';
import api from '../services/api';
import './Notifications.css';

import { AlertTriangle, Bell, Bot, FileEdit, XCircle } from 'lucide-react';
const Notifications = () => {
  const { t } = useTranslation();
  const [notifications, setNotifications] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchNotifications();
  }, []);

  const fetchNotifications = async () => {
    try {
      setLoading(true);
      const response = await api.get('/api/alerts/user?limit=100');
      setNotifications(response.data.alerts || []);
    } catch (error) {
      console.error('Failed to fetch notifications:', error);
    } finally {
      setLoading(false);
    }
  };

  const markAllAsRead = async () => {
    const unreadIds = notifications.filter(n => !n.acknowledged).map(n => n.id);
    if (unreadIds.length === 0) return;
    // allSettled so one failed acknowledge doesn't abort the rest; always
    // refresh and notify the bell afterwards regardless of partial failures.
    await Promise.allSettled(unreadIds.map(id => api.post(`/api/alerts/${id}/acknowledge`)));
    await fetchNotifications();
    window.dispatchEvent(new CustomEvent('notifications-updated'));
  };

  const acknowledgeNotification = async (id) => {
    try {
      await api.post(`/api/alerts/${id}/acknowledge`);
      setNotifications(notifications.map(n =>
        n.id === id ? { ...n, acknowledged: true } : n
      ));
      // Keep the header bell badge in sync without waiting for its 30s poll.
      window.dispatchEvent(new CustomEvent('notifications-updated'));
    } catch (error) {
      console.error('Failed to acknowledge notification:', error);
    }
  };

  const formatDate = (dateString) => {
    if (!dateString) return '';
    const date = new Date(dateString);
    if (isNaN(date.getTime())) return '';
    const now = new Date();
    const diffMs = now - date;
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMins / 60);
    const diffDays = Math.floor(diffHours / 24);

    if (diffMins < 1) return t('notifications.justNow', 'Just now');
    if (diffMins < 60) return t('notifications.minsAgo', '{{count}}m ago', { count: diffMins });
    if (diffHours < 24) return t('notifications.hoursAgo', '{{count}}h ago', { count: diffHours });
    if (diffDays < 7) return t('notifications.daysAgo', '{{count}}d ago', { count: diffDays });
    
    return date.toLocaleDateString();
  };

  const getAlertTypeLabel = (alertType) => {
    const labels = {
      page_changed: t('notifications.pageChanged', 'Page Changed'),
      page_broken: t('notifications.pageBroken', 'Broken Link'),
      quality_degraded: t('notifications.qualityDegraded', 'Quality Issue')
    };
    return labels[alertType] || alertType;
  };

  const getMagnitudeEmoji = (magnitude) => {
    // Match the backend crawler's change_magnitude enum (and NotificationCenter).
    switch (magnitude) {
      case 'broken': return <XCircle size={16} />;
      case 'major':
      case 'complete_rewrite': return <AlertTriangle size={16} />;
      case 'moderate': return 'ℹ️';
      case 'minor':
      case 'trivial': return <FileEdit size={16} />;
      default: return <Bell size={16} />;
    }
  };

  const getSeverityClass = (severity) => {
    return severity ? `severity-${severity}` : 'severity-normal';
  };

  return (
    <Layout>
      <div className="notifications-page">
        <div className="notifications-page-header">
          <h2>{t('notifications.title', 'Notifications')}</h2>
          {notifications.some(n => !n.acknowledged) && (
            <button className="btn-secondary" onClick={markAllAsRead}>
              {t('notifications.markAllRead', 'Mark all as read')}
            </button>
          )}
        </div>

        {loading ? (
          <div className="notifications-loading">
            <div className="spinner"></div>
            <p>{t('common.loading', 'Loading...')}</p>
          </div>
        ) : notifications.length === 0 ? (
          <div className="no-notifications-page">
            <div className="empty-state-icon"><Bell size={16} /></div>
            <h3>{t('notifications.noNotifications', 'No notifications')}</h3>
            <p>{t('notifications.noNotificationsDesc', 'You have no notifications at the moment.')}</p>
          </div>
        ) : (
          <div className="notifications-page-list">
            {notifications.map(notification => (
              <div 
                key={notification.id} 
                className={`notification-page-item ${getSeverityClass(notification.severity)} ${notification.acknowledged ? 'read' : 'unread'}`}
                onClick={() => !notification.acknowledged && acknowledgeNotification(notification.id)}
              >
                <div className="notification-page-icon">
                  {getMagnitudeEmoji(notification.change_magnitude)}
                </div>
                
                <div className="notification-page-content">
                  <div className="notification-page-title-row">
                    <span className="notification-page-type">
                      {getAlertTypeLabel(notification.alert_type)}
                      {!notification.acknowledged && <span className="unread-dot"></span>}
                    </span>
                    <span className="notification-page-time">{formatDate(notification.created_at)}</span>
                  </div>
                  
                  {notification.chatbot_name && (
                    <div className="notification-page-chatbot">
                      <Bot size={16} /> {notification.chatbot_name}
                    </div>
                  )}
                  
                  <div className="notification-page-url">
                    <a href={notification.url} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()}>
                      {notification.url}
                    </a>
                  </div>
                  
                  {notification.change_summary && (notification.change_summary.added_lines > 0 || notification.change_summary.removed_lines > 0) && (
                    <div className="notification-page-summary">
                      {notification.change_summary.added_lines > 0 && (
                        <span className="change-stat added">+{notification.change_summary.added_lines} lines</span>
                      )}
                      {notification.change_summary.removed_lines > 0 && (
                        <span className="change-stat removed">-{notification.change_summary.removed_lines} lines</span>
                      )}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </Layout>
  );
};

export default Notifications;