import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import infoIconSrc from '../assets/info.svg';

export function InfoButton({ label, title }) {
  const tooltipId = useId();
  const wrapRef = useRef(null);
  const pointerDownRef = useRef(false);
  const [openTrigger, setOpenTrigger] = useState(null); // null | 'hover' | 'focus' | 'click'
  const [popoverPosition, setPopoverPosition] = useState({ alignRight: false, shift: 0 });

  const buttonLabel = label || 'More information';
  const infoText = title == null ? '' : String(title);
  const hasInfo = Boolean(infoText.trim());
  const isOpen = hasInfo && openTrigger !== null;

  useEffect(() => {
    if (!isOpen) return undefined;

    const handleKeyDown = (event) => {
      if (event.defaultPrevented || event.isComposing) return;
      if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        event.stopImmediatePropagation?.();
        setOpenTrigger(null);
      }
    };

    const handleOutsidePointer = (event) => {
      if (wrapRef.current && !wrapRef.current.contains(event.target)) {
        setOpenTrigger(null);
      }
    };

    document.addEventListener('keydown', handleKeyDown, true);
    document.addEventListener('pointerdown', handleOutsidePointer);
    document.addEventListener('mousedown', handleOutsidePointer);
    return () => {
      document.removeEventListener('keydown', handleKeyDown, true);
      document.removeEventListener('pointerdown', handleOutsidePointer);
      document.removeEventListener('mousedown', handleOutsidePointer);
    };
  }, [isOpen]);

  useLayoutEffect(() => {
    if (!isOpen || !wrapRef.current || typeof window === 'undefined') {
      setPopoverPosition({ alignRight: false, shift: 0 });
      return;
    }
    const rect = wrapRef.current.getBoundingClientRect();
    if (rect.width > 0 && window.innerWidth > 0) {
      const estWidth = Math.min(304, Math.max(0, window.innerWidth - 32));
      if (rect.left + estWidth > window.innerWidth - 12) {
        if (rect.right - estWidth >= 12) {
          setPopoverPosition({ alignRight: true, shift: 0 });
        } else {
          const shift = Math.min(0, Math.max(12 - rect.left, window.innerWidth - 12 - (rect.left + estWidth)));
          setPopoverPosition({ alignRight: false, shift });
        }
        return;
      }
    }
    setPopoverPosition({ alignRight: false, shift: 0 });
  }, [isOpen]);

  return (
    <span
      ref={wrapRef}
      className="input-info-wrap"
      onMouseEnter={() => {
        if (hasInfo && openTrigger === null) setOpenTrigger('hover');
      }}
      onMouseLeave={() => {
        if (openTrigger === 'hover') setOpenTrigger(null);
      }}
      onBlur={(event) => {
        pointerDownRef.current = false;
        if (!wrapRef.current?.contains(event.relatedTarget)) {
          setOpenTrigger(null);
        }
      }}
    >
      <button
        type="button"
        className="input-info-btn"
        aria-label={buttonLabel}
        aria-expanded={hasInfo ? isOpen : undefined}
        aria-controls={isOpen ? tooltipId : undefined}
        aria-describedby={isOpen ? tooltipId : undefined}
        onPointerDown={() => {
          pointerDownRef.current = true;
        }}
        onMouseDown={() => {
          pointerDownRef.current = true;
        }}
        onPointerCancel={() => {
          pointerDownRef.current = false;
        }}
        onFocus={() => {
          if (hasInfo && !pointerDownRef.current && openTrigger === null) {
            setOpenTrigger('focus');
          }
        }}
        onClick={() => {
          const fromPointer = pointerDownRef.current;
          pointerDownRef.current = false;
          if (!hasInfo) return;
          setOpenTrigger((prev) => (fromPointer ? (prev === 'click' ? null : 'click') : (prev === null ? 'click' : null)));
        }}
      >
        <img className="input-info-icon" src={infoIconSrc} alt="" aria-hidden="true" />
      </button>
      {isOpen ? (
        <span
          id={tooltipId}
          role="tooltip"
          className={`input-info-popover${popoverPosition.alignRight ? ' is-aligned-right' : ''}`}
          style={popoverPosition.shift ? { '--popover-shift': `${popoverPosition.shift}px` } : undefined}
          onMouseDown={(event) => event.preventDefault()}
          onClick={(event) => {
            event.preventDefault();
            event.stopPropagation();
          }}
        >
          {infoText}
        </span>
      ) : null}
    </span>
  );
}
