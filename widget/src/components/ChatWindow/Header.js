import React from 'react';
import { ReactComponent as Logo } from '../../logo.svg';

const Header = ({ config, onClose }) => {
    const primaryColor = config?.primary_color || '#3B82F6';
    const secondaryColor = config?.secondary_color || '#1E40AF';

    return (
        <div className="chat-header" style={{ background: `linear-gradient(135deg, ${primaryColor} 0%, ${secondaryColor} 100%)` }}>
            <div className="header-info">
                {config?.icon_url ? (
                    <img src={config.icon_url} alt="" className="header-icon" />
                ) : (
                    <div className="header-icon-fallback">
                        <Logo width="24" height="24" fill="white" />
                    </div>
                )}
                <h3>{config?.window_title || config?.name || 'Assistant'}</h3>
            </div>
            <button className="close-button" onClick={onClose} aria-label="Fermer le chat">
                ×
            </button>
        </div>
    );
};

export default Header;
