import React, { useState, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import api from '../services/api';
import './NotificationCenter.css';

import { Bot, Circle, FileEdit, XCircle } from 'lucide-react';
const NotificationCenter = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [notifications, setNotifications] = useState([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [isOpen, setIsOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const dropdownRef = useRef(null);

  useEffect(() => {
    fetchNotifications();
    // Poll for new notifications every 30 seconds
    const interval = setInterval(fetchNotifications, 30000);
    // Refresh immediately when the Notifications page acknowledges items,
    // so the badge count doesn't lag behind for up to 30s.
    const onUpdated = () => fetchNotifications();
    window.addEventListener('notifications-updated', onUpdated);
    return () => {
      clearInterval(interval);
      window.removeEventListener('notifications-updated', onUpdated);
    };
  }, []);

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
        setIsOpen(false);
      }
    };

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }

    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [isOpen]);

  const fetchNotifications = async () => {
    try {
      setLoading(true);
      // List = 20 most recent for the dropdown. Unread count must reflect ALL
      // unread (not just those within the 20 shown), otherwise the badge reads
      // 0 while older unread alerts still exist. Fetch the count separately.
      const [listResp, unreadResp] = await Promise.all([
        api.get('/api/alerts/user?limit=20'),
        api.get('/api/alerts/user?acknowledged=false&limit=100'),
      ]);
      setNotifications(listResp.data.alerts || []);
      setUnreadCount((unreadResp.data.alerts || []).length);
    } catch (error) {
      console.error('Failed to fetch notifications:', error);
    } finally {
      setLoading(false);
    }
  };

  const acknowledgeNotification = async (notificationId) => {
    try {
      await api.post(`/api/alerts/${notificationId}/acknowledge`);
      
      // Update local state
      setNotifications(notifications.map(n => 
        n.id === notificationId ? { ...n, acknowledged: true } : n
      ));
      
      // Update unread count
      setUnreadCount(prev => Math.max(0, prev - 1));
    } catch (error) {
      console.error('Failed to acknowledge notification:', error);
    }
  };

  const acknowledgeAll = async () => {
    const unreadIds = notifications.filter(n => !n.acknowledged).map(n => n.id);
    
    for (const id of unreadIds) {
      try {
        await api.post(`/api/alerts/${id}/acknowledge`);
      } catch (error) {
        console.error(`Failed to acknowledge ${id}:`, error);
      }
    }
    
    // Refresh notifications
    await fetchNotifications();
  };

  const getMagnitudeEmoji = (magnitude) => {
    const emojis = {
      trivial: <Circle size={10} fill="#94a3b8" stroke="#94a3b8" />,
      minor: <Circle size={10} fill="#facc15" stroke="#facc15" />,
      moderate: <Circle size={10} fill="#fb923c" stroke="#fb923c" />,
      major: <Circle size={10} fill="#ef4444" stroke="#ef4444" />,
      complete_rewrite: <Circle size={10} fill="#0f172a" stroke="#0f172a" />,
      broken: <XCircle size={12} />
    };
    return emojis[magnitude] || <FileEdit size={12} />;
  };

  const getSeverityClass = (severity) => {
    return `severity-${severity || 'normal'}`;
  };

  const formatDate = (dateString) => {
    if (!dateString) return '';
    const date = new Date(dateString);
    const now = new Date();
    const diffMs = now - date;
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);

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

  return (
    <div className="notification-center" ref={dropdownRef}>
      <button 
        className={`notification-bell ${unreadCount > 0 ? 'has-unread' : ''}`}
        onClick={() => setIsOpen(!isOpen)}
        aria-label={t('notifications.title', 'Notifications')}
      >
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
          <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
        </svg>
      </button>

      {isOpen && (
        <div className="notification-dropdown">
          <div className="notification-panel-header">
            <h3>{t('notifications.title', 'Notifications')}</h3>
            {unreadCount > 0 && (
              <button className="mark-all-read" onClick={acknowledgeAll}>
                {t('notifications.markAllRead', 'Mark all read')}
              </button>
            )}
          </div>

          <div className="notification-list">
            {loading ? (
              <div className="notification-loading">
                <div className="spinner-small"></div>
                <p>{t('common.loading', 'Loading...')}</p>
              </div>
            ) : notifications.length === 0 ? (
              <div className="no-notifications">
                <p>{t('notifications.noNotifications', 'No notifications')}</p>
              </div>
            ) : (
              notifications.map((notification) => (
                <div
                  key={notification.id}
                  className={`notification-item ${getSeverityClass(notification.severity)} ${
                    notification.acknowledged ? 'read' : 'unread'
                  }`}
                  onClick={() => !notification.acknowledged && acknowledgeNotification(notification.id)}
                >
                  <div className="notification-icon">
                    {getMagnitudeEmoji(notification.change_magnitude)}
                  </div>
                  
                  <div className="notification-content">
                    <div className="notification-item-header">
                      <div className="notification-type">
                        {getAlertTypeLabel(notification.alert_type)}
                        {!notification.acknowledged && <span className="unread-dot"></span>}
                      </div>
                      <div className="notification-time">
                        {formatDate(notification.created_at)}
                      </div>
                    </div>
                    
                    {notification.chatbot_name && (
                      <div className="notification-chatbot">
                        <Bot size={16} /> {notification.chatbot_name}
                      </div>
                    )}
                    
                    <div className="notification-url">
                      {notification.url}
                    </div>
                    
                    {notification.change_summary && (notification.change_summary.added_lines > 0 || notification.change_summary.removed_lines > 0) && (
                      <div className="notification-summary">
                        {notification.change_summary.added_lines > 0 && (
                          <span className="change-stat">
                            +{notification.change_summary.added_lines}
                          </span>
                        )}
                        {notification.change_summary.removed_lines > 0 && (
                          <span className="change-stat">
                            -{notification.change_summary.removed_lines}
                          </span>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              ))
            )}
          </div>

          {notifications.length > 0 && (
            <div className="notification-footer">
              <button
                type="button"
                className="view-all-link"
                onClick={() => {
                  setIsOpen(false);
                  navigate('/notifications');
                }}
              >
                {t('notifications.viewAll', 'View all notifications')}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default NotificationCenter;
