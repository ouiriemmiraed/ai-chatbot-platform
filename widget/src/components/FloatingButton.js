import React from 'react';
import './FloatingButton.css';
import { ReactComponent as Logo } from '../logo.svg';

const FloatingButton = ({ onClick, config }) => {
    const primaryColor = config?.primary_color || '#3B82F6';
    const positionStyle = config?.position_style || { bottom: '20px', right: '20px' };
    const size = config?.button_size || 60;
    const isTransparent = config?.transparent_background || false;

    return (
        <button
            className="floating-button"
            onClick={onClick}
            style={{
                backgroundColor: isTransparent ? 'transparent' : primaryColor,
                width: `${size}px`,
                height: `${size}px`,
                boxShadow: isTransparent ? 'none' : undefined,
                ...positionStyle
            }}
            aria-label="Ouvrir le chat"
        >
            {config?.icon_url ? (
                <img src={config.icon_url} alt="Chat" className="floating-icon-img" />
            ) : (
                <Logo width="24" height="24" />
            )}
        </button>
    );
};

export default FloatingButton;
