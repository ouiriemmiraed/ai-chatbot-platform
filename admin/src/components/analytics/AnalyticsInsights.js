// AnalyticsInsights — Sprint D advanced analytics shown when a single
// chatbot is selected. Fetches /analytics/owner/* endpoints and renders:
//   - Anomalies banner (top, real-time alerts)
//   - Funnel (Recharts BarChart)
//   - Heatmap 7×24 (custom CSS grid)
//   - Top questions (drill-down list)
//   - Quality sparkline (LineChart)
//   - Cost breakdown (BarChart by provider + daily cost)
//   - Content gaps (action buttons: addressed / ignored)
//
// One side-effect-free component, takes only chatbotId + days as props.
// Each card is self-contained and silent on its own loading state so the
// page stays responsive while widgets fill in independently.
import React, { useEffect, useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import {
    LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
    ResponsiveContainer, Cell,
} from 'recharts';
import {
    getOwnerFunnel, getOwnerTopQuestions, getOwnerHeatmap,
    getOwnerQualityTrend, getOwnerContentGaps, actionContentGap,
    getOwnerCost, getOwnerAnomalies,
} from '../../services/analyticsApi';
import './AnalyticsInsights.css';

import { AlertTriangle, Check, CircleDot, Info } from 'lucide-react';
import { getErrorMessage } from '../../utils/errorMessage';
const FUNNEL_COLORS = ['#5A8DEE', '#43BFA7', '#FFB02E', '#FF6584'];
const PROVIDER_COLORS = {
    openrouter: '#5A8DEE',
    gemini: '#FFB02E',
    ollama: '#43BFA7',
    frozen: '#94A3B8',
    unknown: '#CBD5E1',
};

// Heat color scale: 0 → light, max → deep red.
function heatColor(value, maxValue) {
    if (!maxValue || value === 0) return '#F1F5F9';
    const t = Math.min(1, value / maxValue);
    const r = Math.round(248 - 80 * t);
    const g = Math.round(180 - 130 * t);
    const b = Math.round(180 - 150 * t);
    return `rgb(${r},${g},${b})`;
}

const useFetch = (fn, deps) => {
    const [data, setData] = useState(null);
    const [error, setError] = useState(null);
    const [loading, setLoading] = useState(false);

    const refetch = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const res = await fn();
            setData(res.data);
        } catch (e) {
            setError(getErrorMessage(e, 'load failed'));
        } finally {
            setLoading(false);
        }
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, deps);

    useEffect(() => { refetch(); }, [refetch]);
    return { data, error, loading, refetch };
};

// ── Anomalies banner ─────────────────────────────────────────────────────────
const AnomaliesBanner = ({ chatbotId }) => {
    const { data, loading } = useFetch(() => getOwnerAnomalies(chatbotId), [chatbotId]);
    if (loading || !data || !data.alerts?.length) return null;
    return (
        <div className="anomalies-banner">
            {data.alerts.map((a, i) => (
                <div key={i} className={`anomaly-card anomaly-${a.level}`}>
                    <span className="anomaly-icon">
                        {a.level === 'danger' ? <CircleDot size={16} /> : a.level === 'warning' ? <AlertTriangle size={16} /> : <Info size={16} />}
                    </span>
                    <span className="anomaly-message">{a.message}</span>
                </div>
            ))}
        </div>
    );
};

// ── Funnel chart ─────────────────────────────────────────────────────────────
const FunnelCard = ({ chatbotId, days }) => {
    const { t } = useTranslation();
    const { data, loading } = useFetch(() => getOwnerFunnel(chatbotId, days), [chatbotId, days]);
    if (loading) return <div className="insight-card">{t('analyticsInsights.loading')}</div>;
    if (!data) return null;
    const stages = data.stages || [];
    return (
        <div className="insight-card">
            <h3>{t('analyticsInsights.funnelTitle')}</h3>
            <div className="funnel-stages">
                {stages.map((s, i) => {
                    const top = stages[0]?.count || 1;
                    const ratio = s.count / top;
                    return (
                        <div key={s.name} className="funnel-stage">
                            <div className="funnel-stage-label">
                                {s.name} <span className="funnel-stage-count">{s.count}</span>
                            </div>
                            <div className="funnel-bar-track">
                                <div
                                    className="funnel-bar-fill"
                                    style={{
                                        width: `${ratio * 100}%`,
                                        background: FUNNEL_COLORS[i % FUNNEL_COLORS.length],
                                    }}
                                />
                            </div>
                            {i > 0 && stages[i - 1].count > 0 && (
                                <div className="funnel-stage-rate">
                                    {t('analyticsInsights.rateOfPrevious', { pct: Math.round((s.count / stages[i - 1].count) * 100) })}
                                </div>
                            )}
                        </div>
                    );
                })}
            </div>
        </div>
    );
};

// ── Heatmap (7×24) ───────────────────────────────────────────────────────────
const HeatmapCard = ({ chatbotId, days }) => {
    const { t } = useTranslation();
    const { data, loading } = useFetch(() => getOwnerHeatmap(chatbotId, days), [chatbotId, days]);
    if (loading) return <div className="insight-card">{t('analyticsInsights.loading')}</div>;
    if (!data) return null;
    const { grid, labels } = data;
    const max = Math.max(0, ...grid.flat());
    return (
        <div className="insight-card">
            <h3>{t('analyticsInsights.heatmapTitle')}</h3>
            <div className="heatmap-wrap">
                <div className="heatmap-row heatmap-header">
                    <div className="heatmap-cell-empty"></div>
                    {labels.cols.map(c => <div key={c} className="heatmap-col-label">{c.slice(0, 2)}</div>)}
                </div>
                {labels.rows.map((rowLabel, dow) => (
                    <div key={rowLabel} className="heatmap-row">
                        <div className="heatmap-row-label">{rowLabel}</div>
                        {grid[dow].map((v, hour) => (
                            <div
                                key={hour}
                                className="heatmap-cell"
                                style={{ background: heatColor(v, max) }}
                                title={t('analyticsInsights.heatmapCellTitle', { day: rowLabel, hour: labels.cols[hour], count: v })}
                            >
                                {v > 0 ? v : ''}
                            </div>
                        ))}
                    </div>
                ))}
            </div>
        </div>
    );
};

// ── Top questions ────────────────────────────────────────────────────────────
const TopQuestionsCard = ({ chatbotId, days }) => {
    const { t } = useTranslation();
    const [onlyUnanswered, setOnlyUnanswered] = useState(false);
    const { data, loading } = useFetch(
        () => getOwnerTopQuestions(chatbotId, { days, onlyUnanswered }),
        [chatbotId, days, onlyUnanswered],
    );
    return (
        <div className="insight-card">
            <div className="insight-card-head">
                <h3>{t('analyticsInsights.topQuestionsTitle')}</h3>
                <label className="checkbox-inline">
                    <input
                        type="checkbox"
                        checked={onlyUnanswered}
                        onChange={e => setOnlyUnanswered(e.target.checked)}
                    />
                    {t('analyticsInsights.onlyUnanswered')}
                </label>
            </div>
            {loading ? <p>{t('analyticsInsights.loading')}</p> : (
                <ul className="top-questions-list">
                    {(data?.items || []).map(it => (
                        <li key={it.keyword} className="top-question-item">
                            <span className="top-question-count">{it.count}</span>
                            <span className="top-question-keyword">{it.keyword}</span>
                            <span className="top-question-summary">{it.sample_summary}</span>
                        </li>
                    ))}
                    {(!data?.items?.length && !loading) && (
                        <li className="top-question-empty">{t('analyticsInsights.noData')}</li>
                    )}
                </ul>
            )}
        </div>
    );
};

// ── Quality sparkline ────────────────────────────────────────────────────────
const QualityCard = ({ chatbotId }) => {
    const { t } = useTranslation();
    const { data, loading } = useFetch(() => getOwnerQualityTrend(chatbotId, 14), [chatbotId]);
    if (loading) return <div className="insight-card">{t('analyticsInsights.loading')}</div>;
    const points = data?.points || [];
    const lastScore = points.length ? points[points.length - 1].score : null;
    return (
        <div className="insight-card">
            <h3>{t('analyticsInsights.qualityTitle')}</h3>
            <div className="quality-headline">
                <span className="quality-score">
                    {lastScore == null ? '—' : `${Math.round(lastScore * 100)} / 100`}
                </span>
                <span className="quality-sub">{t('analyticsInsights.today')}</span>
            </div>
            <div style={{ width: '100%', height: 120 }}>
                <ResponsiveContainer>
                    <LineChart data={points}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" />
                        <XAxis dataKey="date" tick={{ fontSize: 10 }} />
                        <YAxis domain={[0, 1]} tick={{ fontSize: 10 }} />
                        <Tooltip />
                        <Line type="monotone" dataKey="score" stroke="#43BFA7" strokeWidth={2} dot={{ r: 2 }} />
                    </LineChart>
                </ResponsiveContainer>
            </div>
        </div>
    );
};

// ── Cost breakdown ───────────────────────────────────────────────────────────
const CostCard = ({ chatbotId, days }) => {
    const { t } = useTranslation();
    const { data, loading } = useFetch(() => getOwnerCost(chatbotId, days), [chatbotId, days]);
    if (loading) return <div className="insight-card">{t('analyticsInsights.loading')}</div>;
    const total = (data?.daily || []).reduce((s, d) => s + d.cost_usd, 0);
    return (
        <div className="insight-card">
            <h3>{t('analyticsInsights.costTitle')}</h3>
            <div className="cost-headline">
                <span className="cost-total">${total.toFixed(4)}</span>
                <span className="cost-sub">{t('analyticsInsights.costOverDays', { days })}</span>
            </div>
            {data?.by_provider?.length > 0 && (
                <div style={{ width: '100%', height: 160 }}>
                    <ResponsiveContainer>
                        <BarChart data={data.by_provider}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" />
                            <XAxis dataKey="provider" tick={{ fontSize: 11 }} />
                            <YAxis tick={{ fontSize: 11 }} />
                            <Tooltip formatter={v => typeof v === 'number' ? `$${v.toFixed(4)}` : v} />
                            <Bar dataKey="cost_usd">
                                {data.by_provider.map((d, i) => (
                                    <Cell key={i} fill={PROVIDER_COLORS[d.provider] || '#94A3B8'} />
                                ))}
                            </Bar>
                        </BarChart>
                    </ResponsiveContainer>
                </div>
            )}
            <div className="cost-providers">
                {(data?.by_provider || []).map(p => (
                    <div key={p.provider} className="cost-provider-row">
                        <span className="cost-provider-dot" style={{ background: PROVIDER_COLORS[p.provider] || '#94A3B8' }} />
                        <span className="cost-provider-name">{p.provider}</span>
                        <span className="cost-provider-spend">${p.cost_usd.toFixed(4)}</span>
                        <span className="cost-provider-calls">{t('analyticsInsights.calls', { count: p.calls })}</span>
                    </div>
                ))}
            </div>
        </div>
    );
};

// ── Content gaps ─────────────────────────────────────────────────────────────
const ContentGapsCard = ({ chatbotId }) => {
    const { t } = useTranslation();
    const { data, loading, refetch } = useFetch(
        () => getOwnerContentGaps(chatbotId), [chatbotId],
    );
    const handleAction = async (clusterId, action) => {
        try {
            await actionContentGap(chatbotId, clusterId, action);
            await refetch();
        } catch (e) {
            console.error(e);
        }
    };
    if (loading) return <div className="insight-card">{t('analyticsInsights.loading')}</div>;
    const items = data?.items || [];
    return (
        <div className="insight-card">
            <h3>{t('analyticsInsights.contentGapsTitle')}</h3>
            {items.length === 0 ? (
                <p className="content-gaps-empty">{t('analyticsInsights.contentGapsEmpty')}</p>
            ) : (
                <ul className="content-gaps-list">
                    {items.map(g => (
                        <li key={g.cluster_id} className="content-gap-item">
                            <div className="content-gap-head">
                                <span className="content-gap-count">{t('analyticsInsights.timesCount', { count: g.question_count })}</span>
                                <strong className="content-gap-topic">{g.suggested_topic}</strong>
                            </div>
                            <ul className="content-gap-examples">
                                {(g.example_questions || []).slice(0, 3).map((q, i) => (
                                    <li key={i}>« {q} »</li>
                                ))}
                            </ul>
                            <div className="content-gap-actions">
                                <button onClick={() => handleAction(g.cluster_id, 'addressed')}>
                                    <Check size={16} /> {t('analyticsInsights.gapAddressed')}
                                </button>
                                <button onClick={() => handleAction(g.cluster_id, 'ignored')}>
                                    {t('analyticsInsights.gapIgnore')}
                                </button>
                            </div>
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
};

// ── Top-level layout ─────────────────────────────────────────────────────────
const AnalyticsInsights = ({ chatbotId, days }) => {
    const { t } = useTranslation();
    if (!chatbotId) {
        return (
            <div className="insights-empty-hint">
                {t('analyticsInsights.selectChatbotHint')}
            </div>
        );
    }
    return (
        <div className="insights-wrap">
            <AnomaliesBanner chatbotId={chatbotId} />
            <div className="insights-grid">
                <FunnelCard chatbotId={chatbotId} days={days} />
                <QualityCard chatbotId={chatbotId} />
                <CostCard chatbotId={chatbotId} days={days} />
                <ContentGapsCard chatbotId={chatbotId} />
            </div>
            <HeatmapCard chatbotId={chatbotId} days={days} />
            <TopQuestionsCard chatbotId={chatbotId} days={days} />
        </div>
    );
};

export default AnalyticsInsights;
