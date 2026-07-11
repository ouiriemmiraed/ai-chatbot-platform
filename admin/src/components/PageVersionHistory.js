import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import api from '../services/api';
import './PageVersionHistory.css';

import { Circle, X } from 'lucide-react';
const PageVersionHistory = ({ chatbotId, pageId, pageUrl, onClose }) => {
  const { t } = useTranslation();
  const [versions, setVersions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [currentVersion, setCurrentVersion] = useState(null);

  useEffect(() => {
    fetchVersionHistory();
  }, [chatbotId, pageId]);

  const fetchVersionHistory = async () => {
    try {
      setLoading(true);
      const response = await api.get(`/api/chatbots/${chatbotId}/pages/${pageId}/versions?limit=50`);
      setVersions(response.data.versions || []);
      setCurrentVersion(response.data.current_version);
      setError(null);
    } catch (err) {
      console.error('Failed to fetch version history:', err);
      setError(t('pages.version_history.fetch_error', 'Failed to load version history'));
    } finally {
      setLoading(false);
    }
  };

  const formatDate = (dateString) => {
    if (!dateString) return 'N/A';
    const date = new Date(dateString);
    return date.toLocaleString();
  };

  const getMagnitudeColor = (magnitude) => {
    const colors = {
      trivial: '#90caf9',
      minor: '#fff176',
      moderate: '#ffb74d',
      major: '#ff8a65',
      complete_rewrite: '#f44336'
    };
    return colors[magnitude] || '#e0e0e0';
  };

  const getMagnitudeEmoji = (magnitude) => {
    const emojis = {
      trivial: <Circle size={10} fill="#94a3b8" stroke="#94a3b8" />,

      minor: <Circle size={10} fill="#facc15" stroke="#facc15" />,

      moderate: <Circle size={10} fill="#fb923c" stroke="#fb923c" />,

      major: <Circle size={10} fill="#ef4444" stroke="#ef4444" />,

      complete_rewrite: <Circle size={10} fill="#0f172a" stroke="#0f172a" />
    };
    return emojis[magnitude] || <Circle size={10} fill="#cbd5e1" stroke="#cbd5e1" />;
  };

  const getQualityGradeColor = (grade) => {
    const colors = {
      'A+': '#4caf50',
      'A': '#66bb6a',
      'B+': '#9ccc65',
      'B': '#d4e157',
      'C': '#ffeb3b',
      'D': '#ff9800',
      'F': '#f44336'
    };
    return colors[grade] || '#9e9e9e';
  };

  if (loading) {
    return (
      <div className="version-history-modal">
        <div className="version-history-content">
          <div className="version-history-header">
            <h2>{t('pages.version_history.title', 'Version History')}</h2>
            <button className="close-btn" onClick={onClose}><X size={14} /></button>
          </div>
          <div className="loading-spinner">
            <div className="spinner"></div>
            <p>{t('common.loading', 'Loading...')}</p>
          </div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="version-history-modal">
        <div className="version-history-content">
          <div className="version-history-header">
            <h2>{t('pages.version_history.title', 'Version History')}</h2>
            <button className="close-btn" onClick={onClose}><X size={14} /></button>
          </div>
          <div className="error-message">{error}</div>
          <button className="retry-btn" onClick={fetchVersionHistory}>
            {t('common.retry', 'Retry')}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="version-history-modal" onClick={onClose}>
      <div className="version-history-content" onClick={(e) => e.stopPropagation()}>
        <div className="version-history-header">
          <div>
            <h2>{t('pages.version_history.title', 'Version History')}</h2>
            <p className="page-url">{pageUrl}</p>
            <p className="version-count">
              {t('pages.version_history.total_versions', 'Total: {{count}} versions', { count: versions.length })}
              {currentVersion && ` (${t('pages.version_history.current', 'Current')}: v${currentVersion})`}
            </p>
          </div>
          <button className="close-btn" onClick={onClose}><X size={14} /></button>
        </div>

        <div className="versions-timeline">
          {versions.length === 0 ? (
            <div className="no-versions">
              <p>{t('pages.version_history.no_versions', 'No version history available')}</p>
            </div>
          ) : (
            versions.map((version, index) => (
              <div 
                key={version.id} 
                className={`version-card ${version.version_number === currentVersion ? 'current' : ''}`}
              >
                <div className="version-card-header">
                  <div className="version-number">
                    <span className="version-label">
                      v{version.version_number}
                    </span>
                    {version.version_number === currentVersion && (
                      <span className="current-badge">
                        {t('pages.version_history.current', 'Current')}
                      </span>
                    )}
                  </div>
                  <div className="version-date">
                    {formatDate(version.scraped_at)}
                  </div>
                </div>

                <div className="version-card-body">
                  {/* Title */}
                  {version.title && (
                    <div className="version-info-row">
                      <strong>{t('pages.version_history.title_label', 'Title')}:</strong>
                      <span>{version.title}</span>
                    </div>
                  )}

                  {/* Content Length */}
                  <div className="version-info-row">
                    <strong>{t('pages.version_history.content_length', 'Content')}:</strong>
                    <span>{version.char_count?.toLocaleString() || 0} {t('pages.version_history.chars', 'chars')}</span>
                  </div>

                  {/* Quality Score */}
                  {version.quality_score !== null && version.quality_score !== undefined && (
                    <div className="version-info-row">
                      <strong>{t('pages.version_history.quality', 'Quality')}:</strong>
                      <div className="quality-indicator">
                        <span 
                          className="quality-badge"
                          style={{ backgroundColor: getQualityGradeColor(version.quality_grade) }}
                        >
                          {version.quality_grade}
                        </span>
                        <span className="quality-score">{version.quality_score}/100</span>
                      </div>
                    </div>
                  )}

                  {/* Change Magnitude */}
                  {version.change_magnitude && index < versions.length - 1 && (
                    <div className="version-info-row">
                      <strong>{t('pages.version_history.changes', 'Changes')}:</strong>
                      <div className="change-indicator">
                        <span 
                          className="change-badge"
                          style={{ 
                            backgroundColor: getMagnitudeColor(version.change_magnitude),
                            color: ['major', 'complete_rewrite'].includes(version.change_magnitude) ? '#fff' : '#333'
                          }}
                        >
                          {getMagnitudeEmoji(version.change_magnitude)}
                          {' '}
                          {t(`pages.version_history.magnitude.${version.change_magnitude}`, version.change_magnitude)}
                        </span>
                        {version.change_score !== null && version.change_score !== undefined && (
                          <span className="change-score">{version.change_score.toFixed(1)}%</span>
                        )}
                      </div>
                    </div>
                  )}

                  {/* Changes Summary */}
                  {version.changes_summary && (
                    <div className="changes-summary">
                      <strong>{t('pages.version_history.summary', 'Summary')}:</strong>
                      <div className="changes-details">
                        {version.changes_summary.added_lines > 0 && (
                          <span className="change-stat added">
                            +{version.changes_summary.added_lines} {t('pages.version_history.lines_added', 'added')}
                          </span>
                        )}
                        {version.changes_summary.removed_lines > 0 && (
                          <span className="change-stat removed">
                            -{version.changes_summary.removed_lines} {t('pages.version_history.lines_removed', 'removed')}
                          </span>
                        )}
                        {version.changes_summary.modified_lines > 0 && (
                          <span className="change-stat modified">
                            ~{version.changes_summary.modified_lines} {t('pages.version_history.lines_modified', 'modified')}
                          </span>
                        )}
                      </div>
                    </div>
                  )}

                  {/* Content Hash */}
                  <div className="version-info-row content-hash">
                    <strong>{t('pages.version_history.hash', 'Hash')}:</strong>
                    <code>{version.content_hash?.substring(0, 12)}...</code>
                  </div>
                </div>

                {/* Version Timeline Connector */}
                {index < versions.length - 1 && (
                  <div className="timeline-connector"></div>
                )}
              </div>
            ))
          )}
        </div>

        <div className="version-history-footer">
          <button className="btn-secondary" onClick={onClose}>
            {t('common.close', 'Close')}
          </button>
        </div>
      </div>
    </div>
  );
};

export default PageVersionHistory;
