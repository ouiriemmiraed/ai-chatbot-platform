import React, { useState, useEffect, useRef } from 'react';
import './PositionPicker.css';

const PositionPicker = ({ value, onChange }) => {
    const containerRef = useRef(null);
    const [position, setPosition] = useState({ bottom: '20px', right: '20px' });

    useEffect(() => {
        if (value && Object.keys(value).length > 0) {
            setPosition(value);
        }
    }, [value]);

    const handleClick = (e) => {
        if (!containerRef.current) return;

        const rect = containerRef.current.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;

        // Calculate percentages
        const width = rect.width;
        const height = rect.height;

        // Determine quadrant and set styles relative to nearest edges
        // We'll use pixels for more precise control as requested ("point in screen")
        // But for responsiveness, maybe we stick to simple Bottom-Right vs Bottom-Left logic but with custom offsets?
        // User asked: "choose where the position exactly not left and right... make in a screen a point"

        // Let's implement full freedom:
        // We will store Top/Left or Bottom/Right depending on which half of the screen we are

        const isRight = x > width / 2;
        const isBottom = y > height / 2;

        const newStyle = {};

        // If bottom half, use bottom offset
        if (isBottom) {
            const bottomPx = height - y;
            // Convert to percentage for better responsiveness or keep px?
            // Let's use % for the position picker representation, but maybe save as px or %?
            // "point and the position be in this point" -> implies visual exactness.
            // Let's use percentages relative to viewport for maximum responsiveness
            const bottomPct = (bottomPx / height) * 100;
            newStyle.bottom = `${bottomPct.toFixed(1)}%`;
        } else {
            const topPct = (y / height) * 100;
            newStyle.top = `${topPct.toFixed(1)}%`;
        }

        // If right half, use right offset
        if (isRight) {
            const rightPx = width - x;
            const rightPct = (rightPx / width) * 100;
            newStyle.right = `${rightPct.toFixed(1)}%`;
        } else {
            const leftPct = (x / width) * 100;
            newStyle.left = `${leftPct.toFixed(1)}%`;
        }

        setPosition(newStyle);
        onChange(newStyle);
    };

    // Calculate dot position for display
    const getDotStyle = () => {
        const style = { position: 'absolute' };
        if (position.top) style.top = position.top;
        if (position.bottom) style.bottom = position.bottom;
        if (position.left) style.left = position.left;
        if (position.right) style.right = position.right;
        return style;
    };

    return (
        <div className="position-picker-container">
            <label className="picker-label">Widget Position</label>
            <div className="screen-mockup" ref={containerRef} onClick={handleClick}>
                <div className="screen-content">
                    <div className="line line-1"></div>
                    <div className="line line-2"></div>
                    <div className="line line-3"></div>
                </div>
                <div className="widget-dot" style={getDotStyle()}>
                    <div className="pulse"></div>
                </div>
            </div>
            <div className="position-values">
                {Object.entries(position).map(([key, val]) => (
                    <span key={key} className="pos-badge">{key}: {val}</span>
                ))}
            </div>
            <p className="picker-hint">Click inside the box to place the widget</p>
        </div>
    );
};

export default PositionPicker;
